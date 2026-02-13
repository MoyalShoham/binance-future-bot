"""
WebSocket Streaming Manager for Binance Futures

Provides near-zero latency market data via WebSocket streams,
with REST fallback when data is stale.

Streams subscribed:
- Mini ticker: price, volume (per symbol)
- Book ticker: best bid/ask (per symbol)

Klines are NOT streamed — REST is used for indicator calculation
(WebSocket only provides the latest candle, we need 100+).
"""

import time
import asyncio
import threading
import warnings
from typing import Dict, Any, Optional, Set
import structlog

logger = structlog.get_logger()


class WebSocketDataCache:
    """Thread-safe cache for WebSocket data with staleness detection."""

    def __init__(self, stale_threshold_seconds: float = 10.0):
        self._data: Dict[str, Dict[str, Any]] = {}
        self._lock = threading.Lock()
        self.stale_threshold = stale_threshold_seconds

    def update(self, key: str, data: Dict[str, Any]):
        """Update cache entry with timestamp."""
        with self._lock:
            self._data[key] = {
                "data": data,
                "updated_at": time.time(),
            }

    def get(self, key: str) -> Optional[Dict[str, Any]]:
        """Get cache entry if not stale. Returns None if stale or missing."""
        with self._lock:
            entry = self._data.get(key)
            if entry is None:
                return None
            if time.time() - entry["updated_at"] > self.stale_threshold:
                return None
            return entry["data"]

    def get_age(self, key: str) -> float:
        """Get age of cache entry in seconds. Returns inf if missing."""
        with self._lock:
            entry = self._data.get(key)
            if entry is None:
                return float("inf")
            return time.time() - entry["updated_at"]

    def clear(self):
        with self._lock:
            self._data.clear()


