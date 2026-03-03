# Testing Guide

**Last Updated**: 2026-03-03

---

## Quick Start

### Step 1: Environment Setup

Create your `.env` file:
```bash
cp .env.example .env
```

Edit `.env` with your credentials:
```env
BINANCE_API_KEY=your_api_key_here
BINANCE_API_SECRET=your_api_secret_here
ANTHROPIC_API_KEY=your_anthropic_key_here
```

### Step 2: Install Dependencies

```powershell
& '.\.conda\python.exe' -m pip install -r requirements.txt
```

### Step 3: Configure for Testing

Edit `config/trading_config.yaml`:

```yaml
trading:
  enabled: true
  execution_mode: "paper"   # KEEP THIS FOR TESTING
  testnet: false             # Uses mainnet market data
  max_concurrent_positions: 1
  default_leverage: 10

risk:
  max_risk_per_trade_pct: 0.02    # 2% per trade
  max_daily_drawdown_pct: 0.80    # 60% daily drawdown limit
  max_portfolio_exposure_pct: 0.55  # 55% max deployed
```

### Step 4: Run First Test (Single Cycle)

```powershell
& '.\.conda\python.exe' main.py --mode paper --symbol BTCUSDT
```

### Step 5: Run Continuous Mode

```powershell
& '.\.conda\python.exe' main.py --mode paper --symbol BTCUSDT --continuous --interval 30
```

Press Ctrl+C to stop gracefully.

---

## Testing Scenarios

### Test 1: Paper Trading - Basic Flow
**Goal**: Verify full pipeline works end-to-end

```powershell
& '.\.conda\python.exe' main.py --mode paper --symbol BTCUSDT
```

**Check**:
- Connects to Binance API (mainnet)
- Fetches market data (OHLCV, order book, ticker)
- Calculates indicators (EMA, RSI, MACD, ATR, VWAP, Bollinger Bands)
- Makes a decision (LONG/SHORT/NO_TRADE)
- Risk Manager evaluates (12 checks)
- Simulates execution
- Logs everything

### Test 2: Multi-Symbol with Scanner

```powershell
& '.\.conda\python.exe' main.py --mode paper --symbol all --continuous --interval 30
```

**Check**: Scanner discovers symbols, rotates through them each cycle.

### Test 3: Risk Rejection

Edit `config/trading_config.yaml`:
```yaml
risk:
  max_risk_per_trade_pct: 0.0001  # Very low - should reject most trades
```

```powershell
& '.\.conda\python.exe' main.py --mode paper --symbol BTCUSDT
```

**Expected**: Risk Manager should REJECT or heavily MODIFY the trade.

### Test 4: Kill Switch

```powershell
# Set in config: risk.kill_switches.global: true
& '.\.conda\python.exe' main.py --mode paper --symbol BTCUSDT
```

**Expected**: All trades REJECTED with "GLOBAL KILL SWITCH ACTIVE".

### Test 5: Continuous Stability

```powershell
& '.\.conda\python.exe' main.py --mode paper --symbol BTCUSDT --continuous --interval 30
```

**Let it run for 30 minutes** (60 cycles). Monitor memory usage, error rate, decision quality.

### Test 6: Backtest

```powershell
& '.\.conda\python.exe' scripts/run_backtest.py --symbol BTCUSDT --start 2025-12-01 --end 2026-02-01 --interval 5m
```

**Check**: Downloads klines, runs bar-by-bar simulation, outputs metrics.

---

## Verification Checklist

After running tests, verify:

- [ ] **Connection**: System connects to Binance mainnet successfully
- [ ] **Market Data**: Fetches current price, order book, indicators
- [ ] **WebSocket**: Mini ticker + book ticker streams active
- [ ] **Indicators**: Calculates EMA, RSI, MACD, ATR, VWAP, Bollinger Bands
- [ ] **Multi-Timeframe**: HTF bias from 1h/15m/5m
- [ ] **Strategy Selection**: Evaluates 4 enabled strategies
- [ ] **Learning System**: Adjusts confidence from DB history
- [ ] **Decision Making**: Makes LONG/SHORT/NO_TRADE decision
- [ ] **Confluence Gate**: Requires 3+ of 5 factors
- [ ] **Risk Checks**: All 12 risk checks execute
- [ ] **Position Sizing**: Fixed percentage with min/max bounds
- [ ] **Approval Logic**: APPROVED/REJECTED/MODIFIED correctly
- [ ] **Paper Execution**: Simulates order with slippage
- [ ] **Trailing Stop**: Background monitor tracks positions
- [ ] **Regime Detection**: Claude Haiku classifies market regime
- [ ] **Logging**: All events logged clearly
- [ ] **Error Handling**: Gracefully handles errors
- [ ] **Shutdown**: Ctrl+C stops gracefully

---

## Performance Benchmarks

Expected performance (single cycle, no LLM calls):
- **Total Time**: 1-3 seconds
- **Research**: 500-1000ms (REST API + indicator calculation)
- **Decision**: 50-200ms (rule-based strategies)
- **Risk Check**: 10-50ms (rule-based)
- **Execution**: 100-500ms (Binance API for live, instant for paper)
- **Storage**: 10-50ms (SQLite WAL mode)

With regime detection (every 15min):
- **Regime Classification**: 1-3 seconds (Claude Haiku API call)

---

## Troubleshooting

### "Missing environment variables"
Make sure `.env` exists with `BINANCE_API_KEY` and `BINANCE_API_SECRET`.

### "Failed to connect to Binance API"
- Check API keys are correct and have Futures permissions
- Ensure Binance API is accessible (not blocked by firewall)
- Verify IP whitelist settings

### "ModuleNotFoundError"
```powershell
& '.\.conda\python.exe' -m pip install -r requirements.txt
```

### "Database locked"
WAL mode should handle concurrent writes. If persistent, delete `data/trading_system.db` and restart.

---

## Safety Reminders

**NEVER**:
- Run in `live` mode without paper testing first
- Disable risk checks or kill switches
- Ignore consecutive loss cooldowns

**ALWAYS**:
- Start with paper trading
- Monitor the logs
- Understand each trade decision
- Keep kill switches accessible

See `docs/RISK_MANAGEMENT.md` for full risk controls.
