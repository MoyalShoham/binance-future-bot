"""
Market Regime Detection via LLM

Background thread that classifies market regime every 15 minutes.
Uses Claude Opus to analyze BTC/ETH market data and classify into
one of 9 regimes. Trading Decision Agent reads the current regime
and applies multipliers to strategy parameters.

Regimes and their parameter multipliers:
- TREND_FOLLOWING: TP 3.0x, SL 0.8x, size 1.0x, min_conf 0.80, hold 1.5x
- MEAN_REVERSION: TP 1.5x, SL 1.2x, size 1.0x, min_conf 0.75, hold 0.8x
- HIGH_VOLATILITY: TP 2.0x, SL 1.5x, size 0.6x, min_conf 0.85, hold 0.5x
- GREED_EUPHORIA: TP 3.0x, SL 1.0x, size 0.8x, min_conf 0.80, hold 1.0x (LONG only)
- FEAR_CAPITULATION: TP 3.0x, SL 1.0x, size 0.8x, min_conf 0.80, hold 1.0x (SHORT only)
- LOW_VOLATILITY: TP 1.2x, SL 1.0x, size 1.0x, min_conf 0.85, hold 0.8x
- ACCUMULATION: TP 2.5x, SL 1.0x, size 0.8x, min_conf 0.75, hold 1.2x (LONG bias)
- DISTRIBUTION: TP 2.5x, SL 1.0x, size 0.8x, min_conf 0.75, hold 1.2x (SHORT bias)
- DEFAULT: no modifications (config values used as-is)
"""

import json
import time
import threading
from typing import Dict, Any, Optional
from datetime import datetime
import structlog

logger = structlog.get_logger()

# Regime parameter definitions
REGIME_PARAMS = {
    "TREND_FOLLOWING": {
        "direction_bias": None,
        "tp_multiplier": 3.0,
        "sl_multiplier": 0.8,
        "position_size_multiplier": 1.0,
        "min_confidence": 0.80,
        "hold_time_multiplier": 1.5,
    },
    "MEAN_REVERSION": {
        "direction_bias": None,
        "tp_multiplier": 1.5,
        "sl_multiplier": 1.2,
        "position_size_multiplier": 1.0,
        "min_confidence": 0.75,
        "hold_time_multiplier": 0.8,
    },
    "HIGH_VOLATILITY": {
        "direction_bias": None,
        "tp_multiplier": 2.0,
        "sl_multiplier": 1.5,
        "position_size_multiplier": 0.6,
        "min_confidence": 0.85,
        "hold_time_multiplier": 0.5,
    },
    "GREED_EUPHORIA": {
        "direction_bias": "LONG",
        "tp_multiplier": 3.0,
        "sl_multiplier": 1.0,
        "position_size_multiplier": 0.8,
        "min_confidence": 0.80,
        "hold_time_multiplier": 1.0,
    },
    "FEAR_CAPITULATION": {
        "direction_bias": "SHORT",
        "tp_multiplier": 3.0,
        "sl_multiplier": 1.0,
        "position_size_multiplier": 0.8,
        "min_confidence": 0.80,
        "hold_time_multiplier": 1.0,
    },
    "LOW_VOLATILITY": {
        "direction_bias": None,
        "tp_multiplier": 1.2,
        "sl_multiplier": 1.0,
        "position_size_multiplier": 1.0,
        "min_confidence": 0.85,
        "hold_time_multiplier": 0.8,
    },
    "ACCUMULATION": {
        "direction_bias": "LONG_BIAS",
        "tp_multiplier": 2.5,
        "sl_multiplier": 1.0,
        "position_size_multiplier": 0.8,
        "min_confidence": 0.75,
        "hold_time_multiplier": 1.2,
    },
    "DISTRIBUTION": {
        "direction_bias": "SHORT_BIAS",
        "tp_multiplier": 2.5,
        "sl_multiplier": 1.0,
        "position_size_multiplier": 0.8,
        "min_confidence": 0.75,
        "hold_time_multiplier": 1.2,
    },
    "DEFAULT": {
        "direction_bias": None,
        "tp_multiplier": 1.0,
        "sl_multiplier": 1.0,
        "position_size_multiplier": 1.0,
        "min_confidence": None,  # Use config default
        "hold_time_multiplier": 1.0,
    },
}


