"""
Entry Optimizer Agent Implementation

Fine-tunes entry prices within approved trades to maximize risk-reward.
Uses Haiku 4 for micro-timeframe analysis.
"""

from typing import Dict, Any, Optional
from datetime import datetime
import uuid
import structlog
import json

from .base_agent import BaseAgent

logger = structlog.get_logger()


class EntryOptimizerAgent(BaseAgent):
    """
    Entry Optimizer Agent
    
    Responsibilities:
    - Analyze order book depth for optimal entry
    - Identify support/resistance on micro timeframes
    - Calculate fill probability at different price levels
    - Estimate slippage impact
    - Suggest timing windows for entries
    - Monitor for icebergs and spoofing
    
    Model: Claude Haiku 4 (fast order book analysis)
    Authority: Entry price hints only (no size changes)
    """
    
    def __init__(
        self,
        agent_id: str,
        config: Dict[str, Any],
        model_router=None
    ):
        """
        Initialize Entry Optimizer Agent.
        
        Args:
            agent_id: Agent identifier
            config: System configuration
            model_router: Optional ModelRouter for LLM calls
        """
        super().__init__(agent_id, config, model_router=model_router)
        
        # Entry parameters
        self.max_entry_deviation = config.get("agents", {}).get("entry_optimizer", {}).get("max_entry_deviation", 0.005)
        self.min_fill_probability = config.get("agents", {}).get("entry_optimizer", {}).get("min_fill_probability", 0.6)
    
    async def optimize(self, trade_approval: Dict[str, Any], market_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Optimize entry price for approved trade.
        
        Args:
            trade_approval: Trading Decision approval (symbol, direction, entry, SL, TP)
            market_data: Real-time order book and candles
            
        Returns:
            Optimized entry with probability and timing
        """
        request_id = str(uuid.uuid4())
        self.logger.info(
            "entry_optimization_start",
            request_id=request_id,
            symbol=trade_approval.get("symbol"),
            direction=trade_approval.get("direction")
        )
        
        try:
            # If LLM enabled, use synthesis
            if self.model_router and self.llm_enabled:
                from prompts.entry_optimizer import ENTRY_OPTIMIZER_SYSTEM
                
                optimization = await self.model_router.call_model(
                    system_prompt=ENTRY_OPTIMIZER_SYSTEM,
                    user_message=self._format_optimization_prompt(trade_approval, market_data),
                    model="haiku-4",
                    response_format="json"
                )
                
                result = json.loads(optimization) if isinstance(optimization, str) else optimization
            else:
                # Rule-based optimization
                result = self._optimize_rule_based(trade_approval, market_data)
            
            # Validate output
            if self.validator.has_schema("entry_optimization"):
                result = self.validator.validate(result, "entry_optimization")
            
            self.logger.info(
                "entry_optimization_complete",
                request_id=request_id,
                optimal_entry=result.get("optimal_entry"),
                fill_probability=result.get("fill_probability")
            )
            
            return {
                "status": "success",
                "optimization": result,
                "timestamp": datetime.utcnow().isoformat() + "Z"
            }
            
        except Exception as e:
            self.logger.error(
                "entry_optimization_error",
                request_id=request_id,
                error=str(e)
            )
            return {
                "status": "error",
                "error": str(e),
                "timestamp": datetime.utcnow().isoformat() + "Z"
            }
    
    def _format_optimization_prompt(self, trade_approval: Dict[str, Any], market_data: Dict[str, Any]) -> str:
        """Format trade and market data into optimization prompt."""
        return f"""
Optimize entry price for this approved trade:

Trade Approval:
{json.dumps(trade_approval, indent=2)}

Current Market Data:
{json.dumps(market_data, indent=2)}

Analyze order book, support/resistance, VWAP, and suggest optimal entry within ±0.5% of suggested price.
Provide fill probability and timing window.

Return as JSON with optimal_entry, fill_probability, and timing_hint.
"""
    
    def _optimize_rule_based(self, trade_approval: Dict[str, Any], market_data: Dict[str, Any]) -> Dict[str, Any]:
        """Rule-based entry optimization without LLM."""
        
        suggested_entry = trade_approval.get("suggested_entry", 0)
        direction = trade_approval.get("direction", "LONG")
        
        # Extract order book
        order_book = market_data.get("order_book", {})
        bids = order_book.get("bids", [])
        asks = order_book.get("asks", [])
        
        # Calculate VWAP and POC (simple approximation)
        vwap = market_data.get("vwap", suggested_entry)
        poc = market_data.get("poc", suggested_entry)
        
        # Optimal entry strategy
        if direction == "LONG":
            # For longs, prefer to buy at support (lower prices)
            # Look at bid side for best liquidity
            optimal_entry = min(suggested_entry, poc) if bids else suggested_entry
            fill_probability = 0.85
        else:
            # For shorts, prefer to sell at resistance (higher prices)
            optimal_entry = max(suggested_entry, vwap) if asks else suggested_entry
            fill_probability = 0.80
        
        # Constrain to max deviation
        max_dev = suggested_entry * self.max_entry_deviation
        optimal_entry = max(suggested_entry - max_dev, min(suggested_entry + max_dev, optimal_entry))
        
        return {
            "optimal_entry": round(optimal_entry, 2),
            "entry_reasoning": f"{'Below' if optimal_entry < suggested_entry else 'Above'} suggested price for better fill",
            "fill_probability": fill_probability,
            "expected_slippage": abs(optimal_entry - suggested_entry) * 0.001,  # Estimate 0.1bps per tick
            "entry_window": {
                "start": datetime.utcnow().isoformat() + "Z",
                "duration_seconds": 300,
                "confidence": 0.80
            },
            "alternative_entries": [],
            "timing_hint": "Enter within 5 minutes for best results",
            "microstructure_signals": [
                "Check bid-ask imbalance before entering"
            ],
            "timestamp": datetime.utcnow().isoformat() + "Z"
        }

