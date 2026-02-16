"""
Research Coordinator Agent Implementation

Orchestrates market data collection, technical analysis, and sentiment analysis.
Produces normalized Research Summary JSON for Trading Decision Agent.
"""

from typing import Dict, Any, Optional, List
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
import uuid
import structlog

from .base_agent import BaseAgent
from infrastructure.binance_api import BinanceFuturesClient
from infrastructure.binance_api.indicators import TechnicalIndicators
from schemas.validator import SchemaValidator

logger = structlog.get_logger()


class ResearchCoordinatorAgent(BaseAgent):
    """
    Research Coordinator Agent

    Responsibilities:
    - Fetch market data from Binance (price, volume, funding, order book)
    - Calculate technical indicators (EMA, RSI, MACD, ATR, volatility)
    - Analyze market sentiment (if data available)
    - Classify market regime (trending/ranging/high volatility)
    - Detect significant news/events (if applicable)
    - Produce normalized Research Summary JSON

    Authority Boundaries:
    ✅ Query all market data sources
    ✅ Calculate technical indicators
    ✅ Classify market conditions
    ❌ Make trading decisions
    ❌ Execute trades
    ❌ Modify risk parameters

    Design:
    - Deterministic data collection
    - Parallel execution of sub-tasks (if needed)
    - Schema-compliant output
    - Observable (all data sources logged)
    """

    def __init__(
        self,
        agent_id: str,
        config: Dict[str, Any],
        binance_client: BinanceFuturesClient,
        model_router=None,
        ws_manager=None
    ):
        """
        Initialize Research Coordinator Agent.

        Args:
            agent_id: Agent identifier
            config: System configuration
            binance_client: Binance API client
            model_router: Optional ModelRouter for LLM enhancement
            ws_manager: Optional BinanceWebSocketManager for cached market data
        """
        super().__init__(agent_id, config, model_router=model_router)

        self.binance_client = binance_client
        self.ws_manager = ws_manager
        self.validator = SchemaValidator()

        # Technical indicators calculator
        self.indicators = TechnicalIndicators()

        # Default timeframe from config
        self.default_timeframe = config.get("trading", {}).get("scalping", {}).get("timeframes", ["5m"])[0]

        # Lookback periods for indicators
        self.lookback_periods = 100  # Number of candles for indicator calculation

        logger.debug(
            "Research Coordinator Agent initialized",
            agent_id=self.agent_id,
            default_timeframe=self.default_timeframe
        )

    def execute(self, state: Dict[str, Any]) -> Dict[str, Any]:
        """
        Main execution: Gather market data and produce Research Summary.

        Args:
            state: Pipeline state with symbol and optional parameters

        Returns:
            Research Summary JSON (conforms to research_summary.schema.json)
        """
        symbol = state.get("symbol")
        timeframe = state.get("timeframe", self.default_timeframe)
        correlation_id = state.get("correlation_id", str(uuid.uuid4()))

        logger.debug(
            "Research Coordinator started",
            correlation_id=correlation_id,
            symbol=symbol,
            timeframe=timeframe
        )

        start_time = datetime.utcnow()

        try:
            # Step 1+2: Fetch market data and klines in parallel
            market_data, technical_indicators = self._fetch_all_data(symbol, timeframe)

            # Step 3: Analyze sentiment (simplified - can be enhanced with external APIs)
            sentiment = self._analyze_sentiment(symbol, market_data)

            # Step 4: Classify market regime
            market_regime = self._classify_market_regime(technical_indicators, market_data)

            # Step 5: Detect news/events (simplified - placeholder for future enhancement)
            news_events = self._detect_news_events(symbol)

            # Step 6: Generate warnings based on market conditions
            warnings = self._generate_warnings(market_data, technical_indicators)

            # Step 7: Calculate time decay factor (freshness)
            time_decay_factor = 1.0  # Fresh data

            # Step 8: LLM Enhancement (optional - graceful fallback to rule-based)
            llm_enhancement = self._get_llm_insights(
                symbol, market_data, technical_indicators, sentiment, market_regime, warnings
            )

            if llm_enhancement:
                # Append any LLM-detected warnings
                for w in llm_enhancement.get("additional_warnings", []):
                    if w and w not in warnings:
                        warnings.append(f"[LLM] {w}")

            # Build Research Summary
            research_summary = {
                "schema_version": "1.0.0",
                "agent_id": self.agent_id,
                "correlation_id": correlation_id,
                "timestamp": datetime.utcnow().isoformat(),
                "symbol": symbol,
                "timeframe": timeframe,
                "analysis_timestamp": datetime.utcnow().isoformat(),
                "market_data": market_data,
                "technical_indicators": technical_indicators,
                "sentiment": sentiment,
                "market_regime": market_regime,
                "news_events": news_events,
                "warnings": warnings,
                "time_decay_factor": time_decay_factor
            }

            # Add LLM enhancement if available
            if llm_enhancement:
                research_summary["llm_enhancement"] = {
                    "enhanced_sentiment": llm_enhancement.get("enhanced_sentiment"),
                    "pattern_insights": llm_enhancement.get("pattern_insights", []),
                    "regime_reasoning": llm_enhancement.get("regime_reasoning", ""),
                    "additional_warnings": llm_enhancement.get("additional_warnings", []),
                    "key_levels": llm_enhancement.get("key_levels"),
                    "model_used": llm_enhancement.get("_model_used", "unknown"),
                    "llm_confidence": llm_enhancement.get("confidence", 0.0),
                }

            # Validate against schema
            is_valid = self.validator.validate_message(
                research_summary,
                "research_summary",
                strict=True
            )

            if not is_valid:
                logger.error("Research summary failed schema validation")
                raise ValueError("Research summary does not conform to schema")

            processing_time = (datetime.utcnow() - start_time).total_seconds() * 1000

            logger.debug(
                "Research Coordinator completed",
                correlation_id=correlation_id,
                symbol=symbol,
                market_regime=market_regime,
                volatility_pct=technical_indicators.get("volatility_pct"),
                processing_time_ms=processing_time
            )

            return research_summary

        except Exception as e:
            logger.error(
                "Research Coordinator failed",
                correlation_id=correlation_id,
                symbol=symbol,
                error=str(e),
                exc_info=True
            )
            raise

    # ========== DATA COLLECTION ==========

    def _fetch_all_data(
        self,
        symbol: str,
        timeframe: str
    ) -> tuple:
        """
        Fetch market data and klines in parallel using ThreadPoolExecutor.

        Runs all 5 Binance API calls concurrently instead of sequentially,
        reducing data collection time from ~1-2s to ~300-400ms.

        Returns:
            Tuple of (market_data dict, technical_indicators dict)
        """
        # Map scalp timeframe → higher timeframe for trend bias
        HTF_MAP = {"1m": "15m", "5m": "1h", "15m": "4h"}
        higher_tf = HTF_MAP.get(timeframe, "1h")

        try:
            # Try WebSocket cache for ticker and book data (near-zero latency)
            ws_ticker = None
            ws_book = None
            if self.ws_manager:
                ws_ticker = self.ws_manager.get_ticker(symbol)
                ws_book = self.ws_manager.get_book_ticker(symbol)
                if ws_ticker:
                    logger.debug("Using cached ticker data", symbol=symbol)
                if ws_book:
                    logger.debug("Using cached book ticker data", symbol=symbol)

            with ThreadPoolExecutor(max_workers=6) as pool:
                # Only fetch ticker/book via REST if WebSocket cache is stale
                ticker_fut = None
                book_fut = None
                if not ws_ticker:
                    ticker_fut = pool.submit(
                        self.binance_client.client.futures_ticker, symbol=symbol
                    )
                if not ws_book:
                    book_fut = pool.submit(
                        self.binance_client.get_order_book, symbol, limit=100
                    )

                funding_fut = pool.submit(
                    self.binance_client.client.futures_funding_rate, symbol=symbol, limit=1
                )
                oi_fut = pool.submit(
                    self.binance_client.client.futures_open_interest, symbol=symbol
                )
                klines_fut = pool.submit(
                    self.binance_client.get_klines,
                    symbol=symbol, interval=timeframe, limit=self.lookback_periods
                )
                htf_klines_fut = pool.submit(
                    self.binance_client.get_klines,
                    symbol=symbol, interval=higher_tf, limit=50
                )

            # Collect results
            funding_rate_data = funding_fut.result()
            open_interest_data = oi_fut.result()
            klines = klines_fut.result()
            htf_klines = htf_klines_fut.result()

            # Build market data from WS cache or REST fallback
            if ws_ticker:
                current_price = ws_ticker["price"]
                volume_24h = ws_ticker["volume_24h"]
                # price_change_24h_pct not available from mini ticker — compute from open
                open_price = ws_ticker.get("open", current_price)
                price_change_pct = (current_price - open_price) / open_price if open_price > 0 else 0
            else:
                ticker = ticker_fut.result()
                current_price = float(ticker.get("lastPrice", 0))
                volume_24h = float(ticker.get("quoteVolume", 0))
                price_change_pct = float(ticker.get("priceChangePercent", 0)) / 100

            if ws_book:
                # Compute top-of-book imbalance from bid/ask quantities
                bid_qty = ws_book.get("best_bid_qty", 0)
                ask_qty = ws_book.get("best_ask_qty", 0)
                total_qty = bid_qty + ask_qty
                ws_imbalance = (bid_qty - ask_qty) / total_qty if total_qty > 0 else 0

                order_book_data = {
                    "bid_depth": bid_qty,
                    "ask_depth": ask_qty,
                    "imbalance_ratio": round(ws_imbalance, 4),
                    "best_bid": ws_book["best_bid"],
                    "best_ask": ws_book["best_ask"],
                    "spread_bps": ws_book["spread_bps"],
                }
            else:
                order_book_data = book_fut.result()

            market_data = {
                "price": current_price,
                "volume_24h": volume_24h,
                "price_change_24h_pct": price_change_pct,
                "funding_rate": float(funding_rate_data[0].get("fundingRate", 0)) if funding_rate_data else 0,
                "open_interest": float(open_interest_data.get("openInterest", 0)) * current_price,
                "order_book": order_book_data,
            }

            # Build technical indicators from klines
            if klines and len(klines) >= 50:
                kline_price = float(klines[-1]["close"])
                technical_indicators = self.indicators.calculate_all(klines, kline_price)
            else:
                logger.warning(f"Insufficient kline data for {symbol}")
                technical_indicators = self._get_default_indicators()

            # Higher timeframe trend bias (multi-TF confirmation)
            # Relaxed: require EMA9 vs EMA21 alignment (not all 3 perfectly aligned)
            htf_trend = "neutral"
            if htf_klines and len(htf_klines) >= 50:
                htf_indicators = self.indicators.calculate_all(htf_klines, current_price)
                htf_ema9 = htf_indicators.get("ema_9", 0)
                htf_ema21 = htf_indicators.get("ema_21", 0)
                htf_ema50 = htf_indicators.get("ema_50", 0)
                if htf_ema9 > htf_ema21 and htf_ema21 > htf_ema50:
                    htf_trend = "bullish"
                elif htf_ema9 < htf_ema21 and htf_ema21 < htf_ema50:
                    htf_trend = "bearish"
                elif htf_ema9 > htf_ema21:
                    htf_trend = "weak_bullish"
                elif htf_ema9 < htf_ema21:
                    htf_trend = "weak_bearish"
            technical_indicators["htf_trend"] = htf_trend
            technical_indicators["htf_timeframe"] = higher_tf

            logger.debug(
                "Market data fetched (parallel)",
                symbol=symbol,
                price=current_price,
                volume_24h=market_data["volume_24h"],
                funding_rate=market_data["funding_rate"],
                rsi=technical_indicators.get("rsi"),
            )

            return market_data, technical_indicators

        except Exception as e:
            logger.error(f"Failed to fetch market data: {e}")
            raise

    def _get_default_indicators(self) -> Dict[str, Any]:
        """Return default indicators when calculation fails."""
        return {
            "ema_9": 0,
            "ema_21": 0,
            "ema_50": 0,
            "vwap": 0,
            "rsi": 50,
            "macd": {
                "macd_line": 0,
                "signal_line": 0,
                "histogram": 0
            },
            "atr": 0,
            "volatility_pct": 0.03  # Default 3%
        }

    # ========== ANALYSIS ==========

    def _analyze_sentiment(
        self,
        symbol: str,
        market_data: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Analyze market sentiment.

        Simplified implementation - can be enhanced with:
        - News sentiment APIs
        - Social media sentiment
        - On-chain metrics

        For now, derive from price action and order book.
        """
        # Derive sentiment from price change
        price_change = market_data.get("price_change_24h_pct", 0)

        # Derive from order book imbalance
        order_book = market_data.get("order_book", {})
        imbalance = order_book.get("imbalance_ratio", 0)

        # Combine signals
        # Price change: positive = bullish, negative = bearish
        # Imbalance: positive = bullish, negative = bearish
        overall_score = (price_change + imbalance) / 2
        overall_score = max(-1, min(1, overall_score))  # Clamp to [-1, 1]

        # Confidence based on consistency
        confidence = abs(price_change) * 0.5 + abs(imbalance) * 0.5
        confidence = min(1.0, confidence)

        sentiment = {
            "overall_score": round(overall_score, 3),
            "confidence": round(confidence, 3),
            "sources": {
                "news_sentiment": 0,  # Placeholder for future enhancement
                "social_sentiment": 0,  # Placeholder
                "whale_activity": "neutral"  # Placeholder
            }
        }

        return sentiment

    def _classify_market_regime(
        self,
        technical_indicators: Dict[str, Any],
        market_data: Dict[str, Any]
    ) -> str:
        """
        Classify current market regime.

        Regimes:
        - trending_up: Strong uptrend
        - trending_down: Strong downtrend
        - ranging: Sideways market
        - high_volatility: Volatile conditions
        - low_liquidity: Thin order book
        """
        # Get indicators
        ema_9 = technical_indicators.get("ema_9", 0)
        ema_21 = technical_indicators.get("ema_21", 0)
        ema_50 = technical_indicators.get("ema_50", 0)
        volatility_pct = technical_indicators.get("volatility_pct", 0.03)
        rsi = technical_indicators.get("rsi", 50)

        # Get market data
        volume_24h = market_data.get("volume_24h", 0)

        # High volatility check
        if volatility_pct > 0.05:  # > 5%
            return "high_volatility"

        # Low liquidity check (simplified)
        if volume_24h < 100000000:  # < $100M 24h volume
            return "low_liquidity"

        # Trend detection using EMA alignment
        if ema_9 > ema_21 > ema_50:
            # All EMAs aligned upward
            if rsi > 60:
                return "trending_up"
        elif ema_9 < ema_21 < ema_50:
            # All EMAs aligned downward
            if rsi < 40:
                return "trending_down"

        # Default to ranging
        return "ranging"

    def _detect_news_events(
        self,
        symbol: str
    ) -> List[Dict[str, Any]]:
        """
        Detect significant news events.

        Placeholder for future enhancement with news APIs.
        Could integrate:
        - CryptoPanic API
        - Twitter/X API
        - Binance announcements
        - CoinMarketCal
        """
        # Placeholder - return empty for now
        return []

    def _generate_warnings(
        self,
        market_data: Dict[str, Any],
        technical_indicators: Dict[str, Any]
    ) -> List[str]:
        """
        Generate risk warnings based on market conditions.
        """
        warnings = []

        # High volatility warning
        volatility_pct = technical_indicators.get("volatility_pct", 0)
        if volatility_pct > 0.08:  # > 8%
            warnings.append(f"EXTREME VOLATILITY: {volatility_pct:.2%}")
        elif volatility_pct > 0.05:  # > 5%
            warnings.append(f"High volatility: {volatility_pct:.2%}")

        # Extreme funding rate
        funding_rate = market_data.get("funding_rate", 0)
        if abs(funding_rate) > 0.001:  # > 0.1%
            warnings.append(f"Extreme funding rate: {funding_rate:.4%}")

        # Low volume warning
        volume_24h = market_data.get("volume_24h", 0)
        if volume_24h < 50000000:  # < $50M
            warnings.append(f"Low 24h volume: ${volume_24h:,.0f}")

        # Extreme RSI
        rsi = technical_indicators.get("rsi", 50)
        if rsi > 80:
            warnings.append(f"RSI overbought: {rsi:.1f}")
        elif rsi < 20:
            warnings.append(f"RSI oversold: {rsi:.1f}")

        return warnings

    # ========== LLM ENHANCEMENT ==========

    def _get_llm_insights(
        self,
        symbol: str,
        market_data: Dict[str, Any],
        technical_indicators: Dict[str, Any],
        sentiment: Dict[str, Any],
        market_regime: str,
        warnings: List[str]
    ) -> Optional[Dict[str, Any]]:
        """
        Call LLM for enhanced market analysis insights.

        Returns parsed LLM response or None if LLM unavailable/disabled.
        """
        from orchestration.model_router import TaskType
        from prompts.base import build_prompt
        from prompts.research_coordinator import RESEARCH_COORDINATOR_SYSTEM

        context_data = {
            "symbol": symbol,
            "market_data": market_data,
            "technical_indicators": technical_indicators,
            "rule_based_sentiment": sentiment,
            "rule_based_regime": market_regime,
            "current_warnings": warnings,
        }

        system_prompt, user_prompt = build_prompt(RESEARCH_COORDINATOR_SYSTEM, context_data)

        result = self.call_llm(
            task_type=TaskType.PATTERN_MATCHING,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            context="research",
            max_escalations=1,
        )

        if result and result.get("response"):
            response = result["response"]
            response["_model_used"] = result.get("model_used", "unknown")
            logger.debug(
                "LLM research enhancement completed",
                model=result.get("model_used"),
                llm_confidence=response.get("confidence"),
            )
            return response

        return None
