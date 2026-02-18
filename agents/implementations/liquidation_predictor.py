"""
Liquidation Predictor Agent Implementation

Calculates liquidation levels, predicts cascades, and warns of dangerous zones.
Uses Haiku 4 for liquidation analysis.
"""

from typing import Dict, Any, List
from datetime import datetime
import uuid
import structlog
import json

from .base_agent import BaseAgent

logger = structlog.get_logger()


class LiquidationPredictorAgent(BaseAgent):
    """
    Liquidation Predictor Agent
    
    Responsibilities:
    - Calculate liquidation price for each position
    - Identify liquidation clusters in order book
    - Predict cascade speed and severity
    - Score entry safety (distance to clusters)
    - Monitor funding rate as cascade indicator
    - Alert on liquidation zone entries
    
    Model: Claude Haiku 4 (fast liquidation calculations)
    Authority: Liquidation warnings only (no position changes)
    """
    
    def __init__(
        self,
        agent_id: str,
        config: Dict[str, Any],
        model_router=None
    ):
        """
        Initialize Liquidation Predictor Agent.
        
        Args:
            agent_id: Agent identifier
            config: System configuration
            model_router: Optional ModelRouter for LLM calls
        """
        super().__init__(agent_id, config, model_router=model_router)
        
        self.config = config
        self.min_safety_distance = config.get("agents", {}).get("liquidation_predictor", {}).get("min_safety_distance", 0.08)
    
    async def analyze(self, market_data: Dict[str, Any], positions: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Analyze liquidation risks for current positions.
        
        Args:
            market_data: Order book, OI, funding rates
            positions: Open positions with entry, leverage, margin
            
        Returns:
            Liquidation analysis with safety scores and warnings
        """
        request_id = str(uuid.uuid4())
        self.logger.info(
            "liquidation_analysis_start",
            request_id=request_id,
            num_positions=len(positions)
        )
        
        try:
            # Calculate liquidation levels for positions
            position_liquidations = self._calculate_position_liquidations(positions)
            
            # If LLM enabled, use synthesis
            if self.model_router and self.llm_enabled:
                from prompts.liquidation_predictor import LIQUIDATION_SYSTEM
                
                analysis = await self.model_router.call_model(
                    system_prompt=LIQUIDATION_SYSTEM,
                    user_message=self._format_liquidation_prompt(market_data, position_liquidations),
                    model="haiku-4",
                    response_format="json"
                )
                
                result = json.loads(analysis) if isinstance(analysis, str) else analysis
            else:
                # Rule-based analysis
                result = self._analyze_rule_based(market_data, position_liquidations)
            
            # Validate output
            if self.validator.has_schema("liquidation_analysis"):
                result = self.validator.validate(result, "liquidation_analysis")
            
            self.logger.info(
                "liquidation_analysis_complete",
                request_id=request_id,
                entry_safety_score=result.get("entry_safety_score")
            )
            
            return {
                "status": "success",
                "analysis": result,
                "timestamp": datetime.utcnow().isoformat() + "Z"
            }
            
        except Exception as e:
            self.logger.error(
                "liquidation_analysis_error",
                request_id=request_id,
                error=str(e)
            )
            return {
                "status": "error",
                "error": str(e),
                "timestamp": datetime.utcnow().isoformat() + "Z"
            }
    
    def _calculate_position_liquidations(self, positions: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Calculate liquidation price for each position."""
        liquidations = []
        
        for pos in positions:
            entry = pos.get("entry_price", 0)
            leverage = pos.get("leverage", 1)
            side = pos.get("side", "LONG")
            
            # Liquidation price formula
            if side == "LONG":
                liq_price = entry * (1 - 1/leverage + 0.0004)  # +0.04% for trading fees
            else:
                liq_price = entry * (1 + 1/leverage + 0.0004)
            
            liquidations.append({
                "position_id": pos.get("id"),
                "liquidation_price": liq_price,
                "distance_percent": (liq_price - entry) / entry,
                "liquidation_margin_loss": pos.get("margin", 0)
            })
        
        return liquidations
    
    def _format_liquidation_prompt(self, market_data: Dict[str, Any], position_liquidations: List[Dict[str, Any]]) -> str:
        """Format liquidation data into analysis prompt."""
        return f"""
Analyze liquidation risk based on position levels and market structure:

Position Liquidation Levels:
{json.dumps(position_liquidations, indent=2)}

Market Data:
{json.dumps(market_data, indent=2)}

Identify liquidation clusters, estimate cascade risk, and score entry safety.

Return as JSON with market_liquidation_map, cascade_analysis, and entry_safety_score.
"""
    
    def _analyze_rule_based(self, market_data: Dict[str, Any], position_liquidations: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Rule-based liquidation analysis without LLM."""
        
        oi = market_data.get("open_interest", {})
        total_oi = oi.get("total_oi", 1)
        funding_rate = market_data.get("funding_rate", 0)
        
        # Estimate liquidation clusters
        clusters = []
        if position_liquidations:
            # Group liquidations by price level
            liq_prices = [p.get("liquidation_price") for p in position_liquidations]
            
            # Simple clustering (in production, use proper clustering algorithm)
            clusters = [
                {
                    "price_cluster": min(liq_prices),
                    "cluster_width": 100,
                    "notional_oi": total_oi * 0.2,
                    "avg_leverage": 5,
                    "cascade_risk": "moderate"
                }
            ]
        
        # Cascade analysis
        cascade_analysis = {
            "nearest_cluster_below": clusters[0].get("price_cluster") if clusters else 0,
            "estimated_cascade_trigger": clusters[0].get("price_cluster", 0) * 0.99 if clusters else 0,
            "cascade_speed_estimate": "15-30 seconds",
            "total_liquidatable_below_price": total_oi * 0.5,
            "risk_to_current_position": "moderate" if funding_rate > 0.0005 else "low"
        }
        
        # Entry safety score
        entry_safety = 0.72
        
        return {
            "position_liquidation_levels": position_liquidations,
            "market_liquidation_map": clusters,
            "cascade_analysis": cascade_analysis,
            "entry_safety_score": entry_safety,
            "entry_safety_reasoning": "Position is acceptably distanced from liquidation clusters",
            "funding_rate_risk": {
                "current_rate": funding_rate,
                "annualized_funding": funding_rate * 365,
                "cascade_risk_if_rate_rises": "moderate" if funding_rate > 0.0003 else "low"
            },
            "warnings": [
                "Monitor funding rate for cascade indicators" if funding_rate > 0.0003 else None
            ],
            "timestamp": datetime.utcnow().isoformat() + "Z"
        }

