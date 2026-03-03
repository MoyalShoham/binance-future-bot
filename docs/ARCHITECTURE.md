# System Architecture

**Last Updated**: 2026-03-03
**Status**: Production (LIVE trading on Binance Futures mainnet)

---

## Overview

Multi-agent AI trading system for Binance Futures scalping. 6 specialized agents orchestrated via LangGraph, running against a live micro account (~$65-70 equity).

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

---

## Agent Pipeline

### 1. Research Coordinator
Fetches OHLCV, order book (depth=100), 24h ticker, funding rate, open interest. Calculates EMA(9/21/50), VWAP, RSI, MACD, ATR, Bollinger Bands across multiple timeframes. Multi-timeframe HTF bias (1h/15m/5m with weighted scoring). WebSocket streaming with REST fallback if stale (>10s).

### 2. Trading Decision
6 strategies (4 enabled: EMA crossover, RSI pullback, Bollinger squeeze, momentum breakout). Learning system with 6 components (strategy, symbol, hour, regime, funding rate, time-of-day). Kelly sizing with real DB win rate. Leverage-aware TP/SL. Rule-based only (no LLM).

### 3. Risk Manager
Global authority. 12 risk checks including kill switches, daily drawdown, per-trade risk, portfolio exposure, leverage limits, volatility gate, position concentration, margin check, duplicate guard, funding rate filter, consecutive loss cooldown, and fee filter. Rule-based only. Can reject or modify any trade.

### 4. Execution Agent
Binance Futures API (mainnet). PAPER/LIVE/HYBRID modes. Algo SL/TP orders via `/fapi/v1/algoOrder` with `algoType=CONDITIONAL`. Order retry with exponential backoff (3 attempts).

### 5. Storage & Reporting
SQLite persistence (WAL mode, busy_timeout=5000ms). SHA-256 hash chain audit trail. Flat TradesDB for fast queries. Daily/weekly/monthly report generation. JSON/CSV/Markdown export.

### 6. Emergency Controller
Background thread. Health monitoring every 60s (REST API, WebSocket, DB, model APIs). Kill switch management. Anomaly detection. Manages TrailingStopMonitor (checks every 5s) for all open position exits.

---

## Background Systems

### Symbol Scanner
Runs every ~5 minutes. Sources:
- **Binance movers**: Top gainers/losers by 24h price change, filtered by min volume ($200M)
- **CoinGecko trending**: Trending coins API (free, no key needed)
- **CryptoPanic**: News-driven momentum (optional, needs API key)

Multi-source symbols get boosted scores. Always includes BTC, ETH, SOL, XRP as blue-chips. Selects top 4-8 symbols per scan.

### Market Regime Detection
Claude Haiku (`claude-haiku-4-5-20251001`) classifies market regime every 15 minutes. 9 regimes:
- TREND_FOLLOWING, MEAN_REVERSION, HIGH_VOLATILITY
- GREED_EUPHORIA, FEAR_CAPITULATION, LOW_VOLATILITY
- ACCUMULATION, DISTRIBUTION, DEFAULT

Regime overrides are **multipliers** on config values, not absolute replacements. Config stays source of truth.

### Trailing Stop Monitor
Runs every 5 seconds in Emergency Controller thread. Features:
- Dynamic SL: breakeven at 0.5% profit, trail at 0.5% with 0.2% step
- Hard stop from TradingDecision (ATR-based) for initial SL
- Ratcheting TP/SL levels
- Time exit at 15 min max holding
- Position sync with Binance (orphan reconciliation)

### WebSocket Manager
Mini ticker + book ticker streams for real-time price/book data. REST fallback if data stale (>10s). Auto-reconnect on disconnect.

---

## Model Routing

LLM is **disabled** for all real-time agents (adds 15-30s latency, non-deterministic, no statistical edge for scalping). Used only for:
- **Regime Detection**: Claude Haiku every 15min (via Anthropic SDK directly)

Local Ollama (`qwen2.5:14b`) configured as fallback but currently unused.

Model hierarchy (for reference):
- GPT-4o Mini: Classification, simple reasoning
- Gemini 3.0 Flash: Pattern matching, sentiment
- Claude Haiku 4.5: Risk reasoning, regime detection
- Claude Sonnet 4: Critical decisions (unused in production)

---

## Database

- **Development/Production**: SQLite at `data/trading_system.db` (WAL mode)
- 7 tables: research_summaries, trading_decisions, risk_approvals, executions, pnl_ledger, audit_trail, performance_metrics
- Flat trades DB at `data/trades.db` for fast queries
- SHA-256 hash chain audit trail for tamper detection
- See `docs/DATABASE.md` for full schema reference

---

## Execution Safety

- Algo orders via `/fapi/v1/algoOrder` (Binance moved STOP_MARKET/TAKE_PROFIT_MARKET here)
- Idempotent order submission using hash(decision_id + approval_id + timestamp)
- Per-symbol slippage tiers (BTC/ETH: 3bps, majors: 5-6bps, mid-caps: 10bps)
- Duplicate position guard via Binance API check
- Orphan reconciliation for stale DB positions

---

## Risk Limits (Current Production)

| Parameter | Value |
|-----------|-------|
| Max risk per trade | 2% |
| Max daily drawdown | 60% (config: 0.80) |
| Max portfolio exposure | 55% |
| Max concentration/symbol | 100% (single coin mode) |
| Max concurrent positions | 1 |
| Default leverage | 10x (all volatility tiers) |
| Min position size | 13% of equity (margin) |
| Max position size | 15% of equity (margin) |
| Min notional floor | $100 (Binance minimum, auto round-up) |
| Min R:R ratio | 1.5:1 |
| Min SL distance | 0.3% |
| Fee filter | 3.0x round-trip fees + 2min min hold |
| Funding rate limit | 0.05% |
| Consecutive loss cooldown | 3 losses in 60min → 45min pause |
| Global kill switch | 5 consecutive losses → 2h shutdown |
| Min confidence | 75% |
| Confluence gate | 3+ of 5 factors required |
| Max holding time | 15 min |

---

## Backtesting

Module at `backtesting/` with `scripts/run_backtest.py` CLI runner.
- Reuses live system components (indicators, strategy logic)
- Bar-by-bar simulation with TP/SL/trailing
- HTF trend via resampled EMAs
- Confluence gate mirrors live 5-factor scoring
- Walk-forward validation support
- Data cached as parquet in `data/historical/`

See CLAUDE.md for known backtest limitations.

---

## File Structure

```
binance-future-bot/
├── main.py                              # Entry point, main loop, shutdown
├── config/trading_config.yaml           # All configuration (single source of truth)
├── CLAUDE.md                            # Project conventions & instructions
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
│   │   ├── session.py                   # SQLite session manager (WAL mode)
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
