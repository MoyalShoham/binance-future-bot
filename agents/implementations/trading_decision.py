"""
Trading Decision Agent Implementation

Analyzes market research and makes trading decisions using scalping strategies.
Includes learning from past trades: strategy auto-disable, symbol memory,
per-strategy confidence adjustment, time-of-day profitability, and market regime memory.
"""

import time
from typing import Dict, Any, Optional, Tuple, List
from datetime import datetime, timezone
import uuid
import structlog

from .base_agent import BaseAgent
from schemas.validator import SchemaValidator
from infrastructure.database.queries import DatabaseQueries

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
        binance_client=None,
        db_session=None,
        regime_state=None
    ):
        """
        Initialize Trading Decision Agent.

        Args:
            agent_id: Agent identifier
            config: System configuration
            model_router: Optional ModelRouter for LLM enhancement
            binance_client: Optional Binance API client for real balance lookups
            db_session: Optional DatabaseSession for learning from past trades
            regime_state: Optional RegimeState for market regime overrides
        """
        super().__init__(agent_id, config, model_router=model_router)
        self.binance_client = binance_client
        self.db_session = db_session
        self.regime_state = regime_state

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

        # Fee filter config
        fee_config = config.get("execution", {}).get("fees", {})
        self.taker_bps = fee_config.get("taker_bps", 5)
        fee_filter_config = fee_config.get("pre_trade_fee_filter", {})
        self.fee_filter_enabled = fee_filter_config.get("enabled", True)
        self.fee_buffer_multiplier = fee_filter_config.get("fee_buffer_multiplier", 1.5)

        # Confidence threshold for trades
        self.min_confidence = 0.78  # Minimum 78% confidence to trade (raised from 70%)

        # Risk config filters
        risk_config = config.get("risk", {})
        self.max_funding_rate_pct = risk_config.get("max_funding_rate_pct", 0.0005)
        self.max_spread_bps = risk_config.get("max_spread_bps", 8)

        # Pre-filter rejection cache: {symbol: expiry_timestamp}
        # Avoids re-evaluating funding rate/spread for symbols that were just rejected
        self._prefilter_cache: Dict[str, float] = {}
        self._prefilter_cache_ttl = 300  # 5 minutes

        # ===== LEARNING CONFIG =====
        # Learning thresholds (configurable via config.learning or defaults)
        learning_config = config.get("learning", {})
        self.strategy_auto_disable_win_rate = learning_config.get("strategy_auto_disable_win_rate", 0.30)
        self.strategy_auto_disable_min_trades = learning_config.get("strategy_auto_disable_min_trades", 15)
        self.symbol_min_win_rate = learning_config.get("symbol_min_win_rate", 0.25)
        self.symbol_min_trades = learning_config.get("symbol_min_trades", 8)
        self.hour_min_win_rate = learning_config.get("hour_min_win_rate", 0.30)
        self.regime_confidence_boost = learning_config.get("regime_confidence_boost", 0.08)
        self.regime_confidence_penalty = learning_config.get("regime_confidence_penalty", 0.10)

        # Learning cache: refreshed every 5 minutes to avoid DB spam
        self._learning_cache: Dict[str, Any] = {}
        self._learning_cache_time: float = 0
        self._learning_cache_ttl: float = 300  # 5 minutes

        logger.debug(
            "Trading Decision Agent initialized",
            agent_id=self.agent_id,
            enabled_strategies=self.enabled_strategies,
            learning_enabled=db_session is not None,
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

            # ===== LEARNING: refresh cache from past trades =====
            self._refresh_learning_cache()

            # ===== PRE-FILTERS: reject before strategy evaluation =====
            # Check cache first — skip re-evaluation for recently rejected symbols
            cached_expiry = self._prefilter_cache.get(symbol, 0)
            if cached_expiry > time.time():
                return self._build_no_trade_decision(
                    symbol, correlation_id, "cached pre-filter rejection", start_time
                )

            pre_filter_reject, reject_reason = self._apply_pre_filters(research_summary)
            if pre_filter_reject:
                logger.info("Pre-filter rejection", symbol=symbol, reason=reject_reason)
                # Cache rejection for 5 minutes
                self._prefilter_cache[symbol] = time.time() + self._prefilter_cache_ttl
                return self._build_no_trade_decision(
                    symbol, correlation_id, reject_reason, start_time
                )

            # Learning 2: Symbol performance memory — logged as soft penalty
            # (Hard blocking creates death spiral with limited data)
            symbol_reject, symbol_reason = self._check_symbol_memory(symbol)
            if symbol_reject:
                logger.debug("Symbol penalty active (soft)", symbol=symbol, reason=symbol_reason)

            # Learning 4: Time-of-day profitability — logged but NOT a hard block
            # (Hard blocking creates death spiral when bot only runs limited hours)
            hour_reject, hour_reason = self._check_hour_profitability()
            if hour_reject:
                logger.debug("Hour penalty active (soft)", reason=hour_reason)

            # Evaluate all enabled strategies (with auto-disable filtering)
            strategy_signals = self._evaluate_all_strategies(research_summary)

            # Select best strategy and make decision (with learning adjustments)
            decision, strategy_id, confidence = self._make_decision(strategy_signals, research_summary)

            # ===== REGIME OVERRIDES =====
            regime_params = {}
            if self.regime_state:
                regime_data = self.regime_state.get()
                regime_name = regime_data.get("regime", "DEFAULT")
                regime_params = regime_data.get("params", {})

                if regime_name != "DEFAULT" and decision != "NO_TRADE":
                    # Direction bias enforcement
                    direction_bias = regime_params.get("direction_bias")
                    if direction_bias == "LONG" and decision == "SHORT":
                        logger.info("Regime direction filter", regime=regime_name, blocked=decision)
                        decision = "NO_TRADE"
                    elif direction_bias == "SHORT" and decision == "LONG":
                        logger.info("Regime direction filter", regime=regime_name, blocked=decision)
                        decision = "NO_TRADE"
                    elif direction_bias == "LONG_BIAS" and decision == "SHORT":
                        confidence -= 0.05  # Soft penalty for going against bias
                    elif direction_bias == "SHORT_BIAS" and decision == "LONG":
                        confidence -= 0.05

                    # Min confidence override from regime
                    regime_min_conf = regime_params.get("min_confidence")
                    if regime_min_conf and confidence < regime_min_conf and decision != "NO_TRADE":
                        logger.info(
                            "Regime confidence filter",
                            regime=regime_name,
                            confidence=f"{confidence:.0%}",
                            min_required=f"{regime_min_conf:.0%}",
                        )
                        decision = "NO_TRADE"

                    if regime_name != "DEFAULT":
                        logger.debug(
                            "Regime overrides applied",
                            regime=regime_name,
                            tp_mult=regime_params.get("tp_multiplier", 1.0),
                            sl_mult=regime_params.get("sl_multiplier", 1.0),
                            size_mult=regime_params.get("position_size_multiplier", 1.0),
                        )

            # Calculate entry/exit levels (with regime multipliers)
            entry_price, stop_loss, take_profit_levels = self._calculate_levels(
                research_summary,
                decision,
                strategy_id,
                regime_params=regime_params,
            )

            # Estimate position size (will be validated by Risk Manager)
            position_size_usdt = self._estimate_position_size(
                research_summary,
                entry_price,
                stop_loss
            )

            # Apply regime position size multiplier
            size_mult = regime_params.get("position_size_multiplier", 1.0)
            if size_mult != 1.0:
                position_size_usdt = round(position_size_usdt * size_mult, 2)

            # Fee viability check: reject trades where expected profit < fees * buffer
            if decision != "NO_TRADE" and self.fee_filter_enabled:
                fee_viable, fee_reason = self._check_fee_viability(
                    entry_price, take_profit_levels, position_size_usdt
                )
                if not fee_viable:
                    logger.info("Fee filter rejection", symbol=symbol, reason=fee_reason)
                    return self._build_no_trade_decision(
                        symbol, correlation_id, fee_reason, start_time
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

            # Expected holding time based on strategy (with regime multiplier)
            expected_holding_time = self._get_expected_holding_time(strategy_id)
            hold_mult = regime_params.get("hold_time_multiplier", 1.0)
            if hold_mult != 1.0:
                expected_holding_time = int(expected_holding_time * hold_mult)

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
        Learning 1: Skips strategies that are auto-disabled due to poor performance.

        Returns:
            Dict of strategy signals with confidence scores
        """
        signals = {}

        strategy_evaluators = {
            "ema_crossover_scalp": self._evaluate_ema_crossover,
            "vwap_bounce_scalp": self._evaluate_vwap_bounce,
            "orderbook_imbalance_scalp": self._evaluate_orderbook_imbalance,
            "momentum_breakout_scalp": self._evaluate_momentum_breakout,
            "rsi_pullback_scalp": self._evaluate_rsi_pullback,
            "bollinger_squeeze_scalp": self._evaluate_bollinger_squeeze,
        }

        for strategy_id, evaluator in strategy_evaluators.items():
            if strategy_id not in self.enabled_strategies:
                continue
            # Learning 1: Log poor performance but still evaluate (penalty applied later)
            if self._check_strategy_auto_disable(strategy_id):
                logger.debug("Strategy has poor performance, will apply penalty", strategy=strategy_id)
            signals[strategy_id] = evaluator(research_summary)

        return signals

    def _evaluate_ema_crossover(
        self,
        research_summary: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Strategy 1: EMA Crossover Scalp (with trend strength filter)

        Entry — ACTUAL CROSSOVER detection + trend strength:
        - LONG: EMA9 crosses above EMA21, price > EMA50, trend is strong
        - SHORT: EMA9 crosses below EMA21, price < EMA50, trend is strong

        Trend strength: EMA spread (|EMA9 - EMA21| / ATR) must exceed threshold
        to filter out noise crossovers in ranging markets.

        Confirmation:
        - Order book bias in same direction
        - RSI not extreme (30-70)
        - Higher timeframe trend alignment (required, not optional)
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
        atr = tech_ind.get("atr", 0) or (price * 0.01)
        htf_trend = tech_ind.get("htf_trend", "neutral")

        order_book = market_data.get("order_book", {})
        imbalance = order_book.get("imbalance_ratio", 0)

        signal = "neutral"
        confidence = 0.5

        # Trend strength: EMA spread normalized by ATR
        # Filters noise crossovers — only trade when EMAs are meaningfully apart
        ema_spread = abs(ema_9 - ema_21)
        trend_strength = ema_spread / atr if atr > 0 else 0
        if trend_strength < 0.15:  # EMAs too close = noise, not trend
            return {"signal": "neutral", "confidence": 0.5}

        # Bullish crossover: was below/equal, now above
        bullish_cross = prev_ema_9 <= prev_ema_21 and ema_9 > ema_21
        # Bearish crossover: was above/equal, now below
        bearish_cross = prev_ema_9 >= prev_ema_21 and ema_9 < ema_21

        # HTF trend: block contra-trend trades (bearish/weak_bearish blocks longs)
        htf_bearish = htf_trend in ("bearish", "weak_bearish")
        htf_bullish = htf_trend in ("bullish", "weak_bullish")

        if bullish_cross and price > ema_50 and not htf_bearish:
            if 40 < rsi < 65:
                signal = "long"
                confidence = 0.72 + (abs(imbalance) * 0.15)
                if htf_trend == "bullish":
                    confidence += 0.06  # Bonus for full alignment
                elif htf_trend == "weak_bullish":
                    confidence += 0.03
                if trend_strength > 0.4:
                    confidence += 0.04
                if imbalance > 0.1:
                    confidence += 0.03

        elif bearish_cross and price < ema_50 and not htf_bullish:
            if 35 < rsi < 60:
                signal = "short"
                confidence = 0.72 + (abs(imbalance) * 0.15)
                if htf_trend == "bearish":
                    confidence += 0.06
                elif htf_trend == "weak_bearish":
                    confidence += 0.03
                if trend_strength > 0.4:
                    confidence += 0.04
                if imbalance < -0.1:
                    confidence += 0.03

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
        - LONG: Price crosses above VWAP from below (actual bounce), HTF not bearish
        - SHORT: Price crosses below VWAP from above (actual bounce), HTF not bullish

        Confirmation:
        - RSI confirms direction (>50 for long, <50 for short)
        - Relative volume >= 1.2
        - Previous candle was on opposite side of VWAP (proves a cross happened)
        """
        tech_ind = research_summary.get("technical_indicators", {})
        market_data = research_summary.get("market_data", {})

        vwap = tech_ind.get("vwap", 0)
        price = market_data.get("price", 0)
        prev_close = tech_ind.get("prev_close", 0)
        rsi = tech_ind.get("rsi", 50)
        htf_trend = tech_ind.get("htf_trend", "neutral")
        relative_volume = tech_ind.get("relative_volume", 1.0)

        if vwap == 0 or price == 0:
            return {"signal": "neutral", "confidence": 0.0}

        # Volume gate: require meaningful activity
        if relative_volume < 1.2:
            return {"signal": "neutral", "confidence": 0.5}

        # Distance from VWAP — must be within tight zone (0.15%)
        distance_pct = (price - vwap) / vwap
        abs_dist = abs(distance_pct)

        if abs_dist > 0.0015:
            return {"signal": "neutral", "confidence": 0.5}

        signal = "neutral"
        confidence = 0.5

        # Graduated confidence: closer to VWAP = stronger signal
        proximity_bonus = 0.06 * (1 - abs_dist / 0.0015)

        # Determine if price crossed VWAP (prev candle was on other side)
        prev_dist = (prev_close - vwap) / vwap if prev_close > 0 and vwap > 0 else 0

        htf_bearish = htf_trend in ("bearish", "weak_bearish")
        htf_bullish = htf_trend in ("bullish", "weak_bullish")

        # LONG bounce: price was below VWAP, now at or above it
        if (prev_dist < -0.0003 and distance_pct >= 0
                and rsi > 50 and not htf_bearish):
            signal = "long"
            confidence = 0.68 + proximity_bonus
            if htf_trend == "bullish":
                confidence += 0.06
            elif htf_trend == "weak_bullish":
                confidence += 0.03
            if relative_volume >= 2.0:
                confidence += 0.04

        # SHORT bounce: price was above VWAP, now at or below it
        elif (prev_dist > 0.0003 and distance_pct <= 0
              and rsi < 50 and not htf_bullish):
            signal = "short"
            confidence = 0.68 + proximity_bonus
            if htf_trend == "bearish":
                confidence += 0.06
            elif htf_trend == "weak_bearish":
                confidence += 0.03
            if relative_volume >= 2.0:
                confidence += 0.04

        return {
            "signal": signal,
            "confidence": min(1.0, confidence)
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

    def _evaluate_rsi_pullback(
        self,
        research_summary: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Strategy 5: RSI Trend Pullback Scalp

        Buys oversold dips in confirmed uptrends, sells overbought rallies
        in confirmed downtrends. Classic trend-pullback mean reversion.

        Entry:
        - LONG: RSI 25-40 (oversold dip) + htf_trend bullish + price near EMA21
        - SHORT: RSI 60-75 (overbought rally) + htf_trend bearish + price near EMA21

        Confirmation:
        - MACD histogram showing reversal momentum
        - Price not too far from EMA21 (pullback, not crash)
        """
        tech_ind = research_summary.get("technical_indicators", {})
        market_data = research_summary.get("market_data", {})

        rsi = tech_ind.get("rsi", 50)
        ema_21 = tech_ind.get("ema_21", 0)
        ema_50 = tech_ind.get("ema_50", 0)
        price = market_data.get("price", 0)
        htf_trend = tech_ind.get("htf_trend", "neutral")
        macd_data = tech_ind.get("macd", {})
        histogram = macd_data.get("histogram", 0)

        order_book = market_data.get("order_book", {})
        imbalance = order_book.get("imbalance_ratio", 0)

        signal = "neutral"
        confidence = 0.5

        if price <= 0 or ema_21 <= 0:
            return {"signal": "neutral", "confidence": 0.5}

        # Distance from EMA21 — pullback should be close, not a crash
        dist_from_ema21 = (price - ema_21) / ema_21

        htf_bull = htf_trend in ("bullish", "weak_bullish")
        htf_bear = htf_trend in ("bearish", "weak_bearish")

        # LONG: oversold dip in uptrend
        if (28 < rsi < 45
                and htf_bull
                and ema_21 > ema_50  # Uptrend structure
                and -0.015 < dist_from_ema21 < 0.005  # Near or slightly below EMA21
                and histogram > -0.5):  # MACD not deeply bearish (recovering)
            signal = "long"
            confidence = 0.72
            if htf_trend == "bullish":
                confidence += 0.03  # Bonus for strong trend
            # RSI deeper = stronger pullback signal
            if rsi < 35:
                confidence += 0.06
            if imbalance > 0.1:  # Buyers stepping in
                confidence += 0.04

        # SHORT: overbought rally in downtrend
        elif (55 < rsi < 72
              and htf_bear
              and ema_21 < ema_50  # Downtrend structure
              and -0.005 < dist_from_ema21 < 0.015  # Near or slightly above EMA21
              and histogram < 0.5):  # MACD not deeply bullish
            signal = "short"
            confidence = 0.72
            if htf_trend == "bearish":
                confidence += 0.03
            if rsi > 65:
                confidence += 0.06
            if imbalance < -0.1:  # Sellers stepping in
                confidence += 0.04

        return {
            "signal": signal,
            "confidence": min(1.0, confidence)
        }

    def _evaluate_bollinger_squeeze(
        self,
        research_summary: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Strategy 6: Bollinger Band Squeeze Breakout

        Detects when Bollinger Bands contract (squeeze) then price breaks out.
        Squeeze = bandwidth < 3% and contracting (current < previous bandwidth).

        Entry:
        - LONG: price breaks above upper band + volume confirmation + HTF not bearish
        - SHORT: price breaks below lower band + volume confirmation + HTF not bullish

        Confirmation:
        - Relative volume >= 1.2
        - RSI confirms momentum direction
        - Trend alignment bonus
        """
        tech_ind = research_summary.get("technical_indicators", {})
        market_data = research_summary.get("market_data", {})

        bollinger = tech_ind.get("bollinger", {})
        if not bollinger:
            return {"signal": "neutral", "confidence": 0.5}

        upper = bollinger.get("upper", 0)
        lower = bollinger.get("lower", 0)
        bandwidth = bollinger.get("bandwidth", 10)
        prev_bandwidth = bollinger.get("prev_bandwidth", 10)
        pct_b = bollinger.get("pct_b", 0.5)

        price = market_data.get("price", 0)
        rsi = tech_ind.get("rsi", 50)
        htf_trend = tech_ind.get("htf_trend", "neutral")
        relative_volume = tech_ind.get("relative_volume", 1.0)

        signal = "neutral"
        confidence = 0.5

        # Squeeze detection: bandwidth < 3% AND contracting
        is_squeeze = bandwidth < 3.0 and bandwidth < prev_bandwidth

        if not is_squeeze:
            return {"signal": "neutral", "confidence": 0.5}

        # Volume gate
        if relative_volume < 1.2:
            return {"signal": "neutral", "confidence": 0.5}

        htf_bearish = htf_trend in ("bearish", "weak_bearish")
        htf_bullish = htf_trend in ("bullish", "weak_bullish")

        # Breakout above upper band
        if price > upper and pct_b > 1.0 and not htf_bearish:
            if rsi > 50:
                signal = "long"
                confidence = 0.72
                if htf_trend == "bullish":
                    confidence += 0.06
                elif htf_trend == "weak_bullish":
                    confidence += 0.03
                if relative_volume >= 2.0:
                    confidence += 0.04
                if rsi > 60:
                    confidence += 0.03

        # Breakout below lower band
        elif price < lower and pct_b < 0.0 and not htf_bullish:
            if rsi < 50:
                signal = "short"
                confidence = 0.72
                if htf_trend == "bearish":
                    confidence += 0.06
                elif htf_trend == "weak_bearish":
                    confidence += 0.03
                if relative_volume >= 2.0:
                    confidence += 0.04
                if rsi < 40:
                    confidence += 0.03

        return {
            "signal": signal,
            "confidence": min(1.0, confidence)
        }

    # ========== DECISION LOGIC ==========

    def _make_decision(
        self,
        strategy_signals: Dict[str, Dict[str, Any]],
        research_summary: Optional[Dict[str, Any]] = None
    ) -> Tuple[str, str, float]:
        """
        Make final trading decision based on strategy signals.
        Applies all learning adjustments as soft confidence penalties.

        Returns:
            (decision, strategy_id, confidence)
        """
        market_regime = ""
        if research_summary:
            market_regime = research_summary.get("market_regime", "")

        # Soft penalties from learning (applied to all signals)
        hour_penalty = 0.0
        hour_reject, _ = self._check_hour_profitability()
        if hour_reject:
            hour_penalty = 0.05  # -5% for bad hours (soft, not blocking)

        # Find best signal after learning adjustments
        best_strategy = None
        best_signal = "neutral"
        best_confidence = 0.0

        for strategy_id, signal_data in strategy_signals.items():
            signal = signal_data["signal"]
            confidence = signal_data["confidence"]

            # Learning 3: Adjust confidence based on strategy's historical win rate
            confidence = self._apply_strategy_confidence_adjustment(strategy_id, confidence)

            # Learning 4: Hour penalty (soft)
            confidence = max(0.0, confidence - hour_penalty)

            # Learning 6: Adjust confidence based on strategy+regime historical fit
            if market_regime:
                confidence = self._apply_regime_adjustment(strategy_id, market_regime, confidence)

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
        strategy_id: str,
        regime_params: Optional[Dict[str, Any]] = None
    ) -> Tuple[float, float, List[Dict[str, Any]]]:
        """
        Calculate entry, stop loss, and take profit levels.

        Uses configurable ATR multipliers with minimum R:R enforcement.
        Regime multipliers scale the config values when active.

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

        # Read configurable multipliers from config
        risk_config = self.config.get("risk", {})
        sl_mult = risk_config.get("sl_atr_multiplier", 1.0)
        tp_mult = risk_config.get("tp_atr_multiplier", 2.5)
        min_rr = risk_config.get("min_rr_ratio", 2.0)
        min_sl_dist_pct = risk_config.get("min_sl_distance_pct", 0.003)

        # Apply regime multipliers (scale config values, don't replace)
        if regime_params:
            sl_mult *= regime_params.get("sl_multiplier", 1.0)
            tp_mult *= regime_params.get("tp_multiplier", 1.0)

        # Entry: current market price
        entry_price = price

        # Stop loss distance: SL multiplier * ATR, with minimum floor
        sl_distance = atr * sl_mult
        min_sl_distance = entry_price * min_sl_dist_pct
        sl_distance = max(sl_distance, min_sl_distance)

        # Take profit distance: leverage-based or ATR-based
        leverage_based_tp = risk_config.get("leverage_based_tp", False)
        if leverage_based_tp:
            # TP = SL distance * leverage (R:R naturally equals leverage)
            tp_distance = sl_distance * self.default_leverage
            # Regime TP multiplier scales on top (tp_mult not used in this path)
            if regime_params:
                tp_distance *= regime_params.get("tp_multiplier", 1.0)
        else:
            # ATR-based TP (regime multiplier already applied to tp_mult above)
            tp_distance = atr * tp_mult
        tp_distance = max(tp_distance, sl_distance * min_rr)

        if decision == "LONG":
            stop_loss = entry_price - sl_distance
            take_profit_levels = [
                {"price": entry_price + tp_distance, "quantity_pct": 1.0},
            ]
        else:  # SHORT
            stop_loss = entry_price + sl_distance
            take_profit_levels = [
                {"price": entry_price - tp_distance, "quantity_pct": 1.0},
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
        if entry_price <= 0:
            return 0.0
        stop_distance_pct = abs((entry_price - stop_loss) / entry_price)

        if stop_distance_pct > 0:
            position_size = risk_amount / stop_distance_pct
        else:
            position_size = assumed_equity * 0.5  # Default: 50% of equity

        # Cap position size per-position (allow room for multiple concurrent positions)
        leverage = self.default_leverage
        max_concurrent = self.config.get("trading", {}).get("max_concurrent_positions", 3)
        max_exposure_pct = self.config.get("risk", {}).get("max_portfolio_exposure_pct", 0.55)
        per_position_pct = max_exposure_pct / max_concurrent
        max_notional = assumed_equity * per_position_pct * leverage
        position_size = min(position_size, max_notional)

        # Enforce Binance Futures minimum notional ($100)
        min_notional = 100.0
        position_size = max(position_size, min_notional)

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
            "rsi_pullback_scalp": 180,       # 3 minutes
            "bollinger_squeeze_scalp": 240,  # 4 minutes
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
            "momentum_breakout_scalp": "Momentum Breakout",
            "rsi_pullback_scalp": "RSI Trend Pullback",
            "bollinger_squeeze_scalp": "Bollinger Squeeze Breakout",
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

    def _check_fee_viability(
        self,
        entry_price: float,
        take_profit_levels: List[Dict[str, Any]],
        position_size_usdt: float
    ) -> Tuple[bool, str]:
        """
        Check if expected profit exceeds round-trip fees * buffer multiplier.
        Returns (is_viable, reason).
        """
        if not take_profit_levels or entry_price <= 0 or position_size_usdt <= 0:
            return True, ""

        # Round-trip taker fees (entry + exit)
        round_trip_fee_rate = self.taker_bps / 10000 * 2
        round_trip_fees = position_size_usdt * round_trip_fee_rate

        # Expected profit from first TP level
        tp_price = take_profit_levels[0]["price"]
        tp_distance_pct = abs(tp_price - entry_price) / entry_price
        expected_profit = position_size_usdt * tp_distance_pct

        min_required = round_trip_fees * self.fee_buffer_multiplier

        if expected_profit < min_required:
            reason = (
                f"fee_filter: expected profit ${expected_profit:.4f} < "
                f"${min_required:.4f} (fees ${round_trip_fees:.4f} x {self.fee_buffer_multiplier}x buffer)"
            )
            return False, reason

        return True, ""

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

    # ========== LEARNING FROM PAST TRADES ==========

    def _refresh_learning_cache(self):
        """Refresh learning data from DB every 5 minutes."""
        if not self.db_session:
            return

        now = time.time()
        if now - self._learning_cache_time < self._learning_cache_ttl:
            return  # Cache still fresh

        try:
            with self.db_session.session_scope() as session:
                queries = DatabaseQueries(session)

                # Strategy stats (win_rate, avg_win, avg_loss per strategy)
                strategy_stats = {}
                for strat in self.enabled_strategies:
                    stats = queries.get_strategy_full_stats(
                        strat, lookback_days=7, min_trades=self.strategy_auto_disable_min_trades
                    )
                    if stats:
                        strategy_stats[strat] = stats

                # Hourly performance
                hourly_perf = queries.get_hourly_performance(lookback_days=7, min_trades_per_hour=5)

                self._learning_cache = {
                    "strategy_stats": strategy_stats,
                    "hourly_performance": hourly_perf,
                    "symbol_cache": {},  # Populated on-demand per symbol
                    "regime_cache": {},  # Populated on-demand per strategy+regime
                }
                self._learning_cache_time = now

                # Log learning summary
                for strat, stats in strategy_stats.items():
                    logger.info(
                        "Learning: strategy stats",
                        strategy=strat,
                        win_rate=f"{stats['win_rate']:.0%}",
                        trades=stats["total_trades"],
                        pnl=f"${stats['total_pnl']:.2f}",
                    )
                if hourly_perf:
                    losing_hours = [h for h, s in hourly_perf.items() if s["win_rate"] < self.hour_min_win_rate]
                    if losing_hours:
                        logger.info("Learning: low-performance hours", hours=sorted(losing_hours))

        except Exception as e:
            logger.warning("Failed to refresh learning cache", error=str(e))

    def _get_symbol_performance(self, symbol: str) -> Optional[Dict[str, Any]]:
        """Get symbol performance from cache or DB."""
        if not self.db_session:
            return None

        cache = self._learning_cache.get("symbol_cache", {})
        if symbol in cache:
            return cache[symbol]

        try:
            with self.db_session.session_scope() as session:
                queries = DatabaseQueries(session)
                stats = queries.get_symbol_performance(
                    symbol, lookback_days=7, min_trades=self.symbol_min_trades
                )
                cache[symbol] = stats
                return stats
        except Exception:
            return None

    def _get_regime_performance(self, strategy_id: str, market_regime: str) -> Optional[Dict[str, Any]]:
        """Get strategy+regime performance from cache or DB."""
        if not self.db_session:
            return None

        cache_key = f"{strategy_id}:{market_regime}"
        cache = self._learning_cache.get("regime_cache", {})
        if cache_key in cache:
            return cache[cache_key]

        try:
            with self.db_session.session_scope() as session:
                queries = DatabaseQueries(session)
                stats = queries.get_strategy_regime_performance(
                    strategy_id, market_regime, lookback_days=14, min_trades=5
                )
                cache[cache_key] = stats
                return stats
        except Exception:
            return None

    def _check_strategy_auto_disable(self, strategy_id: str) -> bool:
        """
        Learning 1: Auto-disable strategy if win rate is too low.
        Returns True if strategy should be SKIPPED.
        """
        stats = self._learning_cache.get("strategy_stats", {}).get(strategy_id)
        if not stats:
            return False  # Not enough data, allow strategy

        if stats["win_rate"] < self.strategy_auto_disable_win_rate and stats["total_pnl"] < 0:
            logger.info(
                "Learning: strategy auto-disabled",
                strategy=strategy_id,
                win_rate=f"{stats['win_rate']:.0%}",
                pnl=f"${stats['total_pnl']:.2f}",
                trades=stats["total_trades"],
            )
            return True
        return False

    def _check_symbol_memory(self, symbol: str) -> Tuple[bool, str]:
        """
        Learning 2: Avoid symbols that consistently lose money.
        Returns (should_reject, reason).
        """
        stats = self._get_symbol_performance(symbol)
        if not stats:
            return False, ""

        if stats["win_rate"] < self.symbol_min_win_rate and stats["total_pnl"] < 0:
            reason = (
                f"Symbol learning: {symbol} has {stats['win_rate']:.0%} win rate "
                f"over {stats['total_trades']} trades (PnL: ${stats['total_pnl']:.2f})"
            )
            return True, reason

        return False, ""

    def _check_hour_profitability(self) -> Tuple[bool, str]:
        """
        Learning 4: Avoid trading during historically unprofitable hours.
        Returns (should_reject, reason).
        """
        hourly = self._learning_cache.get("hourly_performance", {})
        if not hourly:
            return False, ""

        utc_hour = datetime.now(timezone.utc).hour
        hour_stats = hourly.get(utc_hour)
        if not hour_stats:
            return False, ""

        if hour_stats["win_rate"] < self.hour_min_win_rate and hour_stats["total_pnl"] < 0:
            reason = (
                f"Hour learning: UTC {utc_hour}:00 has {hour_stats['win_rate']:.0%} win rate "
                f"over {hour_stats['total_trades']} trades (PnL: ${hour_stats['total_pnl']:.2f})"
            )
            return True, reason

        return False, ""

    def _apply_strategy_confidence_adjustment(self, strategy_id: str, confidence: float) -> float:
        """
        Learning 3: Adjust confidence based on strategy's historical win rate.
        Boost strategies with proven edge, penalize weak ones.
        """
        stats = self._learning_cache.get("strategy_stats", {}).get(strategy_id)
        if not stats:
            return confidence

        win_rate = stats["win_rate"]

        # Strong performer: boost confidence
        if win_rate >= 0.55 and stats["total_pnl"] > 0:
            boost = min((win_rate - 0.50) * 0.3, 0.10)  # Max +10%
            return min(1.0, confidence + boost)

        # Weak performer: penalize confidence
        if win_rate < 0.40:
            penalty = min((0.40 - win_rate) * 0.5, 0.15)  # Max -15%
            return max(0.0, confidence - penalty)

        return confidence

    def _apply_regime_adjustment(self, strategy_id: str, market_regime: str, confidence: float) -> float:
        """
        Learning 6: Adjust confidence based on strategy+regime historical fit.
        """
        stats = self._get_regime_performance(strategy_id, market_regime)
        if not stats:
            return confidence

        win_rate = stats["win_rate"]

        # Good regime fit: boost
        if win_rate >= 0.55 and stats["total_pnl"] > 0:
            return min(1.0, confidence + self.regime_confidence_boost)

        # Bad regime fit: penalize
        if win_rate < 0.35 and stats["total_pnl"] < 0:
            return max(0.0, confidence - self.regime_confidence_penalty)

        return confidence

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
