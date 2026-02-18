"""
Historical Data Loader

Downloads and caches kline data from Binance for backtesting.
Stores data as parquet files in data/historical/.
"""

import os
import time
from typing import List, Dict, Any, Optional
from datetime import datetime, timezone
import pandas as pd
import structlog

logger = structlog.get_logger()

# Binance kline interval to milliseconds
INTERVAL_MS = {
    "1m": 60_000,
    "3m": 180_000,
    "5m": 300_000,
    "15m": 900_000,
    "30m": 1_800_000,
    "1h": 3_600_000,
    "4h": 14_400_000,
    "1d": 86_400_000,
}


class HistoricalDataLoader:
    """
    Downloads klines from Binance with pagination and caches as parquet.

    Usage:
        loader = HistoricalDataLoader(binance_client)
        df = loader.load_klines("BTCUSDT", "5m", "2025-08-01", "2026-02-01")
    """

    def __init__(self, binance_client, cache_dir: str = "data/historical"):
        self.client = binance_client
        self.cache_dir = cache_dir
        os.makedirs(cache_dir, exist_ok=True)

    def _cache_path(self, symbol: str, interval: str, start: str, end: str) -> str:
        """Generate cache file path."""
        safe_start = start.replace("-", "")
        safe_end = end.replace("-", "")
        return os.path.join(self.cache_dir, f"{symbol}_{interval}_{safe_start}_{safe_end}.parquet")

    def load_klines(
        self,
        symbol: str,
        interval: str,
        start: str,
        end: str,
        force_download: bool = False
    ) -> pd.DataFrame:
        """
        Load klines from cache or download from Binance.

        Args:
            symbol: Trading pair (e.g. "BTCUSDT")
            interval: Candle interval (e.g. "5m", "1h")
            start: Start date (YYYY-MM-DD)
            end: End date (YYYY-MM-DD)
            force_download: Re-download even if cached

        Returns:
            DataFrame with columns: timestamp, open, high, low, close, volume
        """
        cache_path = self._cache_path(symbol, interval, start, end)

        if not force_download and os.path.exists(cache_path):
            logger.info("Loading from cache", path=cache_path)
            return pd.read_parquet(cache_path)

        logger.info("Downloading klines", symbol=symbol, interval=interval, start=start, end=end)
        klines = self._download_klines(symbol, interval, start, end)

        if not klines:
            logger.warning("No klines downloaded", symbol=symbol)
            return pd.DataFrame()

        df = pd.DataFrame(klines)
        df = df[["timestamp", "open", "high", "low", "close", "volume"]]
        for col in ["open", "high", "low", "close", "volume"]:
            df[col] = df[col].astype(float)

        # Save to cache
        df.to_parquet(cache_path, index=False)
        logger.info("Cached klines", path=cache_path, rows=len(df))

        return df

    def _download_klines(
        self,
        symbol: str,
        interval: str,
        start: str,
        end: str
    ) -> List[Dict[str, Any]]:
        """Download klines with pagination (Binance returns max 1500 per request)."""
        start_ts = int(datetime.strptime(start, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp() * 1000)
        end_ts = int(datetime.strptime(end, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp() * 1000)

        interval_ms = INTERVAL_MS.get(interval, 300_000)
        all_klines = []
        current_start = start_ts
        batch_size = 1000

        while current_start < end_ts:
            try:
                klines = self.client.get_klines(
                    symbol=symbol,
                    interval=interval,
                    limit=batch_size,
                    start_time=current_start,
                    end_time=end_ts,
                )

                if not klines:
                    break

                all_klines.extend(klines)

                # Move to next batch
                last_ts = klines[-1]["timestamp"]
                current_start = last_ts + interval_ms

                # Rate limit: Binance allows 1200 req/min for klines
                time.sleep(0.1)

                logger.debug(
                    "Downloaded batch",
                    symbol=symbol,
                    candles=len(klines),
                    total=len(all_klines),
                )

            except Exception as e:
                logger.error("Download failed", symbol=symbol, error=str(e))
                time.sleep(1)
                break

        # Deduplicate by timestamp
        seen = set()
        unique = []
        for k in all_klines:
            ts = k["timestamp"]
            if ts not in seen and ts < end_ts:
                seen.add(ts)
                unique.append(k)

        unique.sort(key=lambda x: x["timestamp"])
        logger.info("Download complete", symbol=symbol, total_candles=len(unique))
        return unique
