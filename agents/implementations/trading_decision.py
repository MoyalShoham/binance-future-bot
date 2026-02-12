"""
Trading Decision Agent Implementation

Analyzes market research and makes trading decisions using scalping strategies.
"""

from typing import Dict, Any, Optional, Tuple, List
from datetime import datetime, timezone
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
        config: Dict[str, Any],
        model_router=None,
        binance_client=None
    ):
        """
        Initialize Trading Decision Agent.

        Args:
            agent_id: Agent identifier
            config: System configuration
            model_router: Optional ModelRouter for LLM enhancement
            binance_client: Optional Binance API client for real balance lookups
        """
        super().__init__(agent_id, config, model_router=model_router)
        self.binance_client = binance_client

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
        self.default_leverage = trading_config.get("default_leverage", 3)

        # Confidence threshold for trades
        self.min_confidence = 0.70  # Minimum 70% confidence to trade

        # Risk config filters
        risk_config = config.get("risk", {})
        self.max_funding_rate_pct = risk_config.get("max_funding_rate_pct", 0.0005)
        self.max_spread_bps = risk_config.get("max_spread_bps", 8)

        logger.debug(
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

        logger.debug(
            "Trading Decision Agent started",
            correlation_id=correlation_id,
            symbol=research_summary.get("symbol") if research_summary else None
        )

        start_time = datetime.utcnow()

        try:
            if not research_summary:
                raise ValueError("Missing research_summary in pipeline state")

            symbol = research_summary["symbol"]

            # ===== PRE-FILTERS: reject before strategy evaluation =====
            pre_filter_reject, reject_reason = self._apply_pre_filters(research_summary)
            if pre_filter_reject:
                logger.info("Pre-filter rejection", symbol=symbol, reason=reject_reason)
                # Build a NO_TRADE decision with the rejection reason
                return self._build_no_trade_decision(
                    symbol, correlation_id, reject_reason, start_time
                )

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

            # LLM Enhancement: Get holistic assessment of the rule-based decision
            model_used = "rule-based"
            llm_reasoning = ""
            llm_enhancement = self._get_llm_enhancement(
                research_summary, decision, strategy_id, confidence, strategy_signals
            )

            if llm_enhancement:
                # Apply confidence adjustment (symmetric range)
                adj = llm_enhancement.get("confidence_adjustment", 0)
                adj = max(-0.15, min(0.15, adj))  # Symmetric: balanced adjustment

                original_confidence = confidence
                confidence = max(0.0, min(1.0, confidence + adj))

                # Safety: LLM can demote trade to NO_TRADE but cannot promote
                if original_confidence >= self.min_confidence and confidence < self.min_confidence:
                    decision = "NO_TRADE"
                    logger.info(
                        "LLM lowered confidence below threshold",
                        original=original_confidence,
                        adjusted=confidence,
                        adjustment=adj
                    )

                # Safety: LLM CANNOT promote NO_TRADE to TRADE
                # (original NO_TRADE stays NO_TRADE regardless of adjustment)

                model_used = llm_enhancement.get("_model_used", "rule-based")
                llm_reasoning = llm_enhancement.get("enhanced_reasoning", "")

            # Expected holding time based on strategy
            expected_holding_time = self._get_expected_holding_time(strategy_id)

            # Build Trading Decision
            rule_reasoning = self._generate_reasoning(decision, strategy_id, research_summary, confidence)
            full_reasoning = rule_reasoning
            if llm_reasoning:
                full_reasoning = f"{rule_reasoning} | LLM: {llm_reasoning}"
            # Truncate to schema maxLength
            full_reasoning = full_reasoning[:1500]

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
                "model_used": model_used,
                "reasoning_summary": full_reasoning,
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

            logger.debug(
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

        Entry — ACTUAL CROSSOVER detection (not just alignment):
        - LONG: EMA9 was <= EMA21 last candle, now EMA9 > EMA21, price > EMA50
        - SHORT: EMA9 was >= EMA21 last candle, now EMA9 < EMA21, price < EMA50

        Confirmation:
        - Order book bias in same direction
        - RSI not extreme (30-70)
        - Higher timeframe trend alignment
        """
        tech_ind = research_summary.get("technical_indicators", {})
        market_data = research_summary.get("market_data", {})

        ema_9 = tech_ind.get("ema_9", 0)
        ema_21 = tech_ind.get("ema_21", 0)
        ema_50 = tech_ind.get("ema_50", 0)
        prev_ema_9 = tech_ind.get("prev_ema_9", ema_9)
        prev_ema_21 = tech_ind.get("prev_ema_21", ema_21)
        price = market_data.get("price", 0)
        rsi = tech_ind.get("rsi", 50)
        htf_trend = tech_ind.get("htf_trend", "neutral")

        order_book = market_data.get("order_book", {})
        imbalance = order_book.get("imbalance_ratio", 0)

        signal = "neutral"
        confidence = 0.5

        # Bullish crossover: was below/equal, now above
        bullish_cross = prev_ema_9 <= prev_ema_21 and ema_9 > ema_21
        # Bearish crossover: was above/equal, now below
        bearish_cross = prev_ema_9 >= prev_ema_21 and ema_9 < ema_21

        if bullish_cross and price > ema_50:
            if imbalance > 0.1 and 30 < rsi < 70:
                signal = "long"
                confidence = 0.75 + (abs(imbalance) * 0.15)
                # Boost if aligned with higher timeframe
                if htf_trend == "bullish":
                    confidence += 0.05

        elif bearish_cross and price < ema_50:
            if imbalance < -0.1 and 30 < rsi < 70:
                signal = "short"
                confidence = 0.75 + (abs(imbalance) * 0.15)
                if htf_trend == "bearish":
                    confidence += 0.05

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
        - LONG: MACD bullish + RSI > 55 + price > EMA50 + high relative volume
        - SHORT: MACD bearish + RSI < 45 + price < EMA50 + high relative volume

        Confirmation:
        - Relative volume > 1.5x (current vs 20-candle average)
        - Higher timeframe trend alignment
        """
        tech_ind = research_summary.get("technical_indicators", {})
        market_data = research_summary.get("market_data", {})

        macd_data = tech_ind.get("macd", {})
        histogram = macd_data.get("histogram", 0)

        rsi = tech_ind.get("rsi", 50)
        ema_50 = tech_ind.get("ema_50", 0)
        price = market_data.get("price", 0)
        volume_24h = market_data.get("volume_24h", 0)
        relative_volume = tech_ind.get("relative_volume", 1.0)
        htf_trend = tech_ind.get("htf_trend", "neutral")

        signal = "neutral"
        confidence = 0.5

        # Use relative volume instead of static 24h threshold
        volume_ok = volume_24h > 500000000 and relative_volume >= 1.5

        # Bullish breakout
        if histogram > 0 and rsi > 55 and price > ema_50 and volume_ok:
            signal = "long"
            confidence = 0.75 + (min(rsi - 55, 20) / 100)
            if htf_trend == "bullish":
                confidence += 0.05
            # Bonus for very high relative volume
            if relative_volume >= 2.5:
                confidence += 0.03

        # Bearish breakout
        elif histogram < 0 and rsi < 45 and price < ema_50 and volume_ok:
            signal = "short"
            confidence = 0.75 + (min(45 - rsi, 20) / 100)
            if htf_trend == "bearish":
                confidence += 0.05
            if relative_volume >= 2.5:
                confidence += 0.03

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
        atr = tech_ind.get("atr", 0) or 0  # Handle None
        if atr <= 0:
            atr = price * 0.01  # Fallback: 1% of price when ATR is 0 or missing

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

        Uses configured balance, leverage, and risk per trade.
        Caps notional position so required margin fits within account.
        """
        # Try to get real balance from Binance API, fall back to config value
        assumed_equity = None
        if self.binance_client:
            try:
                account_balance = self.binance_client.get_account_balance()
                assumed_equity = account_balance.get("total_equity", 0)
                if assumed_equity > 0:
                    logger.info(
                        "Using real Binance balance for position sizing",
                        equity=assumed_equity,
                    )
            except Exception as e:
                logger.warning("Failed to fetch Binance balance, using config fallback", error=str(e))

        if not assumed_equity or assumed_equity <= 0:
            assumed_equity = self.config.get("execution", {}).get(
                "paper_trading", {}
            ).get("simulated_balance_usdt", 100)

        # Risk per trade from config
        risk_pct = self.config.get("risk", {}).get("max_risk_per_trade_pct", 0.10)
        risk_amount = assumed_equity * risk_pct

        # Calculate position size based on stop distance
        stop_distance_pct = abs((entry_price - stop_loss) / entry_price)

        if stop_distance_pct > 0:
            position_size = risk_amount / stop_distance_pct
        else:
            position_size = assumed_equity * 0.5  # Default: 50% of equity

        # Cap position size so margin fits within account (leverage-aware)
        # max_notional = equity * leverage * utilization_factor
        leverage = self.default_leverage
        max_notional = assumed_equity * leverage * 0.80  # Use 80% of margin capacity
        position_size = min(position_size, max_notional)

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

        # Conservative default: assume coin flip until real data proves otherwise
        # Risk Manager will use actual DB stats for Kelly sizing
        win_probability = 0.50

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

    # ========== PRE-FILTERS ==========

    def _apply_pre_filters(
        self,
        research_summary: Dict[str, Any]
    ) -> Tuple[bool, str]:
        """
        Apply pre-filters before strategy evaluation.
        Returns (should_reject: bool, reason: str).
        """
        market_data = research_summary.get("market_data", {})
        order_book = market_data.get("order_book", {})

        # 1. Spread filter — reject if bid-ask spread is too wide
        spread_bps = order_book.get("spread_bps", 0)
        if spread_bps > self.max_spread_bps:
            return True, f"Spread too wide: {spread_bps:.1f} bps > {self.max_spread_bps} bps limit"

        # 2. Funding rate filter — warn but don't block at decision level
        #    (blocking is done per-direction after decision is made, in risk manager)
        #    However, if funding is extreme (>0.1%), reject entirely
        funding_rate = abs(market_data.get("funding_rate", 0))
        if funding_rate > 0.001:  # > 0.1% per 8h = 0.3%/day
            return True, f"Extreme funding rate: {funding_rate:.4%} — too costly to hold"

        # 3. Time-of-day filter — reduce quality during low-volume hours
        #    00:00-04:00 UTC (Asian quiet), 04:00-07:00 UTC (low overlap)
        utc_hour = datetime.now(timezone.utc).hour
        if 0 <= utc_hour < 4:
            # During dead hours, require higher base liquidity
            volume_24h = market_data.get("volume_24h", 0)
            if volume_24h < 200000000:  # Need $200M+ volume during quiet hours
                return True, f"Low-volume hours (UTC {utc_hour}:00) with insufficient liquidity: ${volume_24h:,.0f}"

        return False, ""

    def _build_no_trade_decision(
        self,
        symbol: str,
        correlation_id: str,
        reason: str,
        start_time: datetime
    ) -> Dict[str, Any]:
        """Build a NO_TRADE decision from a pre-filter rejection."""
        processing_time = (datetime.utcnow() - start_time).total_seconds() * 1000
        trading_decision = {
            "schema_version": "1.0.0",
            "agent_id": self.agent_id,
            "correlation_id": correlation_id,
            "timestamp": datetime.utcnow().isoformat(),
            "decision_id": str(uuid.uuid4()),
            "symbol": symbol,
            "decision": "NO_TRADE",
            "confidence": 0.0,
            "expected_holding_time_seconds": 0,
            "strategy_id": "pre_filter",
            "model_used": "rule-based",
            "reasoning_summary": f"Pre-filter: {reason}"[:1500],
            "entry_price": 0,
            "stop_loss": 0,
            "take_profit_levels": [],
            "position_size_usdt": 0,
            "leverage": self.default_leverage,
            "technical_signals": {},
            "risk_metrics": {},
            "research_summary_hash": ""
        }

        is_valid = self.validator.validate_message(trading_decision, "trading_decision", strict=True)
        if not is_valid:
            logger.warning("Pre-filter NO_TRADE failed schema validation, returning anyway")

        return trading_decision

    # ========== LLM ENHANCEMENT ==========

    def _get_llm_enhancement(
        self,
        research_summary: Dict[str, Any],
        decision: str,
        strategy_id: str,
        confidence: float,
        strategy_signals: Dict[str, Dict[str, Any]]
    ) -> Optional[Dict[str, Any]]:
        """
        Call LLM for enhanced trading decision reasoning.

        The LLM can adjust confidence but CANNOT:
        - Flip trade direction (LONG->SHORT or vice versa)
        - Promote NO_TRADE to a TRADE

        Returns parsed LLM response or None if unavailable.
        """
        from orchestration.model_router import TaskType
        from prompts.base import build_prompt
        from prompts.trading_decision import TRADING_DECISION_SYSTEM

        context_data = {
            "symbol": research_summary.get("symbol"),
            "rule_based_decision": decision,
            "rule_based_strategy": strategy_id,
            "rule_based_confidence": confidence,
            "strategy_signals": {k: v for k, v in strategy_signals.items()},
            "market_regime": research_summary.get("market_regime"),
            "technical_indicators": research_summary.get("technical_indicators"),
            "sentiment": research_summary.get("sentiment"),
            "warnings": research_summary.get("warnings", []),
            "llm_enhancement": research_summary.get("llm_enhancement"),
        }

        system_prompt, user_prompt = build_prompt(TRADING_DECISION_SYSTEM, context_data)

        result = self.call_llm(
            task_type=TaskType.COMPLEX_DECISION,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            context="decision",
            max_escalations=1,
        )

        if result and result.get("response"):
            response = result["response"]
            response["_model_used"] = result.get("model_used", "unknown")
            logger.debug(
                "LLM trading decision enhancement completed",
                model=result.get("model_used"),
                confidence_adjustment=response.get("confidence_adjustment"),
                llm_confidence=response.get("confidence"),
            )
            return response

        return None
