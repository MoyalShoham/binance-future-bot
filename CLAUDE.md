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
├── tests/                               # Unit + integration tests
├── data/                                # SQLite databases
└── logs/                                # Rotating log files
```

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

## Risk Configuration (Current)

| Parameter | Value |
|-----------|-------|
| Max risk per trade | 3% |
| Max daily drawdown | 8.5% |
| Max portfolio exposure | 55% |
| Max concentration/symbol | 25% |
| Max concurrent positions | 3 |
| Default leverage | 5x |
| Min R:R ratio | 2.0:1 |
| Min SL distance | 0.5% |
| Fee filter | 1.5x round-trip fees |
| Funding rate limit | 0.05% |
| Consecutive loss cooldown | 3 losses in 30min -> 15min pause |

## Documentation

- `docs/ARCHITECTURE.md` - Full system architecture and implementation details
- `docs/DATABASE.md` - Database schema, queries, and maintenance
- `docs/RISK_MANAGEMENT.md` - Risk controls and safety mechanisms
- `docs/SETUP.md` - Environment setup and installation guide
- `docs/TESTING.md` - Testing scenarios and verification checklist
- `agents/*.md` - Individual agent specifications
