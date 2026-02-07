"""
State Management for Trading Pipeline

Manages state transitions through the agent execution pipeline using LangGraph.
"""

from typing import TypedDict, Optional, Dict, List, Any
from datetime import datetime
from enum import Enum


class ExecutionMode(str, Enum):
    """Trading execution mode."""
    PAPER = "PAPER"
    LIVE = "LIVE"
    HYBRID = "HYBRID"


class TradingState(TypedDict, total=False):
    """
    State object passed through the agent pipeline.

    This state is updated by each agent and passed to the next.
    LangGraph uses this to track progress and enable conditional routing.
    """
    # Pipeline metadata
    correlation_id: str
    timestamp: datetime
    mode: ExecutionMode
    symbol: str

    # Agent outputs (populated as pipeline progresses)
    research_summary: Optional[Dict[str, Any]]
    trading_decision: Optional[Dict[str, Any]]
    risk_approval: Optional[Dict[str, Any]]
    execution_result: Optional[Dict[str, Any]]
    storage_confirmation: Optional[Dict[str, Any]]

    # Pipeline control
    errors: List[Dict[str, Any]]
    warnings: List[str]
    should_trade: bool
    pipeline_stage: str

    # Performance tracking
    stage_timings: Dict[str, float]
    total_processing_time_ms: float


class StateManager:
    """
    Manages state transitions and validation.

    Ensures state integrity across agent boundaries.
    """

    def __init__(self):
        self.valid_stages = [
            "initialized",
            "research_complete",
            "decision_made",
            "risk_evaluated",
            "execution_complete",
            "storage_complete",
            "pipeline_complete",
            "pipeline_failed"
        ]

    def initialize_state(
        self,
        symbol: str,
        mode: ExecutionMode,
        correlation_id: str
    ) -> TradingState:
        """
        Initialize a new trading state.

        Args:
            symbol: Trading pair symbol
            mode: Execution mode (PAPER/LIVE/HYBRID)
            correlation_id: UUID for tracking this pipeline execution

        Returns:
            Fresh TradingState object
        """
        return TradingState(
            correlation_id=correlation_id,
            timestamp=datetime.utcnow(),
            mode=mode,
            symbol=symbol,
            research_summary=None,
            trading_decision=None,
            risk_approval=None,
            execution_result=None,
            storage_confirmation=None,
            errors=[],
            warnings=[],
            should_trade=True,  # Default to True, agents can set to False
            pipeline_stage="initialized",
            stage_timings={},
            total_processing_time_ms=0.0
        )

    def update_stage(
        self,
        state: TradingState,
        new_stage: str,
        processing_time_ms: float = 0.0
    ) -> TradingState:
        """
        Update the pipeline stage and track timing.

        Args:
            state: Current state
            new_stage: New pipeline stage
            processing_time_ms: Time taken for this stage

        Returns:
            Updated state
        """
        if new_stage not in self.valid_stages:
            raise ValueError(f"Invalid stage: {new_stage}")

        state["pipeline_stage"] = new_stage
        state["stage_timings"][new_stage] = processing_time_ms
        state["total_processing_time_ms"] += processing_time_ms

        return state

    def add_error(
        self,
        state: TradingState,
        agent_id: str,
        error_code: str,
        error_message: str,
        severity: str = "error"
    ) -> TradingState:
        """
        Add an error to the state.

        Args:
            state: Current state
            agent_id: Agent that encountered the error
            error_code: Error code identifier
            error_message: Error description
            severity: Error severity (warning/error/critical)

        Returns:
            Updated state
        """
        error = {
            "agent_id": agent_id,
            "code": error_code,
            "message": error_message,
            "severity": severity,
            "timestamp": datetime.utcnow().isoformat()
        }

        state["errors"].append(error)

        # Set pipeline to failed if critical error
        if severity == "critical":
            state["should_trade"] = False
            state["pipeline_stage"] = "pipeline_failed"

        return state

    def add_warning(
        self,
        state: TradingState,
        warning: str
    ) -> TradingState:
        """Add a warning to the state."""
        state["warnings"].append(warning)
        return state

    def should_continue_pipeline(self, state: TradingState) -> bool:
        """
        Check if the pipeline should continue execution.

        Returns:
            True if pipeline can continue, False if should stop
        """
        # Stop if critical errors
        if any(e["severity"] == "critical" for e in state["errors"]):
            return False

        # Stop if should_trade flag is False
        if not state["should_trade"]:
            return False

        # Stop if already in terminal state
        if state["pipeline_stage"] in ["pipeline_complete", "pipeline_failed"]:
            return False

        return True

    def get_stage_summary(self, state: TradingState) -> Dict[str, Any]:
        """
        Get a summary of the current pipeline state.

        Returns:
            Summary dict with key metrics
        """
        return {
            "correlation_id": state["correlation_id"],
            "symbol": state["symbol"],
            "mode": state["mode"],
            "current_stage": state["pipeline_stage"],
            "should_trade": state["should_trade"],
            "error_count": len(state["errors"]),
            "warning_count": len(state["warnings"]),
            "total_time_ms": state["total_processing_time_ms"],
            "stages_completed": list(state["stage_timings"].keys())
        }

    def mark_complete(self, state: TradingState) -> TradingState:
        """Mark the pipeline as successfully completed."""
        state["pipeline_stage"] = "pipeline_complete"
        return state

    def mark_failed(
        self,
        state: TradingState,
        reason: str
    ) -> TradingState:
        """
        Mark the pipeline as failed.

        Args:
            state: Current state
            reason: Failure reason

        Returns:
            Updated state
        """
        state["pipeline_stage"] = "pipeline_failed"
        state["should_trade"] = False
        self.add_error(state, "state_manager", "PIPELINE_FAILED", reason, "critical")
        return state
