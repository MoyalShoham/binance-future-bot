"""Prompt templates for LLM-enhanced trading agents."""

from .base import build_prompt
from .research_coordinator import RESEARCH_COORDINATOR_SYSTEM
from .trading_decision import TRADING_DECISION_SYSTEM
from .execution_agent import EXECUTION_AGENT_SYSTEM
from .storage_reporter import STORAGE_REPORTER_SYSTEM
from .emergency_controller import EMERGENCY_CONTROLLER_SYSTEM

__all__ = [
    "build_prompt",
    "RESEARCH_COORDINATOR_SYSTEM",
    "TRADING_DECISION_SYSTEM",
    "EXECUTION_AGENT_SYSTEM",
    "STORAGE_REPORTER_SYSTEM",
    "EMERGENCY_CONTROLLER_SYSTEM",
]
