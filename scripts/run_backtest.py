#!/usr/bin/env python3
"""
Backtest Runner Script

Usage:
    python scripts/run_backtest.py --symbol BTCUSDT --start 2025-08-01 --end 2026-02-01 --strategy ema_crossover_scalp
    python scripts/run_backtest.py --symbol BTCUSDT --start 2025-08-01 --end 2026-02-01 --walk-forward
"""

import argparse
import os
import sys
import logging

# Add project root to path
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

import yaml
from dotenv import load_dotenv

from infrastructure.binance_api import BinanceFuturesClient
from backtesting import HistoricalDataLoader, BacktestEngine, MetricsCalculator


def main():
    parser = argparse.ArgumentParser(description="Run strategy backtest")
    parser.add_argument("--symbol", type=str, default="BTCUSDT", help="Trading pair")
    parser.add_argument("--start", type=str, required=True, help="Start date (YYYY-MM-DD)")
    parser.add_argument("--end", type=str, required=True, help="End date (YYYY-MM-DD)")
    parser.add_argument("--interval", type=str, default="5m", help="Candle interval (default: 5m)")
    parser.add_argument(
        "--strategy", type=str, nargs="+",
        default=None,
        help="Strategy IDs to test (default: config enabled strategies)"
    )
    parser.add_argument("--capital", type=float, default=125.0, help="Initial capital (default: $125)")
    parser.add_argument("--walk-forward", action="store_true", help="Run walk-forward validation")
    parser.add_argument("--train-days", type=int, default=30, help="Walk-forward train window (days)")
    parser.add_argument("--test-days", type=int, default=7, help="Walk-forward test window (days)")
    parser.add_argument("--force-download", action="store_true", help="Re-download data even if cached")
    parser.add_argument(
        "--config", type=str,
        default=os.path.join(PROJECT_ROOT, "config", "trading_config.yaml"),
        help="Config file path"
    )

    args = parser.parse_args()

    # Setup logging
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    # Load config
    with open(args.config) as f:
        config = yaml.safe_load(f)

    # Load environment for Binance API
    load_dotenv()

    api_key = os.getenv("BINANCE_API_KEY")
    api_secret = os.getenv("BINANCE_API_SECRET")

    if not api_key or not api_secret:
        print("Error: BINANCE_API_KEY and BINANCE_API_SECRET required in .env")
        sys.exit(1)

    # Initialize client (mainnet for historical data)
    client = BinanceFuturesClient(api_key=api_key, api_secret=api_secret, testnet=False)

    if not client.ping():
        print("Error: Cannot connect to Binance API")
        sys.exit(1)

    # Load historical data
    loader = HistoricalDataLoader(client)
    print(f"\nLoading {args.symbol} {args.interval} data: {args.start} to {args.end}")
    klines_df = loader.load_klines(
        args.symbol, args.interval, args.start, args.end,
        force_download=args.force_download
    )

    if klines_df.empty:
        print("Error: No data loaded")
        sys.exit(1)

    print(f"Loaded {len(klines_df)} candles")

    # Initialize engine
    engine = BacktestEngine(config)

    strategies = args.strategy
    if strategies is None:
        strategies = config.get("strategies", {}).get("enabled", ["ema_crossover_scalp"])

    print(f"Testing strategies: {strategies}")
    print(f"Initial capital: ${args.capital}")

    if args.walk_forward:
        print(f"\nRunning walk-forward validation (train={args.train_days}d, test={args.test_days}d)")
        result = engine.walk_forward_validate(
            args.symbol, args.interval, klines_df,
            train_days=args.train_days,
            test_days=args.test_days,
            strategies=strategies,
            initial_capital=args.capital,
        )

        print(f"\nWalk-Forward Results ({result['windows_tested']} windows):")
        print(f"  Walk-Forward Efficiency: {result['walk_forward_efficiency']:.0%}")

        if result["window_results"]:
            print("\n  Per-window OOS results:")
            for i, w in enumerate(result["window_results"]):
                oos = w["oos_metrics"]
                print(
                    f"    Window {i+1}: "
                    f"trades={oos['total_trades']}, "
                    f"win_rate={oos['win_rate']:.0%}, "
                    f"pnl=${oos['total_pnl']:.2f}, "
                    f"sharpe={oos['sharpe_ratio']:.2f}"
                )

        print("\nOverall Out-of-Sample Metrics:")
        MetricsCalculator.print_report(result["oos_metrics"])

    else:
        print(f"\nRunning single backtest...")
        result = engine.run(
            args.symbol, args.interval, klines_df,
            strategies=strategies,
            initial_capital=args.capital,
        )

        MetricsCalculator.print_report(result["metrics"])

        # Per-strategy breakdown
        if result["trades"]:
            strat_trades = {}
            for t in result["trades"]:
                sid = t["strategy_id"]
                strat_trades.setdefault(sid, []).append(t)

            if len(strat_trades) > 1:
                print("\nPer-Strategy Breakdown:")
                for sid, trades in strat_trades.items():
                    m = MetricsCalculator.calculate(trades, args.capital)
                    print(
                        f"  {sid}: "
                        f"trades={m['total_trades']}, "
                        f"win_rate={m['win_rate']:.0%}, "
                        f"pnl=${m['total_pnl']:.2f}, "
                        f"sharpe={m['sharpe_ratio']:.2f}"
                    )

            # Close reason breakdown
            reasons = {}
            for t in result["trades"]:
                r = t["close_reason"]
                reasons.setdefault(r, {"count": 0, "pnl": 0})
                reasons[r]["count"] += 1
                reasons[r]["pnl"] += t["pnl"]

            print("\nClose Reason Breakdown:")
            for reason, stats in sorted(reasons.items()):
                print(f"  {reason}: count={stats['count']}, pnl=${stats['pnl']:.2f}")


if __name__ == "__main__":
    main()
