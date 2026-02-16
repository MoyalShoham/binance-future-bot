"""
Technical Indicators Calculator

Calculates technical indicators using TA library.
"""

from typing import Dict, Any, List
from datetime import datetime, timezone
import pandas as pd
import numpy as np
import ta
import structlog

logger = structlog.get_logger()


class TechnicalIndicators:
    """
    Calculates technical indicators from OHLCV data.
    """

    def calculate_all(
        self,
        klines: List[Dict[str, Any]],
        current_price: float
    ) -> Dict[str, Any]:
        """
        Calculate all technical indicators.

        Args:
            klines: List of OHLCV dicts from Binance
            current_price: Current market price

        Returns:
            Dict of all calculated indicators
        """
        # Convert to DataFrame
        df = pd.DataFrame(klines)

        # Rename columns for TA library
        df = df.rename(columns={
            "open": "Open",
            "high": "High",
            "low": "Low",
            "close": "Close",
            "volume": "Volume"
        })

        # Calculate indicators
        indicators = {}

        # EMAs
        indicators["ema_9"] = round(ta.trend.ema_indicator(df["Close"], window=9).iloc[-1], 2)
        indicators["ema_21"] = round(ta.trend.ema_indicator(df["Close"], window=21).iloc[-1], 2)
        indicators["ema_50"] = round(ta.trend.ema_indicator(df["Close"], window=50).iloc[-1], 2)

        # VWAP — reset daily at 00:00 UTC (institutional standard)
        # Filter klines to only current UTC day, then compute cumulative VWAP
        if "timestamp" in df.columns:
            df["_dt"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
            today_utc = datetime.now(timezone.utc).date()
            today_mask = df["_dt"].dt.date == today_utc
            if today_mask.any():
                day_df = df.loc[today_mask]
            else:
                # If no candles from today (e.g. very long timeframes), use last 20 candles
                day_df = df.tail(20)
        else:
            day_df = df.tail(20)
        typical_price = (day_df["High"] + day_df["Low"] + day_df["Close"]) / 3
        vol_sum = day_df["Volume"].sum()
        indicators["vwap"] = round((typical_price * day_df["Volume"]).sum() / vol_sum, 2) if vol_sum > 0 else round(df["Close"].iloc[-1], 2)

        # RSI
        indicators["rsi"] = round(ta.momentum.rsi(df["Close"], window=14).iloc[-1], 2)

        # MACD
        macd = ta.trend.MACD(df["Close"])
        indicators["macd"] = {
            "macd_line": round(macd.macd().iloc[-1], 2),
            "signal_line": round(macd.macd_signal().iloc[-1], 2),
            "histogram": round(macd.macd_diff().iloc[-1], 2)
        }

        # Bollinger Bands (20-period, 2 std dev)
        bb = ta.volatility.BollingerBands(df["Close"], window=20, window_dev=2)
        bb_upper = bb.bollinger_hband().iloc[-1]
        bb_lower = bb.bollinger_lband().iloc[-1]
        bb_middle = bb.bollinger_mavg().iloc[-1]
        bb_bandwidth = (bb_upper - bb_lower) / bb_middle * 100 if bb_middle > 0 else 0
        bb_pct_b = (df["Close"].iloc[-1] - bb_lower) / (bb_upper - bb_lower) if (bb_upper - bb_lower) > 0 else 0.5

        # Previous bandwidth for squeeze detection (contracting check)
        prev_bb_bandwidth = 0
        if len(df) >= 3:
            prev_upper = bb.bollinger_hband().iloc[-2]
            prev_lower = bb.bollinger_lband().iloc[-2]
            prev_mid = bb.bollinger_mavg().iloc[-2]
            prev_bb_bandwidth = (prev_upper - prev_lower) / prev_mid * 100 if prev_mid > 0 else 0

        indicators["bollinger"] = {
            "upper": round(float(bb_upper), 2),
            "lower": round(float(bb_lower), 2),
            "middle": round(float(bb_middle), 2),
            "bandwidth": round(float(bb_bandwidth), 4),
            "pct_b": round(float(bb_pct_b), 4),
            "prev_bandwidth": round(float(prev_bb_bandwidth), 4),
        }

        # ATR (Average True Range) - use 6 decimal places to avoid rounding to 0 for low-price coins
        indicators["atr"] = round(ta.volatility.average_true_range(
            df["High"],
            df["Low"],
            df["Close"],
            window=14
        ).iloc[-1], 6)

        # Volatility — normalized to hourly equivalent for consistent threshold comparison
        # Per-candle std is meaningless without knowing timeframe, so we annualize to hourly.
        # Detect candle interval from timestamps (ms); fallback to 5 min.
        returns = df["Close"].pct_change().dropna()
        per_candle_std = float(returns.std()) if len(returns) > 1 else 0.0
        candle_minutes = 5  # default
        if "timestamp" in df.columns and len(df) >= 2:
            delta_ms = float(df["timestamp"].iloc[-1] - df["timestamp"].iloc[-2])
            candle_minutes = max(1, delta_ms / 60000)
        candles_per_hour = 60.0 / candle_minutes
        # Scale: hourly vol = per_candle_vol * sqrt(candles_per_hour)
        hourly_volatility_pct = round(per_candle_std * np.sqrt(candles_per_hour), 6)
        indicators["volatility_pct"] = hourly_volatility_pct

        # Relative volume: current candle volume vs 20-candle average
        if len(df) >= 21:
            avg_vol_20 = df["Volume"].iloc[-21:-1].mean()
            current_vol = df["Volume"].iloc[-1]
            indicators["relative_volume"] = round(current_vol / avg_vol_20, 2) if avg_vol_20 > 0 else 1.0
        else:
            indicators["relative_volume"] = 1.0

        # Previous candle EMAs for crossover detection
        if len(df) >= 3:
            ema9_series = ta.trend.ema_indicator(df["Close"], window=9)
            ema21_series = ta.trend.ema_indicator(df["Close"], window=21)
            indicators["prev_ema_9"] = round(ema9_series.iloc[-2], 2)
            indicators["prev_ema_21"] = round(ema21_series.iloc[-2], 2)
        else:
            indicators["prev_ema_9"] = indicators["ema_9"]
            indicators["prev_ema_21"] = indicators["ema_21"]

        # Previous candle close for VWAP bounce crossover detection
        if len(df) >= 2:
            indicators["prev_close"] = round(float(df["Close"].iloc[-2]), 6)
        else:
            indicators["prev_close"] = indicators.get("ema_21", 0)

        logger.debug("Technical indicators calculated", ema_9=indicators["ema_9"], rsi=indicators["rsi"], hourly_vol=hourly_volatility_pct)

        return indicators

    def calculate_ema(self, prices: List[float], period: int) -> float:
        """
        Calculate Exponential Moving Average.

        Args:
            prices: List of prices
            period: EMA period

        Returns:
            EMA value
        """
        df = pd.DataFrame({"price": prices})
        ema = ta.trend.ema_indicator(df["price"], window=period)
        return round(ema.iloc[-1], 2)

    def calculate_rsi(self, prices: List[float], period: int = 14) -> float:
        """
        Calculate Relative Strength Index.

        Args:
            prices: List of prices
            period: RSI period (default: 14)

        Returns:
            RSI value (0-100)
        """
        df = pd.DataFrame({"price": prices})
        rsi = ta.momentum.rsi(df["price"], window=period)
        return round(rsi.iloc[-1], 2)

    def calculate_bollinger_bands(
        self,
        prices: List[float],
        period: int = 20,
        std_dev: float = 2.0
    ) -> Dict[str, float]:
        """
        Calculate Bollinger Bands.

        Args:
            prices: List of prices
            period: Period for moving average
            std_dev: Standard deviation multiplier

        Returns:
            Dict with upper, middle, and lower bands
        """
        df = pd.DataFrame({"price": prices})

        bb = ta.volatility.BollingerBands(
            df["price"],
            window=period,
            window_dev=std_dev
        )

        return {
            "upper": round(bb.bollinger_hband().iloc[-1], 2),
            "middle": round(bb.bollinger_mavg().iloc[-1], 2),
            "lower": round(bb.bollinger_lband().iloc[-1], 2)
        }

    def calculate_support_resistance(
        self,
        klines: List[Dict[str, Any]],
        lookback: int = 20
    ) -> Dict[str, List[float]]:
        """
        Calculate support and resistance levels.

        Simple implementation using pivot points.

        Args:
            klines: List of OHLCV dicts
            lookback: Number of candles to analyze

        Returns:
            Dict with support and resistance levels
        """
        recent_klines = klines[-lookback:]

        highs = [k["high"] for k in recent_klines]
        lows = [k["low"] for k in recent_klines]
        closes = [k["close"] for k in recent_klines]

        # Pivot point
        pivot = (max(highs) + min(lows) + closes[-1]) / 3

        # Resistance levels
        r1 = 2 * pivot - min(lows)
        r2 = pivot + (max(highs) - min(lows))

        # Support levels
        s1 = 2 * pivot - max(highs)
        s2 = pivot - (max(highs) - min(lows))

        return {
            "resistance": [round(r2, 2), round(r1, 2)],
            "support": [round(s1, 2), round(s2, 2)],
            "pivot": round(pivot, 2)
        }