class RegimeState:
    """Thread-safe singleton holding the current market regime classification."""

    def __init__(self):
        self._lock = threading.Lock()
        self._state = {
            "regime": "DEFAULT",
            "params": REGIME_PARAMS["DEFAULT"],
            "confidence": 0.0,
            "reasoning": "No classification yet",
            "last_updated": None,
        }

    def update(self, regime: str, confidence: float, reasoning: str):
        """Update the current regime state."""
        params = REGIME_PARAMS.get(regime, REGIME_PARAMS["DEFAULT"])
        with self._lock:
            self._state = {
                "regime": regime,
                "params": params,
                "confidence": confidence,
                "reasoning": reasoning,
                "last_updated": datetime.utcnow().isoformat(),
            }

    def get(self) -> Dict[str, Any]:
        """Get current regime state (thread-safe copy)."""
        with self._lock:
            return dict(self._state)

    @property
    def regime(self) -> str:
        with self._lock:
            return self._state["regime"]

    @property
    def params(self) -> Dict[str, Any]:
        with self._lock:
            return dict(self._state["params"])


class RegimeDetector:
    """
    Background thread that classifies market regime every N seconds.

    Uses Anthropic API directly (not ModelRouter) since this is a
    fixed-schedule task, not part of the agent pipeline.
    """

    def __init__(
        self,
        binance_client,
        config: Dict[str, Any],
        regime_state: RegimeState,
        db_session=None,
    ):
        self.binance_client = binance_client
        self.config = config
        self.regime_state = regime_state
        self.db_session = db_session

        regime_config = config.get("regime_detection", {})
        self.enabled = regime_config.get("enabled", False)
        self.interval_seconds = regime_config.get("interval_seconds", 900)
        self.model = regime_config.get("model", "claude-haiku-4-5-20251001")
        self.fallback_regime = regime_config.get("fallback_regime", "DEFAULT")

        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()

    def start(self):
        """Start the background regime detection thread."""
        if not self.enabled:
            logger.info("Regime detection disabled via config")
            return

        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run_loop,
            name="regime-detector",
            daemon=True,
        )
        self._thread.start()
        logger.info(
            "Regime detector started",
            interval_seconds=self.interval_seconds,
            model=self.model,
        )

    def stop(self):
        """Stop the background thread."""
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5)
            logger.info("Regime detector stopped")

    def _run_loop(self):
        """Main loop: classify regime every interval_seconds."""
        # Run first classification immediately
        self._classify_and_update()

        while not self._stop_event.is_set():
            self._stop_event.wait(self.interval_seconds)
            if self._stop_event.is_set():
                break
            self._classify_and_update()

    def _classify_and_update(self):
        """Gather context, call LLM, update regime state."""
        try:
            context = self._gather_market_context()
            result = self._call_llm(context)

            if result:
                regime = result.get("regime", self.fallback_regime)
                confidence = result.get("confidence", 0.0)
                reasoning = result.get("reasoning", "")

                # Validate regime name
                if regime not in REGIME_PARAMS:
                    logger.warning("Unknown regime from LLM, using fallback", regime=regime)
                    regime = self.fallback_regime

                self.regime_state.update(regime, confidence, reasoning)

                logger.info(
                    "Regime classified",
                    regime=regime,
                    confidence=f"{confidence:.0%}",
                    reasoning=reasoning[:100],
                )
            else:
                self.regime_state.update(self.fallback_regime, 0.0, "LLM call failed")
                logger.warning("Regime classification failed, using fallback")

        except Exception as e:
            logger.error("Regime detection error", error=str(e), exc_info=True)
            self.regime_state.update(self.fallback_regime, 0.0, f"Error: {str(e)[:100]}")

    def _gather_market_context(self) -> Dict[str, Any]:
        """Gather market context for regime classification."""
        context = {}

        # BTC data
        try:
            btc_ticker = self.binance_client.get_24h_ticker("BTCUSDT")
            btc_klines = self.binance_client.get_klines("BTCUSDT", "1h", limit=24)
            btc_funding = self.binance_client.get_funding_rate("BTCUSDT")

            # Calculate simple indicators from klines
            if btc_klines and len(btc_klines) >= 14:
                closes = [k["close"] for k in btc_klines]
                highs = [k["high"] for k in btc_klines]
                lows = [k["low"] for k in btc_klines]

                # ATR
                true_ranges = []
                for i in range(1, len(btc_klines)):
                    tr = max(
                        highs[i] - lows[i],
                        abs(highs[i] - closes[i - 1]),
                        abs(lows[i] - closes[i - 1]),
                    )
                    true_ranges.append(tr)
                atr_14 = sum(true_ranges[-14:]) / 14 if len(true_ranges) >= 14 else 0

                # RSI (14-period)
                gains, losses = [], []
                for i in range(1, len(closes)):
                    change = closes[i] - closes[i - 1]
                    gains.append(max(change, 0))
                    losses.append(max(-change, 0))
                avg_gain = sum(gains[-14:]) / 14 if len(gains) >= 14 else 0
                avg_loss = sum(losses[-14:]) / 14 if len(losses) >= 14 else 0.001
                rs = avg_gain / avg_loss if avg_loss > 0 else 100
                rsi = 100 - (100 / (1 + rs))

                # Simple EMAs (approx using last N closes)
                ema_9 = sum(closes[-9:]) / min(9, len(closes))
                ema_21 = sum(closes[-21:]) / min(21, len(closes))

                context["btc_data"] = {
                    "price": btc_ticker.get("last_price"),
                    "price_change_24h_pct": btc_ticker.get("price_change_percent"),
                    "volume_24h": btc_ticker.get("quote_volume"),
                    "funding_rate": btc_funding.get("funding_rate", 0),
                    "rsi_14": round(rsi, 1),
                    "atr_14": round(atr_14, 2),
                    "ema_9": round(ema_9, 2),
                    "ema_21": round(ema_21, 2),
                    "high_24h": btc_ticker.get("high_price"),
                    "low_24h": btc_ticker.get("low_price"),
                }
            else:
                context["btc_data"] = {"price": btc_ticker.get("last_price"), "error": "insufficient kline data"}

        except Exception as e:
            context["btc_data"] = {"error": str(e)[:100]}

        # ETH data
        try:
            eth_ticker = self.binance_client.get_24h_ticker("ETHUSDT")
            eth_funding = self.binance_client.get_funding_rate("ETHUSDT")
            context["eth_data"] = {
                "price": eth_ticker.get("last_price"),
                "price_change_24h_pct": eth_ticker.get("price_change_percent"),
                "volume_24h": eth_ticker.get("quote_volume"),
                "funding_rate": eth_funding.get("funding_rate", 0),
            }
        except Exception as e:
            context["eth_data"] = {"error": str(e)[:100]}

        # Recent trading performance from DB
        context["recent_performance"] = {"note": "no DB data"}
        if self.db_session:
            try:
                from infrastructure.database.queries import DatabaseQueries
                with self.db_session.session_scope() as session:
                    queries = DatabaseQueries(session)
                    # Last 24h stats
                    from datetime import timedelta
                    start = datetime.utcnow() - timedelta(hours=24)
                    end = datetime.utcnow()
                    total_pnl = queries.calculate_total_pnl(start, end)
                    open_positions = queries.get_open_positions()
                    context["recent_performance"] = {
                        "pnl_24h_usdt": round(total_pnl, 2),
                        "open_positions": len(open_positions),
                    }
            except Exception as e:
                context["recent_performance"] = {"error": str(e)[:100]}

        context["timestamp"] = datetime.utcnow().isoformat()

        return context

    def _call_llm(self, context: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Call Anthropic API for regime classification."""
        try:
            import anthropic
            from prompts.regime_detector import REGIME_DETECTOR_SYSTEM, REGIME_DETECTOR_USER_TEMPLATE

            # Build user prompt
            user_prompt = REGIME_DETECTOR_USER_TEMPLATE.format(
                btc_data=json.dumps(context.get("btc_data", {}), indent=2, default=str),
                eth_data=json.dumps(context.get("eth_data", {}), indent=2, default=str),
                recent_performance=json.dumps(context.get("recent_performance", {}), indent=2, default=str),
                timestamp=context.get("timestamp", ""),
            )

            client = anthropic.Anthropic()
            response = client.messages.create(
                model=self.model,
                max_tokens=1024,
                temperature=0.1,
                system=REGIME_DETECTOR_SYSTEM,
                messages=[{"role": "user", "content": user_prompt}],
            )

            # Parse response
            text = response.content[0].text.strip()
            # Remove markdown code fences if present
            if text.startswith("```"):
                text = text.split("\n", 1)[1] if "\n" in text else text[3:]
                if text.endswith("```"):
                    text = text[:-3]
                text = text.strip()

            result = json.loads(text)
            return result

        except json.JSONDecodeError as e:
            logger.warning("Failed to parse regime LLM response", error=str(e))
            return None
        except Exception as e:
            logger.warning("Regime LLM call failed", error=str(e))
            return None