class BinanceWebSocketManager:
    """
    Manages Binance Futures WebSocket streams for real-time market data.

    Wraps binance.ThreadedWebsocketManager for:
    - Mini ticker streams (price, volume)
    - Book ticker streams (best bid/ask, spread)

    Auto-reconnects on disconnect. All data stored in WebSocketDataCache.
    """

    def __init__(self, api_key: str, api_secret: str, config: Dict[str, Any]):
        self.api_key = api_key
        self.api_secret = api_secret
        self.config = config

        ws_config = config.get("websocket", {})
        self.enabled = ws_config.get("enabled", True)
        stale_threshold = ws_config.get("stale_threshold_seconds", 10)

        self.cache = WebSocketDataCache(stale_threshold)
        self._twm = None
        self._subscribed_symbols: Set[str] = set()
        self._stream_keys: list = []
        self._started = False

    def start(self):
        """Start the WebSocket manager."""
        if not self.enabled:
            logger.info("WebSocket manager disabled via config")
            return

        try:
            from binance import ThreadedWebsocketManager
            self._twm = ThreadedWebsocketManager(
                api_key=self.api_key,
                api_secret=self.api_secret,
            )
            self._twm.start()
            self._started = True
            logger.info("WebSocket manager started")
        except Exception as e:
            logger.error("Failed to start WebSocket manager", error=str(e))
            self._started = False

    def subscribe(self, symbols: list):
        """Subscribe to streams for the given symbols."""
        if not self._started or not self._twm:
            return

        for symbol in symbols:
            if symbol in self._subscribed_symbols:
                continue

            symbol_lower = symbol.lower()

            try:
                # Symbol ticker stream (futures): price + volume + 24h stats
                key = self._twm.start_symbol_ticker_futures_socket(
                    callback=self._on_mini_ticker,
                    symbol=symbol_lower,
                )
                self._stream_keys.append(key)

                # Book ticker stream: best bid/ask
                key = self._twm.start_symbol_book_ticker_socket(
                    callback=self._on_book_ticker,
                    symbol=symbol_lower,
                )
                self._stream_keys.append(key)

                self._subscribed_symbols.add(symbol)
                logger.info("WebSocket subscribed", symbol=symbol)

            except Exception as e:
                logger.warning("Failed to subscribe WebSocket", symbol=symbol, error=str(e))

    def stop(self):
        """Stop all WebSocket streams.

        Suppresses 'fail_connection' AttributeError from python-binance/websockets
        version mismatch (cosmetic error during shutdown, no data impact).
        """
        if self._twm and self._started:
            try:
                # Suppress asyncio "Task exception was never retrieved" warnings
                # caused by python-binance calling fail_connection() on newer websockets
                loop = getattr(self._twm, '_loop', None)
                if loop and hasattr(loop, 'set_exception_handler'):
                    def _shutdown_exception_handler(loop, context):
                        exc = context.get("exception")
                        if isinstance(exc, AttributeError) and "fail_connection" in str(exc):
                            return  # Suppress known shutdown error
                        loop.default_exception_handler(context)
                    try:
                        loop.call_soon_threadsafe(loop.set_exception_handler, _shutdown_exception_handler)
                    except RuntimeError:
                        pass  # Loop already closed

                self._twm.stop()
                logger.info("WebSocket manager stopped")
            except Exception as e:
                logger.warning("Error stopping WebSocket manager", error=str(e))
            finally:
                self._started = False
                self._subscribed_symbols.clear()
                self._stream_keys.clear()

    # ========== Cache Access Methods ==========

    def get_ticker(self, symbol: str) -> Optional[Dict[str, Any]]:
        """Get cached ticker data (price, volume). Returns None if stale."""
        return self.cache.get(f"ticker:{symbol}")

    def get_book_ticker(self, symbol: str) -> Optional[Dict[str, Any]]:
        """Get cached book ticker (best bid/ask/spread). Returns None if stale."""
        return self.cache.get(f"book:{symbol}")

    def get_price(self, symbol: str) -> Optional[float]:
        """Get cached price. Returns None if stale."""
        ticker = self.get_ticker(symbol)
        if ticker:
            return ticker.get("price")
        return None

    # ========== WebSocket Callbacks ==========

    def _on_mini_ticker(self, msg: Dict[str, Any]):
        """Handle futures symbol ticker stream message."""
        # ThreadedWebsocketManager wraps data in {"stream": ..., "data": ...}
        if "data" in msg:
            msg = msg["data"]

        if msg.get("e") == "error":
            logger.debug("WebSocket ticker queue overflow (non-critical)")
            return

        try:
            symbol = msg.get("s", "")
            price = float(msg.get("c", 0))  # Close/last price
            if price <= 0 or not symbol:
                return  # Skip invalid data
            volume = float(msg.get("q", 0))  # Quote volume 24h

            self.cache.update(f"ticker:{symbol}", {
                "price": price,
                "volume_24h": volume,
                "high": float(msg.get("h", 0)),
                "low": float(msg.get("l", 0)),
                "open": float(msg.get("o", 0)),
            })
        except (ValueError, KeyError) as e:
            logger.debug("Failed to parse ticker", error=str(e))

    def _on_book_ticker(self, msg: Dict[str, Any]):
        """Handle book ticker stream message."""
        if "data" in msg:
            msg = msg["data"]

        if msg.get("e") == "error":
            logger.debug("WebSocket book ticker queue overflow (non-critical)")
            return

        try:
            symbol = msg.get("s", "")
            best_bid = float(msg.get("b", 0))
            best_ask = float(msg.get("a", 0))
            mid_price = (best_bid + best_ask) / 2 if (best_bid + best_ask) > 0 else 1
            spread_bps = ((best_ask - best_bid) / mid_price) * 10000 if mid_price > 0 else 0

            self.cache.update(f"book:{symbol}", {
                "best_bid": best_bid,
                "best_ask": best_ask,
                "best_bid_qty": float(msg.get("B", 0)),
                "best_ask_qty": float(msg.get("A", 0)),
                "spread_bps": round(spread_bps, 2),
            })
        except (ValueError, KeyError) as e:
            logger.debug("Failed to parse book ticker", error=str(e))
