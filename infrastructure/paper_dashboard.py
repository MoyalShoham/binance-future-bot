"""
Paper Trading PnL Dashboard

Separate SQLite database for paper trade tracking, SL/TP simulation
using real 1m klines, and periodic markdown report generation.

Usage:
    sqlite3 data/paper_trades.db "SELECT symbol, side, net_pnl_usdt FROM paper_trades WHERE exit_price IS NOT NULL"
"""

import os
import math
import threading
from datetime import datetime, timedelta
from typing import Dict, Any, Optional, List
from contextlib import contextmanager

from sqlalchemy import create_engine, Column, Integer, String, Float, DateTime, Boolean
from sqlalchemy.orm import declarative_base, sessionmaker
import structlog

logger = structlog.get_logger()

PaperBase = declarative_base()


class PaperTrade(PaperBase):
    """Flat paper trade record with simulation fields."""

    __tablename__ = "paper_trades"

    id = Column(Integer, primary_key=True, autoincrement=True)
    trade_id = Column(String, unique=True, nullable=False)  # execution_id
    timestamp = Column(DateTime, default=datetime.utcnow)
    symbol = Column(String, nullable=False, index=True)
    side = Column(String, nullable=False)  # LONG / SHORT
    strategy_id = Column(String)
    confidence = Column(Float)

    # Entry
    entry_price = Column(Float)
    entry_time = Column(DateTime)
    quantity = Column(Float)
    leverage = Column(Integer)

    # SL/TP targets
    sl_price = Column(Float)
    tp_price = Column(Float)

    # Actual exit (from trailing stop monitor)
    exit_price = Column(Float)
    exit_time = Column(DateTime)
    exit_reason = Column(String)
    gross_pnl_usdt = Column(Float)
    fees_usdt = Column(Float)
    net_pnl_usdt = Column(Float)
    holding_time_seconds = Column(Integer)

    # SL/TP simulation (background thread with real 1m klines)
    sim_exit_price = Column(Float)
    sim_exit_reason = Column(String)
    sim_net_pnl_usdt = Column(Float)
    sim_completed = Column(Boolean, default=False)

    # Rejected trade tracking
    was_rejected = Column(Boolean, default=False)
    rejection_reason = Column(String)
    would_have_been_profitable = Column(Boolean)

    status = Column(String, nullable=False, index=True)  # OPEN / CLOSED / REJECTED


