"""
Trailing Stop Monitor — Ratcheting TP/SL System

Each position gets explicit TP and SL price levels.
When TP is hit, levels ratchet (recalculate from current price) — position stays open.
When SL is hit, position CLOSES.
Time exit remains as safety net.
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
    Monitors open positions with ratcheting TP/SL levels.

    Runs every 10 seconds from EmergencyController's monitoring loop.

    Close reasons:
    - STOP_LOSS: Price hit SL level
    - TIME_EXIT: Position held past max holding time
    """

    def __init__(self, binance_client, db_session: DatabaseSession, config: Dict[str, Any], trades_db=None):
        self.binance_client = binance_client
        self.db_session = db_session
        self.config = config
        self.trades_db = trades_db

        # Trailing stop config
        ts_config = config.get("trailing_stop", {})
        self.enabled = ts_config.get("enabled", True)
        self.tp_pct = ts_config.get("tp_pct", 0.005)
        self.sl_pct = ts_config.get("sl_pct", 0.005)
        self.use_decision_stop_loss = ts_config.get("use_decision_stop_loss", True)
        self.hard_stop_fallback_pct = ts_config.get("hard_stop_fallback_pct", 0.02)
        self.max_holding_time_seconds = ts_config.get(
            "max_holding_time_seconds",
            config.get("trading", {}).get("scalping", {}).get("max_holding_time_seconds", 600)
        )
        self.log_level_updates = ts_config.get("log_level_updates", False)

        # Execution mode from config
        self.execution_mode = config.get("trading", {}).get("execution_mode", "paper")

        # Fee rate for P&L calculation
        self.taker_fee_bps = config.get("execution", {}).get("fees", {}).get("taker_bps", 5)

        # In-memory state: {pnl_ledger_id: {"tp": float, "sl": float, "ratchet_count": int}}
        self.position_levels = {}
        self.closing_in_progress = set()  # prevent race conditions

        # Symbol rules for quantity rounding (reuse from OrderExecutor)
        self.SYMBOL_RULES = OrderExecutor.FALLBACK_SYMBOL_RULES
        self.DEFAULT_RULES = OrderExecutor.DEFAULT_RULES

        logger.info(
            "TrailingStopMonitor initialized",
            enabled=self.enabled,
            tp_pct=self.tp_pct,
            sl_pct=self.sl_pct,
            use_decision_stop_loss=self.use_decision_stop_loss,
            max_holding_seconds=self.max_holding_time_seconds,
            execution_mode=self.execution_mode,
        )

    def check_all_positions(self):
        """
        Check all open positions and apply ratcheting TP/SL logic.
        Called every 10 seconds from EmergencyController loop.
        """
        if not self.enabled:
            return

        try:
            with self.db_session.session_scope() as session:
                queries = DatabaseQueries(session)
                open_positions = queries.get_open_positions()

                if not open_positions:
                    self.position_levels.clear()
                    return

                logger.debug("Trailing stop monitoring", open_positions=len(open_positions))

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
                stale_ids = set(self.position_levels.keys()) - open_ids
                for stale_id in stale_ids:
                    self.position_levels.pop(stale_id, None)
                    self.closing_in_progress.discard(stale_id)

        except Exception as e:
            logger.error("TrailingStopMonitor check_all_positions failed", error=str(e), exc_info=True)

    def _check_position(self, position: PnLLedger, session):
        """Check a single position against TP/SL levels and time exit."""
        pos_id = position.id

        # Skip if already being closed
        if pos_id in self.closing_in_progress:
            return

        # Double-check it's still open
        if position.is_closed:
            return

        # Skip positions with zero quantity (stale DB entries)
        if not position.quantity or position.quantity <= 0:
            logger.warning(
                "Skipping position with zero quantity, marking closed",
                position_id=pos_id,
                symbol=position.symbol,
            )
            position.is_closed = True
            session.commit()
            return

        is_long = position.side == "LONG"
        current_price = self.binance_client.get_ticker_price(position.symbol)

        # === Initialize TP/SL levels for new position ===
        if pos_id not in self.position_levels:
            initial_sl = self._get_initial_sl(position, current_price, is_long, session)
            initial_tp = self._calc_tp(current_price, is_long)

            self.position_levels[pos_id] = {
                "tp": initial_tp,
                "sl": initial_sl,
                "ratchet_count": 0,
            }

            logger.info(
                "Tracking new position",
                position_id=pos_id,
                symbol=position.symbol,
                side=position.side,
                entry_price=position.entry_price,
                current_price=current_price,
                tp=round(initial_tp, 6),
                sl=round(initial_sl, 6),
            )

        levels = self.position_levels[pos_id]
        tp = levels["tp"]
        sl = levels["sl"]

        # === 1. Check SL ===
        if is_long and current_price <= sl:
            self._close_position(position, current_price, "STOP_LOSS", session)
            return
        elif not is_long and current_price >= sl:
            self._close_position(position, current_price, "STOP_LOSS", session)
            return

        # === 2. Check TP → Ratchet ===
        if is_long and current_price >= tp:
            self._ratchet(pos_id, current_price, is_long, position.symbol)
        elif not is_long and current_price <= tp:
            self._ratchet(pos_id, current_price, is_long, position.symbol)

        # === 3. Check time exit ===
        holding_seconds = (datetime.utcnow() - position.entry_time).total_seconds()
        if holding_seconds >= self.max_holding_time_seconds:
            self._close_position(position, current_price, "TIME_EXIT", session)
            return

    def _get_initial_sl(self, position: PnLLedger, current_price: float, is_long: bool, session) -> float:
        """Determine initial SL: use TradingDecision hard stop if available, else fallback."""
        if self.use_decision_stop_loss:
            hard_stop = self._get_hard_stop(position, session)
            if hard_stop is not None:
                return hard_stop

        # Fallback: use hard_stop_fallback_pct from entry price
        if is_long:
            return position.entry_price * (1 - self.hard_stop_fallback_pct)
        else:
            return position.entry_price * (1 + self.hard_stop_fallback_pct)

    def _calc_tp(self, price: float, is_long: bool) -> float:
        """Calculate TP level from a given price."""
        if is_long:
            return price * (1 + self.tp_pct)
        else:
            return price * (1 - self.tp_pct)

    def _calc_sl(self, price: float, is_long: bool) -> float:
        """Calculate SL level from a given price."""
        if is_long:
            return price * (1 - self.sl_pct)
        else:
            return price * (1 + self.sl_pct)

    def _ratchet(self, pos_id: int, current_price: float, is_long: bool, symbol: str):
        """Ratchet TP/SL levels from current price after TP hit."""
        levels = self.position_levels[pos_id]
        old_tp = levels["tp"]
        old_sl = levels["sl"]

        new_tp = self._calc_tp(current_price, is_long)
        new_sl = self._calc_sl(current_price, is_long)

        levels["tp"] = new_tp
        levels["sl"] = new_sl
        levels["ratchet_count"] += 1

        logger.info(
            "TP hit, ratcheting",
            position_id=pos_id,
            symbol=symbol,
            ratchet=levels["ratchet_count"],
            price=current_price,
            old_tp=round(old_tp, 6),
            new_tp=round(new_tp, 6),
            old_sl=round(old_sl, 6),
            new_sl=round(new_sl, 6),
        )

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

            # Record close in flat trades DB
            if self.trades_db and position.execution_id:
                try:
                    self.trades_db.record_close(
                        execution_id=position.execution_id,
                        exit_price=exit_price,
                        pnl_usdt=round(realized_pnl, 6),
                        fees_usdt=round(total_fees, 6),
                        holding_time_seconds=holding_seconds,
                        close_reason=reason,
                    )
                except Exception as e:
                    logger.warning("Failed to record trade close in trades_db", error=str(e))

            # Include ratchet count in close log
            ratchet_count = self.position_levels.get(pos_id, {}).get("ratchet_count", 0)

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
                ratchets=ratchet_count,
            )

            # Clean up tracking state
            self.position_levels.pop(pos_id, None)

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
