"""
Trading Decision Agent Implementation

Analyzes market research and makes trading decisions using scalping strategies.
"""

from typing import Dict, Any, Optional, Tuple
from datetime import datetime
import uuid
import structlog

from .base_agent import BaseAgent
from schemas.validator import SchemaValidator

logger = structlog.get_logger()


class TradingDecisionAgent(BaseAgent):
    """
    Trading Decision Agent

    Responsibilities:
    - Analyze technical indicators from Research Summary
    - Apply scalping strategies (4 strategies)
    - Make trading decisions (LONG/SHORT/NO_TRADE)
    - Calculate entry/exit levels
    - Estimate risk/reward metrics
    - Produce schema-compliant Trading Decision JSON

    Strategies:
    1. EMA Crossover Scalp (5m, 3min hold)
    2. VWAP Bounce Scalp (1m, 2min hold)
    3. Order Book Imbalance Scalp (1m, 90sec hold)
    4. Momentum Breakout Scalp (5m, 4min hold)

    Authority Boundaries:
    ✅ Analyze market data and propose trades
    ✅ Calculate entry/exit levels
    ❌ Execute trades (requires Risk Manager approval)
    ❌ Modify risk parameters
    """

    def __init__(
        self,
        agent_id: str,
        config: Dict[str, Any]
    ):
        """
        Initialize Trading Decision Agent.

        Args:
            agent_id: Agent identifier
            config: System configuration
        """
        super().__init__(agent_id, config)

        self.validator = SchemaValidator()

        # Load strategy configurations
        strategies_config = config.get("strategies", {})
        self.enabled_strategies = strategies_config.get("enabled", [
            "ema_crossover_scalp",
            "vwap_bounce_scalp",
            "orderbook_imbalance_scalp",
            "momentum_breakout_scalp"
        ])

        # Load strategy parameters
        self.ema_config = strategies_config.get("ema_crossover_scalp", {})
        self.vwap_config = strategies_config.get("vwap_bounce_scalp", {})
        self.orderbook_config = strategies_config.get("orderbook_imbalance_scalp", {})
        self.momentum_config = strategies_config.get("momentum_breakout_scalp", {})

        # Default leverage and position sizing
        trading_config = config.get("trading", {})
        self.default_leverage = trading_config.get("default_leverage", 5)

        # Confidence threshold for trades
        self.min_confidence = 0.70  # Minimum 70% confidence to trade

        logger.info(
            "Trading Decision Agent initialized",
            agent_id=self.agent_id,
            enabled_strategies=self.enabled_strategies
        )

    def execute(self, state: Dict[str, Any]) -> Dict[str, Any]:
        """
        Main execution: Analyze research and make trading decision.

        Args:
            state: Pipeline state with research_summary

        Returns:
            Trading Decision JSON (conforms to trading_decision.schema.json)
        """
        research_summary = state.get("research_summary")
        correlation_id = state.get("correlation_id", str(uuid.uuid4()))

        logger.info(
            "Trading Decision Agent started",
            correlation_id=correlation_id,
            symbol=research_summary.get("symbol") if research_summary else None
        )

        start_time = datetime.utcnow()

        try:
            if not research_summary:
                raise ValueError("Missing research_summary in pipeline state")

            symbol = research_summary["symbol"]

            # Evaluate all enabled strategies
            strategy_signals = self._evaluate_all_strategies(research_summary)

            # Select best strategy and make decision
            decision, strategy_id, confidence = self._make_decision(strategy_signals)

            # Calculate entry/exit levels
            entry_price, stop_loss, take_profit_levels = self._calculate_levels(
                research_summary,
                decision,
                strategy_id
            )

            # Estimate position size (will be validated by Risk Manager)
            position_size_usdt = self._estimate_position_size(
                research_summary,
                entry_price,
                stop_loss
            )

            # Calculate risk metrics
            risk_metrics = self._calculate_risk_metrics(
                entry_price,
                stop_loss,
                take_profit_levels,
                strategy_id
            )

            # Get technical signals summary
            technical_signals = self._get_technical_signals(research_summary)

            # Expected holding time based on strategy
            expected_holding_time = self._get_expected_holding_time(strategy_id)

            # Build Trading Decision
            trading_decision = {
                "schema_version": "1.0.0",
                "agent_id": self.agent_id,
                "correlation_id": correlation_id,
                "timestamp": datetime.utcnow().isoformat(),
                "decision_id": str(uuid.uuid4()),
                "symbol": symbol,
                "decision": decision,
                "confidence": confidence,
                "expected_holding_time_seconds": expected_holding_time,
                "strategy_id": strategy_id,
                "model_used": "rule-based",  # No LLM for core strategy logic
                "reasoning_summary": self._generate_reasoning(
                    decision,
                    strategy_id,
                    research_summary,
                    confidence
                ),
                "entry_price": entry_price,
                "stop_loss": stop_loss,
                "take_profit_levels": take_profit_levels,
                "position_size_usdt": position_size_usdt,
                "leverage": self.default_leverage,
                "technical_signals": technical_signals,
                "risk_metrics": risk_metrics,
                "research_summary_hash": self.validator.compute_input_hash(research_summary)
            }

            # Validate against schema
            is_valid = self.validator.validate_message(
                trading_decision,
                "trading_decision",
                strict=True
            )

            if not is_valid:
                logger.error("Trading decision failed schema validation")
                raise ValueError("Trading decision does not conform to schema")

            processing_time = (datetime.utcnow() - start_time).total_seconds() * 1000

            logger.info(
                "Trading Decision made",
                correlation_id=correlation_id,
                symbol=symbol,
                decision=decision,
                strategy_id=strategy_id,
                confidence=confidence,
                processing_time_ms=processing_time
            )

            return trading_decision

        except Exception as e:
            logger.error(
                "Trading Decision Agent failed",
                correlation_id=correlation_id,
                error=str(e),
                exc_info=True
            )
            raise

    # ========== STRATEGY EVALUATION ==========

    def _evaluate_all_strategies(
        self,
        research_summary: Dict[str, Any]
    ) -> Dict[str, Dict[str, Any]]:
        """
        Evaluate all enabled strategies.

        Returns:
            Dict of strategy signals with confidence scores
        """
        signals = {}

        if "ema_crossover_scalp" in self.enabled_strategies:
            signals["ema_crossover_scalp"] = self._evaluate_ema_crossover(research_summary)

        if "vwap_bounce_scalp" in self.enabled_strategies:
            signals["vwap_bounce_scalp"] = self._evaluate_vwap_bounce(research_summary)

        if "orderbook_imbalance_scalp" in self.enabled_strategies:
            signals["orderbook_imbalance_scalp"] = self._evaluate_orderbook_imbalance(research_summary)

        if "momentum_breakout_scalp" in self.enabled_strategies:
            signals["momentum_breakout_scalp"] = self._evaluate_momentum_breakout(research_summary)

        return signals

    def _evaluate_ema_crossover(
        self,
        research_summary: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Strategy 1: EMA Crossover Scalp

        Entry:
        - LONG: EMA9 crosses above EMA21, price > EMA50
        - SHORT: EMA9 crosses below EMA21, price < EMA50

        Confirmation:
        - Order book bias in same direction
        - RSI not extreme (30-70)
        """
        tech_ind = research_summary.get("technical_indicators", {})
        market_data = research_summary.get("market_data", {})

        ema_9 = tech_ind.get("ema_9", 0)
        ema_21 = tech_ind.get("ema_21", 0)
        ema_50 = tech_ind.get("ema_50", 0)
        price = market_data.get("price", 0)
        rsi = tech_ind.get("rsi", 50)

        order_book = market_data.get("order_book", {})
        imbalance = order_book.get("imbalance_ratio", 0)

        # Signal detection
        signal = "neutral"
        confidence = 0.5

        # Bullish setup
        if ema_9 > ema_21 and price > ema_50:
            if imbalance > 0.1 and 30 < rsi < 70:
                signal = "long"
                confidence = 0.75 + (abs(imbalance) * 0.15)

        # Bearish setup
        elif ema_9 < ema_21 and price < ema_50:
            if imbalance < -0.1 and 30 < rsi < 70:
                signal = "short"
                confidence = 0.75 + (abs(imbalance) * 0.15)

        return {
            "signal": signal,
            "confidence": min(1.0, confidence)
        }

    def _evaluate_vwap_bounce(
        self,
        research_summary: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Strategy 2: VWAP Bounce Scalp

        Entry:
        - LONG: Price touches VWAP from above and bounces (mean reversion)
        - SHORT: Price touches VWAP from below and bounces

        Confirmation:
        - RSI confirms direction
        """
        tech_ind = research_summary.get("technical_indicators", {})
        market_data = research_summary.get("market_data", {})

        vwap = tech_ind.get("vwap", 0)
        price = market_data.get("price", 0)
        rsi = tech_ind.get("rsi", 50)

        if vwap == 0:
            return {"signal": "neutral", "confidence": 0.0}

        # Calculate distance from VWAP
        distance_pct = (price - vwap) / vwap

        signal = "neutral"
        confidence = 0.5

        # Price near VWAP (within 0.2%)
        if abs(distance_pct) < 0.002:
            # Bounce up from VWAP
            if distance_pct > 0 and rsi > 50:
                signal = "long"
                confidence = 0.70

            # Bounce down from VWAP
            elif distance_pct < 0 and rsi < 50:
                signal = "short"
                confidence = 0.70

        return {
            "signal": signal,
            "confidence": confidence
        }

    def _evaluate_orderbook_imbalance(
        self,
        research_summary: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Strategy 3: Order Book Imbalance Scalp

        Entry:
        - LONG: Strong buy pressure (imbalance > 0.3)
        - SHORT: Strong sell pressure (imbalance < -0.3)

        Confirmation:
        - Volume confirmation
        """
        market_data = research_summary.get("market_data", {})

        order_book = market_data.get("order_book", {})
        imbalance = order_book.get("imbalance_ratio", 0)
        volume_24h = market_data.get("volume_24h", 0)

        signal = "neutral"
        confidence = 0.5

        # High volume market (> $500M)
        if volume_24h > 500000000:
            # Strong buy pressure
            if imbalance > 0.3:
                signal = "long"
                confidence = 0.70 + (imbalance * 0.2)

            # Strong sell pressure
            elif imbalance < -0.3:
                signal = "short"
                confidence = 0.70 + (abs(imbalance) * 0.2)

        return {
            "signal": signal,
            "confidence": min(1.0, confidence)
        }

    def _evaluate_momentum_breakout(
        self,
        research_summary: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Strategy 4: Momentum Breakout Scalp

        Entry:
        - LONG: MACD bullish cross + RSI > 55 + price > EMA50
        - SHORT: MACD bearish cross + RSI < 45 + price < EMA50

        Confirmation:
        - High volume
        """
        tech_ind = research_summary.get("technical_indicators", {})
        market_data = research_summary.get("market_data", {})

        macd_data = tech_ind.get("macd", {})
        macd_line = macd_data.get("macd_line", 0)
        signal_line = macd_data.get("signal_line", 0)
        histogram = macd_data.get("histogram", 0)

        rsi = tech_ind.get("rsi", 50)
        ema_50 = tech_ind.get("ema_50", 0)
        price = market_data.get("price", 0)
        volume_24h = market_data.get("volume_24h", 0)

        signal = "neutral"
        confidence = 0.5

        # Bullish breakout
        if histogram > 0 and rsi > 55 and price > ema_50 and volume_24h > 500000000:
            signal = "long"
            confidence = 0.75 + (min(rsi - 55, 20) / 100)

        # Bearish breakout
        elif histogram < 0 and rsi < 45 and price < ema_50 and volume_24h > 500000000:
            signal = "short"
            confidence = 0.75 + (min(45 - rsi, 20) / 100)

        return {
            "signal": signal,
            "confidence": min(1.0, confidence)
        }

    # ========== DECISION LOGIC ==========

    def _make_decision(
        self,
        strategy_signals: Dict[str, Dict[str, Any]]
    ) -> Tuple[str, str, float]:
        """
        Make final trading decision based on strategy signals.

        Returns:
            (decision, strategy_id, confidence)
        """
        # Find best signal
        best_strategy = None
        best_signal = "neutral"
        best_confidence = 0.0

        for strategy_id, signal_data in strategy_signals.items():
            signal = signal_data["signal"]
            confidence = signal_data["confidence"]

            if confidence > best_confidence:
                best_confidence = confidence
                best_signal = signal
                best_strategy = strategy_id

        # Make decision
        if best_confidence >= self.min_confidence and best_signal != "neutral":
            decision = "LONG" if best_signal == "long" else "SHORT"
            return decision, best_strategy, best_confidence
        else:
            return "NO_TRADE", best_strategy or "none", best_confidence

    def _calculate_levels(
        self,
        research_summary: Dict[str, Any],
        decision: str,
        strategy_id: str
    ) -> Tuple[float, float, List[Dict[str, Any]]]:
        """
        Calculate entry, stop loss, and take profit levels.

        Returns:
            (entry_price, stop_loss, take_profit_levels)
        """
        market_data = research_summary.get("market_data", {})
        tech_ind = research_summary.get("technical_indicators", {})

        price = market_data.get("price", 0)
        atr = tech_ind.get("atr", price * 0.01)  # Default 1% ATR

        if decision == "NO_TRADE":
            return price, price, []

        # Entry: current market price
        entry_price = price

        # Stop loss: 1.5 ATR away
        if decision == "LONG":
            stop_loss = entry_price - (atr * 1.5)
        else:  # SHORT
            stop_loss = entry_price + (atr * 1.5)

        # Take profit: Multiple levels for partial exits
        if decision == "LONG":
            take_profit_levels = [
                {"price": entry_price + (atr * 2.0), "quantity_pct": 0.5},  # 2 ATR
                {"price": entry_price + (atr * 3.0), "quantity_pct": 0.5}   # 3 ATR
            ]
        else:  # SHORT
            take_profit_levels = [
                {"price": entry_price - (atr * 2.0), "quantity_pct": 0.5},
                {"price": entry_price - (atr * 3.0), "quantity_pct": 0.5}
            ]

        return entry_price, stop_loss, take_profit_levels

    def _estimate_position_size(
        self,
        research_summary: Dict[str, Any],
        entry_price: float,
        stop_loss: float
    ) -> float:
        """
        Estimate initial position size (Risk Manager will validate/adjust).

        Uses simplified 2% risk per trade assumption.
        """
        # Assume $10,000 account (will be validated by Risk Manager)
        assumed_equity = 10000.0

        # Risk 2% per trade
        risk_amount = assumed_equity * 0.02

        # Calculate position size
        stop_distance_pct = abs((entry_price - stop_loss) / entry_price)

        if stop_distance_pct > 0:
            position_size = risk_amount / stop_distance_pct
        else:
            position_size = 1000.0  # Default fallback

        return round(position_size, 2)

    def _calculate_risk_metrics(
        self,
        entry_price: float,
        stop_loss: float,
        take_profit_levels: List[Dict[str, Any]],
        strategy_id: str
    ) -> Dict[str, Any]:
        """Calculate risk/reward metrics."""

        if not take_profit_levels:
            return {}

        # Calculate risk distance
        risk_distance = abs(entry_price - stop_loss)

        # Calculate average reward
        avg_tp_price = sum(tp["price"] for tp in take_profit_levels) / len(take_profit_levels)
        reward_distance = abs(avg_tp_price - entry_price)

        # Risk/reward ratio
        rr_ratio = reward_distance / risk_distance if risk_distance > 0 else 0

        # Win probability (estimated based on historical strategy performance)
        # Simplified - could be enhanced with backtesting data
        win_probability = 0.65  # Default 65% win rate

        # Max adverse excursion (estimated)
        max_adverse_excursion_pct = abs((stop_loss - entry_price) / entry_price)

        return {
            "risk_reward_ratio": round(rr_ratio, 2),
            "win_probability": win_probability,
            "max_adverse_excursion_pct": round(max_adverse_excursion_pct, 4)
        }

    def _get_technical_signals(
        self,
        research_summary: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Get summary of technical signals."""

        tech_ind = research_summary.get("technical_indicators", {})
        market_data = research_summary.get("market_data", {})

        ema_9 = tech_ind.get("ema_9", 0)
        ema_21 = tech_ind.get("ema_21", 0)
        rsi = tech_ind.get("rsi", 50)
        macd_data = tech_ind.get("macd", {})
        histogram = macd_data.get("histogram", 0)

        order_book = market_data.get("order_book", {})
        imbalance = order_book.get("imbalance_ratio", 0)

        # EMA alignment
        if ema_9 > ema_21:
            ema_alignment = "bullish"
        elif ema_9 < ema_21:
            ema_alignment = "bearish"
        else:
            ema_alignment = "neutral"

        # RSI condition
        if rsi > 70:
            rsi_condition = "overbought"
        elif rsi < 30:
            rsi_condition = "oversold"
        else:
            rsi_condition = "neutral"

        # MACD signal
        if histogram > 0:
            macd_signal = "bullish_cross"
        elif histogram < 0:
            macd_signal = "bearish_cross"
        else:
            macd_signal = "neutral"

        # Order book pressure
        if imbalance > 0.2:
            orderbook_pressure = "buy_pressure"
        elif imbalance < -0.2:
            orderbook_pressure = "sell_pressure"
        else:
            orderbook_pressure = "neutral"

        return {
            "ema_alignment": ema_alignment,
            "rsi_condition": rsi_condition,
            "macd_signal": macd_signal,
            "volume_confirmation": market_data.get("volume_24h", 0) > 500000000,
            "orderbook_pressure": orderbook_pressure
        }

    def _get_expected_holding_time(self, strategy_id: str) -> int:
        """Get expected holding time for strategy (in seconds)."""

        holding_times = {
            "ema_crossover_scalp": 180,      # 3 minutes
            "vwap_bounce_scalp": 120,        # 2 minutes
            "orderbook_imbalance_scalp": 90, # 90 seconds
            "momentum_breakout_scalp": 240,  # 4 minutes
            "none": 0
        }

        return holding_times.get(strategy_id, 180)

    def _generate_reasoning(
        self,
        decision: str,
        strategy_id: str,
        research_summary: Dict[str, Any],
        confidence: float
    ) -> str:
        """Generate human-readable reasoning for the decision."""

        market_regime = research_summary.get("market_regime", "unknown")
        tech_ind = research_summary.get("technical_indicators", {})
        rsi = tech_ind.get("rsi", 50)
        volatility = tech_ind.get("volatility_pct", 0.03)

        if decision == "NO_TRADE":
            return f"No clear signal ({confidence:.0%} confidence). Market regime: {market_regime}. Volatility: {volatility:.2%}."

        strategy_names = {
            "ema_crossover_scalp": "EMA Crossover",
            "vwap_bounce_scalp": "VWAP Bounce",
            "orderbook_imbalance_scalp": "Order Book Imbalance",
            "momentum_breakout_scalp": "Momentum Breakout"
        }

        strategy_name = strategy_names.get(strategy_id, strategy_id)

        return (
            f"{decision} signal from {strategy_name} strategy with {confidence:.0%} confidence. "
            f"Market regime: {market_regime}, RSI: {rsi:.0f}, Volatility: {volatility:.2%}."
        )
