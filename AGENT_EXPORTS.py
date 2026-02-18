"""
New agent exports for the agent army.

Add this to agents/implementations/__init__.py to register all agents.
"""

from .recommendation_agent import RecommendationAgent
from .sentiment_analyzer import SentimentAnalyzerAgent
from .volatility_predictor import VolatilityPredictorAgent
from .correlation_monitor import CorrelationMonitorAgent
from .entry_optimizer import EntryOptimizerAgent
from .liquidation_predictor import LiquidationPredictorAgent
from .macro_analyst import MacroAnalystAgent
from .risk_adjuster import RiskAdjusterAgent

__all__ = [
    "RecommendationAgent",
    "SentimentAnalyzerAgent",
    "VolatilityPredictorAgent",
    "CorrelationMonitorAgent",
    "EntryOptimizerAgent",
    "LiquidationPredictorAgent",
    "MacroAnalystAgent",
    "RiskAdjusterAgent",
]

