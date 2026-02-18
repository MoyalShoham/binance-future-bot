"""
Backtest Metrics Calculator

Computes performance metrics from backtest trade results.
"""

import math
from typing import List, Dict, Any
import structlog

logger = structlog.get_logger()


class MetricsCalculator:
    """
    Calculates backtest performance metrics.

    Minimum thresholds to go live:
    - Sharpe > 1.0
    - Profit factor > 1.3
    - Max drawdown < 20%
    - Expectancy > 0
    """

    @staticmethod
    def calculate(trades: List[Dict[str, Any]], initial_capital: float = 125.0) -> Dict[str, Any]:
        """
        Calculate all performance metrics from trade list.

        Args:
            trades: List of trade dicts with keys: pnl, entry_time, exit_time, strategy_id
            initial_capital: Starting capital

        Returns:
            Dict of performance metrics
        """
        if not trades:
            return MetricsCalculator._empty_metrics()

        pnls = [t["pnl"] for t in trades]
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p <= 0]

        total_trades = len(trades)
        win_count = len(wins)
        loss_count = len(losses)
        win_rate = win_count / total_trades if total_trades > 0 else 0

        # Profit factor: gross profit / gross loss
        gross_profit = sum(wins) if wins else 0
        gross_loss = abs(sum(losses)) if losses else 0.001  # Avoid division by zero
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else float("inf")

        # Averages
        avg_win = sum(wins) / len(wins) if wins else 0
        avg_loss = sum(losses) / len(losses) if losses else 0
        avg_pnl = sum(pnls) / total_trades if total_trades > 0 else 0

        # Expectancy per trade
        expectancy = avg_pnl

        # Largest win/loss
        largest_win = max(wins) if wins else 0
        largest_loss = min(losses) if losses else 0

        # Total P&L
        total_pnl = sum(pnls)

        # Max drawdown
        equity_curve = [initial_capital]
        for pnl in pnls:
            equity_curve.append(equity_curve[-1] + pnl)

        peak = equity_curve[0]
        max_drawdown = 0
        max_drawdown_pct = 0
        for eq in equity_curve:
            if eq > peak:
                peak = eq
            dd = peak - eq
            dd_pct = dd / peak if peak > 0 else 0
            if dd_pct > max_drawdown_pct:
                max_drawdown_pct = dd_pct
                max_drawdown = dd

        # Sharpe ratio (daily returns, annualized)
        # Group PnL by day for daily returns
        daily_pnls = MetricsCalculator._daily_pnls(trades, initial_capital)
        sharpe = MetricsCalculator._sharpe_ratio(daily_pnls)

        # Sortino ratio (only downside deviation)
        sortino = MetricsCalculator._sortino_ratio(daily_pnls)

        # Calmar ratio: annualized return / max drawdown
        if len(trades) >= 2:
            first_time = trades[0].get("entry_time")
            last_time = trades[-1].get("exit_time") or trades[-1].get("entry_time")
            if first_time and last_time:
                days = max(1, (last_time - first_time).total_seconds() / 86400)
                annualized_return = (total_pnl / initial_capital) * (365 / days)
                calmar = annualized_return / max_drawdown_pct if max_drawdown_pct > 0 else float("inf")
            else:
                calmar = 0
        else:
            calmar = 0

        # Average holding time
        holding_times = []
        for t in trades:
            entry = t.get("entry_time")
            exit_ = t.get("exit_time")
            if entry and exit_:
                holding_times.append((exit_ - entry).total_seconds())
        avg_holding_time = sum(holding_times) / len(holding_times) if holding_times else 0

        return {
            "total_trades": total_trades,
            "win_count": win_count,
            "loss_count": loss_count,
            "win_rate": round(win_rate, 4),
            "profit_factor": round(profit_factor, 2),
            "total_pnl": round(total_pnl, 2),
            "avg_win": round(avg_win, 4),
            "avg_loss": round(avg_loss, 4),
            "avg_pnl": round(avg_pnl, 4),
            "expectancy": round(expectancy, 4),
            "largest_win": round(largest_win, 4),
            "largest_loss": round(largest_loss, 4),
            "max_drawdown": round(max_drawdown, 2),
            "max_drawdown_pct": round(max_drawdown_pct, 4),
            "sharpe_ratio": round(sharpe, 2),
            "sortino_ratio": round(sortino, 2),
            "calmar_ratio": round(calmar, 2),
            "avg_holding_time_seconds": round(avg_holding_time, 0),
            "equity_final": round(equity_curve[-1], 2),
            "return_pct": round(total_pnl / initial_capital * 100, 2),
            "passes_live_threshold": (
                sharpe > 1.0
                and profit_factor > 1.3
                and max_drawdown_pct < 0.20
                and expectancy > 0
            ),
        }

    @staticmethod
    def _daily_pnls(trades: List[Dict[str, Any]], initial_capital: float) -> List[float]:
        """Group trade PnLs by day and return daily return percentages."""
        if not trades:
            return []

        daily = {}
        for t in trades:
            exit_time = t.get("exit_time") or t.get("entry_time")
            if not exit_time:
                continue
            day_key = exit_time.strftime("%Y-%m-%d")
            daily.setdefault(day_key, 0)
            daily[day_key] += t["pnl"]

        equity = initial_capital
        daily_returns = []
        for day in sorted(daily.keys()):
            ret = daily[day] / equity if equity > 0 else 0
            daily_returns.append(ret)
            equity += daily[day]

        return daily_returns

    @staticmethod
    def _sharpe_ratio(daily_returns: List[float], risk_free_rate: float = 0.0) -> float:
        """Annualized Sharpe ratio from daily returns."""
        if len(daily_returns) < 2:
            return 0.0

        mean_ret = sum(daily_returns) / len(daily_returns)
        variance = sum((r - mean_ret) ** 2 for r in daily_returns) / (len(daily_returns) - 1)
        std_dev = math.sqrt(variance) if variance > 0 else 0.001

        sharpe = (mean_ret - risk_free_rate) / std_dev * math.sqrt(365)
        return sharpe

    @staticmethod
    def _sortino_ratio(daily_returns: List[float], risk_free_rate: float = 0.0) -> float:
        """Annualized Sortino ratio (downside deviation only)."""
        if len(daily_returns) < 2:
            return 0.0

        mean_ret = sum(daily_returns) / len(daily_returns)
        downside = [r for r in daily_returns if r < 0]
        if not downside:
            return float("inf") if mean_ret > 0 else 0.0

        downside_var = sum(r ** 2 for r in downside) / len(downside)
        downside_dev = math.sqrt(downside_var) if downside_var > 0 else 0.001

        sortino = (mean_ret - risk_free_rate) / downside_dev * math.sqrt(365)
        return sortino

    @staticmethod
    def _empty_metrics() -> Dict[str, Any]:
        return {
            "total_trades": 0, "win_count": 0, "loss_count": 0,
            "win_rate": 0, "profit_factor": 0, "total_pnl": 0,
            "avg_win": 0, "avg_loss": 0, "avg_pnl": 0, "expectancy": 0,
            "largest_win": 0, "largest_loss": 0,
            "max_drawdown": 0, "max_drawdown_pct": 0,
            "sharpe_ratio": 0, "sortino_ratio": 0, "calmar_ratio": 0,
            "avg_holding_time_seconds": 0, "equity_final": 0,
            "return_pct": 0, "passes_live_threshold": False,
        }

    @staticmethod
    def print_report(metrics: Dict[str, Any]):
        """Print a formatted metrics report."""
        print("\n" + "=" * 60)
        print("BACKTEST RESULTS")
        print("=" * 60)
        print(f"  Total Trades:      {metrics['total_trades']}")
        print(f"  Win Rate:          {metrics['win_rate']:.1%}")
        print(f"  Profit Factor:     {metrics['profit_factor']:.2f}")
        print(f"  Total P&L:         ${metrics['total_pnl']:.2f}")
        print(f"  Return:            {metrics['return_pct']:.1f}%")
        print(f"  Expectancy/Trade:  ${metrics['expectancy']:.4f}")
        print(f"  Avg Win:           ${metrics['avg_win']:.4f}")
        print(f"  Avg Loss:          ${metrics['avg_loss']:.4f}")
        print(f"  Largest Win:       ${metrics['largest_win']:.4f}")
        print(f"  Largest Loss:      ${metrics['largest_loss']:.4f}")
        print(f"  Max Drawdown:      {metrics['max_drawdown_pct']:.1%} (${metrics['max_drawdown']:.2f})")
        print(f"  Sharpe Ratio:      {metrics['sharpe_ratio']:.2f}")
        print(f"  Sortino Ratio:     {metrics['sortino_ratio']:.2f}")
        print(f"  Calmar Ratio:      {metrics['calmar_ratio']:.2f}")
        print(f"  Avg Hold Time:     {metrics['avg_holding_time_seconds']:.0f}s")
        print(f"  Final Equity:      ${metrics['equity_final']:.2f}")
        print("-" * 60)

        passes = metrics["passes_live_threshold"]
        if passes:
            print("  LIVE THRESHOLD:    PASS")
        else:
            print("  LIVE THRESHOLD:    FAIL")
            if metrics["sharpe_ratio"] <= 1.0:
                print("    - Sharpe < 1.0")
            if metrics["profit_factor"] <= 1.3:
                print("    - Profit Factor < 1.3")
            if metrics["max_drawdown_pct"] >= 0.20:
                print("    - Max Drawdown >= 20%")
            if metrics["expectancy"] <= 0:
                print("    - Expectancy <= 0")
        print("=" * 60)
