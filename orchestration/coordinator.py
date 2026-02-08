"""
Trading Coordinator - Main Orchestration Logic

Implements LangChain/LangGraph supervisor pattern to coordinate all agents
in the trading decision pipeline.
"""

import uuid
from typing import Dict, Any, Optional
from datetime import datetime
import structlog

from langgraph.graph import StateGraph, END
from .state_manager import TradingState, StateManager, ExecutionMode
from .model_router import ModelRouter, TaskType

logger = structlog.get_logger()


class TradingCoordinator:
    """
    Main coordinator for the multi-agent trading system.

    Orchestrates the flow: Research → Decision → Risk Check → Execution → Storage

    Uses LangGraph for state machine management and conditional routing.
    """

    def __init__(self, config: Dict[str, Any]):
        """
        Initialize the trading coordinator.

        Args:
            config: System configuration dict
        """
        self.config = config
        self.state_manager = StateManager()
        self.model_router = ModelRouter(config.get("models", {}))

        # Agent references (will be injected)
        self.agents = {}

        # Build execution graph
        self.graph = self._build_execution_graph()

        logger.info(
            "TradingCoordinator initialized",
            execution_mode=config.get("trading", {}).get("execution_mode"),
            symbols=config.get("trading", {}).get("symbols", []),
            max_positions=config.get("trading", {}).get("max_concurrent_positions")
        )

    def register_agent(self, agent_name: str, agent_instance: Any) -> None:
        """
        Register an agent with the coordinator.

        Args:
            agent_name: Agent identifier (research, decision, risk_check, etc.)
            agent_instance: Agent instance
        """
        self.agents[agent_name] = agent_instance
        logger.info("Agent registered", agent_name=agent_name)

    def _build_execution_graph(self) -> StateGraph:
        """
        Build the LangGraph execution graph.

        Defines the agent pipeline with conditional routing based on decisions.

        Returns:
            Compiled StateGraph
        """
        workflow = StateGraph(TradingState)

        # Add agent nodes
        workflow.add_node("research", self._research_node)
        workflow.add_node("decision", self._decision_node)
        workflow.add_node("risk_check", self._risk_check_node)
        workflow.add_node("execute", self._execute_node)
        workflow.add_node("store", self._store_node)

        # Set entry point
        workflow.set_entry_point("research")

        # Define edges
        workflow.add_edge("research", "decision")
        workflow.add_edge("decision", "risk_check")

        # Conditional routing from risk_check
        workflow.add_conditional_edges(
            "risk_check",
            self._route_after_risk_check,
            {
                "approved": "execute",
                "modified": "execute",
                "rejected": "store",
                "error": "store"
            }
        )

        workflow.add_edge("execute", "store")
        workflow.add_edge("store", END)

        return workflow.compile()

    # ========== Agent Node Implementations ==========

    def _research_node(self, state: TradingState) -> TradingState:
        """
        Research Coordinator node.

        Gathers market data, news, sentiment, and technical indicators.
        """
        start_time = datetime.utcnow()
        logger.info("Research node started", correlation_id=state["correlation_id"])

        try:
            # Call Research Coordinator agent
            agent = self.agents.get("research_coordinator")
            if not agent:
                raise ValueError("Research Coordinator agent not registered")

            research_summary = agent.execute(state)

            state["research_summary"] = research_summary
            state = self.state_manager.update_stage(
                state,
                "research_complete",
                (datetime.utcnow() - start_time).total_seconds() * 1000
            )

            logger.info("Research node completed", correlation_id=state["correlation_id"])

        except Exception as e:
            logger.error("Research node failed", error=str(e), correlation_id=state["correlation_id"])
            state = self.state_manager.add_error(
                state,
                "research-coordinator",
                "RESEARCH_FAILED",
                str(e),
                "critical"
            )

        return state

    def _decision_node(self, state: TradingState) -> TradingState:
        """
        Trading Decision node.

        Analyzes research and decides whether to trade (LONG/SHORT/NO_TRADE).
        """
        start_time = datetime.utcnow()
        logger.info("Decision node started", correlation_id=state["correlation_id"])

        try:
            # Call Trading Decision agent
            agent = self.agents.get("trading_decision")
            if not agent:
                raise ValueError("Trading Decision agent not registered")

            trading_decision = agent.execute(state)

            state["trading_decision"] = trading_decision

            # Check if NO_TRADE decision
            if trading_decision.get("decision") == "NO_TRADE":
                state["should_trade"] = False
                logger.info("NO_TRADE decision made", correlation_id=state["correlation_id"])

            state = self.state_manager.update_stage(
                state,
                "decision_made",
                (datetime.utcnow() - start_time).total_seconds() * 1000
            )

            logger.info(
                "Decision node completed",
                decision=trading_decision.get("decision"),
                confidence=trading_decision.get("confidence"),
                correlation_id=state["correlation_id"]
            )

        except Exception as e:
            logger.error("Decision node failed", error=str(e), correlation_id=state["correlation_id"])
            state = self.state_manager.add_error(
                state,
                "trading-decision",
                "DECISION_FAILED",
                str(e),
                "critical"
            )

        return state

    def _risk_check_node(self, state: TradingState) -> TradingState:
        """
        Risk Management node.

        Validates trade against risk limits. Has authority to approve/reject/modify.
        """
        start_time = datetime.utcnow()
        logger.info("Risk check node started", correlation_id=state["correlation_id"])

        try:
            # If NO_TRADE or errors, skip risk check
            if not state["should_trade"] or state["errors"]:
                logger.info("Skipping risk check (no trade or errors)", correlation_id=state["correlation_id"])
                state = self.state_manager.update_stage(
                    state,
                    "risk_evaluated",
                    (datetime.utcnow() - start_time).total_seconds() * 1000
                )
                return state

            # Call Risk Manager agent
            agent = self.agents.get("risk_manager")
            if not agent:
                raise ValueError("Risk Manager agent not registered")

            risk_approval = agent.execute(state)

            state["risk_approval"] = risk_approval

            # Check approval status
            approval_status = risk_approval.get("approval_status")
            if approval_status == "REJECTED":
                state["should_trade"] = False
                logger.warning(
                    "Trade rejected by Risk Manager",
                    reason=risk_approval.get("rejection_reason"),
                    correlation_id=state["correlation_id"]
                )

            state = self.state_manager.update_stage(
                state,
                "risk_evaluated",
                (datetime.utcnow() - start_time).total_seconds() * 1000
            )

            logger.info(
                "Risk check node completed",
                approval_status=approval_status,
                correlation_id=state["correlation_id"]
            )

        except Exception as e:
            logger.error("Risk check node failed", error=str(e), correlation_id=state["correlation_id"])
            state = self.state_manager.add_error(
                state,
                "risk-manager",
                "RISK_CHECK_FAILED",
                str(e),
                "critical"
            )

        return state

    def _execute_node(self, state: TradingState) -> TradingState:
        """
        Execution node.

        Places orders via Binance API (paper/live/hybrid mode).
        """
        start_time = datetime.utcnow()
        logger.info("Execution node started", correlation_id=state["correlation_id"])

        try:
            # Call Execution agent
            agent = self.agents.get("execution_agent")
            if not agent:
                raise ValueError("Execution agent not registered")

            execution_result = agent.execute(state)

            state["execution_result"] = execution_result

            state = self.state_manager.update_stage(
                state,
                "execution_complete",
                (datetime.utcnow() - start_time).total_seconds() * 1000
            )

            logger.info(
                "Execution node completed",
                status=execution_result.get("execution_status"),
                mode=execution_result.get("execution_mode"),
                correlation_id=state["correlation_id"]
            )

        except Exception as e:
            logger.error("Execution node failed", error=str(e), correlation_id=state["correlation_id"])
            state = self.state_manager.add_error(
                state,
                "execution-agent",
                "EXECUTION_FAILED",
                str(e),
                "error"
            )

        return state

    def _store_node(self, state: TradingState) -> TradingState:
        """
        Storage & Reporting node.

        Persists all pipeline data to database and generates reports.
        """
        start_time = datetime.utcnow()
        logger.info("Storage node started", correlation_id=state["correlation_id"])

        try:
            # Call Storage & Reporting agent
            agent = self.agents.get("storage_reporter")
            if not agent:
                raise ValueError("Storage & Reporting agent not registered")

            storage_confirmation = agent.execute(state)

            state["storage_confirmation"] = storage_confirmation

            state = self.state_manager.update_stage(
                state,
                "storage_complete",
                (datetime.utcnow() - start_time).total_seconds() * 1000
            )

            # Mark pipeline complete
            state = self.state_manager.mark_complete(state)

            logger.info("Storage node completed", correlation_id=state["correlation_id"])

        except Exception as e:
            logger.error("Storage node failed", error=str(e), correlation_id=state["correlation_id"])
            state = self.state_manager.add_error(
                state,
                "storage-reporter",
                "STORAGE_FAILED",
                str(e),
                "error"
            )

        return state

    # ========== Conditional Routing Logic ==========

    def _route_after_risk_check(self, state: TradingState) -> str:
        """
        Conditional routing after risk check.

        Returns:
            Next node: "approved", "modified", "rejected", or "error"
        """
        # Check for errors
        if state["errors"]:
            logger.info("Routing to store (errors)", correlation_id=state["correlation_id"])
            return "error"

        # Check if trading should proceed
        if not state["should_trade"]:
            logger.info("Routing to store (no trade)", correlation_id=state["correlation_id"])
            return "rejected"

        # Check risk approval status
        risk_approval = state.get("risk_approval", {})
        approval_status = risk_approval.get("approval_status", "REJECTED")

        if approval_status == "APPROVED":
            logger.info("Routing to execute (approved)", correlation_id=state["correlation_id"])
            return "approved"
        elif approval_status == "MODIFIED":
            logger.info("Routing to execute (modified)", correlation_id=state["correlation_id"])
            return "modified"
        else:
            logger.info("Routing to store (rejected)", correlation_id=state["correlation_id"])
            return "rejected"

    # ========== Main Execution Methods ==========

    def run_trading_cycle(
        self,
        symbol: str,
        mode: ExecutionMode = ExecutionMode.PAPER
    ) -> TradingState:
        """
        Execute a complete trading cycle.

        Args:
            symbol: Trading pair symbol (e.g., "BTCUSDT")
            mode: Execution mode (PAPER/LIVE/HYBRID)

        Returns:
            Final TradingState after pipeline completion
        """
        correlation_id = str(uuid.uuid4())

        logger.info(
            "Starting trading cycle",
            symbol=symbol,
            mode=mode,
            correlation_id=correlation_id
        )

        # Initialize state
        initial_state = self.state_manager.initialize_state(
            symbol=symbol,
            mode=mode,
            correlation_id=correlation_id
        )

        # Execute pipeline
        try:
            final_state = self.graph.invoke(initial_state)

            logger.info(
                "Trading cycle completed",
                correlation_id=correlation_id,
                stage=final_state["pipeline_stage"],
                total_time_ms=final_state["total_processing_time_ms"]
            )

            return final_state

        except Exception as e:
            logger.error(
                "Trading cycle failed",
                error=str(e),
                correlation_id=correlation_id
            )
            return self.state_manager.mark_failed(initial_state, str(e))

    def get_pipeline_status(self) -> Dict[str, Any]:
        """
        Get current pipeline status and statistics.

        Returns:
            Status dict with metrics
        """
        return {
            "registered_agents": list(self.agents.keys()),
            "model_usage": self.model_router.get_usage_stats(),
            "execution_mode": self.config.get("execution_mode", "PAPER")
        }
