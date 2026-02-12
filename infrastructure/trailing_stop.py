"""
Position Sync Monitor (formerly Trailing Stop Monitor)

Runs inside EmergencyController's background thread to manage open positions.
For new positions with Binance-side SL/TP orders: syncs with Binance to detect closures.
For legacy positions (no SL/TP order IDs): falls back to in-memory trailing stop logic.

Close reasons:
- SL_TRIGGERED: Binance-side stop loss order filled
- TP_TRIGGERED: Binance-side take profit order filled
- TIME_EXIT: Position held past max holding time (cancel SL/TP + market close)
- TRAIL_STOP: (legacy) Price retraced past trailing distance from peak
- HARD_STOP: (legacy) Price hit hard stop loss
- BREAKEVEN_STOP: (legacy) Price returned to entry after profit
"""

import math
from typing import Dict, Any, Optional, List
from datetime import datetime
import structlog

from infrastructure.database import DatabaseSession, PnLLedger, Execution, TradingDecision
from infrastructure.database.queries import DatabaseQueries
from infrastructure.execution_modes import OrderExecutor

logger = structlog.get_logger()


class TrailingStopMonitor:
    """
    Monitors open positions and syncs with Binance.

    New positions (with sl_order_id/tp_order_id):
    - Detects when Binance closes the position via SL/TP
    - Handles time-based exits by cancelling SL/TP and sending market close

    Legacy positions (no sl_order_id/tp_order_id):
    - Falls back to old in-memory trailing stop logic
    """

    def __init__(self, binance_client, db_session: DatabaseSession, config: Dict[str, Any], trades_db=None):
        self.binance_client = binance_client
        self.db_session = db_session
        self.config = config
        self.trades_db = trades_db

        # Trailing stop config (used for legacy fallback)
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

        # Dynamic SL config (for Binance-side SL adjustment)
        dynamic_sl_config = ts_config.get("dynamic_sl", {})
        self.dynamic_sl_enabled = dynamic_sl_config.get("enabled", True)
        self.dsl_breakeven_pct = dynamic_sl_config.get("breakeven_move_pct", 0.004)
        self.dsl_trail_activation_pct = dynamic_sl_config.get("trail_activation_pct", 0.008)
        self.dsl_trail_step_pct = dynamic_sl_config.get("trail_step_pct", 0.003)
        self.dsl_min_move_pct = dynamic_sl_config.get("min_move_pct", 0.001)

        # Dynamic SL states: {pnl_ledger_id: {"stage": "initial"|"breakeven"|"trailing", "current_sl": float}}
        self.sl_states = {}

        # In-memory state (legacy trailing stop)
        self.peak_prices = {}  # {pnl_ledger_id: peak_price}
        self.position_states = {}  # {pnl_ledger_id: "MONITORING" | "TRAILING_ACTIVE" | "BREAKEVEN_ACTIVE"}
        self.closing_in_progress = set()  # prevent race conditions

        # Symbol rules for quantity rounding (reuse from OrderExecutor)
        self.SYMBOL_RULES = OrderExecutor.FALLBACK_SYMBOL_RULES
        self.DEFAULT_RULES = OrderExecutor.DEFAULT_RULES

        logger.info(
            "TrailingStopMonitor initialized",
            enabled=self.enabled,
            max_holding_seconds=self.max_holding_time_seconds,
            execution_mode=self.execution_mode,
        )

    def check_all_positions(self):
        """
        Check all open positions.
        For positions with SL/TP order IDs: sync with Binance.
        For legacy positions: apply trailing stop logic.
        Called every 5 seconds from EmergencyController loop.
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

                logger.debug("Position sync monitoring", open_positions=len(open_positions))

                # Fetch Binance positions once for all checks
                binance_positions = {}
                try:
                    if self.execution_mode != "paper":
                        raw_positions = self.binance_client.get_positions()
                        for bp in raw_positions:
                            binance_positions[bp["symbol"]] = bp
                except Exception as e:
                    logger.error("Failed to fetch Binance positions", error=str(e))
                    # Continue with empty — will skip sync logic but still do time exits

                for position in open_positions:
                    try:
                        if position.sl_order_id or position.tp_order_id:
                            # New-style: Binance-side SL/TP
                            self._check_synced_position(position, binance_positions, session)
                        else:
                            # Legacy: in-memory trailing stop
                            self._check_legacy_position(position, session)
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
                stale_ids = (set(self.peak_prices.keys()) | set(self.sl_states.keys())) - open_ids
                for stale_id in stale_ids:
                    self.peak_prices.pop(stale_id, None)
                    self.position_states.pop(stale_id, None)
                    self.sl_states.pop(stale_id, None)
                    self.closing_in_progress.discard(stale_id)

        except Exception as e:
            logger.error("TrailingStopMonitor check_all_positions failed", error=str(e), exc_info=True)

    # ========== NEW-STYLE: Binance position sync ==========

    def _check_synced_position(self, position: PnLLedger, binance_positions: Dict[str, Any], session):
        """Check a position that has Binance-side SL/TP orders."""
        pos_id = position.id

        if pos_id in self.closing_in_progress:
            return
        if position.is_closed:
            return

        symbol = position.symbol
        binance_pos = binance_positions.get(symbol)

        # Position gone from Binance (or zero amount) = SL or TP triggered
        position_gone = (
            binance_pos is None or
            binance_pos.get("position_amount", 0) == 0
        )

        if position_gone and self.execution_mode != "paper":
            self._handle_sl_tp_triggered(position, session)
            return

        # Position still open on Binance — check time-based exit
        holding_seconds = (datetime.utcnow() - position.entry_time).total_seconds()
        if holding_seconds >= self.max_holding_time_seconds:
            self._handle_time_exit(position, session)
            return

        # Dynamic SL adjustment (breakeven + trail)
        if self.dynamic_sl_enabled and position.sl_order_id and self.execution_mode != "paper":
            self._adjust_dynamic_sl(position, session)

    def _handle_sl_tp_triggered(self, position: PnLLedger, session):
        """Position is gone from Binance — use recent trades for accurate fill data."""
        pos_id = position.id
        self.closing_in_progress.add(pos_id)

        try:
            close_reason = "SL_TRIGGERED"
            exit_price = 0.0
            actual_pnl = None
            actual_fees = None

            # PRIMARY: Get actual fill data from account trades API
            try:
                recent_trades = self.binance_client.get_recent_trades(position.symbol, limit=20)
                # Find closing trades (opposite side of position)
                close_side = "SELL" if position.side == "LONG" else "BUY"
                # Filter trades that are recent (within last 60 seconds) and match close side
                closing_fills = [
                    t for t in recent_trades
                    if t["side"] == close_side and t["realized_pnl"] != 0
                ]

                if closing_fills:
                    # Use the most recent closing fill(s)
                    latest_fill = closing_fills[-1]
                    exit_price = latest_fill["price"]
                    actual_pnl = sum(t["realized_pnl"] for t in closing_fills[-5:])
                    actual_fees = sum(t["commission"] for t in closing_fills[-5:])

                    # Determine SL vs TP by realized PnL sign
                    is_long = position.side == "LONG"
                    if actual_pnl > 0:
                        close_reason = "TP_TRIGGERED"
                    else:
                        close_reason = "SL_TRIGGERED"

                    logger.info(
                        "Got fill data from account trades",
                        symbol=position.symbol,
                        exit_price=exit_price,
                        realized_pnl=actual_pnl,
                        fees=actual_fees,
                    )
            except Exception as e:
                logger.warning("Failed to get recent trades, falling back to algo order status", error=str(e))

            # FALLBACK: Check algo order statuses if trades API failed
            if exit_price <= 0:
                if position.sl_order_id:
                    try:
                        sl_status = self.binance_client.get_algo_order_status(
                            position.symbol, int(position.sl_order_id)
                        )
                        algo_status = sl_status.get("algoStatus", "")
                        if algo_status in ("TRIGGERED", "FINISHED"):
                            close_reason = "SL_TRIGGERED"
                            exit_price = float(sl_status.get("triggerPrice", 0))
                    except Exception as e:
                        logger.warning("Failed to check SL algo order status", error=str(e))

                if position.tp_order_id:
                    try:
                        tp_status = self.binance_client.get_algo_order_status(
                            position.symbol, int(position.tp_order_id)
                        )
                        algo_status = tp_status.get("algoStatus", "")
                        if algo_status in ("TRIGGERED", "FINISHED"):
                            close_reason = "TP_TRIGGERED"
                            exit_price = float(tp_status.get("triggerPrice", 0))
                    except Exception as e:
                        logger.warning("Failed to check TP algo order status", error=str(e))

            # LAST RESORT: Use current ticker price
            if exit_price <= 0:
                try:
                    exit_price = self.binance_client.get_ticker_price(position.symbol)
                except Exception:
                    exit_price = position.entry_price

            # Cancel remaining counterpart orders (both algo and regular)
            try:
                self.binance_client.cancel_all_algo_orders(position.symbol)
            except Exception as e:
                logger.warning("Failed to cancel remaining algo orders", symbol=position.symbol, error=str(e))
            try:
                self.binance_client.cancel_all_open_orders(position.symbol)
            except Exception as e:
                logger.warning("Failed to cancel remaining regular orders", symbol=position.symbol, error=str(e))

            # Calculate realized P&L
            self._finalize_close(position, exit_price, close_reason, session,
                                 actual_pnl=actual_pnl, actual_fees=actual_fees)

        except Exception as e:
            logger.error("Failed to handle SL/TP triggered close", position_id=pos_id, error=str(e), exc_info=True)
        finally:
            self.closing_in_progress.discard(pos_id)

    def _handle_time_exit(self, position: PnLLedger, session):
        """Time-based exit: cancel SL/TP orders, then market close."""
        pos_id = position.id
        if pos_id in self.closing_in_progress:
            return
        self.closing_in_progress.add(pos_id)

        try:
            is_long = position.side == "LONG"
            close_side = "SELL" if is_long else "BUY"
            quantity = self._round_quantity(position.symbol, position.quantity)

            logger.info(
                "Time exit: closing position",
                position_id=pos_id,
                symbol=position.symbol,
                side=position.side,
            )

            if self.execution_mode != "paper":
                # Cancel SL/TP orders first (both algo and regular)
                try:
                    self.binance_client.cancel_all_algo_orders(position.symbol)
                except Exception as e:
                    logger.warning("Failed to cancel algo orders before time exit", error=str(e))
                try:
                    self.binance_client.cancel_all_open_orders(position.symbol)
                except Exception as e:
                    logger.warning("Failed to cancel regular orders before time exit", error=str(e))

                # Send market close
                try:
                    self.binance_client.create_order(
                        symbol=position.symbol,
                        side=close_side,
                        order_type="MARKET",
                        quantity=quantity,
                        reduce_only=True,
                    )
                except Exception as e:
                    logger.error("Failed to send market close for time exit", error=str(e), exc_info=True)
                    self.closing_in_progress.discard(pos_id)
                    return

            current_price = self.binance_client.get_ticker_price(position.symbol)
            self._finalize_close(position, current_price, "TIME_EXIT", session)

        except Exception as e:
            logger.error("Failed to handle time exit", position_id=pos_id, error=str(e), exc_info=True)
        finally:
            self.closing_in_progress.discard(pos_id)

    # ========== DYNAMIC SL: Breakeven + Trail ==========

    def _adjust_dynamic_sl(self, position: PnLLedger, session):
        """Adjust Binance-side SL order based on price movement.

        Stage 1 (initial → breakeven): At breakeven_move_pct profit, move SL to entry price.
        Stage 2 (breakeven → trailing): At trail_activation_pct profit, trail SL behind price.
        SL only moves forward (never backward).
        """
        pos_id = position.id
        is_long = position.side == "LONG"

        try:
            current_price = self.binance_client.get_ticker_price(position.symbol)
        except Exception:
            return

        entry_price = position.entry_price

        # Calculate current profit %
        if is_long:
            profit_pct = (current_price - entry_price) / entry_price
        else:
            profit_pct = (entry_price - current_price) / entry_price

        # Initialize state if needed
        if pos_id not in self.sl_states:
            self.sl_states[pos_id] = {
                "stage": "initial",
                "current_sl": 0.0,  # 0 means using original SL
            }

        state = self.sl_states[pos_id]
        new_sl = None

        # Stage 1: Move to breakeven
        if state["stage"] == "initial" and profit_pct >= self.dsl_breakeven_pct:
            # Move SL to entry price (breakeven)
            new_sl = entry_price
            state["stage"] = "breakeven"
            logger.info(
                "Moving SL to breakeven",
                symbol=position.symbol,
                side=position.side,
                profit_pct=f"{profit_pct:.3%}",
                new_sl=new_sl,
            )

        # Stage 2: Trail SL behind price
        if (state["stage"] in ("breakeven", "trailing") and
                profit_pct >= self.dsl_trail_activation_pct):
            state["stage"] = "trailing"

            # Calculate trailing SL
            if is_long:
                trail_sl = current_price * (1 - self.dsl_trail_step_pct)
            else:
                trail_sl = current_price * (1 + self.dsl_trail_step_pct)

            # Only move SL forward (tighter), never backward
            current_sl = state["current_sl"]
            if is_long:
                if trail_sl > current_sl:
                    new_sl = trail_sl
            else:
                if current_sl == 0 or trail_sl < current_sl:
                    new_sl = trail_sl

        # Apply SL change if needed
        if new_sl is not None:
            current_sl = state["current_sl"]
            # Check minimum move threshold
            if current_sl > 0:
                move_pct = abs(new_sl - current_sl) / entry_price
                if move_pct < self.dsl_min_move_pct:
                    return  # Move too small, skip

            self._replace_sl_order(position, new_sl, session)
            state["current_sl"] = new_sl

    def _round_price(self, symbol: str, price: float) -> float:
        """Round price to symbol's price precision."""
        rules = self.SYMBOL_RULES.get(symbol, self.DEFAULT_RULES)
        precision = rules.get("price_precision", 2)
        return round(price, precision)

    def _replace_sl_order(self, position: PnLLedger, new_sl_price: float, session):
        """Cancel existing SL order and place a new one at new_sl_price."""
        symbol = position.symbol
        is_long = position.side == "LONG"
        close_side = "SELL" if is_long else "BUY"

        # Round to symbol's price precision (e.g. BTCUSDT=1 decimal)
        new_sl_price = self._round_price(symbol, new_sl_price)

        # Cancel old SL
        if position.sl_order_id:
            try:
                self.binance_client.cancel_algo_order(symbol, int(position.sl_order_id))
                logger.info("Cancelled old SL order", symbol=symbol, old_sl_id=position.sl_order_id)
            except Exception as e:
                logger.warning("Failed to cancel old SL order", symbol=symbol, error=str(e))
                # Continue anyway — we'll place the new one

        # Place new SL
        try:
            result = self.binance_client.create_algo_order(
                symbol=symbol,
                side=close_side,
                order_type="STOP_MARKET",
                trigger_price=new_sl_price,
                close_position=True,
            )

            new_sl_id = str(result.get("algoId", ""))
            position.sl_order_id = new_sl_id
            session.flush()

            logger.info(
                "SL order replaced",
                symbol=symbol,
                new_sl_price=new_sl_price,
                new_sl_id=new_sl_id,
            )
        except Exception as e:
            logger.error(
                "Failed to place new SL order",
                symbol=symbol,
                new_sl_price=new_sl_price,
                error=str(e),
                exc_info=True,
            )

    # ========== LEGACY: In-memory trailing stop ==========

    def _check_legacy_position(self, position: PnLLedger, session):
        """Legacy trailing stop logic for positions without Binance-side SL/TP."""
        pos_id = position.id

        if pos_id in self.closing_in_progress:
            return
        if position.is_closed:
            return

        # Skip positions with zero quantity
        if not position.quantity or position.quantity <= 0:
            logger.warning("Skipping position with zero quantity, marking closed", position_id=pos_id)
            position.is_closed = True
            session.commit()
            return

        # Fetch current price
        current_price = self.binance_client.get_ticker_price(position.symbol)

        # Initialize tracking state if new
        if pos_id not in self.peak_prices:
            self.peak_prices[pos_id] = current_price
            self.position_states[pos_id] = "MONITORING"
            logger.info(
                "Tracking legacy position",
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
        else:
            if current_price < old_peak:
                self.peak_prices[pos_id] = current_price

        peak_price = self.peak_prices[pos_id]
        entry_price = position.entry_price
        state = self.position_states[pos_id]

        # Calculate profit percentages
        if is_long:
            profit_from_entry_pct = (current_price - entry_price) / entry_price
            profit_from_peak_pct = (peak_price - entry_price) / entry_price
            retrace_from_peak_pct = (peak_price - current_price) / peak_price if peak_price > 0 else 0
        else:
            profit_from_entry_pct = (entry_price - current_price) / entry_price
            profit_from_peak_pct = (entry_price - peak_price) / entry_price
            retrace_from_peak_pct = (current_price - peak_price) / peak_price if peak_price > 0 else 0

        # 1. Check hard stop loss
        hard_stop = self._get_hard_stop(position, session)
        if hard_stop is not None:
            if is_long and current_price <= hard_stop:
                self._close_position(position, current_price, "HARD_STOP", session)
                return
            elif not is_long and current_price >= hard_stop:
                self._close_position(position, current_price, "HARD_STOP", session)
                return
        else:
            if profit_from_entry_pct <= -self.hard_stop_fallback_pct:
                self._close_position(position, current_price, "HARD_STOP", session)
                return

        # 2. Check time-based exit
        holding_seconds = (datetime.utcnow() - position.entry_time).total_seconds()
        if holding_seconds >= self.max_holding_time_seconds:
            self._close_position(position, current_price, "TIME_EXIT", session)
            return

        # 3. Check breakeven activation
        if state == "MONITORING" and profit_from_peak_pct >= self.breakeven_threshold_pct:
            self.position_states[pos_id] = "BREAKEVEN_ACTIVE"
            state = "BREAKEVEN_ACTIVE"

        # 4. Check trailing stop activation
        if state in ("MONITORING", "BREAKEVEN_ACTIVE") and profit_from_peak_pct >= self.activation_threshold_pct:
            self.position_states[pos_id] = "TRAILING_ACTIVE"
            state = "TRAILING_ACTIVE"

        # 5. Check breakeven stop trigger
        if state == "BREAKEVEN_ACTIVE" and profit_from_entry_pct <= 0:
            self._close_position(position, current_price, "BREAKEVEN_STOP", session)
            return

        # 6. Check trailing stop trigger
        if state == "TRAILING_ACTIVE" and retrace_from_peak_pct >= self.trail_distance_pct:
            self._close_position(position, current_price, "TRAIL_STOP", session)
            return

    # ========== SHARED HELPERS ==========

    def _get_hard_stop(self, position: PnLLedger, session) -> Optional[float]:
        """Get hard stop loss price from original TradingDecision."""
        try:
            if not position.execution_id:
                return None

            result = (
                session.query(TradingDecision.stop_loss)
                .join(Execution, Execution.decision_id == TradingDecision.id)
                .filter(Execution.id == position.execution_id)
                .first()
            )

            if result and result[0]:
                return result[0]
            return None
        except Exception as e:
            logger.error("Failed to get hard stop", position_id=position.id, error=str(e))
            return None

    def _finalize_close(self, position: PnLLedger, exit_price: float, reason: str, session,
                        actual_pnl: float = None, actual_fees: float = None):
        """Update PnLLedger and trades DB after position close."""
        if actual_pnl is not None and actual_fees is not None:
            # Use actual Binance data (most accurate)
            realized_pnl = actual_pnl - actual_fees
            total_fees = actual_fees
        else:
            # Estimate from entry/exit prices
            is_long = position.side == "LONG"
            if is_long:
                raw_pnl = (exit_price - position.entry_price) * position.quantity * position.leverage
            else:
                raw_pnl = (position.entry_price - exit_price) * position.quantity * position.leverage

            notional = position.entry_price * position.quantity
            fee_rate = self.taker_fee_bps / 10000
            total_fees = notional * fee_rate * 2
            realized_pnl = raw_pnl - total_fees

        holding_seconds = int((datetime.utcnow() - position.entry_time).total_seconds())

        # Update PnLLedger
        position.exit_price = exit_price
        position.exit_time = datetime.utcnow()
        position.realized_pnl_usdt = round(realized_pnl, 6)
        position.fees_usdt = round(total_fees, 6)
        position.holding_time_seconds = holding_seconds
        position.is_closed = True
        position.close_reason = reason

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

        logger.info(
            "Position closed",
            position_id=position.id,
            symbol=position.symbol,
            reason=reason,
            entry_price=position.entry_price,
            exit_price=exit_price,
            realized_pnl=round(realized_pnl, 6),
            fees=round(total_fees, 6),
            holding_time_seconds=holding_seconds,
        )

        # Clean up tracking state
        self.peak_prices.pop(position.id, None)
        self.position_states.pop(position.id, None)
        self.sl_states.pop(position.id, None)

    def _close_position(self, position: PnLLedger, current_price: float, reason: str, session):
        """Close a legacy position via Binance API (or paper) and update PnLLedger."""
        pos_id = position.id

        if pos_id in self.closing_in_progress:
            return
        self.closing_in_progress.add(pos_id)

        try:
            is_long = position.side == "LONG"
            close_side = "SELL" if is_long else "BUY"
            quantity = self._round_quantity(position.symbol, position.quantity)

            logger.info(
                "Closing legacy position",
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

            self._finalize_close(position, current_price, reason, session)

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
