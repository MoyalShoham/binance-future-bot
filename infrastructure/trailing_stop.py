"""
Position Sync Monitor with Ratcheting TP/SL

Runs inside EmergencyController's background thread to manage open positions.

For new positions with Binance-side SL/TP orders:
- Syncs with Binance to detect closures (SL/TP triggered)
- Dynamic SL adjustment: moves SL to breakeven, then trails behind price
- Time-based exit as safety net

For legacy positions (no SL/TP order IDs):
- Ratcheting TP/SL system: when TP hit, levels ratchet from current price
- When SL hit, position closes
- Time exit remains as safety net

Close reasons:
- SL_TRIGGERED: Binance-side stop loss order filled
- TP_TRIGGERED: Binance-side take profit order filled
- STOP_LOSS: Price hit SL level (legacy ratcheting)
- TIME_EXIT: Position held past max holding time
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
    Monitors open positions and manages exits.

    New positions (with sl_order_id/tp_order_id):
    - Detects when Binance closes the position via SL/TP
    - Dynamic SL: breakeven at 0.4% profit, trail at 0.8%
    - Handles time-based exits by cancelling SL/TP and sending market close

    Legacy positions (no sl_order_id/tp_order_id):
    - Ratcheting TP/SL: when TP hit, recalculate levels from current price
    - When SL hit, position closes
    """

    def __init__(self, binance_client, db_session: DatabaseSession, config: Dict[str, Any], trades_db=None, ws_manager=None):
        self.binance_client = binance_client
        self.db_session = db_session
        self.config = config
        self.trades_db = trades_db
        self.ws_manager = ws_manager

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

        # Dynamic SL config (for Binance-side SL adjustment)
        dynamic_sl_config = ts_config.get("dynamic_sl", {})
        self.dynamic_sl_enabled = dynamic_sl_config.get("enabled", True)
        self.dsl_breakeven_pct = dynamic_sl_config.get("breakeven_move_pct", 0.004)
        self.dsl_trail_activation_pct = dynamic_sl_config.get("trail_activation_pct", 0.008)
        self.dsl_trail_step_pct = dynamic_sl_config.get("trail_step_pct", 0.003)
        self.dsl_min_move_pct = dynamic_sl_config.get("min_move_pct", 0.001)

        # Dynamic TP/SL recalculation config
        dtp_config = ts_config.get("dynamic_tp_sl", {})
        self.dtp_enabled = dtp_config.get("enabled", False)
        self.dtp_recalc_threshold_pct = dtp_config.get("recalc_threshold_pct", 0.004)
        self.dtp_tp_atr_mult = dtp_config.get("tp_atr_multiplier", 2.0)
        self.dtp_sl_atr_mult = dtp_config.get("sl_atr_multiplier", 1.0)
        self.dtp_min_rr = dtp_config.get("min_rr_ratio", 1.5)
        self.dtp_max_recalcs = dtp_config.get("max_recalcs", 10)

        # Dynamic SL states: {pnl_ledger_id: {"stage": ..., "current_sl": ..., "last_recalc_price": ..., "recalc_count": ..., "current_tp": ...}}
        self.sl_states = {}

        # In-memory state for legacy ratcheting: {pnl_ledger_id: {"tp": float, "sl": float, "ratchet_count": int}}
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
        Check all open positions.
        For positions with SL/TP order IDs: sync with Binance.
        For legacy positions: apply ratcheting TP/SL logic.
        Also reconciles orphaned Binance positions (no DB record / no SL/TP).
        Called every 5 seconds from EmergencyController loop.
        """
        if not self.enabled:
            return

        try:
            with self.db_session.session_scope() as session:
                queries = DatabaseQueries(session)
                open_positions = queries.get_open_positions()

                # Fetch Binance positions once for all checks
                binance_positions = {}
                try:
                    if self.execution_mode != "paper":
                        raw_positions = self.binance_client.get_positions()
                        for bp in raw_positions:
                            binance_positions[bp["symbol"]] = bp
                except Exception as e:
                    logger.error("Failed to fetch Binance positions", error=str(e))

                # Reconcile: find Binance positions without DB records or without SL/TP
                if binance_positions and self.execution_mode != "paper":
                    db_symbols = {p.symbol for p in open_positions} if open_positions else set()
                    self._reconcile_orphaned_positions(binance_positions, db_symbols, open_positions or [], session)
                    # Re-fetch after reconciliation may have added records
                    open_positions = queries.get_open_positions()

                if not open_positions:
                    self.position_levels.clear()
                    self.sl_states.clear()
                    return

                logger.debug("Position sync monitoring", open_positions=len(open_positions))

                for position in open_positions:
                    try:
                        if position.sl_order_id or position.tp_order_id:
                            # New-style: Binance-side SL/TP
                            self._check_synced_position(position, binance_positions, session)
                        else:
                            # Legacy: ratcheting TP/SL
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
                stale_ids = (set(self.position_levels.keys()) | set(self.sl_states.keys())) - open_ids
                for stale_id in stale_ids:
                    self.position_levels.pop(stale_id, None)
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

        # Dynamic TP/SL recalculation (ATR-based, both SL and TP)
        if self.dtp_enabled and position.sl_order_id and position.tp_order_id:
            self._adjust_dynamic_tp_sl(position, session)

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
                closing_fills = [
                    t for t in recent_trades
                    if t["side"] == close_side and t["realized_pnl"] != 0
                ]

                if closing_fills:
                    latest_fill = closing_fills[-1]
                    exit_price = latest_fill["price"]
                    actual_pnl = sum(t["realized_pnl"] for t in closing_fills[-5:])
                    actual_fees = sum(t["commission"] for t in closing_fills[-5:])

                    # Determine SL vs TP by realized PnL sign
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
                if position.sl_order_id and position.sl_order_id.isdigit():
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

                if position.tp_order_id and position.tp_order_id.isdigit():
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
                    exit_price = self._get_current_price(position.symbol)
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
                db_qty=position.quantity,
                rounded_qty=quantity,
            )

            if self.execution_mode != "paper":
                # If quantity rounds to 0, check if position is already gone on Binance
                if quantity <= 0:
                    binance_qty = self._get_binance_position_qty(position.symbol)
                    if binance_qty > 0:
                        # Position exists on Binance with different qty — use Binance qty
                        quantity = self._round_quantity(position.symbol, binance_qty)
                    if quantity <= 0:
                        # Position already closed on Binance (or qty still rounds to 0)
                        logger.info(
                            "Position already closed on Binance, finalizing in DB",
                            position_id=pos_id,
                            symbol=position.symbol,
                        )
                        # Remove from closing_in_progress so _handle_sl_tp_triggered can proceed
                        self.closing_in_progress.discard(pos_id)
                        self._handle_sl_tp_triggered(position, session)
                        return

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
                    error_str = str(e)
                    if "-2022" in error_str:
                        logger.warning(
                            "ReduceOnly rejected on time exit — position gone or side mismatch",
                            position_id=pos_id,
                            symbol=position.symbol,
                        )
                        exit_price = self._get_current_price(position.symbol)
                        self._finalize_close(position, exit_price, "STALE_CLOSED", session)
                        return
                    logger.error("Failed to send market close for time exit", error=error_str, exc_info=True)
                    self.closing_in_progress.discard(pos_id)
                    return

                # Fetch actual fill data from Binance for accurate PnL
                actual_pnl = None
                actual_fees = None
                exit_price = None
                try:
                    recent_trades = self.binance_client.get_recent_trades(position.symbol, limit=10)
                    closing_fills = [
                        t for t in recent_trades
                        if t["side"] == close_side and t["realized_pnl"] != 0
                    ]
                    if closing_fills:
                        latest_fill = closing_fills[-1]
                        exit_price = latest_fill["price"]
                        actual_pnl = sum(t["realized_pnl"] for t in closing_fills[-5:])
                        actual_fees = sum(t["commission"] for t in closing_fills[-5:])
                except Exception as e:
                    logger.warning("Failed to get fill data for time exit", error=str(e))

                if exit_price is None:
                    exit_price = self._get_current_price(position.symbol)

                self._finalize_close(position, exit_price, "TIME_EXIT", session,
                                     actual_pnl=actual_pnl, actual_fees=actual_fees,
                                     close_quantity=quantity)
                return

            # Paper mode: estimate from ticker
            current_price = self._get_current_price(position.symbol)
            self._finalize_close(position, current_price, "TIME_EXIT", session,
                                 close_quantity=position.quantity)

        except Exception as e:
            logger.error("Failed to handle time exit", position_id=pos_id, error=str(e), exc_info=True)
        finally:
            self.closing_in_progress.discard(pos_id)

    # ========== DYNAMIC SL: Breakeven + Trail ==========

    def _adjust_dynamic_sl(self, position: PnLLedger, session):
        """Adjust Binance-side SL order based on price movement.

        Stage 1 (initial -> breakeven): At breakeven_move_pct profit, move SL to entry price.
        Stage 2 (breakeven -> trailing): At trail_activation_pct profit, trail SL behind price.
        SL only moves forward (never backward).
        """
        pos_id = position.id
        is_long = position.side == "LONG"

        try:
            current_price = self._get_current_price(position.symbol)
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

        # Cancel old SL (cancel all for symbol since ID may be "EXISTING" placeholder)
        if position.sl_order_id:
            try:
                if position.sl_order_id.isdigit():
                    self.binance_client.cancel_algo_order(symbol, int(position.sl_order_id))
                else:
                    # "EXISTING" or unknown ID — cancel all algo orders for symbol
                    self.binance_client.cancel_all_algo_orders(symbol)
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

    # ========== DYNAMIC TP/SL RECALCULATION ==========

    def _adjust_dynamic_tp_sl(self, position: PnLLedger, session):
        """Recalculate both SL and TP using fresh ATR when price moves significantly.

        Triggered when price moves >= recalc_threshold_pct from last recalc price.
        Fetches current ATR (20 candles of 5m klines), computes new SL and TP.
        SL only moves forward (tighter). TP can adjust. min R:R enforced.
        """
        pos_id = position.id
        is_long = position.side == "LONG"

        try:
            current_price = self._get_current_price(position.symbol)
        except Exception:
            return

        # Initialize recalc state if needed
        if pos_id not in self.sl_states:
            self.sl_states[pos_id] = {
                "stage": "initial",
                "current_sl": 0.0,
            }
        state = self.sl_states[pos_id]

        if "last_recalc_price" not in state:
            state["last_recalc_price"] = position.entry_price
            state["recalc_count"] = 0
            state["current_tp"] = 0.0

        # Check if price moved enough to trigger recalculation
        last_recalc = state["last_recalc_price"]
        if last_recalc <= 0:
            last_recalc = position.entry_price
        price_move_pct = abs(current_price - last_recalc) / last_recalc
        if price_move_pct < self.dtp_recalc_threshold_pct:
            return

        # Check max recalcs
        if state["recalc_count"] >= self.dtp_max_recalcs:
            return

        # Only recalculate when price is moving favorably
        if is_long and current_price <= position.entry_price:
            return
        if not is_long and current_price >= position.entry_price:
            return

        # Fetch current ATR
        atr = self._get_current_atr(position.symbol)
        if atr is None or atr <= 0:
            return

        # Calculate new levels
        if is_long:
            new_sl = current_price - atr * self.dtp_sl_atr_mult
            new_tp = current_price + atr * self.dtp_tp_atr_mult
        else:
            new_sl = current_price + atr * self.dtp_sl_atr_mult
            new_tp = current_price - atr * self.dtp_tp_atr_mult

        # Enforce SL only moves forward (tighter)
        current_sl = state["current_sl"]
        if current_sl > 0:
            if is_long and new_sl < current_sl:
                new_sl = current_sl  # Don't loosen SL for longs
            elif not is_long and new_sl > current_sl:
                new_sl = current_sl  # Don't loosen SL for shorts

        # Enforce minimum R:R ratio
        sl_dist = abs(current_price - new_sl)
        tp_dist = abs(new_tp - current_price)
        if sl_dist > 0 and tp_dist / sl_dist < self.dtp_min_rr:
            # Widen TP to maintain min R:R
            if is_long:
                new_tp = current_price + sl_dist * self.dtp_min_rr
            else:
                new_tp = current_price - sl_dist * self.dtp_min_rr

        # Round prices
        new_sl = self._round_price(position.symbol, new_sl)
        new_tp = self._round_price(position.symbol, new_tp)

        # Replace orders on Binance (live mode only)
        sl_replaced = False
        tp_replaced = False

        if self.execution_mode != "paper":
            if current_sl == 0 or new_sl != self._round_price(position.symbol, current_sl):
                self._replace_sl_order(position, new_sl, session)
                sl_replaced = True

            current_tp_val = state.get("current_tp", 0)
            if current_tp_val == 0 or new_tp != self._round_price(position.symbol, current_tp_val):
                self._replace_tp_order(position, new_tp, session)
                tp_replaced = True

        # Update state
        state["current_sl"] = new_sl
        state["current_tp"] = new_tp
        state["last_recalc_price"] = current_price
        state["recalc_count"] += 1

        logger.info(
            "Dynamic TP/SL recalculated",
            symbol=position.symbol,
            side=position.side,
            current_price=current_price,
            new_sl=new_sl,
            new_tp=new_tp,
            atr=round(atr, 4),
            recalc_count=state["recalc_count"],
            sl_replaced=sl_replaced,
            tp_replaced=tp_replaced,
        )

    def _get_current_atr(self, symbol: str) -> Optional[float]:
        """Fetch current ATR(14) from 20 candles of 5m klines. Lightweight ~100ms."""
        try:
            klines = self.binance_client.get_klines(symbol=symbol, interval="5m", limit=20)
            if not klines or len(klines) < 14:
                return None

            # Calculate ATR(14)
            true_ranges = []
            for i in range(1, len(klines)):
                high = klines[i]["high"]
                low = klines[i]["low"]
                prev_close = klines[i - 1]["close"]
                tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
                true_ranges.append(tr)

            if len(true_ranges) < 14:
                return None

            # Simple moving average of last 14 true ranges
            atr = sum(true_ranges[-14:]) / 14
            return atr

        except Exception as e:
            logger.warning("Failed to fetch ATR for dynamic TP/SL", symbol=symbol, error=str(e))
            return None

    def _replace_tp_order(self, position: PnLLedger, new_tp_price: float, session):
        """Cancel existing TP order and place a new one at new_tp_price."""
        symbol = position.symbol
        is_long = position.side == "LONG"
        close_side = "SELL" if is_long else "BUY"

        new_tp_price = self._round_price(symbol, new_tp_price)

        # Cancel old TP
        if position.tp_order_id:
            try:
                if position.tp_order_id.isdigit():
                    self.binance_client.cancel_algo_order(symbol, int(position.tp_order_id))
                else:
                    self.binance_client.cancel_all_algo_orders(symbol)
                logger.info("Cancelled old TP order", symbol=symbol, old_tp_id=position.tp_order_id)
            except Exception as e:
                logger.warning("Failed to cancel old TP order", symbol=symbol, error=str(e))

        # Place new TP
        try:
            result = self.binance_client.create_algo_order(
                symbol=symbol,
                side=close_side,
                order_type="TAKE_PROFIT_MARKET",
                trigger_price=new_tp_price,
                close_position=True,
            )

            new_tp_id = str(result.get("algoId", ""))
            position.tp_order_id = new_tp_id
            session.flush()

            logger.info(
                "TP order replaced",
                symbol=symbol,
                new_tp_price=new_tp_price,
                new_tp_id=new_tp_id,
            )
        except Exception as e:
            logger.error(
                "Failed to place new TP order",
                symbol=symbol,
                new_tp_price=new_tp_price,
                error=str(e),
                exc_info=True,
            )

    # ========== LEGACY: Ratcheting TP/SL ==========

    def _check_legacy_position(self, position: PnLLedger, session):
        """Legacy ratcheting TP/SL logic for positions without Binance-side SL/TP."""
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

        is_long = position.side == "LONG"
        current_price = self._get_current_price(position.symbol)

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
                "Tracking legacy position",
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

        # === 2. Check TP -> Ratchet ===
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
                        actual_pnl: float = None, actual_fees: float = None,
                        close_quantity: float = None):
        """Update PnLLedger and trades DB after position close.

        Args:
            close_quantity: Actual quantity closed. Used for PnL estimation when
                           position.quantity in DB is 0 or wrong.
        """
        if actual_pnl is not None and actual_fees is not None:
            # Use actual Binance data (most accurate)
            realized_pnl = actual_pnl - actual_fees
            total_fees = actual_fees
        else:
            # Estimate from entry/exit prices
            # Use close_quantity if provided (covers DB qty=0 case)
            qty = close_quantity if close_quantity and close_quantity > 0 else position.quantity
            is_long = position.side == "LONG"
            if is_long:
                raw_pnl = (exit_price - position.entry_price) * qty * position.leverage
            else:
                raw_pnl = (position.entry_price - exit_price) * qty * position.leverage

            notional = position.entry_price * qty
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

        # Include ratchet count in close log if available
        ratchet_count = self.position_levels.get(position.id, {}).get("ratchet_count", 0)

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
            ratchets=ratchet_count,
        )

        # Clean up tracking state
        self.position_levels.pop(position.id, None)
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

            # Guard: if quantity rounds to 0, check Binance position
            if quantity <= 0 and self.execution_mode != "paper":
                binance_qty = self._get_binance_position_qty(position.symbol)
                if binance_qty > 0:
                    quantity = self._round_quantity(position.symbol, binance_qty)
                if quantity <= 0:
                    # Position already gone on Binance — finalize in DB
                    logger.info(
                        "Position already closed on Binance (qty=0), finalizing",
                        position_id=pos_id,
                        symbol=position.symbol,
                    )
                    self._finalize_close(position, current_price, reason, session)
                    return

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
                    error_str = str(e)
                    # -2022: ReduceOnly rejected = position doesn't exist or side mismatch
                    if "-2022" in error_str:
                        logger.warning(
                            "ReduceOnly rejected — position gone or side mismatch, marking closed in DB",
                            position_id=pos_id,
                            symbol=position.symbol,
                            side=position.side,
                        )
                        self._finalize_close(position, current_price, "STALE_CLOSED", session)
                        return
                    logger.error(
                        "Failed to close position on Binance",
                        position_id=pos_id,
                        error=error_str,
                        exc_info=True,
                    )
                    self.closing_in_progress.discard(pos_id)
                    return

            self._finalize_close(position, current_price, reason, session,
                                 close_quantity=quantity)

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

    # ========== ORPHAN RECONCILIATION ==========

    def _reconcile_orphaned_positions(self, binance_positions: Dict[str, Any],
                                       db_symbols: set, db_positions: List[PnLLedger], session):
        """
        Reconcile Binance positions with DB records:
        1. Close stale DB records (side mismatch or symbol no longer on Binance)
        2. Create PnLLedger entries for orphaned Binance positions (no DB record)
        3. Place SL/TP protective orders for any position missing them
        """
        risk_config = self.config.get("risk", {})
        sl_distance_pct = risk_config.get("min_sl_distance_pct", 0.003)
        fallback_sl_pct = self.hard_stop_fallback_pct  # 2%
        min_rr = risk_config.get("min_rr_ratio", 2.0)

        # --- Step 0: Close stale DB records (no Binance position or side mismatch) ---
        for db_pos in db_positions:
            if db_pos.is_closed:
                continue
            bp = binance_positions.get(db_pos.symbol)
            if bp is None or bp.get("position_amount", 0) == 0:
                # Position gone from Binance — likely SL/TP triggered
                logger.info(
                    "Position gone from Binance, checking fill data",
                    position_id=db_pos.id,
                    symbol=db_pos.symbol,
                    side=db_pos.side,
                )
                # Use proper SL/TP handler to get actual fill data from account trades
                self._handle_sl_tp_triggered(db_pos, session)
                continue

            # Side mismatch: DB says LONG but Binance has SHORT (or vice versa)
            binance_side = "LONG" if bp.get("position_amount", 0) > 0 else "SHORT"
            if db_pos.side != binance_side:
                logger.info(
                    "Closing stale DB record (side mismatch)",
                    position_id=db_pos.id,
                    symbol=db_pos.symbol,
                    db_side=db_pos.side,
                    binance_side=binance_side,
                )
                try:
                    exit_price = self._get_current_price(db_pos.symbol)
                except Exception:
                    exit_price = db_pos.entry_price
                self._finalize_close(db_pos, exit_price, "STALE_CLOSED", session)

        # Refresh db_symbols after closing stale records
        db_symbols = {p.symbol for p in db_positions if not p.is_closed}

        for symbol, bp in binance_positions.items():
            pos_amount = bp.get("position_amount", 0)
            if pos_amount == 0:
                continue

            is_long = pos_amount > 0
            side = "LONG" if is_long else "SHORT"
            entry_price = bp.get("entry_price", 0)
            quantity = abs(pos_amount)
            leverage = bp.get("leverage", 3)

            if entry_price <= 0:
                continue

            # --- Step 1: Create DB record if missing ---
            db_pos = None
            if symbol not in db_symbols:
                # Re-check DB to avoid race condition with execution agent
                existing = session.query(PnLLedger).filter(
                    PnLLedger.symbol == symbol,
                    PnLLedger.is_closed == False,
                ).first()
                if existing:
                    db_pos = existing
                    logger.debug("Found recently created DB record, skipping orphan creation",
                                 symbol=symbol, position_id=existing.id)
                else:
                    db_pos = PnLLedger(
                        symbol=symbol,
                        side=side,
                        entry_price=entry_price,
                        quantity=quantity,
                        leverage=leverage,
                        entry_time=datetime.utcnow(),
                        is_closed=False,
                    )
                    session.add(db_pos)
                    session.flush()  # Get the ID

                    logger.info(
                        "Created DB record for orphaned Binance position",
                        position_id=db_pos.id,
                        symbol=symbol,
                        side=side,
                        entry_price=entry_price,
                        quantity=quantity,
                        leverage=leverage,
                    )
            else:
                # Find existing DB position for this symbol
                for p in db_positions:
                    if p.symbol == symbol and not p.is_closed:
                        db_pos = p
                        break

            if not db_pos:
                continue

            # --- Step 2: Check and place SL/TP if missing ---
            has_sl = bool(db_pos.sl_order_id)
            has_tp = bool(db_pos.tp_order_id)

            # If already has both, skip
            if has_sl and has_tp:
                continue

            # Verify against Binance algo orders
            try:
                algo_orders = self.binance_client.get_open_algo_orders(symbol)
            except Exception as e:
                logger.warning("Failed to check algo orders for reconciliation", symbol=symbol, error=str(e))
                continue

            existing_sl = None
            existing_tp = None
            for order in algo_orders:
                order_type = order.get("type", "")
                if order_type == "STOP_MARKET":
                    existing_sl = order
                elif order_type == "TAKE_PROFIT_MARKET":
                    existing_tp = order

            # Calculate SL/TP prices using percentage-based (no ATR for orphans)
            sl_distance = max(fallback_sl_pct, sl_distance_pct)
            tp_distance = sl_distance * min_rr  # R:R enforced

            if is_long:
                sl_price = entry_price * (1 - sl_distance)
                tp_price = entry_price * (1 + tp_distance)
                close_side = "SELL"
            else:
                sl_price = entry_price * (1 + sl_distance)
                tp_price = entry_price * (1 - tp_distance)
                close_side = "BUY"

            sl_price = self._round_price(symbol, sl_price)
            tp_price = self._round_price(symbol, tp_price)

            # Place SL if missing
            if not has_sl and not existing_sl:
                try:
                    sl_result = self.binance_client.create_algo_order(
                        symbol=symbol,
                        side=close_side,
                        order_type="STOP_MARKET",
                        trigger_price=sl_price,
                        close_position=True,
                    )
                    sl_id = str(sl_result.get("algoId", ""))
                    db_pos.sl_order_id = sl_id
                    session.flush()
                    logger.info(
                        "Placed protective SL for position",
                        symbol=symbol,
                        side=side,
                        sl_price=sl_price,
                        sl_id=sl_id,
                    )
                except Exception as e:
                    if "-4130" in str(e):
                        # SL already exists on Binance — mark as protected
                        db_pos.sl_order_id = "EXISTING"
                        session.flush()
                        logger.info("SL already exists on Binance", symbol=symbol)
                    else:
                        logger.error("Failed to place protective SL", symbol=symbol, error=str(e))
            elif existing_sl and not has_sl:
                # SL exists on Binance but not in DB — store the ID
                db_pos.sl_order_id = str(existing_sl.get("orderId", existing_sl.get("algoId", "EXISTING")))
                session.flush()
                logger.info("Linked existing Binance SL to DB", symbol=symbol,
                            sl_id=db_pos.sl_order_id)

            # Place TP if missing
            if not has_tp and not existing_tp:
                try:
                    tp_result = self.binance_client.create_algo_order(
                        symbol=symbol,
                        side=close_side,
                        order_type="TAKE_PROFIT_MARKET",
                        trigger_price=tp_price,
                        close_position=True,
                    )
                    tp_id = str(tp_result.get("algoId", ""))
                    db_pos.tp_order_id = tp_id
                    session.flush()
                    logger.info(
                        "Placed protective TP for position",
                        symbol=symbol,
                        side=side,
                        tp_price=tp_price,
                        tp_id=tp_id,
                    )
                except Exception as e:
                    if "-4130" in str(e):
                        # TP already exists on Binance — mark as protected
                        db_pos.tp_order_id = "EXISTING"
                        session.flush()
                        logger.info("TP already exists on Binance", symbol=symbol)
                    else:
                        logger.error("Failed to place protective TP", symbol=symbol, error=str(e))
            elif existing_tp and not has_tp:
                # TP exists on Binance but not in DB — store the ID
                db_pos.tp_order_id = str(existing_tp.get("orderId", existing_tp.get("algoId", "EXISTING")))
                session.flush()
                logger.info("Linked existing Binance TP to DB", symbol=symbol,
                            tp_id=db_pos.tp_order_id)

    def _get_current_price(self, symbol: str) -> float:
        """Get current price from WebSocket cache, falling back to REST."""
        if self.ws_manager:
            ws_price = self.ws_manager.get_price(symbol)
            if ws_price is not None:
                return ws_price
        return self._get_current_price(symbol)

    def _get_binance_position_qty(self, symbol: str) -> float:
        """Get actual position quantity from Binance. Returns 0 if no position or on error."""
        try:
            positions = self.binance_client.get_positions()
            for p in positions:
                if p["symbol"] == symbol:
                    return abs(p.get("position_amount", 0))
        except Exception as e:
            logger.warning("Failed to get Binance position qty", symbol=symbol, error=str(e))
        return 0.0

    def _round_quantity(self, symbol: str, quantity: float) -> float:
        """Round quantity to symbol's step size."""
        rules = self.SYMBOL_RULES.get(symbol, self.DEFAULT_RULES)
        step = rules["step_size"]
        rounded = math.floor(quantity / step) * step
        decimals = len(str(step).rstrip('0').split('.')[-1]) if '.' in str(step) else 0
        return round(rounded, decimals)
