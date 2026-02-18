"""
Recommendation Agent Implementation

Scans for hot coins, trending assets, and macro signals using Sonnet 4.5.
Provides high-conviction trade recommendations.
"""

from typing import Dict, Any, Optional, List
from datetime import datetime
import uuid
import structlog
import json

from .base_agent import BaseAgent
from infrastructure.binance_api import BinanceFuturesClient

logger = structlog.get_logger()


class RecommendationAgent(BaseAgent):
    """
    Recommendation Agent
    
    Responsibilities:
    - Scan Binance movers (24h gainers/losers)
    - Pull CoinGecko trending (social momentum)
    - Monitor macro events (Fed, geopolitical)
    - Scan on-chain metrics (whale activity, inflows)
    - Synthesize into top 3-5 recommendations
    
    Model: Claude Sonnet 4.5 (deep analysis)
    Authority: Recommend only (no trading decisions)
    """
    
    def __init__(
        self,
        agent_id: str,
        config: Dict[str, Any],
        binance_client: BinanceFuturesClient,
        model_router=None
    ):
        """
        Initialize Recommendation Agent.
        
        Args:
            agent_id: Agent identifier
            config: System configuration
            binance_client: Binance API client
            model_router: Optional ModelRouter for LLM calls
        """
        super().__init__(agent_id, config, model_router=model_router)
        
        self.binance_client = binance_client
        self.config = config
        
        # Recommendation parameters
        self.min_conviction = config.get("agents", {}).get("recommendation", {}).get("min_conviction", 75)
        self.max_top_n = config.get("agents", {}).get("recommendation", {}).get("max_top_n", 5)
        self.altseason_boost = config.get("agents", {}).get("recommendation", {}).get("altseason_boost", 0.15)
    
    async def analyze(self, market_state: Dict[str, Any]) -> Dict[str, Any]:
        """
        Analyze market for trade recommendations.
        
        Args:
            market_state: Current market state (movers, macro, on-chain)
            
        Returns:
            Recommendation analysis with top symbols and conviction scores
        """
        agent_request_id = str(uuid.uuid4())
        self.logger.info(
            "recommendation_analysis_start",
            request_id=agent_request_id,
            market_state_keys=list(market_state.keys())
        )
        
        try:
            # If LLM is enabled, use model router for synthesis
            if self.model_router and self.llm_enabled:
                from prompts.recommendation_agent import RECOMMENDATION_SYSTEM
                
                analysis = await self.model_router.call_model(
                    system_prompt=RECOMMENDATION_SYSTEM,
                    user_message=self._format_recommendation_prompt(market_state),
                    model="sonnet-4-5",  # Use Sonnet for deep analysis
                    response_format="json"
                )
                
                recommendation = json.loads(analysis) if isinstance(analysis, str) else analysis
            else:
                # Rule-based analysis only
                recommendation = self._analyze_rule_based(market_state)
            
            # Validate output
            if self.validator.has_schema("recommendation_output"):
                recommendation = self.validator.validate(
                    recommendation,
                    "recommendation_output"
                )
            
            self.logger.info(
                "recommendation_analysis_complete",
                request_id=agent_request_id,
                top_n=len(recommendation.get("recommendations", []))
            )
            
            return {
                "status": "success",
                "recommendations": recommendation.get("recommendations", []),
                "market_regime": recommendation.get("market_regime"),
                "timestamp": datetime.utcnow().isoformat() + "Z"
            }
            
        except Exception as e:
            self.logger.error(
                "recommendation_analysis_error",
                request_id=agent_request_id,
                error=str(e)
            )
            return {
                "status": "error",
                "error": str(e),
                "timestamp": datetime.utcnow().isoformat() + "Z"
            }
    
    def _format_recommendation_prompt(self, market_state: Dict[str, Any]) -> str:
        """Format market state into recommendation prompt."""
        return f"""
Analyze the following market data and provide top 3-5 trade recommendations:

Market State:
{json.dumps(market_state, indent=2)}

Provide recommendations with:
- Symbol (e.g., ETHUSDT)
- Conviction (0-100)
- Reasoning
- Time window (how long valid)
- Risk level
- Key factors driving recommendation

Return as JSON array.
"""
    
    def _analyze_rule_based(self, market_state: Dict[str, Any]) -> Dict[str, Any]:
        """Rule-based recommendation analysis (no LLM)."""
        recommendations = []
        
        # Extract movers
        movers = market_state.get("top_movers", {})
        gainers = movers.get("gainers_24h", [])
        
        for gainer in gainers[:self.max_top_n]:
            symbol = gainer.get("symbol")
            change = gainer.get("change", 0)
            volume = gainer.get("volume", 0)
            
            # Simple conviction calculation
            conviction = min(100, 50 + (change * 100) + (min(volume, 2e9) / 2e9) * 20)
            
            recommendations.append({
                "symbol": symbol,
                "conviction": conviction,
                "reasoning": f"24h gain {change*100:.1f}% + strong volume",
                "time_window": "2 hours",
                "risk_level": "medium"
            })
        
        # Altseason boost
        altseason_score = market_state.get("market_state", {}).get("altseason_score", 0)
        if altseason_score > 0.6:
            for rec in recommendations:
                rec["conviction"] = min(100, rec["conviction"] + self.altseason_boost * 100)
        
        return {
            "recommendations": recommendations[:self.max_top_n],
            "market_regime": market_state.get("market_state", {}).get("overall_sentiment", "neutral")
        }

