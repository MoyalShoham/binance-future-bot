# Binance Futures Trading Bot

Multi-agent AI trading system for Binance Futures scalping. 6 specialized agents orchestrated via LangGraph, running against a live micro account.

## Architecture

```
Symbol Scanner (Binance movers + CoinGecko trending + CryptoPanic news)
    |
    v
Research Coordinator ──> Trading Decision ──> Risk Manager ──> Execution Agent ──> Storage & Reporting
  (market data,           (6 strategies,      (GLOBAL AUTH,     (Binance API,       (SQLite + audit
   indicators,             learning system,    12 risk checks,   PAPER/LIVE/HYBRID,   trail, P&L,
   WebSocket + REST)       fee filter)         kill switches)    algo SL/TP orders)   reports)

Background threads:
  - Emergency Controller (health checks 60s, trailing stop 5s, orphan reconciliation)
  - Regime Detector (Claude Haiku classifies 9 market regimes every 15min)
```

### Agent Pipeline

1. **Research Coordinator** - Fetches OHLCV, order book (depth=100), 24h ticker. Calculates EMA(9/21/50), VWAP, RSI, MACD, ATR, Bollinger Bands. Multi-timeframe HTF bias. WebSocket streaming with REST fallback.
2. **Trading Decision** - 6 strategies (4 enabled), learning system (6 components), Kelly sizing with real DB win rate, leverage-aware TP/SL, regime overrides. Rule-based only (no LLM).
3. **Risk Manager** - Global authority. 12 risk checks, kill switches, duplicate guard, consecutive loss cooldown, funding rate filter. Rule-based only. Can reject or modify any trade.
4. **Execution Agent** - Binance Futures API. PAPER/LIVE/HYBRID modes. Algo SL/TP orders (STOP_MARKET, TAKE_PROFIT_MARKET via `/fapi/v1/algoOrder`). Order retry with backoff.
5. **Storage & Reporting** - SQLite persistence (WAL mode). SHA-256 hash chain audit trail. Flat TradesDB for fast queries. Daily/weekly/monthly reports.
6. **Emergency Controller** - Background thread. Health monitoring, kill switches, anomaly detection. Manages TrailingStopMonitor for all open position exits.

## Environment

```bash
# Python interpreter (conda env)
.\.conda\python.exe

# Run paper trading
& '.\.conda\python.exe' main.py --mode paper --symbol BTCUSDT --continuous --interval 30

# Run live trading (all symbols, dynamic scanner)
& '.\.conda\python.exe' main.py --mode live --symbol all --continuous --interval 5

# Run backtest
& '.\.conda\python.exe' scripts/run_backtest.py --symbol BTCUSDT --start 2025-12-01 --end 2026-02-01 --interval 5m

# Run walk-forward validation
& '.\.conda\python.exe' scripts/run_backtest.py --symbol BTCUSDT --start 2025-08-01 --end 2026-02-01 --walk-forward

# Compile check all Python files
& '.\.conda\python.exe' -m py_compile main.py

# PowerShell startup script (launches Ollama + bot)
.\start.ps1
```

**Database**: SQLite at `data/trading_system.db` (WAL mode, busy_timeout=5000ms)
**Config**: `config/trading_config.yaml` (single source of truth for all parameters)

## Key Conventions

- **Config is source of truth** - All risk limits, strategy params, and thresholds live in `config/trading_config.yaml`. Never hardcode values that should be configurable.
- **Regime overrides are multipliers** on config values, not absolute replacements. Config stays source of truth.
- **Risk Manager is rule-based only** - No LLM. Deterministic safety checks. Never make it non-deterministic.
- **Learning is soft penalties only** - Never use hard blocks. Hard blocks create death spirals with limited data.
- **Fee-aware trading** - All trades must pass pre-trade filter: expected profit >= 1.5x round-trip fees.
- **Kelly sizing uses real DB stats** - Falls back to 50% win rate if <30 trades.
- **Leverage-aware TP** - TP = SL_distance x leverage (e.g., SL=0.5%, 5x leverage -> TP=2.5%).
- **WebSocket + REST fallback** - Mini ticker + book ticker streams, REST fallback if stale (>10s).
- **SQLite WAL mode** - Enables concurrent thread writes without locking.

