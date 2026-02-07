"""
Trading Decision Agent Implementation

Analyzes research and applies scalping strategies to make trading decisions.
"""

from typing import Dict, Any, Optional, Tuple
from datetime import datetime
import uuid
import structlog

from .base_agent import BaseAgent
from orchestration.state_manager import TradingState
from orchestration.model_router import ModelRouter, TaskType

logger = structlog.get_logger()


class TradingDecisionAgent(BaseAgent):
    """
    Trading Decision Agent: Strategy-based decision making with AI reasoning.
    """

    def __init__(self, config: Dict[str, Any]):
        super().__init__("trading-decision", config)

        self.model_router = ModelRouter(config.get("models", {}))
        self.strategies = self._load_strategies(config)

    def execute(self, state: TradingState) -> Dict[str, Any]:
        """
        Execute trading decision logic.

        Args:
            state: Current trading state with research_summary

        Returns:
            Trading decision conforming to trading_decision schema
        """
        start_time = datetime.utcnow()

        research_summary = state.get("research_summary")
        if not research_summary:
            raise ValueError("No research summary in state")

        symbol = research_summary["symbol"]

        self.logger.info(
            "Starting trading decision",
            symbol=symbol,
            market_regime=research_summary.get("market_regime"),
            correlation_id=state.get("correlation_id")
        )

        # Check research freshness
        if research_summary.get("time_decay_factor", 0) < 0.5:
            return self._create_no_trade_decision(
                symbol,
                "Research data too stale (time_decay < 0.5)"
            )

        # Check market regime
        regime = research_summary["market_regime"]
        if regime in ["high_volatility", "low_liquidity"]:
            return self._create_no_trade_decision(
                symbol,
                f"Unfavorable market regime: {regime}"
            )

        # Select strategy
        strategy = self._select_strategy(regime)

        # Evaluate technical signals
        signals = self._evaluate_technical_signals(research_summary)
        signal_score = sum(1 if s == "bullish" else -1 if s == "bearish" else 0
                          for s in signals.values() if isinstance(s, str))

        # Check if signals are strong enough
        if abs(signal_score) < 3:
            return self._create_no_trade_decision(
                symbol,
                f"Insufficient technical edge (signal_score={signal_score})"
            )

        # Determine direction
        direction = "LONG" if signal_score > 0 else "SHORT"

        # Calculate levels
        entry_price = research_summary["market_data"]["price"]
        stop_loss, take_profit_levels = self._calculate_levels(
            entry_price,
            direction,
            strategy
        )

        # Get AI model reasoning
        ai_reasoning = self._get_ai_reasoning(
            research_summary,
            strategy,
            direction,
            entry_price,
            stop_loss,
            take_profit_levels
        )

        # Check AI confidence
        if ai_reasoning["confidence"] < 0.75:
            return self._create_no_trade_decision(
                symbol,
                f"AI confidence too low ({ai_reasoning['confidence']:.2f})"
            )

        # Calculate position size proposal
        position_size, leverage = self._calculate_position_size(
            entry_price,
            stop_loss,
            research_summary["technical_indicators"].get("volatility_pct", 3.0)
        )

        # Create trading decision
        decision = self._create_trading_decision(
            symbol=symbol,
            decision=direction,
            confidence=ai_reasoning["confidence"],
            strategy_id=strategy["id"],
            entry_price=entry_price,
            stop_loss=stop_loss,
            take_profit_levels=take_profit_levels,
            position_size_usdt=position_size,
            leverage=leverage,
            reasoning=ai_reasoning["reasoning"],
            technical_signals=signals,
            risk_metrics=ai_reasoning.get("risk_metrics", {}),
            research_summary=research_summary
        )

        processing_time_ms = (datetime.utcnow() - start_time).total_seconds() * 1000

        self.logger.info(
            "Trading decision completed",
            decision=direction,
            confidence=ai_reasoning["confidence"],
            strategy=strategy["id"],
            processing_time_ms=processing_time_ms
        )

        return decision

    def _select_strategy(self, market_regime: str) -> Dict[str, Any]:
        """
        Select appropriate strategy based on market regime.

        Args:
            market_regime: Current market regime

        Returns:
            Strategy configuration
        """
        if market_regime == "trending_up":
            return self.strategies["ema_crossover_scalp"]
        elif market_regime == "trending_down":
            return self.strategies["ema_crossover_scalp"]
        elif market_regime == "ranging":
            return self.strategies["vwap_bounce_scalp"]
        else:
            return self.strategies["ema_crossover_scalp"]  # Default

    def _evaluate_technical_signals(
        self,
        research_summary: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Evaluate all technical indicators and return signal strengths.

        Args:
            research_summary: Research summary with indicators

        Returns:
            Dict of technical signals
        """
        indicators = research_summary["technical_indicators"]
        market_data = research_summary["market_data"]
        price = market_data["price"]

        # EMA alignment
        ema_9 = indicators.get("ema_9", price)
        ema_21 = indicators.get("ema_21", price)
        ema_50 = indicators.get("ema_50", price)

        if ema_9 > ema_21 > ema_50:
            ema_alignment = "bullish"
        elif ema_9 < ema_21 < ema_50:
            ema_alignment = "bearish"
        else:
            ema_alignment = "neutral"

        # RSI condition
        rsi = indicators.get("rsi", 50)
        if rsi < 30:
            rsi_condition = "oversold"
        elif rsi > 70:
            rsi_condition = "overbought"
        else:
            rsi_condition = "neutral"

        # MACD signal
        macd = indicators.get("macd", {})
        macd_line = macd.get("macd_line", 0)
        signal_line = macd.get("signal_line", 0)

        if macd_line > signal_line:
            macd_signal = "bullish_cross"
        elif macd_line < signal_line:
            macd_signal = "bearish_cross"
        else:
            macd_signal = "neutral"

        # Volume confirmation (simplified)
        volume_confirmation = True  # TODO: Compare with average volume

        # Order book pressure
        order_book = market_data.get("order_book", {})
        imbalance = order_book.get("imbalance_ratio", 0)

        if imbalance > 0.1:
            orderbook_pressure = "buy_pressure"
        elif imbalance < -0.1:
            orderbook_pressure = "sell_pressure"
        else:
            orderbook_pressure = "neutral"

        return {
            "ema_alignment": ema_alignment,
            "rsi_condition": rsi_condition,
            "macd_signal": macd_signal,
            "volume_confirmation": volume_confirmation,
            "orderbook_pressure": orderbook_pressure
        }

    def _calculate_levels(
        self,
        entry_price: float,
        direction: str,
        strategy: Dict[str, Any]
    ) -> Tuple[float, List[Dict[str, Any]]]:
        """
        Calculate stop loss and take profit levels.

        Args:
            entry_price: Entry price
            direction: LONG or SHORT
            strategy: Strategy configuration

        Returns:
            Tuple of (stop_loss, take_profit_levels)
        """
        exit_conditions = strategy["exit_conditions"]
        profit_target_pct = exit_conditions["profit_target_pct"]
        stop_loss_pct = exit_conditions["stop_loss_pct"]

        if direction == "LONG":
            stop_loss = entry_price * (1 - stop_loss_pct)
            tp1 = entry_price * (1 + profit_target_pct * 0.5)
            tp2 = entry_price * (1 + profit_target_pct)
        else:  # SHORT
            stop_loss = entry_price * (1 + stop_loss_pct)
            tp1 = entry_price * (1 - profit_target_pct * 0.5)
            tp2 = entry_price * (1 - profit_target_pct)

        take_profit_levels = [
            {"price": round(tp1, 2), "quantity_pct": 0.5},
            {"price": round(tp2, 2), "quantity_pct": 0.5}
        ]

        return round(stop_loss, 2), take_profit_levels

    def _calculate_position_size(
        self,
        entry_price: float,
        stop_loss: float,
        volatility_pct: float
    ) -> Tuple[float, int]:
        """
        Calculate position size and leverage proposal.

        Args:
            entry_price: Entry price
            stop_loss: Stop loss price
            volatility_pct: Market volatility

        Returns:
            Tuple of (position_size_usdt, leverage)
        """
        # TODO: Get actual account equity from state
        account_equity = 10000.0  # Mock value
        risk_per_trade_pct = 0.02  # 2%

        risk_amount = account_equity * risk_per_trade_pct
        stop_distance_pct = abs(entry_price - stop_loss) / entry_price
        position_size = risk_amount / stop_distance_pct

        # Cap position size
        position_size = min(position_size, account_equity * 0.30)

        # Calculate leverage based on volatility
        if volatility_pct < 2.0:
            leverage = 8
        elif volatility_pct < 5.0:
            leverage = 5
        else:
            leverage = 3

        return round(position_size, 2), leverage

    def _get_ai_reasoning(
        self,
        research_summary: Dict[str, Any],
        strategy: Dict[str, Any],
        direction: str,
        entry_price: float,
        stop_loss: float,
        take_profit_levels: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """
        Get AI model reasoning for trade decision.

        Args:
            research_summary: Research summary
            strategy: Strategy configuration
            direction: LONG or SHORT
            entry_price: Entry price
            stop_loss: Stop loss
            take_profit_levels: Take profit levels

        Returns:
            AI reasoning with confidence and win probability
        """
        # TODO: Implement real AI model calls
        # For now, return mock reasoning

        self.logger.info("Getting AI reasoning", direction=direction, strategy=strategy["id"])

        # Mock routing
        routing_plan = self.model_router.route_with_escalation(
            task_type=TaskType.COMPLEX_DECISION,
            prompt="Analyze trade decision",
            context="decision"
        )

        # Mock AI response
        return {
            "confidence": 0.82,
            "reasoning": f"Strong {direction.lower()} signals with {strategy['name']}. "
                        f"Technical indicators aligned. Sentiment positive.",
            "risk_metrics": {
                "risk_reward_ratio": 1.5,
                "win_probability": 0.68,
                "max_adverse_excursion_pct": 0.003
            },
            "model_used": routing_plan["initial_model"]
        }

    def _create_trading_decision(
        self,
        symbol: str,
        decision: str,
        confidence: float,
        strategy_id: str,
        entry_price: float,
        stop_loss: float,
        take_profit_levels: List[Dict[str, Any]],
        position_size_usdt: float,
        leverage: int,
        reasoning: str,
        technical_signals: Dict[str, Any],
        risk_metrics: Dict[str, Any],
        research_summary: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Create trading decision object.

        Args:
            All decision parameters

        Returns:
            Trading decision dict
        """
        decision_dict = {
            "decision_id": str(uuid.uuid4()),
            "symbol": symbol,
            "decision": decision,
            "confidence": confidence,
            "expected_holding_time_seconds": self.strategies[strategy_id]["expected_holding_time"],
            "strategy_id": strategy_id,
            "model_used": risk_metrics.get("model_used", "gpt-4o-nano"),
            "reasoning_summary": reasoning,
            "entry_price": entry_price,
            "stop_loss": stop_loss,
            "take_profit_levels": take_profit_levels,
            "position_size_usdt": position_size_usdt,
            "leverage": leverage,
            "technical_signals": technical_signals,
            "risk_metrics": risk_metrics,
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "research_summary_hash": self.compute_input_hash(research_summary)
        }

        # Validate
        self.validate_output(decision_dict, "trading_decision")

        return decision_dict

    def _create_no_trade_decision(self, symbol: str, reason: str) -> Dict[str, Any]:
        """
        Create NO_TRADE decision.

        Args:
            symbol: Trading symbol
            reason: Reason for no trade

        Returns:
            NO_TRADE decision
        """
        decision = {
            "decision_id": str(uuid.uuid4()),
            "symbol": symbol,
            "decision": "NO_TRADE",
            "confidence": 0.95,
            "expected_holding_time_seconds": 0,
            "strategy_id": None,
            "model_used": "gpt-4o-nano",
            "reasoning_summary": reason,
            "timestamp": datetime.utcnow().isoformat() + "Z"
        }

        self.logger.info("NO_TRADE decision", reason=reason)

        return decision

    def _load_strategies(self, config: Dict[str, Any]) -> Dict[str, Any]:
        """
        Load strategy configurations.

        Args:
            config: System configuration

        Returns:
            Dict of strategies
        """
        return {
            "ema_crossover_scalp": {
                "id": "ema_crossover_scalp",
                "name": "EMA Crossover Scalp",
                "timeframe": "5m",
                "expected_holding_time": 180,
                "exit_conditions": {
                    "profit_target_pct": 0.003,
                    "stop_loss_pct": 0.002,
                    "time_limit_seconds": 300
                }
            },
            "vwap_bounce_scalp": {
                "id": "vwap_bounce_scalp",
                "name": "VWAP Bounce Scalp",
                "timeframe": "1m",
                "expected_holding_time": 120,
                "exit_conditions": {
                    "profit_target_pct": 0.0025,
                    "stop_loss_pct": 0.0015,
                    "time_limit_seconds": 180
                }
            },
            "orderbook_imbalance_scalp": {
                "id": "orderbook_imbalance_scalp",
                "name": "Order Book Imbalance Scalp",
                "timeframe": "1m",
                "expected_holding_time": 90,
                "exit_conditions": {
                    "profit_target_pct": 0.002,
                    "stop_loss_pct": 0.0015,
                    "time_limit_seconds": 120
                }
            },
            "momentum_breakout_scalp": {
                "id": "momentum_breakout_scalp",
                "name": "Momentum Breakout Scalp",
                "timeframe": "5m",
                "expected_holding_time": 240,
                "exit_conditions": {
                    "profit_target_pct": 0.004,
                    "stop_loss_pct": 0.0025,
                    "time_limit_seconds": 360
                }
            }
        }
