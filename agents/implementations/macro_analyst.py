"""
Macro Analyst Agent Implementation

Synthesizes macroeconomic data for regime classification and conviction adjustments.
Uses Sonnet 4.5 for deep macro reasoning.
"""

from typing import Dict, Any
from datetime import datetime
import uuid
import structlog
import json

from .base_agent import BaseAgent

logger = structlog.get_logger()


class MacroAnalystAgent(BaseAgent):
    """
    Macro Analyst Agent
    
    Responsibilities:
    - Monitor Federal Reserve policy and rate decisions
    - Track Treasury yields and real rates
    - Monitor geopolitical events
    - Track regulatory announcements
    - Classify macro regime (risk-on/risk-off, easing/hiking)
    - Adjust trading conviction based on macro tailwinds/headwinds
    
    Model: Claude Sonnet 4.5 (deep macro reasoning)
    Authority: Conviction adjustments only (±% to base signals)
    """
    
    def __init__(
        self,
        agent_id: str,
        config: Dict[str, Any],
        model_router=None
    ):
        """
        Initialize Macro Analyst Agent.
        
        Args:
            agent_id: Agent identifier
            config: System configuration
            model_router: Optional ModelRouter for LLM calls
        """
        super().__init__(agent_id, config, model_router=model_router)
        
        self.config = config
        self.max_conviction_adjustment = config.get("agents", {}).get("macro_analyst", {}).get("max_conviction_adjustment", 0.25)
    
    async def analyze(self, macro_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Analyze macroeconomic conditions and adjust trading conviction.
        
        Args:
            macro_data: Fed policy, yields, geopolitical, regulatory data
            
        Returns:
            Macro regime, directional bias, and conviction adjustments
        """
        request_id = str(uuid.uuid4())
        self.logger.info(
            "macro_analysis_start",
            request_id=request_id,
            fed_rate=macro_data.get("fed_policy", {}).get("current_rate")
        )
        
        try:
            # If LLM enabled, use synthesis
            if self.model_router and self.llm_enabled:
                from prompts.macro_analyst import MACRO_ANALYST_SYSTEM
                
                analysis = await self.model_router.call_model(
                    system_prompt=MACRO_ANALYST_SYSTEM,
                    user_message=self._format_macro_prompt(macro_data),
                    model="sonnet-4-5",  # Use Sonnet for deep reasoning
                    response_format="json"
                )
                
                result = json.loads(analysis) if isinstance(analysis, str) else analysis
            else:
                # Rule-based analysis
                result = self._analyze_rule_based(macro_data)
            
            # Validate output
            if self.validator.has_schema("macro_analysis"):
                result = self.validator.validate(result, "macro_analysis")
            
            self.logger.info(
                "macro_analysis_complete",
                request_id=request_id,
                regime=result.get("macro_regime"),
                conviction_adjustment=result.get("conviction_adjustment")
            )
            
            return {
                "status": "success",
                "analysis": result,
                "timestamp": datetime.utcnow().isoformat() + "Z"
            }
            
        except Exception as e:
            self.logger.error(
                "macro_analysis_error",
                request_id=request_id,
                error=str(e)
            )
            return {
                "status": "error",
                "error": str(e),
                "timestamp": datetime.utcnow().isoformat() + "Z"
            }
    
    def _format_macro_prompt(self, macro_data: Dict[str, Any]) -> str:
        """Format macro data into analysis prompt."""
        return f"""
Analyze the following macroeconomic data and classify the regime:

Federal Reserve:
{json.dumps(macro_data.get("fed_policy", {}), indent=2)}

Treasury Yields:
{json.dumps(macro_data.get("treasury_yields", {}), indent=2)}

Currency Metrics:
{json.dumps(macro_data.get("currency_metrics", {}), indent=2)}

Geopolitical Events:
{json.dumps(macro_data.get("geopolitical", []), indent=2)}

Regulatory News:
{json.dumps(macro_data.get("regulatory", []), indent=2)}

Macro Indicators:
{json.dumps(macro_data.get("macro_indicators", {}), indent=2)}

Classify the macro regime, identify tailwinds/headwinds, and provide a conviction adjustment for crypto trading (-25% to +25%).

Return as JSON with macro_regime, conviction_adjustment, and key_drivers.
"""
    
    def _analyze_rule_based(self, macro_data: Dict[str, Any]) -> Dict[str, Any]:
        """Rule-based macro analysis without LLM."""
        
        fed = macro_data.get("fed_policy", {})
        yields = macro_data.get("treasury_yields", {})
        fx = macro_data.get("currency_metrics", {})
        
        # Base conviction = 0 (neutral)
        conviction_adjustment = 0
        
        # Fed analysis
        fed_decision = fed.get("recent_decision", "hold")
        if fed_decision == "easing":
            conviction_adjustment += 0.10
            regime = "easing"
        elif fed_decision == "hiking":
            conviction_adjustment -= 0.10
            regime = "hiking"
        else:
            regime = "hold"
        
        # Yield analysis
        yield_10y = yields.get("yield_10y", 0.04)
        if yield_10y < 0.03:
            conviction_adjustment += 0.08
        elif yield_10y > 0.05:
            conviction_adjustment -= 0.08
        
        # Dollar analysis
        dxy_trend = fx.get("dxy_trend", "stable")
        if dxy_trend == "weakening":
            conviction_adjustment += 0.06
        elif dxy_trend == "strengthening":
            conviction_adjustment -= 0.06
        
        # Clamp to max adjustment
        conviction_adjustment = max(-self.max_conviction_adjustment, min(self.max_conviction_adjustment, conviction_adjustment))
        
        return {
            "macro_regime": f"risk_on_{regime}" if conviction_adjustment > 0 else f"risk_off_{regime}",
            "regime_confidence": 0.75,
            "crypto_directional_bias": "bullish" if conviction_adjustment > 0.05 else "bearish" if conviction_adjustment < -0.05 else "neutral",
            "conviction_adjustment": conviction_adjustment,
            "conviction_reasoning": f"Fed {fed_decision}, Yields {yield_10y:.1%}, DXY {dxy_trend}",
            "key_drivers": [
                {
                    "factor": f"Fed {fed_decision}",
                    "impact": f"{int(conviction_adjustment * 100 * 0.5)}%"
                }
            ],
            "risk_factors": [],
            "regime_history": {},
            "comparable_periods": [],
            "trading_implications": {
                "position_sizing": 1.0 + min(conviction_adjustment * 0.2, 0.2),
                "risk_tolerance": "elevated" if conviction_adjustment > 0 else "reduced"
            },
            "next_catalyst": {
                "date": (datetime.utcnow().replace(day=15)).isoformat() + "Z",
                "event": "FOMC meeting",
                "expected_impact": "high",
                "directional_bias": "bullish" if fed.get("forward_guidance") == "easing_coming" else "bearish"
            },
            "timestamp": datetime.utcnow().isoformat() + "Z"
        }

