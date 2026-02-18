"""
Volatility Predictor Agent Implementation

Forecasts volatility regimes and spikes using Haiku 4.
Classifies market conditions (calm, elevated, high-vol) for position sizing.
"""

from typing import Dict, Any
from datetime import datetime
import uuid
import structlog
import json
import statistics

from .base_agent import BaseAgent

logger = structlog.get_logger()


class VolatilityPredictorAgent(BaseAgent):
    """
    Volatility Predictor Agent
    
    Responsibilities:
    - Compute realized volatility metrics (ATR, BB width)
    - Classify volatility regime (calm/elevated/high)
    - Forecast 1h and 4h volatility
    - Adjust position sizing multipliers
    - Alert on volatility spikes
    
    Model: Claude Haiku 4 (cost-effective forecasting)
    Authority: Volatility opinion only (multipliers for Risk Manager)
    """
    
    def __init__(
        self,
        agent_id: str,
        config: Dict[str, Any],
        model_router=None
    ):
        """
        Initialize Volatility Predictor Agent.
        
        Args:
            agent_id: Agent identifier
            config: System configuration
            model_router: Optional ModelRouter for LLM calls
        """
        super().__init__(agent_id, config, model_router=model_router)
        
        # Thresholds
        self.calm_threshold = 0.01
        self.elevated_threshold = 0.02
        self.high_threshold = 0.03
    
    async def analyze(self, volatility_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Analyze volatility and forecast future regimes.
        
        Args:
            volatility_data: Historical volatility, ATR, macro calendar
            
        Returns:
            Volatility forecast with regime and multipliers
        """
        request_id = str(uuid.uuid4())
        self.logger.info(
            "volatility_analysis_start",
            request_id=request_id
        )
        
        try:
            # If LLM enabled, use synthesis
            if self.model_router and self.llm_enabled:
                from prompts.volatility_predictor import VOLATILITY_SYSTEM
                
                analysis = await self.model_router.call_model(
                    system_prompt=VOLATILITY_SYSTEM,
                    user_message=self._format_volatility_prompt(volatility_data),
                    model="haiku-4",
                    response_format="json"
                )
                
                forecast = json.loads(analysis) if isinstance(analysis, str) else analysis
            else:
                # Rule-based analysis
                forecast = self._analyze_rule_based(volatility_data)
            
            # Validate output
            if self.validator.has_schema("volatility_forecast"):
                forecast = self.validator.validate(forecast, "volatility_forecast")
            
            self.logger.info(
                "volatility_analysis_complete",
                request_id=request_id,
                regime=forecast.get("current_regime"),
                forecast_1h=forecast.get("forecasts", {}).get("1h_ahead", {}).get("expected_vol")
            )
            
            return {
                "status": "success",
                "forecast": forecast,
                "timestamp": datetime.utcnow().isoformat() + "Z"
            }
            
        except Exception as e:
            self.logger.error(
                "volatility_analysis_error",
                request_id=request_id,
                error=str(e)
            )
            return {
                "status": "error",
                "error": str(e),
                "timestamp": datetime.utcnow().isoformat() + "Z"
            }
    
    def _format_volatility_prompt(self, volatility_data: Dict[str, Any]) -> str:
        """Format volatility data into forecast prompt."""
        return f"""
Analyze the following volatility metrics and forecast future regimes:

Current Metrics:
{json.dumps(volatility_data, indent=2)}

Classify current regime as: calm (<1%), elevated (1-2%), high (2-3%), or extreme (>3%)
Forecast 1-hour and 4-hour ahead volatility with confidence levels.
Consider macro calendar events and historical patterns.

Return as JSON with current_regime, realized_volatility, and forecasts.
"""
    
    def _analyze_rule_based(self, volatility_data: Dict[str, Any]) -> Dict[str, Any]:
        """Rule-based volatility analysis without LLM."""
        
        realized_vol = volatility_data.get("volatility_metrics", {}).get("realized_vol_20", 0.015)
        atr = volatility_data.get("volatility_metrics", {}).get("atr_14", 0)
        
        # Classify current regime
        if realized_vol < self.calm_threshold:
            regime = "calm"
            confidence = 0.85
        elif realized_vol < self.elevated_threshold:
            regime = "elevated"
            confidence = 0.80
        elif realized_vol < self.high_threshold:
            regime = "high"
            confidence = 0.75
        else:
            regime = "extreme"
            confidence = 0.70
        
        # Simple forecast (persistence + macro events)
        historical_spikes = volatility_data.get("historical_spikes", [])
        macro_calendar = volatility_data.get("macro_calendar", [])
        
        # If macro event imminent, increase forecast
        forecast_multiplier = 1.1 if len(macro_calendar) > 0 else 1.0
        
        forecast_1h = realized_vol * forecast_multiplier
        forecast_4h = realized_vol * forecast_multiplier * 1.1  # Slight increase for longer horizon
        
        return {
            "current_regime": regime,
            "regime_confidence": confidence,
            "realized_volatility": realized_vol,
            "forecasts": {
                "1h_ahead": {
                    "expected_vol": forecast_1h,
                    "confidence": 0.75,
                    "range_low": forecast_1h * 0.8,
                    "range_high": forecast_1h * 1.2
                },
                "4h_ahead": {
                    "expected_vol": forecast_4h,
                    "confidence": 0.65,
                    "range_low": forecast_4h * 0.7,
                    "range_high": forecast_4h * 1.4
                }
            },
            "risk_warnings": [
                f"Macro event in {len(macro_calendar)} hour(s)" if macro_calendar else None
            ],
            "positioning_guidance": self._get_positioning_guidance(regime),
            "timestamp": datetime.utcnow().isoformat() + "Z"
        }
    
    def _get_positioning_guidance(self, regime: str) -> str:
        """Get positioning guidance based on volatility regime."""
        guidance = {
            "calm": "Aggressive leverage, normal stops",
            "elevated": "Normal leverage, normal stops",
            "high": "Reduce leverage, widen stops",
            "extreme": "Minimal leverage, very wide stops"
        }
        return guidance.get(regime, "Neutral")

