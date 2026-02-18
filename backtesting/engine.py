"""
Backtest Engine

Walks through historical candles bar-by-bar, evaluates strategies,
simulates fills at next candle open, and tracks positions with TP/SL/trailing.
"""

import copy
from typing import Dict, Any, List, Optional
from datetime import datetime, timezone, timedelta
import numpy as np
import pandas as pd
import ta
import structlog

from infrastructure.binance_api.indicators import TechnicalIndicators
from .metrics import MetricsCalculator

logger = structlog.get_logger()


class BacktestEngine:
    """
    Backtests trading strategies on historical data.

    Reuses existing TechnicalIndicators.calculate_all() and strategy
    evaluation methods. Simulates fills, fees, and TP/SL exits.

    Usage:
        engine = BacktestEngine(config)
        results = engine.run("BTCUSDT", "5m", klines_df, strategies=["ema_crossover_scalp"])
    """

    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.indicators = TechnicalIndicators()

        # Fee config
        fee_config = config.get("execution", {}).get("fees", {})
        self.taker_bps = fee_config.get("taker_bps", 5)
        self.slippage_bps = config.get("execution", {}).get("paper_trading", {}).get("base_slippage_bps", 5)

        # Risk config
        risk_config = config.get("risk", {})
        self.max_risk_pct = risk_config.get("max_risk_per_trade_pct", 0.01)
        self.sl_atr_mult = risk_config.get("sl_atr_multiplier", 0.8)
        self.tp_atr_mult = risk_config.get("tp_atr_multiplier", 2.0)
        self.min_rr = risk_config.get("min_rr_ratio", 2.5)
        self.min_sl_pct = risk_config.get("min_sl_distance_pct", 0.004)

        # Fee filter
        fee_filter = fee_config.get("pre_trade_fee_filter", {})
        self.fee_buffer_mult = fee_filter.get("fee_buffer_multiplier", 3.0)

        # Trading config
        trading_config = config.get("trading", {})
        self.leverage = trading_config.get("default_leverage", 5)
        self.max_holding_seconds = trading_config.get("scalping", {}).get("max_holding_time_seconds", 900)

        # Max exposure from config
        self.max_exposure_pct = risk_config.get("max_portfolio_exposure_pct", 0.55)

        # Trailing stop config
        ts_config = config.get("trailing_stop", {})
        dsl = ts_config.get("dynamic_sl", {})
        self.dsl_breakeven_pct = dsl.get("breakeven_move_pct", 0.004)
        self.dsl_trail_pct = dsl.get("trail_activation_pct", 0.008)
        self.dsl_trail_step = dsl.get("trail_step_pct", 0.003)

        # HTF trend cache
        self._htf_trend_cache = {"trend": "neutral", "last_bar": -1}

    def run(
        self,
        symbol: str,
        interval: str,
        klines_df: pd.DataFrame,
        strategies: Optional[List[str]] = None,
        initial_capital: float = 125.0,
        min_confidence: float = 0.70,
    ) -> Dict[str, Any]:
        """
        Run backtest on historical data.

        Args:
            symbol: Trading pair
            interval: Candle interval
            klines_df: DataFrame with columns: timestamp, open, high, low, close, volume
            strategies: List of strategy IDs to test (default: all enabled)
            initial_capital: Starting capital in USDT
            min_confidence: Minimum confidence to take a trade

        Returns:
            Dict with trades list, metrics, and equity curve
        """
        if strategies is None:
            strategies = self.config.get("strategies", {}).get("enabled", ["ema_crossover_scalp"])

        # Convert to list of dicts for indicator calculation
        klines = klines_df.to_dict("records")

        if len(klines) < 60:
            logger.warning("Not enough candles for backtest", count=len(klines))
            return {"trades": [], "metrics": MetricsCalculator._empty_metrics(), "equity_curve": []}

        trades = []
        equity = initial_capital
        equity_curve = [{"timestamp": klines[0]["timestamp"], "equity": equity}]
        position = None  # Current open position
        trailing_sl = None  # Dynamic SL price
        self._htf_trend_cache = {"trend": "neutral", "last_bar": -1}  # Reset per run

        # Walk through candles bar-by-bar (need at least 55 candles for EMA50)
        lookback = 55
        for i in range(lookback, len(klines)):
            candle = klines[i]
            candle_time = datetime.fromtimestamp(candle["timestamp"] / 1000, tz=timezone.utc)

            # Current window for indicator calculation
            window = klines[max(0, i - lookback):i + 1]

            # Check if we have an open position
            if position is not None:
                # Check SL/TP/time exit using current candle's high/low
                exit_result = self._check_exits(position, candle, candle_time, trailing_sl)

                if exit_result:
                    # Close position
                    exit_price = exit_result["exit_price"]
                    # Apply slippage
                    exit_price = self._apply_slippage(exit_price, position["side"] == "LONG", closing=True)

                    pnl = self._calculate_pnl(position, exit_price)
                    equity += pnl

                    trades.append({
                        "symbol": symbol,
                        "side": position["side"],
                        "strategy_id": position["strategy_id"],
                        "entry_price": position["entry_price"],
                        "exit_price": exit_price,
                        "quantity": position["quantity"],
                        "pnl": pnl,
                        "entry_time": position["entry_time"],
                        "exit_time": candle_time,
                        "close_reason": exit_result["reason"],
                    })

                    position = None
                    trailing_sl = None

                    equity_curve.append({"timestamp": candle["timestamp"], "equity": equity})
                    continue

                # Update trailing SL
                trailing_sl = self._update_trailing_sl(position, candle, trailing_sl)
                continue  # Don't open new position while one is open

            # No open position — evaluate strategies
            try:
                indicators = self.indicators.calculate_all(window, candle["close"], reference_time=candle_time)
            except Exception:
                continue

            # Compute HTF trend from available data
            htf_trend = self._compute_htf_trend(klines, i, interval)
            indicators["htf_trend"] = htf_trend

            # Build research_summary (simplified version of what live system produces)
            research_summary = self._build_research_summary(symbol, candle, indicators)

            # Evaluate strategies
            best_signal = None
            best_confidence = 0
            best_strategy = None

            for strategy_id in strategies:
                signal = self._evaluate_strategy(strategy_id, research_summary)
                if signal["signal"] != "neutral" and signal["confidence"] > best_confidence:
                    best_confidence = signal["confidence"]
                    best_signal = signal["signal"]
                    best_strategy = strategy_id

            if best_signal and best_confidence >= min_confidence:
                direction = "LONG" if best_signal == "long" else "SHORT"

                # Confluence gate — reject low-quality signals
                confluence = self._check_confluence(research_summary, direction)
                if confluence["size_multiplier"] == 0.0:
                    continue  # Score <= 1, skip

                # Calculate entry, SL, TP
                atr = indicators.get("atr", 0) or (candle["close"] * 0.01)
                entry_price = candle["close"]  # Enter at current candle close
                # Simulate fill at next candle open
                if i + 1 < len(klines):
                    entry_price = klines[i + 1]["open"]
                entry_price = self._apply_slippage(entry_price, direction == "LONG", closing=False)

                sl_distance = max(atr * self.sl_atr_mult, entry_price * self.min_sl_pct)
                tp_distance = max(atr * self.tp_atr_mult, sl_distance * self.min_rr)

                if direction == "LONG":
                    sl = entry_price - sl_distance
                    tp = entry_price + tp_distance
                else:
                    sl = entry_price + sl_distance
                    tp = entry_price - tp_distance

                # Fee viability check
                round_trip_fee_rate = self.taker_bps / 10000 * 2
                expected_profit_pct = tp_distance / entry_price
                if expected_profit_pct < round_trip_fee_rate * self.fee_buffer_mult:
                    continue  # Skip — not enough profit to cover fees

                # Position sizing (scaled by confluence multiplier)
                risk_amount = equity * self.max_risk_pct
                stop_pct = sl_distance / entry_price
                if stop_pct > 0:
                    notional = risk_amount / stop_pct
                else:
                    notional = equity * 0.1
                notional *= confluence["size_multiplier"]
                notional = min(notional, equity * self.max_exposure_pct * self.leverage)
                notional = max(notional, 100)  # Binance minimum

                quantity = notional / entry_price

                position = {
                    "side": direction,
                    "strategy_id": best_strategy,
                    "entry_price": entry_price,
                    "sl": sl,
                    "tp": tp,
                    "quantity": quantity,
                    "notional": notional,
                    "entry_time": candle_time,
                }
                trailing_sl = sl  # Initialize trailing SL at original SL

        # Close any remaining position at last candle
        if position is not None:
            exit_price = klines[-1]["close"]
            pnl = self._calculate_pnl(position, exit_price)
            equity += pnl
            last_time = datetime.fromtimestamp(klines[-1]["timestamp"] / 1000, tz=timezone.utc)
            trades.append({
                "symbol": symbol,
                "side": position["side"],
                "strategy_id": position["strategy_id"],
                "entry_price": position["entry_price"],
                "exit_price": exit_price,
                "quantity": position["quantity"],
                "pnl": pnl,
                "entry_time": position["entry_time"],
                "exit_time": last_time,
                "close_reason": "BACKTEST_END",
            })
            equity_curve.append({"timestamp": klines[-1]["timestamp"], "equity": equity})

        # Calculate metrics
        metrics = MetricsCalculator.calculate(trades, initial_capital)

        return {
            "trades": trades,
            "metrics": metrics,
            "equity_curve": equity_curve,
        }

    def walk_forward_validate(
        self,
        symbol: str,
        interval: str,
        klines_df: pd.DataFrame,
        train_days: int = 30,
        test_days: int = 7,
        step_days: int = 7,
        strategies: Optional[List[str]] = None,
        initial_capital: float = 125.0,
    ) -> Dict[str, Any]:
        """
        Walk-forward validation: train on window, test on next window, step forward.

        Only out-of-sample (test) results count.
        Walk-Forward Efficiency target: > 50%.

        Returns:
            Dict with oos_trades, oos_metrics, and per-window results
        """
        if strategies is None:
            strategies = self.config.get("strategies", {}).get("enabled", ["ema_crossover_scalp"])

        # Convert timestamp to datetime for slicing
        klines_df = klines_df.copy()
        klines_df["_dt"] = pd.to_datetime(klines_df["timestamp"], unit="ms", utc=True)

        start_date = klines_df["_dt"].min()
        end_date = klines_df["_dt"].max()

        window_results = []
        all_oos_trades = []
        current_start = start_date

        while current_start + timedelta(days=train_days + test_days) <= end_date:
            train_end = current_start + timedelta(days=train_days)
            test_end = train_end + timedelta(days=test_days)

            # Split data
            train_df = klines_df[(klines_df["_dt"] >= current_start) & (klines_df["_dt"] < train_end)]
            test_df = klines_df[(klines_df["_dt"] >= train_end) & (klines_df["_dt"] < test_end)]

            if len(train_df) < 60 or len(test_df) < 10:
                current_start += timedelta(days=step_days)
                continue

            # Run on train (in-sample)
            train_result = self.run(
                symbol, interval,
                train_df.drop(columns=["_dt"]),
                strategies=strategies,
                initial_capital=initial_capital,
            )

            # Run on test (out-of-sample) — this is what counts
            test_result = self.run(
                symbol, interval,
                test_df.drop(columns=["_dt"]),
                strategies=strategies,
                initial_capital=initial_capital,
            )

            all_oos_trades.extend(test_result["trades"])

            window_results.append({
                "train_start": current_start.isoformat(),
                "train_end": train_end.isoformat(),
                "test_start": train_end.isoformat(),
                "test_end": test_end.isoformat(),
                "is_metrics": train_result["metrics"],
                "oos_metrics": test_result["metrics"],
            })

            current_start += timedelta(days=step_days)

        # Calculate overall OOS metrics
        oos_metrics = MetricsCalculator.calculate(all_oos_trades, initial_capital)

        # Walk-forward efficiency: OOS Sharpe / IS Sharpe
        is_sharpes = [w["is_metrics"]["sharpe_ratio"] for w in window_results if w["is_metrics"]["sharpe_ratio"] != 0]
        oos_sharpes = [w["oos_metrics"]["sharpe_ratio"] for w in window_results if w["oos_metrics"]["sharpe_ratio"] != 0]
        wfe = 0
        if is_sharpes and oos_sharpes:
            avg_is = sum(is_sharpes) / len(is_sharpes)
            avg_oos = sum(oos_sharpes) / len(oos_sharpes)
            wfe = avg_oos / avg_is if avg_is != 0 else 0

        return {
            "oos_trades": all_oos_trades,
            "oos_metrics": oos_metrics,
            "window_results": window_results,
            "walk_forward_efficiency": round(wfe, 2),
            "windows_tested": len(window_results),
        }

    # ========== Internal helpers ==========

    def _check_exits(
        self,
        position: Dict[str, Any],
        candle: Dict[str, Any],
        candle_time: datetime,
        trailing_sl: Optional[float]
    ) -> Optional[Dict[str, Any]]:
        """Check if position should be closed on this candle."""
        is_long = position["side"] == "LONG"
        high = candle["high"]
        low = candle["low"]
        sl = trailing_sl or position["sl"]
        tp = position["tp"]

        # SL hit
        if is_long and low <= sl:
            return {"exit_price": sl, "reason": "STOP_LOSS"}
        if not is_long and high >= sl:
            return {"exit_price": sl, "reason": "STOP_LOSS"}

        # TP hit
        if is_long and high >= tp:
            return {"exit_price": tp, "reason": "TAKE_PROFIT"}
        if not is_long and low <= tp:
            return {"exit_price": tp, "reason": "TAKE_PROFIT"}

        # Time exit
        holding = (candle_time - position["entry_time"]).total_seconds()
        if holding >= self.max_holding_seconds:
            return {"exit_price": candle["close"], "reason": "TIME_EXIT"}

        return None

    def _update_trailing_sl(
        self,
        position: Dict[str, Any],
        candle: Dict[str, Any],
        current_sl: float
    ) -> float:
        """Update trailing stop loss based on price movement."""
        is_long = position["side"] == "LONG"
        entry = position["entry_price"]

        if is_long:
            profit_pct = (candle["high"] - entry) / entry
            # Move SL to breakeven
            if profit_pct >= self.dsl_breakeven_pct and current_sl < entry:
                current_sl = entry
            # Trail SL behind price
            if profit_pct >= self.dsl_trail_pct:
                new_sl = candle["high"] * (1 - self.dsl_trail_step)
                if new_sl > current_sl:
                    current_sl = new_sl
        else:
            profit_pct = (entry - candle["low"]) / entry
            if profit_pct >= self.dsl_breakeven_pct and (current_sl > entry or current_sl == position["sl"]):
                current_sl = entry
            if profit_pct >= self.dsl_trail_pct:
                new_sl = candle["low"] * (1 + self.dsl_trail_step)
                if new_sl < current_sl or current_sl == entry:
                    current_sl = new_sl

        return current_sl

    def _apply_slippage(self, price: float, is_buy: bool, closing: bool = False) -> float:
        """Apply simulated slippage to fill price."""
        slip = self.slippage_bps / 10000
        if is_buy and not closing:
            return price * (1 + slip)  # Pay more when buying
        elif not is_buy and not closing:
            return price * (1 - slip)  # Receive less when selling
        elif is_buy and closing:
            return price * (1 + slip)  # Pay more to buy back short
        else:
            return price * (1 - slip)  # Receive less when closing long

    def _calculate_pnl(self, position: Dict[str, Any], exit_price: float) -> float:
        """Calculate realized PnL including fees."""
        qty = position["quantity"]
        entry = position["entry_price"]
        is_long = position["side"] == "LONG"

        if is_long:
            raw_pnl = (exit_price - entry) * qty
        else:
            raw_pnl = (entry - exit_price) * qty

        # Round-trip fees
        fee_rate = self.taker_bps / 10000
        fees = (entry * qty + exit_price * qty) * fee_rate

        return raw_pnl - fees

    def _build_research_summary(
        self,
        symbol: str,
        candle: Dict[str, Any],
        indicators: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Build a research_summary dict matching the live system's format."""
        return {
            "symbol": symbol,
            "market_data": {
                "price": candle["close"],
                "volume_24h": candle["volume"] * candle["close"] * 288,  # Rough 24h estimate from 5m candle
                "order_book": {
                    "imbalance_ratio": 0,  # No order book in backtest
                    "spread_bps": 2,
                },
                "funding_rate": 0,
            },
            "technical_indicators": indicators,
            "market_regime": "DEFAULT",
        }

    def _compute_htf_trend(
        self,
        klines: List[Dict[str, Any]],
        current_bar: int,
        interval: str
    ) -> str:
        """
        Compute higher-timeframe trend by resampling candles.

        Resamples to ~12x the base interval (e.g. 5m -> 1h) using EMA 9/21/50
        alignment on the resampled closes. Caches result and recomputes every
        resample_factor bars for performance.
        """
        # Determine resample factor based on interval
        resample_factors = {
            "1m": 60, "3m": 20, "5m": 12, "15m": 4, "30m": 4, "1h": 4, "4h": 6
        }
        resample_factor = resample_factors.get(interval, 12)

        # Use cache if still valid
        if current_bar - self._htf_trend_cache["last_bar"] < resample_factor:
            return self._htf_trend_cache["trend"]

        # Need enough bars for resampling + EMA50
        min_bars = resample_factor * 55
        if current_bar < min_bars:
            return "neutral"

        # Resample: take every resample_factor-th candle's close
        start = max(0, current_bar - min_bars)
        slice_klines = klines[start:current_bar + 1]

        closes = [k["close"] for k in slice_klines]
        # Group into HTF bars by averaging each chunk
        htf_closes = []
        for j in range(0, len(closes) - resample_factor + 1, resample_factor):
            chunk = closes[j:j + resample_factor]
            htf_closes.append(chunk[-1])  # Use last close of chunk (standard OHLC convention)

        if len(htf_closes) < 50:
            return "neutral"

        # Compute EMAs on HTF closes
        s = pd.Series(htf_closes)
        ema9 = ta.trend.ema_indicator(s, window=9).iloc[-1]
        ema21 = ta.trend.ema_indicator(s, window=21).iloc[-1]
        ema50 = ta.trend.ema_indicator(s, window=50).iloc[-1]

        if np.isnan(ema9) or np.isnan(ema21) or np.isnan(ema50):
            trend = "neutral"
        elif ema9 > ema21 > ema50:
            trend = "bullish"
        elif ema9 > ema21:
            trend = "weak_bullish"
        elif ema9 < ema21 < ema50:
            trend = "bearish"
        elif ema9 < ema21:
            trend = "weak_bearish"
        else:
            trend = "neutral"

        self._htf_trend_cache = {"trend": trend, "last_bar": current_bar}
        return trend

    def _check_confluence(
        self,
        research_summary: Dict[str, Any],
        direction: str
    ) -> Dict[str, Any]:
        """
        Score 5 independent factors for confluence gate.
        Mirrors live logic from TradingDecisionAgent._calculate_confluence_score().

        Factors:
        1. Trend: HTF aligned + EMA direction agrees
        2. Momentum: RSI + MACD histogram supports direction
        3. Trend Strength: ADX > 20
        4. Volume: Relative volume >= 1.3
        5. Structure: Price near support (long) / resistance (short)
        """
        tech_ind = research_summary.get("technical_indicators", {})
        market_data = research_summary.get("market_data", {})
        is_long = direction == "LONG"
        score = 0

        # 1. Trend: HTF aligned + EMA direction agrees
        htf_trend = tech_ind.get("htf_trend", "neutral")
        ema_9 = tech_ind.get("ema_9", 0)
        ema_21 = tech_ind.get("ema_21", 0)
        if is_long:
            trend_ok = htf_trend in ("bullish", "weak_bullish") and ema_9 > ema_21
        else:
            trend_ok = htf_trend in ("bearish", "weak_bearish") and ema_9 < ema_21
        if trend_ok:
            score += 1

        # 2. Momentum: RSI + MACD histogram supports direction
        rsi = tech_ind.get("rsi", 50)
        histogram = tech_ind.get("macd", {}).get("histogram", 0)
        if is_long:
            momentum_ok = rsi > 45 and histogram > 0
        else:
            momentum_ok = rsi < 55 and histogram < 0
        if momentum_ok:
            score += 1

        # 3. Trend Strength: ADX > 20
        adx = tech_ind.get("adx", 0)
        if adx > 20:
            score += 1

        # 4. Volume: Relative volume >= 1.3
        relative_volume = tech_ind.get("relative_volume", 1.0)
        if relative_volume >= 1.3:
            score += 1

        # 5. Structure: Price near support (long) / resistance (short)
        sr = tech_ind.get("support_resistance", {})
        price = market_data.get("price", 0)
        if sr and price > 0:
            atr = tech_ind.get("atr", 0) or (price * 0.01)
            proximity = atr * 3
            if is_long:
                for s in sr.get("support", []):
                    if s > 0 and abs(price - s) < proximity:
                        score += 1
                        break
            else:
                for r in sr.get("resistance", []):
                    if r > 0 and abs(price - r) < proximity:
                        score += 1
                        break

        # Size multiplier (matches live system tiers)
        if score <= 1:
            size_multiplier = 0.0
        elif score == 2:
            size_multiplier = 0.5
        elif score == 3:
            size_multiplier = 0.75
        elif score == 4:
            size_multiplier = 1.0
        else:
            size_multiplier = 1.2

        return {"score": score, "size_multiplier": size_multiplier}

    def _evaluate_strategy(
        self,
        strategy_id: str,
        research_summary: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Evaluate a strategy directly (no full agent pipeline).
        Reuses the same logic as TradingDecisionAgent strategy evaluators.
        """
        tech_ind = research_summary.get("technical_indicators", {})
        market_data = research_summary.get("market_data", {})
        price = market_data.get("price", 0)

        if strategy_id == "ema_crossover_scalp":
            return self._eval_ema_crossover(tech_ind, market_data)
        elif strategy_id == "rsi_pullback_scalp":
            return self._eval_rsi_pullback(tech_ind, market_data)
        elif strategy_id == "vwap_bounce_scalp":
            return self._eval_vwap_bounce(tech_ind, market_data)
        elif strategy_id == "bollinger_squeeze_scalp":
            return self._eval_bollinger_squeeze(tech_ind, market_data)

        return {"signal": "neutral", "confidence": 0.5}

    def _eval_ema_crossover(self, tech_ind: Dict, market_data: Dict) -> Dict[str, Any]:
        """Simplified EMA crossover evaluation for backtest."""
        ema_9 = tech_ind.get("ema_9", 0)
        ema_21 = tech_ind.get("ema_21", 0)
        ema_50 = tech_ind.get("ema_50", 0)
        prev_ema_9 = tech_ind.get("prev_ema_9", ema_9)
        prev_ema_21 = tech_ind.get("prev_ema_21", ema_21)
        price = market_data.get("price", 0)
        rsi = tech_ind.get("rsi", 50)
        atr = tech_ind.get("atr", 0) or (price * 0.01)
        htf_trend = tech_ind.get("htf_trend", "neutral")

        # Trend strength filter
        ema_spread = abs(ema_9 - ema_21)
        trend_strength = ema_spread / atr if atr > 0 else 0
        if trend_strength < 0.15:
            return {"signal": "neutral", "confidence": 0.5}

        bullish_cross = prev_ema_9 <= prev_ema_21 and ema_9 > ema_21
        bearish_cross = prev_ema_9 >= prev_ema_21 and ema_9 < ema_21

        htf_bearish = htf_trend in ("bearish", "weak_bearish")
        htf_bullish = htf_trend in ("bullish", "weak_bullish")

        if bullish_cross and price > ema_50 and not htf_bearish and 40 < rsi < 65:
            conf = 0.72
            if htf_trend == "bullish":
                conf += 0.06
            if trend_strength > 0.4:
                conf += 0.04
            return {"signal": "long", "confidence": min(1.0, conf)}

        if bearish_cross and price < ema_50 and not htf_bullish and 35 < rsi < 60:
            conf = 0.72
            if htf_trend == "bearish":
                conf += 0.06
            if trend_strength > 0.4:
                conf += 0.04
            return {"signal": "short", "confidence": min(1.0, conf)}

        return {"signal": "neutral", "confidence": 0.5}

    def _eval_rsi_pullback(self, tech_ind: Dict, market_data: Dict) -> Dict[str, Any]:
        """Simplified RSI pullback evaluation for backtest."""
        rsi = tech_ind.get("rsi", 50)
        ema_21 = tech_ind.get("ema_21", 0)
        ema_50 = tech_ind.get("ema_50", 0)
        price = market_data.get("price", 0)
        htf_trend = tech_ind.get("htf_trend", "neutral")
        macd_data = tech_ind.get("macd", {})
        histogram = macd_data.get("histogram", 0)

        if price <= 0 or ema_21 <= 0:
            return {"signal": "neutral", "confidence": 0.5}

        dist_from_ema21 = (price - ema_21) / ema_21
        htf_bull = htf_trend in ("bullish", "weak_bullish")
        htf_bear = htf_trend in ("bearish", "weak_bearish")

        if (28 < rsi < 45 and htf_bull and ema_21 > ema_50
                and -0.015 < dist_from_ema21 < 0.005 and histogram > -0.5):
            conf = 0.72
            if rsi < 35:
                conf += 0.06
            return {"signal": "long", "confidence": min(1.0, conf)}

        if (55 < rsi < 72 and htf_bear and ema_21 < ema_50
                and -0.005 < dist_from_ema21 < 0.015 and histogram < 0.5):
            conf = 0.72
            if rsi > 65:
                conf += 0.06
            return {"signal": "short", "confidence": min(1.0, conf)}

        return {"signal": "neutral", "confidence": 0.5}

    def _eval_vwap_bounce(self, tech_ind: Dict, market_data: Dict) -> Dict[str, Any]:
        """Simplified VWAP bounce evaluation for backtest."""
        vwap = tech_ind.get("vwap", 0)
        price = market_data.get("price", 0)
        prev_close = tech_ind.get("prev_close", 0)
        rsi = tech_ind.get("rsi", 50)
        relative_volume = tech_ind.get("relative_volume", 1.0)

        if vwap == 0 or price == 0 or relative_volume < 1.2:
            return {"signal": "neutral", "confidence": 0.5}

        distance_pct = (price - vwap) / vwap
        if abs(distance_pct) > 0.005:
            return {"signal": "neutral", "confidence": 0.5}

        prev_dist = (prev_close - vwap) / vwap if prev_close > 0 else 0

        if prev_dist < -0.001 and distance_pct >= 0 and rsi > 50:
            return {"signal": "long", "confidence": 0.72}
        if prev_dist > 0.001 and distance_pct <= 0 and rsi < 50:
            return {"signal": "short", "confidence": 0.72}

        return {"signal": "neutral", "confidence": 0.5}

    def _eval_bollinger_squeeze(self, tech_ind: Dict, market_data: Dict) -> Dict[str, Any]:
        """Simplified Bollinger squeeze evaluation for backtest."""
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
        relative_volume = tech_ind.get("relative_volume", 1.0)

        is_squeeze = bandwidth < 3.0 and bandwidth < prev_bandwidth
        if not is_squeeze or relative_volume < 1.2:
            return {"signal": "neutral", "confidence": 0.5}

        if price > upper and pct_b > 1.0 and rsi > 50:
            return {"signal": "long", "confidence": 0.75}
        if price < lower and pct_b < 0.0 and rsi < 50:
            return {"signal": "short", "confidence": 0.75}

        return {"signal": "neutral", "confidence": 0.5}
