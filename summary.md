# Binance Futures Trading Bot - System Summary

*Last updated: 2026-02-12*

## System Overview

Multi-agent AI trading system for Binance Futures scalping with 6 specialized agents orchestrated via LangGraph. Running in **LIVE mode** against a micro account (~$125 USDT). Symbols selected dynamically via CoinGecko trending scanner + configured watchlist.

## Architecture

```
Research Coordinator (market data + technical indicators, rule-based)
       |
Trading Decision Agent (4 scalping strategies, rule-based)
       |
Risk Manager (GLOBAL AUTHORITY - rule-based, no LLM)
    /     \
APPROVED   REJECTED --> Store & skip
    |
Execution Agent (Binance Futures API)
    |
Storage & Reporting (SQLite + audit trail)

Emergency Controller (background thread: health checks + trailing stops)
```

## LLM Decision (Feb 12, 2026)

**LLM disabled for all real-time trading agents.** Rationale:
- Added 15-30s latency per symbol (critical for scalping)
- Non-deterministic: same data could produce different decisions, impossible to backtest
- No statistical edge: language models don't predict price movements
- Single point of failure (Ollama going down degrades the system)
- Rule-based indicators (EMA, VWAP, order book, momentum) provide the actual quantitative edge
- LLM previously biased toward caution, reducing confidence on most signals (-0.10 to -0.30)

Ollama (qwen2.5:14b) remains configured and available for future use in post-trade analysis/reporting.

## Symbol Selection

Dynamic scanner (`SymbolScanner`) combines:
- **Binance**: Top volume futures pairs
- **CoinGecko**: Trending coins
- **Fallback**: Config watchlist (XRPUSDT, LINKUSDT, DOGEUSDT, 1000SHIBUSDT, 1000FLOKIUSDT, ADAUSDT, DOTUSDT, AVAXUSDT)
- Symbols with open positions always included (prevents orphaned trades)
- Re-scans each round in continuous mode

## Scalping Strategies

| Strategy | Timeframe | Hold Time | Trigger |
|----------|-----------|-----------|---------|
| EMA Crossover | 5m | 3 min | EMA(9)/EMA(21) cross |
| VWAP Bounce | 1m | 2 min | Price at VWAP deviation bands |
| Order Book Imbalance | 1m | 90 sec | 30%+ buy/sell imbalance |
| Momentum Breakout | 5m | 4 min | Breakout on 1.5x volume |

## Risk Configuration

| Parameter | Value |
|-----------|-------|
| Max risk per trade | 3% |
| Max daily drawdown | 6% |
| Max portfolio exposure | 55% |
| Max concentration per symbol | 25% |
| Leverage (low/med/high vol) | 5x / 3x / 2x |
| Stop loss/take profit orders | Enabled (Binance Algo API) |

## Infrastructure

- **Database**: SQLite with WAL mode (`data/trading_system.db`)
- **Trades DB**: `data/trades.db` - flat queryable table (no JSON blobs), separate from main DB
- **Execution**: Live mode with stop-loss and take-profit algo orders
- **Trailing stops**: Enabled (0.2% activation, 0.5% trail, 10 min max hold)
- **Logging**: Structured (structlog), console=INFO (1 CYCLE line/symbol), file=DEBUG
- **Startup script**: `start.ps1` - launches Ollama + bot in one command

## Recent Changes (Feb 12, 2026)

### Performance & Architecture
- Disabled LLM for all real-time trading agents (rule-based only for speed + determinism)
- Parallelized API calls for faster data gathering
- Optimized DB queries with WAL mode and busy_timeout
- Added Ollama timeout configuration (30s)
- Updated paper trading balance to $125

### Bug Fixes
- Fixed SQLAlchemy detached instance error in `trades_db.py` (logger accessed Trade object after session closed)
- Fixed JSON schema null handling for paper trading fields
- Fixed numpy type serialization for SQLAlchemy JSON columns
- Fixed futures margin calculation (price * qty / leverage)

### New Features
- `start.ps1` - PowerShell startup script (Ollama + bot)
- `infrastructure/symbol_scanner.py` - Dynamic symbol selection via CoinGecko + Binance volume
- Smart trailing stop system with volatility-adjusted parameters
- Clean console logs (1 line per cycle per symbol)
- Flat trades database for easy SQL analysis
