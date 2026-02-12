"""
Storage & Reporting Agent Implementation

Persists all trading data to database and generates reports.
"""

from typing import Dict, Any, Optional, List
from datetime import datetime, timedelta, date
import structlog
import hashlib
import json

from .base_agent import BaseAgent
from infrastructure.database import (
    DatabaseSession,
    ResearchSummary,
    TradingDecision,
    RiskApproval,
    Execution,
    PnLLedger,
    AuditTrail,
    PerformanceMetrics,
    DatabaseQueries
)

logger = structlog.get_logger()


class StorageReporterAgent(BaseAgent):
    """
    Storage & Reporting Agent

    Responsibilities:
    - Persist all research summaries to database
    - Persist all trading decisions to database
    - Persist all risk approvals to database
    - Persist all execution results to database
    - Maintain audit trail with hash chain
    - Track P&L for all positions
    - Generate daily/weekly/monthly reports
    - Calculate performance metrics

    Authority Boundaries:
    ✅ Store all trading data
    ✅ Generate reports and analytics
    ✅ Maintain audit trail
    ❌ Make trading decisions
    ❌ Modify stored data (immutability)
    """

    def __init__(self, agent_id: str, config: Dict[str, Any], db_session: DatabaseSession, model_router=None, trades_db=None):
        """
        Initialize Storage & Reporting Agent.

        Args:
            agent_id: Unique agent identifier
            config: Agent configuration
            db_session: Database session instance
            model_router: Optional ModelRouter for LLM enhancement
            trades_db: Optional TradesDB for flat trade records
        """
        super().__init__(agent_id, config, model_router=model_router)
        self.db_session = db_session
        self.queries = DatabaseQueries(db_session.get_session())
        self.trades_db = trades_db

        logger.debug(
            "Storage & Reporting Agent initialized",
            agent_id=self.agent_id
        )

    def execute(self, state: Dict[str, Any]) -> Dict[str, Any]:
        """
        Main execution method for storage and reporting.

        Args:
            state: Current trading pipeline state

        Returns:
            Updated state with storage confirmation
        """
        logger.debug(
            "Storage node started",
            correlation_id=state.get("correlation_id")
        )

        start_time = datetime.utcnow()

        try:
            with self.db_session.session_scope() as session:
                self.queries.session = session

                # Store research summary if present
                if state.get("research_summary"):
                    research_id = self._store_research_summary(
                        state["research_summary"],
                        session
                    )
                    logger.debug("Research summary stored", research_id=research_id)

                # Store trading decision if present
                decision_id = None
                if state.get("trading_decision"):
                    decision_id = self._store_trading_decision(
                        state["trading_decision"],
                        session
                    )
                    logger.debug("Trading decision stored", decision_id=decision_id)

                # Store risk approval if present
                approval_id = None
                if state.get("risk_approval"):
                    approval_id = self._store_risk_approval(
                        state["risk_approval"],
                        session
                    )
                    logger.debug("Risk approval stored", approval_id=approval_id)

                # Store execution result if present
                execution_id = None
                if state.get("execution_result"):
                    execution_id = self._store_execution_result(
                        state["execution_result"],
                        session
                    )
                    logger.debug("Execution result stored", execution_id=execution_id)

                    # Update P&L if execution was successful
                    if state["execution_result"].get("execution_status") in ("FILLED", "PARTIALLY_FILLED"):
                        self._update_pnl(state["execution_result"], session)

                        # Record in flat trades DB
                        if self.trades_db:
                            td = state.get("trading_decision") or {}
                            od = state["execution_result"].get("order_details", {})
                            self.trades_db.record_open(
                                trade_id=state["execution_result"]["execution_id"],
                                symbol=state["execution_result"]["symbol"],
                                side=state["execution_result"]["side"],
                                entry_price=od.get("avg_fill_price", 0),
                                quantity=od.get("filled_quantity", 0),
                                leverage=od.get("leverage", 1),
                                strategy=td.get("strategy_id"),
                                confidence=td.get("confidence"),
                                fees_usdt=od.get("commission_usdt", 0),
                                correlation_id=state.get("correlation_id"),
                                decision_id=td.get("decision_id"),
                                execution_id=state["execution_result"]["execution_id"],
                            )

                    elif state["execution_result"].get("execution_status") == "REJECTED":
                        # Record rejected trade in flat trades DB
                        if self.trades_db:
                            td = state.get("trading_decision") or {}
                            self.trades_db.record_rejected(
                                trade_id=state["execution_result"]["execution_id"],
                                symbol=state["execution_result"]["symbol"],
                                side=state["execution_result"]["side"],
                                strategy=td.get("strategy_id"),
                                confidence=td.get("confidence"),
                                correlation_id=state.get("correlation_id"),
                                decision_id=td.get("decision_id"),
                            )

                # Store audit trail entry
                self._store_audit_entry(
                    correlation_id=state.get("correlation_id"),
                    event_type="trading_cycle_complete",
                    event_data=state,
                    session=session
                )

                # LLM Enhancement: Generate cycle analysis
                cycle_analysis = self._get_cycle_analysis(state)
                if cycle_analysis:
                    self._store_audit_entry(
                        correlation_id=state.get("correlation_id"),
                        event_type="llm_cycle_analysis",
                        event_data=cycle_analysis,
                        session=session
                    )

            processing_time = (datetime.utcnow() - start_time).total_seconds() * 1000

            logger.debug(
                "Storage node completed",
                correlation_id=state.get("correlation_id"),
                processing_time_ms=processing_time
            )

            # Return state with storage confirmation
            return {
                "storage_complete": True,
                "processing_time_ms": processing_time
            }

        except Exception as e:
            logger.error(
                "Storage node failed",
                correlation_id=state.get("correlation_id"),
                error=str(e)
            )
            return {
                "storage_complete": False,
                "error": str(e)
            }

    def _store_research_summary(
        self,
        research_data: Dict[str, Any],
        session
    ) -> str:
        """Store research summary to database."""

        # Serialize JSON fields to handle numpy types
        research_summary = ResearchSummary(
            id=research_data.get("research_id"),
            symbol=research_data["symbol"],
            timeframe=research_data["timeframe"],
            analysis_timestamp=datetime.fromisoformat(research_data["timestamp"]),
            market_data=self._serialize_for_json(research_data.get("market_data", {})),
            technical_indicators=self._serialize_for_json(research_data.get("technical_indicators", {})),
            sentiment=self._serialize_for_json(research_data.get("sentiment", {})),
            market_regime=research_data.get("market_regime"),
            news_events=self._serialize_for_json(research_data.get("news_events", [])),
            warnings=research_data.get("warnings", []),
            time_decay_factor=research_data.get("time_decay_factor", 1.0)
        )

        session.add(research_summary)
        return research_summary.id

    def _store_trading_decision(
        self,
        decision_data: Dict[str, Any],
        session
    ) -> str:
        """Store trading decision to database."""

        # Serialize JSON fields to handle numpy types
        trading_decision = TradingDecision(
            id=decision_data["decision_id"],
            research_summary_id=decision_data.get("research_summary_id"),
            symbol=decision_data["symbol"],
            decision=decision_data["decision"],
            confidence=decision_data["confidence"],
            strategy_id=decision_data.get("strategy_id"),
            model_used=decision_data.get("model_used"),
            reasoning_summary=decision_data.get("reasoning_summary"),
            entry_price=decision_data.get("entry_price"),
            stop_loss=decision_data.get("stop_loss"),
            take_profit_levels=self._serialize_for_json(decision_data.get("take_profit_levels", [])),
            position_size_usdt=decision_data.get("position_size_usdt"),
            leverage=decision_data.get("leverage"),
            technical_signals=self._serialize_for_json(decision_data.get("technical_signals", {})),
            risk_metrics=self._serialize_for_json(decision_data.get("risk_metrics", {})),
            timestamp=datetime.fromisoformat(decision_data["timestamp"]),
            research_summary_hash=decision_data.get("research_summary_hash")
        )

        session.add(trading_decision)
        return trading_decision.id

    def _store_risk_approval(
        self,
        approval_data: Dict[str, Any],
        session
    ) -> str:
        """Store risk approval to database."""

        # Serialize JSON fields to handle numpy types
        risk_approval = RiskApproval(
            id=approval_data["approval_id"],
            decision_id=approval_data["decision_id"],
            approval_status=approval_data["approval_status"],
            rejection_reason=approval_data.get("rejection_reason"),
            modified_parameters=self._serialize_for_json(approval_data.get("modified_parameters", {})),
            risk_checks=self._serialize_for_json(approval_data.get("risk_checks", {})),
            position_sizing=self._serialize_for_json(approval_data.get("position_sizing", {})),
            account_status=self._serialize_for_json(approval_data.get("account_status", {})),
            kill_switches=self._serialize_for_json(approval_data.get("kill_switches", {})),
            timestamp=datetime.fromisoformat(approval_data["timestamp"]),
            processing_time_ms=approval_data.get("processing_time_ms")
        )

        session.add(risk_approval)
        return risk_approval.id

    def _store_execution_result(
        self,
        execution_data: Dict[str, Any],
        session
    ) -> str:
        """Store execution result to database."""

        # Serialize JSON fields to handle numpy types
        execution = Execution(
            id=execution_data["execution_id"],
            approval_id=execution_data["approval_id"],
            decision_id=execution_data["decision_id"],
            execution_mode=execution_data["execution_mode"],
            execution_status=execution_data["execution_status"],
            symbol=execution_data["symbol"],
            side=execution_data["side"],
            order_details=self._serialize_for_json(execution_data.get("order_details", {})),
            stop_loss_order=self._serialize_for_json(execution_data.get("stop_loss_order")) if execution_data.get("stop_loss_order") else None,
            take_profit_orders=self._serialize_for_json(execution_data.get("take_profit_orders", [])),
            shadow_paper_execution=self._serialize_for_json(execution_data.get("shadow_paper_execution")) if execution_data.get("shadow_paper_execution") else None,
            paper_trading_simulation=self._serialize_for_json(execution_data.get("paper_trading_simulation")) if execution_data.get("paper_trading_simulation") else None,
            execution_timeline=self._serialize_for_json(execution_data.get("execution_timeline", [])),
            errors=execution_data.get("errors", []),
            timestamp=datetime.fromisoformat(execution_data["timestamp"]),
            processing_time_ms=execution_data.get("processing_time_ms"),
            idempotency_check=execution_data.get("idempotency_check")
        )

        session.add(execution)
        return execution.id

    def _update_pnl(self, execution_data: Dict[str, Any], session):
        """Update P&L ledger with new execution."""

        order_details = execution_data.get("order_details", {})

        # Extract SL/TP order IDs if present
        sl_order = execution_data.get("stop_loss_order") or {}
        tp_orders = execution_data.get("take_profit_orders") or []
        sl_order_id = sl_order.get("binance_order_id") if sl_order.get("status") == "PLACED" else None
        tp_order_id = None
        if tp_orders:
            for tp in tp_orders:
                if tp.get("status") == "PLACED" and tp.get("binance_order_id"):
                    tp_order_id = tp["binance_order_id"]
                    break

        # Create new P&L entry for the position
        pnl_entry = PnLLedger(
            execution_id=execution_data["execution_id"],
            symbol=execution_data["symbol"],
            side=execution_data["side"],
            entry_price=order_details.get("avg_fill_price", 0.0),
            quantity=order_details.get("filled_quantity", 0.0),
            leverage=order_details.get("leverage", 1),
            entry_time=datetime.fromisoformat(execution_data["timestamp"]),
            fees_usdt=order_details.get("commission_usdt", 0.0),
            unrealized_pnl_usdt=0.0,
            is_closed=False,
            sl_order_id=sl_order_id,
            tp_order_id=tp_order_id,
        )

        session.add(pnl_entry)

        logger.debug(
            "P&L entry created",
            execution_id=execution_data["execution_id"],
            symbol=execution_data["symbol"],
            side=execution_data["side"],
            entry_price=pnl_entry.entry_price,
            sl_order_id=sl_order_id,
            tp_order_id=tp_order_id,
        )

    def _store_audit_entry(
        self,
        correlation_id: str,
        event_type: str,
        event_data: Dict[str, Any],
        session
    ):
        """Store audit trail entry with hash chain."""

        # Serialize event_data to handle datetime objects
        serialized_event_data = self._serialize_for_json(event_data)

        # Compute input hash
        input_hash = self._compute_hash(event_data)

        # Get previous audit entry for this correlation_id
        previous_entry = session.query(AuditTrail).filter(
            AuditTrail.correlation_id == correlation_id
        ).order_by(AuditTrail.timestamp.desc()).first()

        previous_hash = previous_entry.current_hash if previous_entry else None

        # Compute current hash (input_hash + previous_hash)
        hash_input = f"{input_hash}:{previous_hash if previous_hash else ''}"
        current_hash = hashlib.sha256(hash_input.encode()).hexdigest()

        audit_entry = AuditTrail(
            correlation_id=correlation_id,
            agent_id=self.agent_id,
            event_type=event_type,
            event_data=serialized_event_data,
            input_hash=input_hash,
            previous_hash=previous_hash,
            current_hash=current_hash,
            timestamp=datetime.utcnow()
        )

        session.add(audit_entry)

        logger.debug(
            "Audit trail entry created",
            correlation_id=correlation_id,
            event_type=event_type,
            current_hash=current_hash[:16]
        )

    def _compute_hash(self, data: Dict[str, Any]) -> str:
        """Compute SHA-256 hash of data."""
        json_str = json.dumps(data, sort_keys=True, default=str)
        return hashlib.sha256(json_str.encode()).hexdigest()

    def _serialize_for_json(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Serialize data to be JSON-compatible (convert datetime, numpy types to native Python types)."""
        import numpy as np

        def convert_value(obj):
            """Convert non-JSON-serializable objects to JSON-serializable ones."""
            if isinstance(obj, (datetime, date)):
                return obj.isoformat()
            elif isinstance(obj, np.bool_):
                return bool(obj)
            elif isinstance(obj, np.integer):
                return int(obj)
            elif isinstance(obj, np.floating):
                return float(obj)
            elif isinstance(obj, np.ndarray):
                return obj.tolist()
            else:
                return str(obj)

        return json.loads(json.dumps(data, default=convert_value))

    # ========== Reporting Methods ==========

    def generate_daily_report(self, target_date: date = None) -> Dict[str, Any]:
        """
        Generate daily trading report.

        Args:
            target_date: Date for report (default: today)

        Returns:
            Daily report data
        """
        if target_date is None:
            target_date = datetime.utcnow().date()

        start_datetime = datetime.combine(target_date, datetime.min.time())
        end_datetime = datetime.combine(target_date, datetime.max.time())

        with self.db_session.session_scope() as session:
            self.queries.session = session

            # Get all decisions for the day
            decisions = self.queries.get_decisions_by_date(start_datetime, end_datetime)

            # Get all executions for the day
            executions = self.queries.get_executions_by_date(start_datetime, end_datetime)

            # Get closed positions for the day
            closed_positions = self.queries.get_closed_positions_by_date(start_datetime, end_datetime)

            # Calculate metrics
            total_pnl = self.queries.calculate_total_pnl(start_datetime, end_datetime)
            win_rate = self.queries.get_win_rate(start_datetime, end_datetime)

            # Decision breakdown
            decision_counts = self.queries.count_decisions_by_type(start_datetime)

            # Approval stats
            approval_stats = self.queries.get_approval_stats(start_datetime)

            report = {
                "date": target_date.isoformat(),
                "summary": {
                    "total_decisions": len(decisions),
                    "total_executions": len(executions),
                    "total_closed_positions": len(closed_positions),
                    "total_pnl_usdt": round(total_pnl, 2),
                    "win_rate": round(win_rate, 4)
                },
                "decisions": {
                    "LONG": decision_counts.get("LONG", 0),
                    "SHORT": decision_counts.get("SHORT", 0),
                    "NO_TRADE": decision_counts.get("NO_TRADE", 0)
                },
                "risk_approvals": {
                    "APPROVED": approval_stats.get("APPROVED", 0),
                    "REJECTED": approval_stats.get("REJECTED", 0),
                    "MODIFIED": approval_stats.get("MODIFIED", 0)
                },
                "trades": [
                    {
                        "symbol": pos.symbol,
                        "side": pos.side,
                        "entry_price": pos.entry_price,
                        "exit_price": pos.exit_price,
                        "pnl_usdt": pos.realized_pnl_usdt,
                        "holding_time_seconds": pos.holding_time_seconds
                    }
                    for pos in closed_positions
                ]
            }

            logger.info(
                "Daily report generated",
                date=target_date.isoformat(),
                total_pnl=total_pnl,
                win_rate=win_rate
            )

            return report

    def calculate_performance_metrics(
        self,
        start_date: date,
        end_date: date,
        metric_type: str = "daily"
    ) -> Dict[str, Any]:
        """
        Calculate comprehensive performance metrics.

        Args:
            start_date: Start date for analysis
            end_date: End date for analysis
            metric_type: Type of metrics (daily/weekly/monthly)

        Returns:
            Performance metrics
        """
        start_datetime = datetime.combine(start_date, datetime.min.time())
        end_datetime = datetime.combine(end_date, datetime.max.time())

        with self.db_session.session_scope() as session:
            self.queries.session = session

            # Get closed positions
            closed_positions = self.queries.get_closed_positions_by_date(
                start_datetime,
                end_datetime
            )

            if not closed_positions:
                return {
                    "metric_type": metric_type,
                    "period": f"{start_date} to {end_date}",
                    "total_trades": 0,
                    "message": "No closed positions in this period"
                }

            # Calculate basic metrics
            total_trades = len(closed_positions)
            winning_trades = sum(1 for p in closed_positions if p.realized_pnl_usdt > 0)
            losing_trades = sum(1 for p in closed_positions if p.realized_pnl_usdt < 0)
            win_rate = winning_trades / total_trades if total_trades > 0 else 0

            # P&L metrics
            total_pnl = sum(p.realized_pnl_usdt for p in closed_positions)
            wins = [p.realized_pnl_usdt for p in closed_positions if p.realized_pnl_usdt > 0]
            losses = [p.realized_pnl_usdt for p in closed_positions if p.realized_pnl_usdt < 0]

            avg_win = sum(wins) / len(wins) if wins else 0
            avg_loss = sum(losses) / len(losses) if losses else 0
            largest_win = max(wins) if wins else 0
            largest_loss = min(losses) if losses else 0

            # Holding time
            holding_times = [p.holding_time_seconds for p in closed_positions if p.holding_time_seconds]
            avg_holding_time = sum(holding_times) / len(holding_times) if holding_times else 0

            # Strategy breakdown
            strategy_performance = self.queries.get_strategy_performance(start_datetime)

            metrics = {
                "metric_type": metric_type,
                "date": end_date.isoformat(),
                "period": f"{start_date} to {end_date}",
                "total_trades": total_trades,
                "winning_trades": winning_trades,
                "losing_trades": losing_trades,
                "win_rate": round(win_rate, 4),
                "total_pnl_usdt": round(total_pnl, 2),
                "avg_win_usdt": round(avg_win, 2),
                "avg_loss_usdt": round(avg_loss, 2),
                "largest_win_usdt": round(largest_win, 2),
                "largest_loss_usdt": round(largest_loss, 2),
                "avg_holding_time_seconds": round(avg_holding_time, 0),
                "strategy_breakdown": strategy_performance
            }

            # Store metrics to database
            performance_metric = PerformanceMetrics(
                date=datetime.combine(end_date, datetime.min.time()),
                metric_type=metric_type,
                **metrics
            )

            session.add(performance_metric)

            logger.info(
                "Performance metrics calculated",
                metric_type=metric_type,
                period=metrics["period"],
                total_pnl=total_pnl,
                win_rate=win_rate
            )

            return metrics

    def verify_audit_trail(self, correlation_id: str) -> bool:
        """
        Verify audit trail hash chain integrity.

        Args:
            correlation_id: Correlation ID to verify

        Returns:
            True if hash chain is valid, False otherwise
        """
        with self.db_session.session_scope() as session:
            self.queries.session = session
            return self.queries.verify_audit_chain(correlation_id)

    def export_report(
        self,
        report_data: Dict[str, Any],
        format: str = "json",
        output_path: str = None
    ) -> str:
        """
        Export report to file.

        Args:
            report_data: Report data to export
            format: Export format (json/csv/markdown)
            output_path: Output file path

        Returns:
            Path to exported file
        """
        if output_path is None:
            timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
            output_path = f"reports/report_{timestamp}.{format}"

        if format == "json":
            with open(output_path, 'w') as f:
                json.dump(report_data, f, indent=2, default=str)

        elif format == "markdown":
            with open(output_path, 'w') as f:
                f.write(f"# Trading Report\n\n")
                f.write(f"**Generated**: {datetime.utcnow().isoformat()}\n\n")
                f.write(f"## Summary\n\n")
                for key, value in report_data.get("summary", {}).items():
                    f.write(f"- **{key}**: {value}\n")

        logger.info("Report exported", path=output_path, format=format)
        return output_path

    # ========== LLM ENHANCEMENT ==========

    def _get_cycle_analysis(self, state: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        Call LLM for post-cycle analytical summary.

        Returns parsed LLM response or None if unavailable.
        """
        from orchestration.model_router import TaskType
        from prompts.base import build_prompt
        from prompts.storage_reporter import STORAGE_REPORTER_SYSTEM

        # Build a concise context (avoid sending the entire state to save tokens)
        trading_decision = state.get("trading_decision") or {}
        execution_result = state.get("execution_result") or {}
        research_summary = state.get("research_summary") or {}

        context_data = {
            "symbol": state.get("symbol"),
            "decision": trading_decision.get("decision"),
            "strategy": trading_decision.get("strategy_id"),
            "confidence": trading_decision.get("confidence"),
            "execution_status": execution_result.get("execution_status"),
            "market_regime": research_summary.get("market_regime"),
            "warnings": research_summary.get("warnings", []),
            "pipeline_stage": state.get("pipeline_stage"),
            "errors": state.get("errors", []),
        }

        system_prompt, user_prompt = build_prompt(STORAGE_REPORTER_SYSTEM, context_data)

        result = self.call_llm(
            task_type=TaskType.SIMPLE_REASONING,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            context="research",
            max_escalations=0,  # No escalation for post-trade analysis
        )

        if result and result.get("response"):
            logger.debug("LLM cycle analysis generated", model=result.get("model_used"))
            return result["response"]

        return None
