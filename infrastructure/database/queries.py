"""
Database Query Utilities

Common database queries for trading system.
"""

from typing import List, Dict, Any, Optional
from datetime import datetime, date, timedelta
from sqlalchemy import func, and_, or_
from sqlalchemy.orm import Session
import structlog

from .models import (
    ResearchSummary,
    TradingDecision,
    RiskApproval,
    Execution,
    PnLLedger,
    AuditTrail,
    PerformanceMetrics
)

logger = structlog.get_logger()


class DatabaseQueries:
    """
    Database query utilities for common operations.
    """

    def __init__(self, session: Session):
        """
        Initialize query utilities.

        Args:
            session: SQLAlchemy session
        """
        self.session = session

    # ========== Research Queries ==========

    def get_latest_research(self, symbol: str) -> Optional[ResearchSummary]:
        """Get most recent research summary for symbol."""
        return self.session.query(ResearchSummary).filter(
            ResearchSummary.symbol == symbol
        ).order_by(
            ResearchSummary.analysis_timestamp.desc()
        ).first()

    def get_research_by_id(self, research_id: str) -> Optional[ResearchSummary]:
        """Get research summary by ID."""
        return self.session.query(ResearchSummary).filter(
            ResearchSummary.id == research_id
        ).first()

    # ========== Decision Queries ==========

    def get_decision_by_id(self, decision_id: str) -> Optional[TradingDecision]:
        """Get trading decision by ID."""
        return self.session.query(TradingDecision).filter(
            TradingDecision.id == decision_id
        ).first()

    def get_decisions_by_date(
        self,
        start_date: datetime,
        end_date: datetime,
        symbol: Optional[str] = None
    ) -> List[TradingDecision]:
        """Get trading decisions within date range."""
        query = self.session.query(TradingDecision).filter(
            and_(
                TradingDecision.timestamp >= start_date,
                TradingDecision.timestamp <= end_date
            )
        )

        if symbol:
            query = query.filter(TradingDecision.symbol == symbol)

        return query.order_by(TradingDecision.timestamp.desc()).all()

    def count_decisions_by_type(self, start_date: datetime) -> Dict[str, int]:
        """Count decisions by type (LONG/SHORT/NO_TRADE) since date."""
        results = self.session.query(
            TradingDecision.decision,
            func.count(TradingDecision.id)
        ).filter(
            TradingDecision.timestamp >= start_date
        ).group_by(
            TradingDecision.decision
        ).all()

        return {decision: count for decision, count in results}

    # ========== Risk Approval Queries ==========

    def get_approval_stats(self, start_date: datetime) -> Dict[str, int]:
        """Get risk approval statistics since date."""
        results = self.session.query(
            RiskApproval.approval_status,
            func.count(RiskApproval.id)
        ).filter(
            RiskApproval.timestamp >= start_date
        ).group_by(
            RiskApproval.approval_status
        ).all()

        return {status: count for status, count in results}

    # ========== Execution Queries ==========

    def get_executions_by_date(
        self,
        start_date: datetime,
        end_date: datetime,
        execution_mode: Optional[str] = None
    ) -> List[Execution]:
        """Get executions within date range."""
        query = self.session.query(Execution).filter(
            and_(
                Execution.timestamp >= start_date,
                Execution.timestamp <= end_date
            )
        )

        if execution_mode:
            query = query.filter(Execution.execution_mode == execution_mode)

        return query.order_by(Execution.timestamp.desc()).all()

    def get_execution_by_id(self, execution_id: str) -> Optional[Execution]:
        """Get execution by ID."""
        return self.session.query(Execution).filter(
            Execution.id == execution_id
        ).first()

    # ========== P&L Queries ==========

    def get_open_positions(self) -> List[PnLLedger]:
        """Get all open positions."""
        return self.session.query(PnLLedger).filter(
            PnLLedger.is_closed == False
        ).all()

    def get_position_by_symbol(self, symbol: str) -> Optional[PnLLedger]:
        """Get open position for symbol."""
        return self.session.query(PnLLedger).filter(
            and_(
                PnLLedger.symbol == symbol,
                PnLLedger.is_closed == False
            )
        ).first()

    def get_closed_positions_by_date(
        self,
        start_date: datetime,
        end_date: datetime
    ) -> List[PnLLedger]:
        """Get closed positions within date range."""
        return self.session.query(PnLLedger).filter(
            and_(
                PnLLedger.is_closed == True,
                PnLLedger.exit_time >= start_date,
                PnLLedger.exit_time <= end_date
            )
        ).order_by(PnLLedger.exit_time.desc()).all()

    def calculate_total_pnl(
        self,
        start_date: datetime,
        end_date: datetime
    ) -> float:
        """Calculate total realized P&L within date range."""
        result = self.session.query(
            func.sum(PnLLedger.realized_pnl_usdt)
        ).filter(
            and_(
                PnLLedger.is_closed == True,
                PnLLedger.exit_time >= start_date,
                PnLLedger.exit_time <= end_date
            )
        ).scalar()

        return result or 0.0

    def get_win_rate(
        self,
        start_date: datetime,
        end_date: datetime
    ) -> float:
        """Calculate win rate within date range."""
        closed_positions = self.get_closed_positions_by_date(start_date, end_date)

        if not closed_positions:
            return 0.0

        winning_trades = sum(1 for p in closed_positions if p.realized_pnl_usdt > 0)
        return winning_trades / len(closed_positions)

    def get_stop_loss_for_position(self, execution_id: str) -> Optional[float]:
        """Get the original stop_loss price from the trading decision linked to an execution."""
        execution = self.session.query(Execution).filter(
            Execution.id == execution_id
        ).first()
        if execution:
            decision = self.session.query(TradingDecision).filter(
                TradingDecision.id == execution.decision_id
            ).first()
            if decision:
                return decision.stop_loss
        return None

    # ========== Audit Trail Queries ==========

    def get_audit_trail(
        self,
        correlation_id: str
    ) -> List[AuditTrail]:
        """Get full audit trail for a trading cycle."""
        return self.session.query(AuditTrail).filter(
            AuditTrail.correlation_id == correlation_id
        ).order_by(AuditTrail.timestamp.asc()).all()

    def verify_audit_chain(self, correlation_id: str) -> bool:
        """Verify hash chain integrity for audit trail."""
        entries = self.get_audit_trail(correlation_id)

        for i in range(1, len(entries)):
            if entries[i].previous_hash != entries[i-1].current_hash:
                logger.error(
                    "Hash chain broken",
                    correlation_id=correlation_id,
                    entry_id=entries[i].id
                )
                return False

        return True

    # ========== Performance Metrics Queries ==========

    def get_daily_metrics(self, date: date) -> Optional[PerformanceMetrics]:
        """Get daily performance metrics."""
        return self.session.query(PerformanceMetrics).filter(
            and_(
                func.date(PerformanceMetrics.date) == date,
                PerformanceMetrics.metric_type == "daily"
            )
        ).first()

    def get_metrics_range(
        self,
        start_date: date,
        end_date: date,
        metric_type: str = "daily"
    ) -> List[PerformanceMetrics]:
        """Get performance metrics for date range."""
        return self.session.query(PerformanceMetrics).filter(
            and_(
                PerformanceMetrics.date >= start_date,
                PerformanceMetrics.date <= end_date,
                PerformanceMetrics.metric_type == metric_type
            )
        ).order_by(PerformanceMetrics.date.desc()).all()

    # ========== Strategy Win Rate & Cooldown Queries ==========

    def get_strategy_win_rate(
        self,
        strategy_id: str,
        lookback_days: int = 7,
        min_trades: int = 30
    ) -> Optional[float]:
        """
        Get actual win rate for a strategy from closed P&L records.

        Returns:
            Win rate (0.0-1.0) if enough trades, else None (use conservative default).
        """
        cutoff = datetime.utcnow() - timedelta(days=lookback_days)

        # Join PnLLedger → Execution → TradingDecision to get strategy_id
        results = (
            self.session.query(PnLLedger.realized_pnl_usdt)
            .join(Execution, Execution.id == PnLLedger.execution_id)
            .join(TradingDecision, TradingDecision.id == Execution.decision_id)
            .filter(
                and_(
                    TradingDecision.strategy_id == strategy_id,
                    PnLLedger.is_closed == True,
                    PnLLedger.exit_time >= cutoff,
                )
            )
            .all()
        )

        total = len(results)
        if total < min_trades:
            return None  # Not enough data — caller should use conservative default

        wins = sum(1 for (pnl,) in results if pnl and pnl > 0)
        return wins / total

    def get_consecutive_losses(
        self,
        symbol: str,
        lookback_minutes: int = 30
    ) -> int:
        """
        Count consecutive losses (most recent first) for a symbol within lookback window.

        Returns:
            Number of consecutive losses from the most recent trade backwards.
        """
        cutoff = datetime.utcnow() - timedelta(minutes=lookback_minutes)

        recent_closed = (
            self.session.query(PnLLedger.realized_pnl_usdt)
            .filter(
                and_(
                    PnLLedger.symbol == symbol,
                    PnLLedger.is_closed == True,
                    PnLLedger.exit_time >= cutoff,
                )
            )
            .order_by(PnLLedger.exit_time.desc())
            .limit(10)
            .all()
        )

        consecutive = 0
        for (pnl,) in recent_closed:
            if pnl is not None and pnl < 0:
                consecutive += 1
            else:
                break  # First non-loss breaks the streak

        return consecutive

    # ========== Analytics ==========

    def get_strategy_performance(
        self,
        start_date: datetime
    ) -> Dict[str, Dict[str, Any]]:
        """
        Get performance breakdown by strategy.

        Returns:
            Dict with strategy performance metrics
        """
        decisions = self.session.query(TradingDecision).filter(
            TradingDecision.timestamp >= start_date
        ).all()

        strategy_stats = {}

        for decision in decisions:
            strategy_id = decision.strategy_id
            if not strategy_id or decision.decision == "NO_TRADE":
                continue

            if strategy_id not in strategy_stats:
                strategy_stats[strategy_id] = {
                    "total_trades": 0,
                    "decisions": {"LONG": 0, "SHORT": 0}
                }

            strategy_stats[strategy_id]["total_trades"] += 1
            strategy_stats[strategy_id]["decisions"][decision.decision] += 1

        return strategy_stats

    def get_model_accuracy(
        self,
        start_date: datetime
    ) -> Dict[str, float]:
        """
        Calculate accuracy for each AI model used.

        Returns:
            Dict with model accuracy scores
        """
        # TODO: Implement by comparing decision confidence with actual P&L
        return {}
