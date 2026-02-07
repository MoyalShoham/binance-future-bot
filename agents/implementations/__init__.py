"""
Agent Implementations

Python implementations of all trading agents.
"""

from .base_agent import BaseAgent
from .research_coordinator import ResearchCoordinatorAgent
from .trading_decision import TradingDecisionAgent
from .risk_manager import RiskManagerAgent
from .execution_agent import ExecutionAgent, PositionTracker
from .storage_reporter import StorageReporterAgent, AuditTrail, PnLCalculator
from .emergency_controller import (
    EmergencyControllerAgent,
    KillSwitchManager,
    SystemHealthMonitor,
    AnomalyDetector,
    send_emergency_alert
)

__all__ = [
    # Base
    "BaseAgent",

    # Agents
    "ResearchCoordinatorAgent",
    "TradingDecisionAgent",
    "RiskManagerAgent",
    "ExecutionAgent",
    "StorageReporterAgent",
    "EmergencyControllerAgent",

    # Utilities
    "PositionTracker",
    "AuditTrail",
    "PnLCalculator",
    "KillSwitchManager",
    "SystemHealthMonitor",
    "AnomalyDetector",
    "send_emergency_alert"
]
