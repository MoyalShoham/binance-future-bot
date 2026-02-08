"""
Trailing Stop Monitor

Runs inside EmergencyController's background thread to actively manage open positions.
Tracks peak prices, applies trailing stops, hard stops, breakeven stops, and time-based exits.
"""

import math
from typing import Dict, Any, Optional
from datetime import datetime
import structlog

from infrastructure.database import DatabaseSession, PnLLedger, Execution, TradingDecision
from infrastructure.database.queries import DatabaseQueries
from infrastructure.execution_modes import OrderExecutor

logger = structlog.get_logger()


class TrailingStopMonitor:
    """
    Monitors open positions and applies trailing stop logic.

    Runs every 10 seconds from EmergencyController's monitoring loop.
    Tracks peak prices in-memory and closes positions when stop conditions are met.

    Close reasons:
    - TRAIL_STOP: Price retraced past trailing distance from peak
    - HARD_STOP: Price hit hard stop loss from trading decision
    - TIME_EXIT: Position held past max holding time
    - BREAKEVEN_STOP: Price returned to entry after initially moving in profit
    """

    def __init__(self, binance_client, db_session: DatabaseSession, config: Dict[str, Any]):
        self.binance_client = binance_client
        self.db_session = db_session
        self.config = config

        # Trailing stop config
        ts_config = config.get("trailing_stop", {})
        self.enabled = ts_config.get("enabled", True)
        self.activation_threshold_pct = ts_config.get("activation_threshold_pct", 0.002)
        self.trail_distance_pct = ts_config.get("trail_distance_pct", 0.005)
        self.breakeven_threshold_pct = ts_config.get("breakeven_threshold_pct", 0.003)
        self.hard_stop_fallback_pct = ts_config.get("hard_stop_fallback_pct", 0.02)
        self.max_holding_time_seconds = ts_config.get(
            "max_holding_time_seconds",
            config.get("trading", {}).get("scalping", {}).get("max_holding_time_seconds", 600)
        )
        self.log_peak_updates = ts_config.get("log_peak_updates", False)

        # Execution mode from config
        self.execution_mode = config.get("trading", {}).get("execution_mode", "paper")

        # Fee rate for P&L calculation
        self.taker_fee_bps = config.get("execution", {}).get("fees", {}).get("taker_bps", 5)

        # In-memory state
        self.peak_prices = {}  # {pnl_ledger_id: peak_price}
        self.position_states = {}  # {pnl_ledger_id: "MONITORING" | "TRAILING_ACTIVE" | "BREAKEVEN_ACTIVE"}
        self.closing_in_progress = set()  # prevent race conditions

        # Symbol rules for quantity rounding (reuse from OrderExecutor)
        self.SYMBOL_RULES = OrderExecutor.SYMBOL_RULES
        self.DEFAULT_RULES = OrderExecutor.DEFAULT_RULES

        logger.info(
            "TrailingStopMonitor initialized",
            enabled=self.enabled,
            activation_pct=self.activation_threshold_pct,
            trail_distance_pct=self.trail_distance_pct,
            breakeven_pct=self.breakeven_threshold_pct,
            max_holding_seconds=self.max_holding_time_seconds,
            execution_mode=self.execution_mode,
        )

    def check_all_positions(self):
        """
        Check all open positions and apply trailing stop logic.
        Called every 10 seconds from EmergencyController loop.
        """
        if not self.enabled:
            return

        try:
            with self.db_session.session_scope() as session:
                queries = DatabaseQueries(session)
                open_positions = queries.get_open_positions()

                if not open_positions:
                    # Clean up stale tracking state
                    self.peak_prices.clear()
                    self.position_states.clear()
                    return

                logger.info("Trailing stop monitoring", open_positions=len(open_positions))

                for position in open_positions:
                    try:
                        self._check_position(position, session)
                    except Exception as e:
                        logger.error(
                            "Error checking position",
                            position_id=position.id,
                            symbol=position.symbol,
                            error=str(e),
                            exc_info=True,
                        )

                # Clean up tracking for positions that no longer exist
                open_ids = {p.id for p in open_positions}
                stale_ids = set(self.peak_prices.keys()) - open_ids
                for stale_id in stale_ids:
                    self.peak_prices.pop(stale_id, None)
                    self.position_states.pop(stale_id, None)
                    self.closing_in_progress.discard(stale_id)

        except Exception as e:
            logger.error("TrailingStopMonitor check_all_positions failed", error=str(e), exc_info=True)

    def _check_position(self, position: PnLLedger, session):
        """Check a single position against all stop conditions."""
        pos_id = position.id

        # Skip if already being closed
        if pos_id in self.closing_in_progress:
            return

        # Double-check it's still open
        if position.is_closed:
            return

        # Fetch current price
        current_price = self.binance_client.get_ticker_price(position.symbol)

        # Initialize tracking state if new
        if pos_id not in self.peak_prices:
            self.peak_prices[pos_id] = current_price
            self.position_states[pos_id] = "MONITORING"
            logger.info(
                "Tracking new position",
                position_id=pos_id,
                symbol=position.symbol,
                side=position.side,
                entry_price=position.entry_price,
                current_price=current_price,
            )

        # Update peak price
        is_long = position.side == "LONG"
        old_peak = self.peak_prices[pos_id]

        if is_long:
            if current_price > old_peak:
                self.peak_prices[pos_id] = current_price
                if self.log_peak_updates:
                    logger.debug("Peak price updated (LONG)", position_id=pos_id, peak=current_price)
        else:  # SHORT - peak is the lowest price
            if current_price < old_peak:
                self.peak_prices[pos_id] = current_price
                if self.log_peak_updates:
                    logger.debug("Peak price updated (SHORT)", position_id=pos_id, peak=current_price)

        peak_price = self.peak_prices[pos_id]
        entry_price = position.entry_price
        state = self.position_states[pos_id]

        # Calculate profit percentage from entry
        if is_long:
            profit_from_entry_pct = (current_price - entry_price) / entry_price
            profit_from_peak_pct = (peak_price - entry_price) / entry_price
            retrace_from_peak_pct = (peak_price - current_price) / peak_price if peak_price > 0 else 0
        else:
            profit_from_entry_pct = (entry_price - current_price) / entry_price
            profit_from_peak_pct = (entry_price - peak_price) / entry_price
            retrace_from_peak_pct = (current_price - peak_price) / peak_price if peak_price > 0 else 0

        # === 1. Check hard stop loss ===
        hard_stop = self._get_hard_stop(position, session)
        if hard_stop is not None:
            if is_long and current_price <= hard_stop:
                self._close_position(position, current_price, "HARD_STOP", session)
                return
            elif not is_long and current_price >= hard_stop:
                self._close_position(position, current_price, "HARD_STOP", session)
                return
        else:
            # Fallback hard stop
            if is_long and profit_from_entry_pct <= -self.hard_stop_fallback_pct:
                self._close_position(position, current_price, "HARD_STOP", session)
                return
            elif not is_long and profit_from_entry_pct <= -self.hard_stop_fallback_pct:
                self._close_position(position, current_price, "HARD_STOP", session)
                return

        # === 2. Check time-based exit ===
        holding_seconds = (datetime.utcnow() - position.entry_time).total_seconds()
        if holding_seconds >= self.max_holding_time_seconds:
            self._close_position(position, current_price, "TIME_EXIT", session)
            return

        # === 3. Check breakeven activation ===
        if state == "MONITORING" and profit_from_peak_pct >= self.breakeven_threshold_pct:
            self.position_states[pos_id] = "BREAKEVEN_ACTIVE"
            logger.info(
                "Breakeven stop activated",
                position_id=pos_id,
                symbol=position.symbol,
                profit_pct=f"{profit_from_peak_pct:.4f}",
            )
            state = "BREAKEVEN_ACTIVE"

        # === 4. Check trailing stop activation ===
        if state in ("MONITORING", "BREAKEVEN_ACTIVE") and profit_from_peak_pct >= self.activation_threshold_pct:
            self.position_states[pos_id] = "TRAILING_ACTIVE"
            logger.info(
                "Trailing stop activated",
                position_id=pos_id,
                symbol=position.symbol,
                profit_pct=f"{profit_from_peak_pct:.4f}",
                peak_price=peak_price,
            )
            state = "TRAILING_ACTIVE"

        # === 5. Check breakeven stop trigger ===
        if state == "BREAKEVEN_ACTIVE" and profit_from_entry_pct <= 0:
            self._close_position(position, current_price, "BREAKEVEN_STOP", session)
            return

        # === 6. Check trailing stop trigger ===
        if state == "TRAILING_ACTIVE" and retrace_from_peak_pct >= self.trail_distance_pct:
            self._close_position(position, current_price, "TRAIL_STOP", session)
            return

    def _get_hard_stop(self, position: PnLLedger, session) -> Optional[float]:
        """Get the hard stop loss price from the original TradingDecision."""
        try:
            if not position.execution_id:
                return None

            execution = session.query(Execution).filter(
                Execution.id == position.execution_id
            ).first()

            if not execution:
                return None

            decision = session.query(TradingDecision).filter(
                TradingDecision.id == execution.decision_id
            ).first()

            if decision and decision.stop_loss:
                return decision.stop_loss

            return None
        except Exception as e:
            logger.error("Failed to get hard stop", position_id=position.id, error=str(e))
            return None

    def _close_position(self, position: PnLLedger, current_price: float, reason: str, session):
        """Close a position via Binance API (or paper) and update PnLLedger."""
        pos_id = position.id

        # Prevent duplicate close attempts
        if pos_id in self.closing_in_progress:
            return
        self.closing_in_progress.add(pos_id)

        try:
            is_long = position.side == "LONG"
            close_side = "SELL" if is_long else "BUY"
            quantity = self._round_quantity(position.symbol, position.quantity)

            logger.info(
                "Closing position",
                position_id=pos_id,
                symbol=position.symbol,
                side=position.side,
                reason=reason,
                entry_price=position.entry_price,
                current_price=current_price,
                quantity=quantity,
            )

            # Execute close order (skip API call in paper mode)
            if self.execution_mode != "paper":
                try:
                    self.binance_client.create_order(
                        symbol=position.symbol,
                        side=close_side,
                        order_type="MARKET",
                        quantity=quantity,
                        reduce_only=True,
                    )
                except Exception as e:
                    logger.error(
                        "Failed to close position on Binance",
                        position_id=pos_id,
                        error=str(e),
                        exc_info=True,
                    )
                    self.closing_in_progress.discard(pos_id)
                    return

            # Calculate realized P&L
            exit_price = current_price
            if is_long:
                raw_pnl = (exit_price - position.entry_price) * position.quantity * position.leverage
            else:
                raw_pnl = (position.entry_price - exit_price) * position.quantity * position.leverage

            # Estimate fees (entry + exit)
            notional = position.entry_price * position.quantity
            fee_rate = self.taker_fee_bps / 10000
            total_fees = notional * fee_rate * 2  # entry + exit fees
            realized_pnl = raw_pnl - total_fees

            holding_seconds = int((datetime.utcnow() - position.entry_time).total_seconds())

            # Update PnLLedger
            position.exit_price = exit_price
            position.exit_time = datetime.utcnow()
            position.realized_pnl_usdt = round(realized_pnl, 6)
            position.fees_usdt = round(total_fees, 6)
            position.holding_time_seconds = holding_seconds
            position.is_closed = True

            session.flush()

            logger.info(
                "Position closed",
                position_id=pos_id,
                symbol=position.symbol,
                reason=reason,
                entry_price=position.entry_price,
                exit_price=exit_price,
                realized_pnl=round(realized_pnl, 6),
                fees=round(total_fees, 6),
                holding_time_seconds=holding_seconds,
            )

            # Clean up tracking state
            self.peak_prices.pop(pos_id, None)
            self.position_states.pop(pos_id, None)

        except Exception as e:
            logger.error(
                "Failed to close position",
                position_id=pos_id,
                reason=reason,
                error=str(e),
                exc_info=True,
            )
        finally:
            self.closing_in_progress.discard(pos_id)

    def _round_quantity(self, symbol: str, quantity: float) -> float:
        """Round quantity to symbol's step size."""
        rules = self.SYMBOL_RULES.get(symbol, self.DEFAULT_RULES)
        step = rules["step_size"]
        rounded = math.floor(quantity / step) * step
        decimals = len(str(step).rstrip('0').split('.')[-1]) if '.' in str(step) else 0
        return round(rounded, decimals)
