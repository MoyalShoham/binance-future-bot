"""
Technical Indicators Calculator

Calculates technical indicators using TA library.
"""

from typing import Dict, Any, List
import pandas as pd
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

        # VWAP (approximation using typical price)
        typical_price = (df["High"] + df["Low"] + df["Close"]) / 3
        indicators["vwap"] = round((typical_price * df["Volume"]).sum() / df["Volume"].sum(), 2)

        # RSI
        indicators["rsi"] = round(ta.momentum.rsi(df["Close"], window=14).iloc[-1], 2)

        # MACD
        macd = ta.trend.MACD(df["Close"])
        indicators["macd"] = {
            "macd_line": round(macd.macd().iloc[-1], 2),
            "signal_line": round(macd.macd_signal().iloc[-1], 2),
            "histogram": round(macd.macd_diff().iloc[-1], 2)
        }

        # ATR (Average True Range)
        indicators["atr"] = round(ta.volatility.average_true_range(
            df["High"],
            df["Low"],
            df["Close"],
            window=14
        ).iloc[-1], 2)

        # Volatility (standard deviation of returns)
        returns = df["Close"].pct_change()
        volatility_pct = round(returns.std() * 100, 2)
        indicators["volatility_pct"] = volatility_pct

        logger.debug("Technical indicators calculated", ema_9=indicators["ema_9"], rsi=indicators["rsi"])

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
