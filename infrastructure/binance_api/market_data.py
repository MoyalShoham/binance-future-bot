"""
Market Data Fetcher

Aggregates market data from Binance Futures API for trading decisions.
"""

from typing import Dict, Any, List
from datetime import datetime
import structlog

from .client import BinanceFuturesClient
from .indicators import TechnicalIndicators

logger = structlog.get_logger()


class MarketDataFetcher:
    """
    Fetches and aggregates market data for trading analysis.
    """

    def __init__(self, client: BinanceFuturesClient):
        """
        Initialize market data fetcher.

        Args:
            client: Binance Futures client instance
        """
        self.client = client
        self.indicators = TechnicalIndicators()

    def fetch_complete_market_data(
        self,
        symbol: str,
        timeframe: str = "5m"
    ) -> Dict[str, Any]:
        """
        Fetch complete market data including price, volume, order book, and indicators.

        Args:
            symbol: Trading symbol (e.g., "BTCUSDT")
            timeframe: Analysis timeframe (default: "5m")

        Returns:
            Complete market data dict conforming to research_summary requirements
        """
        logger.info("Fetching complete market data", symbol=symbol, timeframe=timeframe)

        # Get current price
        current_price = self.client.get_ticker_price(symbol)

        # Get 24h ticker stats
        ticker_24h = self.client.get_24h_ticker(symbol)

        # Get order book
        order_book = self.client.get_order_book(symbol, limit=10)

        # Get funding rate
        funding_rate_info = self.client.get_funding_rate(symbol)

        # Get OHLCV data for indicators
        klines = self.client.get_klines(symbol, timeframe, limit=100)

        # Calculate technical indicators
        technical_indicators = self.indicators.calculate_all(klines, current_price)

        # Build market data structure
        market_data = {
            "market_data": {
                "price": current_price,
                "volume_24h": ticker_24h["quote_volume"],
                "price_change_24h_pct": ticker_24h["price_change_percent"],
                "funding_rate": funding_rate_info["funding_rate"],
                "open_interest": 0,  # TODO: Implement open interest fetching
                "order_book": {
                    "bid_depth": order_book["bid_depth"],
                    "ask_depth": order_book["ask_depth"],
                    "imbalance_ratio": order_book["imbalance_ratio"]
                }
            },
            "technical_indicators": technical_indicators
        }

        logger.info("Market data fetched successfully", symbol=symbol)

        return market_data

    def fetch_multi_timeframe_data(
        self,
        symbol: str,
        timeframes: List[str] = None
    ) -> Dict[str, Dict[str, Any]]:
        """
        Fetch market data across multiple timeframes.

        Args:
            symbol: Trading symbol
            timeframes: List of timeframes (default: ["1m", "5m", "15m"])

        Returns:
            Dict of market data per timeframe
        """
        if timeframes is None:
            timeframes = ["1m", "5m", "15m"]

        multi_tf_data = {}

        for tf in timeframes:
            try:
                multi_tf_data[tf] = self.fetch_complete_market_data(symbol, tf)
            except Exception as e:
                logger.error(f"Failed to fetch {tf} data", symbol=symbol, error=str(e))
                multi_tf_data[tf] = None

        return multi_tf_data
