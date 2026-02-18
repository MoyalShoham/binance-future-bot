"""
Correlation Monitor Agent Implementation

Monitors inter-asset correlations to detect over-concentration and suggest diversification.
Uses Haiku 4 for correlation analysis.
"""

from typing import Dict, Any, List
from datetime import datetime
import uuid
import structlog
import json

from .base_agent import BaseAgent

logger = structlog.get_logger()


class CorrelationMonitorAgent(BaseAgent):
    """
    Correlation Monitor Agent
    
    Responsibilities:
    - Track rolling correlations between major assets (BTC, ETH, etc.)
    - Detect portfolio concentration risk
    - Suggest uncorrelated trading pairs
    - Monitor tail correlation (crisis risk)
    - Flag when portfolio becomes over-clustered
    
    Model: Claude Haiku 4 (fast correlation analysis)
    Authority: Diversification hints only (no trading decisions)
    """
    
    def __init__(
        self,
        agent_id: str,
        config: Dict[str, Any],
        model_router=None
    ):
        """
        Initialize Correlation Monitor Agent.
        
        Args:
            agent_id: Agent identifier
            config: System configuration
            model_router: Optional ModelRouter for LLM calls
        """
        super().__init__(agent_id, config, model_router=model_router)
        
        # Correlation thresholds
        self.high_correlation_threshold = 0.7
        self.low_correlation_threshold = 0.5
        self.tail_correlation_threshold = 0.85
    
    async def analyze(self, correlation_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Analyze asset correlations and diversification.
        
        Args:
            correlation_data: Price time series and current positions
            
        Returns:
            Correlation analysis with diversification recommendations
        """
        request_id = str(uuid.uuid4())
        self.logger.info(
            "correlation_analysis_start",
            request_id=request_id
        )
        
        try:
            # Calculate correlation matrix if not provided
            if "correlation_matrix" not in correlation_data:
                correlation_data = self._compute_correlations(correlation_data)
            
            # If LLM enabled, use synthesis
            if self.model_router and self.llm_enabled:
                from prompts.correlation_monitor import CORRELATION_SYSTEM
                
                analysis = await self.model_router.call_model(
                    system_prompt=CORRELATION_SYSTEM,
                    user_message=self._format_correlation_prompt(correlation_data),
                    model="haiku-4",
                    response_format="json"
                )
                
                result = json.loads(analysis) if isinstance(analysis, str) else analysis
            else:
                # Rule-based analysis
                result = self._analyze_rule_based(correlation_data)
            
            # Validate output
            if self.validator.has_schema("correlation_analysis"):
                result = self.validator.validate(result, "correlation_analysis")
            
            self.logger.info(
                "correlation_analysis_complete",
                request_id=request_id,
                diversity_score=result.get("portfolio_diversity_score")
            )
            
            return {
                "status": "success",
                "analysis": result,
                "timestamp": datetime.utcnow().isoformat() + "Z"
            }
            
        except Exception as e:
            self.logger.error(
                "correlation_analysis_error",
                request_id=request_id,
                error=str(e)
            )
            return {
                "status": "error",
                "error": str(e),
                "timestamp": datetime.utcnow().isoformat() + "Z"
            }
    
    def _compute_correlations(self, correlation_data: Dict[str, Any]) -> Dict[str, Any]:
        """Compute correlation matrix from price time series."""
        timeseries = correlation_data.get("price_timeseries", {})
        
        # Simple correlation calculation
        symbols = list(timeseries.keys())
        correlation_matrix = {}
        
        for i, sym1 in enumerate(symbols):
            for sym2 in symbols[i+1:]:
                prices1 = timeseries.get(sym1, [])
                prices2 = timeseries.get(sym2, [])
                
                if len(prices1) > 1 and len(prices2) > 1:
                    # Simple correlation approximation
                    # In production, use numpy.corrcoef
                    correlation = 0.7  # Placeholder
                    correlation_matrix[f"{sym1}_{sym2}"] = correlation
        
        correlation_data["correlation_matrix"] = correlation_matrix
        return correlation_data
    
    def _format_correlation_prompt(self, correlation_data: Dict[str, Any]) -> str:
        """Format correlation data into analysis prompt."""
        return f"""
Analyze the following asset correlations and current positions:

Correlation Matrix:
{json.dumps(correlation_data.get("correlation_matrix", {}), indent=2)}

Current Positions:
{json.dumps(correlation_data.get("current_positions", []), indent=2)}

Assess portfolio diversification risk, recommend uncorrelated assets to add,
and flag any concentration risks.

Return as JSON with correlation_regime, portfolio_diversity_score, and recommendations.
"""
    
    def _analyze_rule_based(self, correlation_data: Dict[str, Any]) -> Dict[str, Any]:
        """Rule-based correlation analysis without LLM."""
        
        corr_matrix = correlation_data.get("correlation_matrix", {})
        positions = correlation_data.get("current_positions", [])
        
        # Calculate average correlation
        if corr_matrix:
            avg_corr = sum(corr_matrix.values()) / len(corr_matrix)
        else:
            avg_corr = 0.65
        
        # Classify regime
        if avg_corr < 0.5:
            regime = "low_correlation"
        elif avg_corr < 0.7:
            regime = "moderate_correlation"
        else:
            regime = "high_correlation"
        
        # Portfolio diversity score
        num_positions = len(positions)
        avg_size = 1.0 / max(num_positions, 1) if num_positions > 0 else 0
        concentration = max(pos.get("size", 0) for pos in positions) if positions else 0
        
        diversity_score = (1 - avg_corr) * (1 - min(concentration, 1.0))
        diversity_score = max(0, min(1, diversity_score))
        
        return {
            "correlation_matrix": corr_matrix,
            "regime": regime,
            "regime_confidence": 0.75,
            "portfolio_diversity_score": diversity_score,
            "current_position_correlation": {},
            "recommendations": [
                {
                    "action": "Monitor concentration",
                    "reason": f"Avg correlation {avg_corr:.2f}",
                    "expected_portfolio_diversity": diversity_score
                }
            ],
            "risk_flags": [
                "High correlation detected" if avg_corr > 0.7 else None
            ],
            "tail_correlation": min(avg_corr + 0.15, 1.0),  # Tail typically higher
            "crisis_flag": avg_corr > self.tail_correlation_threshold,
            "timestamp": datetime.utcnow().isoformat() + "Z"
        }