## Critical Rules

- **NEVER modify risk limits** without explicit user approval (max_risk_per_trade, max_daily_drawdown, kill switches)
- **NEVER force push** to any branch
- **NEVER disable risk checks or kill switches** in code
- **NEVER hardcode API keys** - use .env file
- **Delete `data/trading_system.db`** to reset all positions and trade history
- **Anthropic SDK** used directly for regime detection (not ModelRouter)

## File Structure

```
binance-future-bot/
├── main.py                              # Entry point, main loop, shutdown
├── config/trading_config.yaml           # All configuration (single source of truth)
├── CLAUDE.md                            # This file
├── agents/
│   ├── *.md                             # Agent specification docs
│   └── implementations/
│       ├── base_agent.py                # Base class with LLM integration
│       ├── research_coordinator.py      # Market data + indicators
│       ├── trading_decision.py          # 6 strategies + learning system
│       ├── risk_manager.py              # 12 risk checks, global authority
│       ├── execution_agent.py           # Order execution (Binance API)
│       ├── storage_reporter.py          # DB persistence + reports
│       └── emergency_controller.py      # Health monitoring + kill switches
├── infrastructure/
│   ├── binance_api/
│   │   ├── client.py                    # Binance Futures API wrapper
│   │   └── indicators.py               # Technical indicator calculations
│   ├── database/
│   │   ├── session.py                   # SQLite/PostgreSQL session manager
│   │   ├── models.py                    # SQLAlchemy ORM models (7 tables)
│   │   ├── queries.py                   # 30+ query methods
│   │   └── trades_db.py                # Flat trades database
│   ├── websocket_manager.py             # Real-time price/book streaming
│   ├── trailing_stop.py                 # Dynamic SL/TP + position sync
│   ├── regime_detector.py               # LLM regime classification
│   ├── execution_modes.py               # OrderExecutor (paper/live/hybrid)
│   └── symbol_scanner.py               # Multi-source symbol discovery
├── orchestration/
│   ├── coordinator.py                   # LangGraph pipeline orchestration
│   ├── model_router.py                  # Cheap-first LLM routing
│   └── state_manager.py                # Pipeline state tracking
├── schemas/                             # JSON schema validation
├── docs/                                # Reference documentation
├── backtesting/
│   ├── __init__.py                      # Package exports
│   ├── data_loader.py                   # Historical kline downloader + parquet cache
│   ├── engine.py                        # Bar-by-bar backtest with TP/SL/trailing
│   └── metrics.py                       # Sharpe, Sortino, profit factor, etc.
├── scripts/
│   └── run_backtest.py                  # CLI backtest runner
├── tests/                               # Unit + integration tests
├── data/                                # SQLite databases + historical parquet
└── logs/                                # Rotating log files
```

## Backtesting

The backtesting module (`backtesting/`, `scripts/run_backtest.py`) reuses live system components (indicators, strategy logic) to simulate historical trading.

### How It Works
- **Data Loader** downloads klines from Binance with pagination, caches as parquet in `data/historical/`
- **Engine** walks bar-by-bar, computes indicators via `TechnicalIndicators.calculate_all()`, evaluates strategies, simulates fills at next candle open with slippage/fees
- **HTF Trend** resampled from base candles (e.g. 5m→1h via 12x factor), EMA 9/21/50 alignment classified as bullish/weak_bullish/bearish/weak_bearish/neutral
- **Confluence Gate** mirrors live system's 5-factor scoring (trend, momentum, ADX, volume, structure). Rejects score ≤ 2, scales position size by tier
- **Walk-Forward Validation** trains on N days, tests on next M days, steps forward — only OOS results count

