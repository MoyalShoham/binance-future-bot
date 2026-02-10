# Binance Futures Trading Bot - System Summary

*Last updated: 2026-02-10*

## System Overview

Multi-agent AI trading system for Binance Futures scalping with 6 specialized agents orchestrated via LangGraph. Currently running in **LIVE mode** against a micro account (~$6 USDT) across 8 altcoin pairs.

## Architecture

```
Research Coordinator (market data + indicators + LLM analysis)
       |
Trading Decision Agent (4 scalping strategies + LLM enhancement)
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

## Current Runtime Status (Feb 9, 2026)

| Metric | Value |
|--------|-------|
| **Uptime** | Running since Feb 8, 22:34 (multiple restarts) |
| **Total cycles logged** | ~1,228 completed cycles |
| **Trade signals (LONG/SHORT)** | 35 signals generated |
| **Trades REJECTED by Risk Manager** | 29 (83%) |
| **Trades MODIFIED by Risk Manager** | 2 (6%) |
| **Trades reaching execution** | 2 (both FAILED) |
| **Successful fills** | 0 |
| **NO_TRADE decisions** | ~3,657 (vast majority) |
| **Errors logged** | 2 (both Binance precision errors on ADAUSDT) |
| **Trailing stop closures** | 0 (no positions opened) |

### Key Observations from Logs

1. **The bot has not successfully executed a single trade in this session.** Every trade signal is either:
   - Rejected by Risk Manager due to insufficient margin (needs $18-36, has ~$5.70)
   - Failed at execution due to Binance precision error (ADAUSDT quantity precision)

2. **LLM confidence adjustments are almost always negative** (-0.10 to -0.30), meaning the LLM consistently reduces strategy confidence. This is overly cautious.

3. **Model escalation is excessive**: 978 escalations logged. Early session used Anthropic (Haiku->Sonnet) for every cycle. After switching to Ollama at 17:10, no more escalations occur (Ollama has no escalation chain).

4. **Cycle time**: ~17-22s with Ollama (down from ~27-35s with Anthropic API calls + escalation).

## LLM Provider History

| Period | Provider | Model | Escalation | Cost |
|--------|----------|-------|------------|------|
| Feb 8 22:34 - Feb 9 16:30 | Anthropic | Haiku -> Sonnet | 978 escalations | ~$2-5 estimated |
| Feb 9 17:10 - present | Ollama (local) | qwen2.5:14b | None (single tier) | $0 |

## Trading Symbols

XRPUSDT, LINKUSDT, DOGEUSDT, 1000SHIBUSDT, 1000FLOKIUSDT, ADAUSDT, DOTUSDT, AVAXUSDT

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
| Max risk per trade | 10% ($0.60) |
| Max daily drawdown | 20% ($1.20) |
| Max portfolio exposure | 90% |
| Leverage (low/med/high vol) | 20x / 15x / 10x |
| Volatility gate | >5% blocks trading |
| Stop loss/take profit orders | DISABLED (Binance Algo API migration) |

## Infrastructure

- **Database**: SQLite (12 MB), 7 tables with SHA-256 audit trail
- **Execution**: Live mode, stop/take-profit orders disabled
- **Trailing stops**: Enabled (0.2% activation, 0.5% trail, 10 min max hold)
- **Logging**: Structured (structlog), console=INFO (1 CYCLE line/symbol), file=DEBUG (full detail)
- **Trades DB**: `data/trades.db` - flat queryable table (no JSON blobs), separate from main DB

## Uncommitted Changes

- `config/trading_config.yaml` - Switched provider to Ollama
- `orchestration/model_router.py` - Added Ollama/ChatOllama support, init log→debug
- `orchestration/coordinator.py` - All node/routing logs→debug
- `infrastructure/trailing_stop.py` - Zero-quantity safety check, monitoring log→debug, trades_db integration
- `infrastructure/database/trades_db.py` - **NEW**: flat trades table + TradesDB class
- `infrastructure/database/__init__.py` - Export TradesDB, Trade
- `agents/implementations/base_agent.py` - Agent initialized→debug
- `agents/implementations/research_coordinator.py` - All verbose logs→debug
- `agents/implementations/trading_decision.py` - All verbose logs→debug (kept LLM confidence warning)
- `agents/implementations/risk_manager.py` - Verbose logs→debug (kept REJECTED at WARNING)
- `agents/implementations/execution_agent.py` - Verbose logs→debug (kept stop loss placed at INFO)
- `agents/implementations/storage_reporter.py` - Verbose logs→debug, trades_db integration
- `agents/implementations/emergency_controller.py` - Pass trades_db to TrailingStopMonitor
- `main.py` - Root/file logger→DEBUG, CYCLE summary line, TradesDB init/wiring
- `prompts/base.py` - Prompt updates
- `schemas/trading_decision.schema.json` - Schema fix

---

## Critical Issues

### 1. Account too small for any configured symbol
**Severity: BLOCKING**

The risk manager correctly rejects every trade because the account balance (~$5.70 USDT) cannot cover the minimum margin required for any of the 8 configured symbols ($18-36 USDT needed even at 20x leverage). The bot is running 24/7 burning compute cycles but can never trade.

### 2. ADAUSDT precision error
**Severity: HIGH**

The only 2 trades that passed risk checks (MODIFIED, not APPROVED) failed at Binance with `APIError(code=-1111): Precision is over the maximum defined for this asset`. The execution agent is sending a quantity with too many decimal places for ADAUSDT.

### 3. LLM always reduces confidence
**Severity: MEDIUM**

In almost every logged decision, the LLM applies a negative confidence adjustment (-0.10 to -0.30). Out of ~35 trade signals, the LLM boosted confidence only once (+0.05). This suggests the LLM prompt is biased toward caution, effectively nullifying many legitimate strategy signals.

### 4. Model escalation cost burn (resolved)
**Severity: LOW (fixed)**

Before the Ollama switch, every single cycle triggered Haiku->Sonnet escalation (978 times). Haiku always returned confidence ~0.55, below the 0.70 threshold, triggering an expensive Sonnet call. This was a systematic issue where Haiku consistently underperformed the confidence threshold. Now resolved by switching to Ollama.

### 5. No protective orders on positions
**Severity: HIGH (when trading resumes)**

Stop-loss and take-profit orders are disabled because Binance moved them to the Algo Order API. Positions are only protected by the trailing stop monitor (polling every 10 seconds) - a 10-second gap with no protection if the bot crashes.

---

## Suggested Improvements

### Immediate (DONE - Fixed Feb 9)

1. **~~Fix minimum position sizing for micro accounts~~** FIXED - Position sizing is now leverage-aware. `_estimate_position_size` caps notional at `equity * leverage * 0.80`. Risk manager's `_calculate_modifications` also caps position size to fit available margin. With $6 equity and 10x leverage, max notional ~$48, margin ~$4.80.

2. **~~Fix ADAUSDT quantity precision~~** FIXED - `OrderExecutor` now fetches real exchange info from Binance API at startup (`get_symbol_info`). Step sizes, min quantities, and min notional are loaded dynamically. Hardcoded fallback table also corrected (ADAUSDT step_size: 0.1 -> 1).

3. **Commit all changes** - Multiple files modified, need committing.

4. **~~Recalibrate LLM confidence adjustment~~** FIXED - Changed from asymmetric (-0.30/+0.15) to symmetric (-0.15/+0.15). Prompt rewritten to default to 0.0 adjustment (no change) unless specific reason exists. Removed pessimistic bias language.

### Short-term (Trading Quality)

5. **Implement Binance Algo Order API** - Re-enable protective stop-loss orders using the new API endpoint so positions aren't solely dependent on the trailing stop polling loop.

6. **~~Add symbol exchange info caching~~** DONE - Part of fix #2.

### Medium-term (System Maturity)

7. **Add a performance dashboard** - The `performance_metrics` table exists but is never queried. Add a simple CLI report or web dashboard showing win rate, P&L, Sharpe ratio.

8. **Parallel symbol processing** - Currently processes 8 symbols sequentially (~17s each = ~136s full cycle). Use asyncio or threading to analyze symbols in parallel.

9. **Backtest before live** - No backtesting system exists. The 4 strategies should be validated against historical data before risking real funds.

10. **~~Reduce log verbosity~~** DONE - Console shows 1 CYCLE summary line per symbol per cycle (~500 lines/hour instead of ~25,000). Full DEBUG detail preserved in `logs/trading_system.log`. Added `data/trades.db` with flat queryable `trades` table for easy SQL analysis.

11. **Add Ollama health check** - If Ollama is down, the system should gracefully fall back to API providers or pause trading, not crash.