class PaperDashboard:
    """
    Manages paper trade recording, SL/TP simulation, and report generation.

    Hooks into StorageReporter (open/rejected) and TrailingStopMonitor (close).
    """

    def __init__(self, config: Dict[str, Any], binance_client=None):
        dashboard_config = config.get("paper_dashboard", {})
        self.enabled = dashboard_config.get("enabled", True)
        self.reports_dir = dashboard_config.get("reports_dir", "reports")
        self.report_interval_minutes = dashboard_config.get("report_interval_minutes", 60)
        self.simulate_sl_tp = dashboard_config.get("simulate_sl_tp", True)
        self.binance_client = binance_client

        # Fee config
        self.taker_fee_bps = config.get("execution", {}).get("fees", {}).get("taker_bps", 5)
        self.max_holding_time = config.get("trading", {}).get("scalping", {}).get(
            "max_holding_time_seconds", 900
        )

        # Database
        db_path = "data/paper_trades.db"
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        self.engine = create_engine(
            f"sqlite:///{db_path}",
            echo=False,
            connect_args={"timeout": 5},
        )
        PaperBase.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)

        # Periodic report timer
        self._report_timer = None
        self._stop_event = threading.Event()

        os.makedirs(self.reports_dir, exist_ok=True)
        logger.info("PaperDashboard initialized", db=db_path, reports_dir=self.reports_dir)

    @contextmanager
    def _session_scope(self):
        session = self.Session()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    # ==================== Trade Recording ====================

    def record_trade_open(
        self,
        trade_id: str,
        symbol: str,
        side: str,
        entry_price: float,
        quantity: float,
        leverage: int,
        strategy_id: Optional[str] = None,
        confidence: Optional[float] = None,
        sl_price: Optional[float] = None,
        tp_price: Optional[float] = None,
    ):
        """Record a newly opened paper trade."""
        if not self.enabled:
            return

        try:
            with self._session_scope() as session:
                trade = PaperTrade(
                    trade_id=trade_id,
                    timestamp=datetime.utcnow(),
                    symbol=symbol,
                    side=side,
                    strategy_id=strategy_id,
                    confidence=confidence,
                    entry_price=entry_price,
                    entry_time=datetime.utcnow(),
                    quantity=quantity,
                    leverage=leverage,
                    sl_price=sl_price,
                    tp_price=tp_price,
                    status="OPEN",
                )
                session.add(trade)
            logger.debug("Paper trade opened", trade_id=trade_id, symbol=symbol, side=side)
        except Exception as e:
            logger.warning("Failed to record paper trade open", error=str(e))

    def record_trade_close(
        self,
        execution_id: str,
        exit_price: float,
        pnl_usdt: float,
        fees_usdt: float,
        holding_time_seconds: int,
        close_reason: str,
    ):
        """Record a paper trade close (called from TrailingStopMonitor)."""
        if not self.enabled:
            return

        try:
            with self._session_scope() as session:
                trade = (
                    session.query(PaperTrade)
                    .filter(PaperTrade.trade_id == execution_id, PaperTrade.status == "OPEN")
                    .first()
                )
                if not trade:
                    logger.debug("No open paper trade found for close", execution_id=execution_id)
                    return

                trade.exit_price = exit_price
                trade.exit_time = datetime.utcnow()
                trade.exit_reason = close_reason
                trade.net_pnl_usdt = pnl_usdt
                trade.fees_usdt = fees_usdt
                trade.gross_pnl_usdt = pnl_usdt + fees_usdt
                trade.holding_time_seconds = holding_time_seconds
                trade.status = "CLOSED"

                logger.debug(
                    "Paper trade closed",
                    trade_id=trade.trade_id,
                    symbol=trade.symbol,
                    pnl=pnl_usdt,
                    reason=close_reason,
                )

            # Run SL/TP simulation in background
            if self.simulate_sl_tp and self.binance_client:
                sim_thread = threading.Thread(
                    target=self._simulate_sl_tp,
                    args=(execution_id,),
                    daemon=True,
                )
                sim_thread.start()

        except Exception as e:
            logger.warning("Failed to record paper trade close", error=str(e))

    def record_rejected_trade(
        self,
        trade_id: str,
        symbol: str,
        side: str,
        strategy_id: Optional[str] = None,
        confidence: Optional[float] = None,
        rejection_reason: Optional[str] = None,
    ):
        """Record a rejected trade for analysis."""
        if not self.enabled:
            return

        try:
            with self._session_scope() as session:
                trade = PaperTrade(
                    trade_id=trade_id,
                    timestamp=datetime.utcnow(),
                    symbol=symbol,
                    side=side,
                    strategy_id=strategy_id,
                    confidence=confidence,
                    was_rejected=True,
                    rejection_reason=rejection_reason,
                    status="REJECTED",
                )
                session.add(trade)
            logger.debug("Paper rejected trade recorded", trade_id=trade_id, symbol=symbol)
        except Exception as e:
            logger.warning("Failed to record paper rejected trade", error=str(e))

    # ==================== SL/TP Simulation ====================

    def _simulate_sl_tp(self, trade_id: str):
        """
        Simulate SL/TP outcome using real 1m klines from Binance.

        Fetches candles from entry_time to entry_time + max_holding_time,
        walks bar-by-bar checking if SL or TP would have been hit.
        """
        try:
            with self._session_scope() as session:
                trade = (
                    session.query(PaperTrade)
                    .filter(PaperTrade.trade_id == trade_id)
                    .first()
                )
                if not trade or not trade.entry_time or not trade.sl_price or not trade.tp_price:
                    return

                symbol = trade.symbol
                side = trade.side
                entry_price = trade.entry_price
                entry_time = trade.entry_time
                sl_price = trade.sl_price
                tp_price = trade.tp_price
                quantity = trade.quantity or 0

            # Fetch 1m klines from entry to entry + max_holding_time
            start_ms = int(entry_time.timestamp() * 1000)
            end_ms = start_ms + (self.max_holding_time * 1000)

            klines = self.binance_client.get_klines(
                symbol=symbol,
                interval="1m",
                start_time=start_ms,
                end_time=end_ms,
                limit=int(self.max_holding_time / 60) + 5,
            )

            if not klines:
                return

            # Walk candles
            is_long = side == "LONG"
            sim_exit_price = None
            sim_exit_reason = None

            for candle in klines:
                high = candle["high"]
                low = candle["low"]

                if is_long:
                    sl_hit = low <= sl_price
                    tp_hit = high >= tp_price
                else:
                    sl_hit = high >= sl_price
                    tp_hit = low <= tp_price

                if sl_hit and tp_hit:
                    # Same-candle conflict — SL wins (conservative)
                    sim_exit_price = sl_price
                    sim_exit_reason = "SIM_STOP_LOSS"
                    break
                elif sl_hit:
                    sim_exit_price = sl_price
                    sim_exit_reason = "SIM_STOP_LOSS"
                    break
                elif tp_hit:
                    sim_exit_price = tp_price
                    sim_exit_reason = "SIM_TAKE_PROFIT"
                    break

            if sim_exit_price is None:
                # Neither hit — TIME_EXIT at last candle close
                sim_exit_price = klines[-1]["close"]
                sim_exit_reason = "SIM_TIME_EXIT"

            # Calculate simulated PnL
            if is_long:
                raw_pnl = (sim_exit_price - entry_price) * quantity
            else:
                raw_pnl = (entry_price - sim_exit_price) * quantity

            fee_rate = self.taker_fee_bps / 10000
            entry_notional = entry_price * quantity
            exit_notional = sim_exit_price * quantity
            total_fees = (entry_notional + exit_notional) * fee_rate
            sim_net_pnl = raw_pnl - total_fees

            # Update record
            with self._session_scope() as session:
                trade = (
                    session.query(PaperTrade)
                    .filter(PaperTrade.trade_id == trade_id)
                    .first()
                )
                if trade:
                    trade.sim_exit_price = sim_exit_price
                    trade.sim_exit_reason = sim_exit_reason
                    trade.sim_net_pnl_usdt = round(sim_net_pnl, 6)
                    trade.sim_completed = True

            logger.debug(
                "SL/TP simulation completed",
                trade_id=trade_id,
                sim_reason=sim_exit_reason,
                sim_pnl=round(sim_net_pnl, 4),
            )

        except Exception as e:
            logger.warning("SL/TP simulation failed", trade_id=trade_id, error=str(e))

    # ==================== Rejected Trade Simulation ====================

    def _simulate_rejected_trade(self, trade_id: str, symbol: str, side: str,
                                  entry_price: float, sl_price: float, tp_price: float):
        """Check if a rejected trade would have been profitable."""
        try:
            start_ms = int(datetime.utcnow().timestamp() * 1000)
            end_ms = start_ms + (self.max_holding_time * 1000)

            klines = self.binance_client.get_klines(
                symbol=symbol,
                interval="1m",
                start_time=start_ms,
                end_time=end_ms,
                limit=int(self.max_holding_time / 60) + 5,
            )

            if not klines:
                return

            is_long = side == "LONG"
            would_profit = False

            for candle in klines:
                high = candle["high"]
                low = candle["low"]

                if is_long:
                    if low <= sl_price:
                        break  # SL hit first
                    if high >= tp_price:
                        would_profit = True
                        break
                else:
                    if high >= sl_price:
                        break
                    if low <= tp_price:
                        would_profit = True
                        break

            with self._session_scope() as session:
                trade = (
                    session.query(PaperTrade)
                    .filter(PaperTrade.trade_id == trade_id)
                    .first()
                )
                if trade:
                    trade.would_have_been_profitable = would_profit

        except Exception as e:
            logger.warning("Rejected trade simulation failed", trade_id=trade_id, error=str(e))

    # ==================== Report Generation ====================

    def generate_report(self) -> str:
        """Generate a markdown report of paper trading performance."""
        try:
            with self._session_scope() as session:
                closed = (
                    session.query(PaperTrade)
                    .filter(PaperTrade.status == "CLOSED")
                    .all()
                )
                rejected = (
                    session.query(PaperTrade)
                    .filter(PaperTrade.status == "REJECTED")
                    .all()
                )
                open_trades = (
                    session.query(PaperTrade)
                    .filter(PaperTrade.status == "OPEN")
                    .all()
                )

                # Build report data while session is active
                report = self._build_report(closed, rejected, open_trades)

            # Write report file
            now = datetime.utcnow()
            filename = f"paper_{now.strftime('%Y%m%d_%H%M')}.md"
            filepath = os.path.join(self.reports_dir, filename)
            with open(filepath, "w", encoding="utf-8") as f:
                f.write(report)

            logger.info("Paper trading report generated", path=filepath, closed_trades=len(closed))
            return filepath

        except Exception as e:
            logger.error("Failed to generate paper report", error=str(e))
            return ""

    def _build_report(self, closed: List[PaperTrade], rejected: List[PaperTrade],
                       open_trades: List[PaperTrade]) -> str:
        """Build markdown report from trade records."""
        lines = []
        now = datetime.utcnow()
        lines.append(f"# Paper Trading Report")
        lines.append(f"**Generated**: {now.strftime('%Y-%m-%d %H:%M UTC')}")
        lines.append("")

        # ---- Summary ----
        lines.append("## Summary")
        lines.append("")

        total = len(closed)
        if total == 0:
            lines.append("No closed trades yet.")
            lines.append("")
        else:
            winners = [t for t in closed if t.net_pnl_usdt and t.net_pnl_usdt > 0]
            losers = [t for t in closed if t.net_pnl_usdt and t.net_pnl_usdt <= 0]
            win_rate = len(winners) / total if total > 0 else 0
            net_pnl = sum(t.net_pnl_usdt or 0 for t in closed)
            total_fees = sum(t.fees_usdt or 0 for t in closed)
            avg_win = (sum(t.net_pnl_usdt for t in winners) / len(winners)) if winners else 0
            avg_loss = (sum(t.net_pnl_usdt for t in losers) / len(losers)) if losers else 0
            best = max((t.net_pnl_usdt or 0) for t in closed) if closed else 0
            worst = min((t.net_pnl_usdt or 0) for t in closed) if closed else 0
            holding_times = [t.holding_time_seconds for t in closed if t.holding_time_seconds]
            avg_hold = sum(holding_times) / len(holding_times) if holding_times else 0

            lines.append(f"| Metric | Value |")
            lines.append(f"|--------|-------|")
            lines.append(f"| Total Trades | {total} |")
            lines.append(f"| Win Rate | {win_rate:.1%} |")
            lines.append(f"| Net PnL | ${net_pnl:+.2f} |")
            lines.append(f"| Total Fees | ${total_fees:.2f} |")
            lines.append(f"| Avg Win | ${avg_win:+.2f} |")
            lines.append(f"| Avg Loss | ${avg_loss:+.2f} |")
            lines.append(f"| Best Trade | ${best:+.2f} |")
            lines.append(f"| Worst Trade | ${worst:+.2f} |")
            lines.append(f"| Avg Holding Time | {avg_hold:.0f}s |")
            lines.append(f"| Open Positions | {len(open_trades)} |")

            # Sharpe ratio (annualized, if >=10 trades)
            if total >= 10:
                pnls = [t.net_pnl_usdt or 0 for t in closed]
                mean_pnl = sum(pnls) / len(pnls)
                variance = sum((p - mean_pnl) ** 2 for p in pnls) / len(pnls)
                std_pnl = math.sqrt(variance) if variance > 0 else 0
                if std_pnl > 0:
                    # Annualize: assume ~96 trades/day (15min holding, 24h)
                    sharpe = (mean_pnl / std_pnl) * math.sqrt(96)
                    lines.append(f"| Sharpe Ratio | {sharpe:.2f} |")

            lines.append("")

        # ---- Strategy Breakdown ----
        if closed:
            lines.append("## Strategy Breakdown")
            lines.append("")
            lines.append("| Strategy | Trades | Win Rate | Net PnL |")
            lines.append("|----------|--------|----------|---------|")

            strategies = {}
            for t in closed:
                key = t.strategy_id or "unknown"
                if key not in strategies:
                    strategies[key] = {"trades": 0, "wins": 0, "pnl": 0.0}
                strategies[key]["trades"] += 1
                if t.net_pnl_usdt and t.net_pnl_usdt > 0:
                    strategies[key]["wins"] += 1
                strategies[key]["pnl"] += t.net_pnl_usdt or 0

            for strat, data in sorted(strategies.items(), key=lambda x: x[1]["pnl"], reverse=True):
                wr = data["wins"] / data["trades"] if data["trades"] > 0 else 0
                lines.append(f"| {strat} | {data['trades']} | {wr:.0%} | ${data['pnl']:+.2f} |")

            lines.append("")

        # ---- Symbol Breakdown ----
        if closed:
            lines.append("## Symbol Breakdown")
            lines.append("")
            lines.append("| Symbol | Trades | Win Rate | Net PnL |")
            lines.append("|--------|--------|----------|---------|")

            symbols = {}
            for t in closed:
                key = t.symbol
                if key not in symbols:
                    symbols[key] = {"trades": 0, "wins": 0, "pnl": 0.0}
                symbols[key]["trades"] += 1
                if t.net_pnl_usdt and t.net_pnl_usdt > 0:
                    symbols[key]["wins"] += 1
                symbols[key]["pnl"] += t.net_pnl_usdt or 0

            for sym, data in sorted(symbols.items(), key=lambda x: x[1]["pnl"], reverse=True):
                wr = data["wins"] / data["trades"] if data["trades"] > 0 else 0
                lines.append(f"| {sym} | {data['trades']} | {wr:.0%} | ${data['pnl']:+.2f} |")

            lines.append("")

        # ---- SL/TP Simulation ----
        sim_trades = [t for t in closed if t.sim_completed]
        if sim_trades:
            lines.append("## SL/TP Simulation (what if trailing stop didn't intervene)")
            lines.append("")
            sim_pnl = sum(t.sim_net_pnl_usdt or 0 for t in sim_trades)
            actual_pnl = sum(t.net_pnl_usdt or 0 for t in sim_trades)
            lines.append(f"- Simulated PnL: ${sim_pnl:+.2f}")
            lines.append(f"- Actual PnL: ${actual_pnl:+.2f}")
            lines.append(f"- Difference: ${sim_pnl - actual_pnl:+.2f}")

            sim_reasons = {}
            for t in sim_trades:
                r = t.sim_exit_reason or "unknown"
                sim_reasons[r] = sim_reasons.get(r, 0) + 1
            lines.append(f"- Sim exit reasons: {sim_reasons}")
            lines.append("")

        # ---- Rejected Trades ----
        if rejected:
            lines.append("## Rejected Trades")
            lines.append("")
            profitable_count = sum(1 for t in rejected if t.would_have_been_profitable)
            lines.append(f"- Total rejected: {len(rejected)}")
            lines.append(f"- Would have been profitable: {profitable_count}")
            if len(rejected) > 0:
                lines.append(f"- Missed opportunity rate: {profitable_count / len(rejected):.0%}")

            # Rejection reasons
            reasons = {}
            for t in rejected:
                r = t.rejection_reason or "unknown"
                reasons[r] = reasons.get(r, 0) + 1
            if reasons:
                lines.append(f"- Rejection reasons: {reasons}")
            lines.append("")

        # ---- Recent Trades ----
        if closed:
            recent = sorted(closed, key=lambda t: t.exit_time or t.timestamp, reverse=True)[:10]
            lines.append("## Recent Trades (last 10)")
            lines.append("")
            lines.append("| Time | Symbol | Side | Strategy | PnL | Reason | Hold |")
            lines.append("|------|--------|------|----------|-----|--------|------|")
            for t in recent:
                exit_t = t.exit_time.strftime("%H:%M") if t.exit_time else "?"
                hold = f"{t.holding_time_seconds}s" if t.holding_time_seconds else "?"
                lines.append(
                    f"| {exit_t} | {t.symbol} | {t.side} | "
                    f"{t.strategy_id or '?'} | ${t.net_pnl_usdt or 0:+.2f} | "
                    f"{t.exit_reason or '?'} | {hold} |"
                )
            lines.append("")

        return "\n".join(lines)

    # ==================== Periodic Reports ====================

    def start_periodic_reports(self):
        """Start a daemon timer that generates reports at regular intervals."""
        if not self.enabled or self.report_interval_minutes <= 0:
            return

        self._stop_event.clear()
        self._schedule_next_report()
        logger.info("Periodic paper reports started", interval_min=self.report_interval_minutes)

    def _schedule_next_report(self):
        """Schedule the next report generation."""
        if self._stop_event.is_set():
            return

        self._report_timer = threading.Timer(
            self.report_interval_minutes * 60,
            self._periodic_report_callback,
        )
        self._report_timer.daemon = True
        self._report_timer.start()

    def _periodic_report_callback(self):
        """Timer callback: generate report and reschedule."""
        if self._stop_event.is_set():
            return
        try:
            self.generate_report()
        except Exception as e:
            logger.warning("Periodic report generation failed", error=str(e))
        self._schedule_next_report()

    def stop_periodic_reports(self):
        """Stop the periodic report timer."""
        self._stop_event.set()
        if self._report_timer:
            self._report_timer.cancel()
            self._report_timer = None
        logger.debug("Periodic paper reports stopped")

    # ==================== Cleanup ====================

    def close(self):
        """Stop threads and dispose engine."""
        self.stop_periodic_reports()
        self.engine.dispose()
        logger.debug("PaperDashboard closed")
