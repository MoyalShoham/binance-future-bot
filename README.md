# Multi-Agent AI Trading System for Binance Futures

Autonomous multi-agent trading system for Binance Futures scalping. 6 specialized agents handle market research, trade decisions, risk management, order execution, data persistence, and system health monitoring.

## Quick Start

```bash
# 1. Set up environment
cp .env.example .env
# Add: BINANCE_API_KEY, BINANCE_API_SECRET, ANTHROPIC_API_KEY

# 2. Install dependencies
pip install -r requirements.txt

# 3. Run paper trading
python main.py --mode paper --symbol BTCUSDT --continuous --interval 30

# 4. Run live trading (dynamic symbol selection)
python main.py --mode live --symbol all --continuous --interval 5
```

## Architecture

```
Symbol Scanner (Binance + CoinGecko + CryptoPanic)
         |
         v
Research Coordinator --> Trading Decision --> Risk Manager --> Execution --> Storage
  (indicators,           (6 strategies,      (12 checks,      (Binance API,   (SQLite,
   WebSocket+REST)        learning system)    kill switches)    algo SL/TP)     audit trail)

Background: Emergency Controller (health + trailing stop) | Regime Detector (Claude Haiku, 15min)
```

### Agents

| Agent | Role |
|-------|------|
| **Research Coordinator** | Market data, technical indicators (EMA, VWAP, RSI, MACD, ATR, Bollinger), multi-timeframe analysis |
| **Trading Decision** | 6 scalping strategies, learning system (6 components), Kelly sizing, fee filter |
| **Risk Manager** | Global authority over all trades. 12 risk checks, kill switches, duplicate guard |
| **Execution Agent** | Binance Futures API. PAPER/LIVE/HYBRID modes. Algo SL/TP orders |
| **Storage & Reporting** | SQLite persistence, SHA-256 audit trail, performance reports |
| **Emergency Controller** | Health monitoring, trailing stop management, orphan reconciliation |

### Strategies

| Strategy | Timeframe | Entry Trigger |
|----------|-----------|---------------|
| EMA Crossover | 5m | EMA(9)/EMA(21) crossover + trend strength |
| VWAP Bounce | 1m | Price near VWAP + volume confirmation |
| RSI Pullback | 5m | RSI reversal in trend + MACD confirmation |
| Bollinger Squeeze | 5m | Low bandwidth + breakout + volume gate |

## Execution Modes

| Mode | Description |
|------|-------------|
| **Paper** | Simulated orders with realistic slippage (5 bps + market impact) |
| **Live** | Real Binance orders + shadow paper execution for comparison |
| **Hybrid** | Both modes with divergence alerts (>0.5% threshold) |

## Configuration

All parameters in `config/trading_config.yaml`:

- Risk limits (3% per trade, 8.5% daily drawdown, 55% max exposure)
- Leverage (5x default, volatility-adjusted)
- Strategy parameters and enable/disable toggles
- Trailing stop settings (dynamic SL/TP, time exits)
- Model routing and LLM configuration
- Symbol scanner settings

## Documentation

| Document | Description |
|----------|-------------|
| [`CLAUDE.md`](CLAUDE.md) | Project conventions, file structure, gotchas |
| [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) | Full system architecture and implementation details |
| [`docs/DATABASE.md`](docs/DATABASE.md) | Database schema, queries, maintenance |
| [`docs/RISK_MANAGEMENT.md`](docs/RISK_MANAGEMENT.md) | Risk controls and safety mechanisms |
| [`docs/SETUP.md`](docs/SETUP.md) | Environment setup and installation |
| [`docs/TESTING.md`](docs/TESTING.md) | Testing scenarios and verification |
| [`agents/*.md`](agents/) | Individual agent specifications |

## Safety

- Kill switches (global, per-symbol, per-strategy, volatility circuit breaker)
- Pre-trade validation (risk checks, fee filter, duplicate guard)
- Post-execution audit trail (SHA-256 hash chain)
- Dynamic trailing stop with orphan reconciliation
- Consecutive loss cooldown (3 losses in 30min -> 15min pause)

## License

Proprietary - All Rights Reserved