### Known Backtest Limitations (by design)
- Order book imbalance always 0 (no historical orderbook data)
- No learning adjustments (backtest is pre-learning baseline)
- EMA convergence pre-signal absent
- Strategy base confidence hardcoded (0.72/0.75) vs adaptive in live
- Market regime always DEFAULT (no LLM classification in backtest)
- Multi-timeframe analysis disabled (MTF code guarded by `if mtf`, backtest uses single-HTF resampling)

## Known Gotchas

These bugs have been encountered and fixed. Be aware of them when modifying code:

1. **JSON serialization** - Always use `_serialize_for_json()` before storing data in SQLAlchemy JSON columns. numpy bool_/int/float types and datetime objects cause TypeError.
2. **Schema strictness** - Schemas use `additionalProperties: false`. Every field must be listed. New fields require schema updates.
3. **Schema nullability** - Paper trading returns null for `binance_order_id`, `original_execution_id`, etc. Schemas must allow null for these.
4. **Margin math** - Futures margin = `price * qty / leverage` (NOT `* leverage` - that's notional value).
5. **Algo orders** - Binance moved STOP_MARKET/TAKE_PROFIT_MARKET to Algo Order API (`/fapi/v1/algoOrder`). Uses `algoType=CONDITIONAL`, `triggerPrice` (not `stopPrice`), `clientAlgoId` (not `newClientOrderId`).
6. **Exchange info** - Fetch symbol precision from Binance API at startup. Never hardcode step_size or min_qty.
7. **Logging level** - Python default is WARNING. Always set `logging.basicConfig(level=logging.INFO)`.
8. **DatabaseSession** - No `.execute()` method. Use `session_scope()` context manager.
9. **Stale positions** - DB positions can become stale if Binance closes them server-side. Orphan reconciliation handles this.
10. **Learning death spirals** - Learning must use soft confidence penalties, not hard blocks. Limited data + hard blocks = bot stops trading entirely.
11. **VWAP in backtest** - `calculate_all()` uses `reference_time` param for VWAP daily reset. Live callers omit it (defaults to `datetime.now()`). Backtest must pass candle timestamp or VWAP falls back to rolling window.
12. **get_klines() for historical data** - Use `start_time`/`end_time` (ms) params for paginated downloads. Live callers use only `limit` param.

## Risk Configuration (Current)

| Parameter | Value |
|-----------|-------|
| Max risk per trade | 2% |
| Max daily drawdown | 60% |
| Max portfolio exposure | 55% |
| Max concentration/symbol | 100% (single coin) |
| Max concurrent positions | 1 |
| Default leverage | 10x |
| Min position size | 13% of equity (margin) |
| Max position size | 15% of equity (margin) |
| Min notional floor | $100 (Binance minimum, auto round-up) |
| Min R:R ratio | 1.5:1 |
| Min SL distance | 0.3% |
| Fee filter | 3.0x round-trip fees + 2min min hold |
| Funding rate limit | 0.05% |
| Consecutive loss cooldown | 3 losses in 60min -> 45min pause |
| Global kill switch | 5 consecutive losses -> 2h shutdown |
| Min confidence | 75% |
| Confluence gate | 3+ of 5 factors required (score ≤ 2 rejected) |
| Scanner | Enabled (dynamic multi-source) |
| Strategies | 4 enabled (VWAP bounce disabled) |
| Multi-timeframe | 1h/15m/5m (weights: 0.45/0.35/0.20), primary TF: 5m |
| Max holding time | 15 min |

## Documentation

- `docs/ARCHITECTURE.md` - Full system architecture and implementation details
- `docs/DATABASE.md` - Database schema, queries, and maintenance
- `docs/RISK_MANAGEMENT.md` - Risk controls and safety mechanisms
- `docs/SETUP.md` - Environment setup and installation guide
- `docs/TESTING.md` - Testing scenarios and verification checklist
- `agents/*.md` - Individual agent specifications
