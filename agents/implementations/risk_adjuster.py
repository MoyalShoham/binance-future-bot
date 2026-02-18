"""
Risk Adjuster Agent Implementation

Dynamically adjusts risk parameters based on market conditions and system health.
Uses Haiku 4.5 for adaptive risk management.
"""

from typing import Dict, Any
from datetime import datetime
import uuid
import structlog
import json

from .base_agent import BaseAgent

logger = structlog.get_logger()


class RiskAdjusterAgent(BaseAgent):
    """
    Risk Adjuster Agent
    
    Responsibilities:
    - Adjust stop loss and take profit based on volatility
    - Scale position sizes dynamically
    - Apply Kelly sizing from win rate
    - Monitor drawdown and apply brakes
    - Track consecutive losses and cooldowns
    - Penalize low-confidence strategies
    
    Model: Claude Haiku 4.5 (fast adaptive risk management)
    Authority: Parameter adjustment multipliers only (no hard hard blocks)
    """
    
    def __init__(
        self,
        agent_id: str,
        config: Dict[str, Any],
        model_router=None
    ):
        """
        Initialize Risk Adjuster Agent.
        
        Args:
            agent_id: Agent identifier
            config: System configuration
            model_router: Optional ModelRouter for LLM calls
        """
        super().__init__(agent_id, config, model_router=model_router)
        
        self.config = config
        self.max_daily_drawdown = config.get("risk", {}).get("max_daily_drawdown", -0.06)
        self.max_leverage = config.get("trading", {}).get("default_leverage", 5)
    
    async def adjust_parameters(self, system_state: Dict[str, Any]) -> Dict[str, Any]:
        """
        Calculate adjusted risk parameters based on system state.
        
        Args:
            system_state: Market conditions, positions, performance metrics
            
        Returns:
            Adjusted parameters (multipliers for SL, TP, position size)
        """
        request_id = str(uuid.uuid4())
        self.logger.info(
            "risk_adjustment_start",
            request_id=request_id,
            daily_pnl=system_state.get("system_state", {}).get("daily_pnl")
        )
        
        try:
            # If LLM enabled, use synthesis
            if self.model_router and self.llm_enabled:
                from prompts.risk_adjuster import RISK_ADJUSTER_SYSTEM
                
                adjustment = await self.model_router.call_model(
                    system_prompt=RISK_ADJUSTER_SYSTEM,
                    user_message=self._format_adjustment_prompt(system_state),
                    model="haiku-4-5",
                    response_format="json"
                )
                
                result = json.loads(adjustment) if isinstance(adjustment, str) else adjustment
            else:
                # Rule-based adjustment
                result = self._adjust_rule_based(system_state)
            
            # Validate output
            if self.validator.has_schema("risk_adjustment"):
                result = self.validator.validate(result, "risk_adjustment")
            
            self.logger.info(
                "risk_adjustment_complete",
                request_id=request_id,
                position_size_multiplier=result.get("adjusted_parameters", {}).get("position_size_multiplier")
            )
            
            return {
                "status": "success",
                "adjustment": result,
                "timestamp": datetime.utcnow().isoformat() + "Z"
            }
            
        except Exception as e:
            self.logger.error(
                "risk_adjustment_error",
                request_id=request_id,
                error=str(e)
            )
            return {
                "status": "error",
                "error": str(e),
                "timestamp": datetime.utcnow().isoformat() + "Z"
            }
    
    def _format_adjustment_prompt(self, system_state: Dict[str, Any]) -> str:
        """Format system state into adjustment prompt."""
        return f"""
Adjust risk parameters based on current system state:

Market Conditions:
{json.dumps(system_state.get("market_conditions", {}), indent=2)}

System State:
{json.dumps(system_state.get("system_state", {}), indent=2)}

Position State:
{json.dumps(system_state.get("position_state", {}), indent=2)}

Strategy Performance:
{json.dumps(system_state.get("strategy_performance", {}), indent=2)}

Provide adjusted multipliers (0.3-1.5 range) for stop_loss, take_profit, and position_size.
Apply Kelly sizing. Flag any concerning conditions.

Return as JSON with adjusted_parameters and reasoning.
"""
    
    def _adjust_rule_based(self, system_state: Dict[str, Any]) -> Dict[str, Any]:
        """Rule-based risk adjustment without LLM."""
        
        # Extract state
        market = system_state.get("market_conditions", {})
        system = system_state.get("system_state", {})
        positions = system_state.get("position_state", {})
        strategy = system_state.get("strategy_performance", {})
        
        # Volatility multiplier
        vol_multiplier = market.get("volatility_multiplier", 1.0)
        
        # Drawdown adjustment
        daily_pnl = system.get("daily_pnl_percent", 0)
        if daily_pnl < -0.02:
            drawdown_multiplier = 0.85
        elif daily_pnl < -0.01:
            drawdown_multiplier = 0.90
        else:
            drawdown_multiplier = 1.0
        
        # Consecutive loss adjustment
        consecutive_losses = system.get("consecutive_losses", 0)
        if consecutive_losses >= 2:
            loss_multiplier = 0.80
        else:
            loss_multiplier = 1.0
        
        # Kelly sizing
        win_rate = strategy.get("win_rate", 0.5)
        rr_ratio = strategy.get("avg_rr_ratio", 2.0)
        
        if win_rate > 0:
            kelly = win_rate - (1 - win_rate) / max(rr_ratio, 1)
            kelly_sizing = max(0.3, min(1.5, kelly * 2))  # Half Kelly for safety
        else:
            kelly_sizing = 1.0
        
        # Combined multipliers
        sl_multiplier = vol_multiplier * drawdown_multiplier
        tp_multiplier = 1.0 / vol_multiplier if vol_multiplier > 0 else 1.0  # Tighter TP in high vol
        position_size_multiplier = drawdown_multiplier * loss_multiplier * kelly_sizing
        
        return {
            "adjusted_parameters": {
                "stop_loss_multiplier": round(sl_multiplier, 2),
                "take_profit_multiplier": round(tp_multiplier, 2),
                "position_size_multiplier": round(position_size_multiplier, 2),
                "max_leverage_allowed": max(2, self.max_leverage * drawdown_multiplier)
            },
            "reasoning": [
                f"Volatility multiplier: {vol_multiplier:.2f}x" if vol_multiplier != 1.0 else None,
                f"Drawdown adjustment: {drawdown_multiplier:.2f}x" if drawdown_multiplier != 1.0 else None,
                f"Consecutive losses: {consecutive_losses}" if consecutive_losses > 0 else None
            ],
            "kelly_recommendation": {
                "edge_percent": kelly_sizing * 100,
                "recommended_size": kelly_sizing,
                "current_size": strategy.get("kelly_percent", kelly_sizing),
                "action": "neutral"
            },
            "cooldown_status": {
                "active": consecutive_losses >= 3,
                "reason": f"{consecutive_losses} consecutive losses" if consecutive_losses >= 3 else None
            },
            "warning_flags": [
                "Daily drawdown approaching limit" if daily_pnl < -0.04 else None,
                "High volatility - reduce size" if vol_multiplier > 1.3 else None
            ],
            "recommendations": [],
            "timestamp": datetime.utcnow().isoformat() + "Z"
        }

