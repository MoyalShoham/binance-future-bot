# Setup Guide

**Last Updated**: 2026-03-03

---

## Prerequisites

- Python 3.11+ (via local conda environment at `.conda/`)
- Binance Futures account with API keys (mainnet)
- Anthropic API key (for regime detection)
- Optional: CryptoPanic API key (for news-driven symbol scanning)

---

## Environment

The project uses a local conda environment at `.conda/` in the project root. All commands use the local Python interpreter directly:

```powershell
# PowerShell syntax (Windows)
& '.\.conda\python.exe' main.py --mode paper --symbol BTCUSDT
```

### Install Dependencies

```powershell
& '.\.conda\python.exe' -m pip install -r requirements.txt
```

---

## Configuration

### `.env` - API Credentials

```bash
BINANCE_API_KEY=your_mainnet_key
BINANCE_API_SECRET=your_mainnet_secret
ANTHROPIC_API_KEY=your_anthropic_key       # Required for regime detection
CRYPTOPANIC_API_KEY=your_cryptopanic_key   # Optional: news-driven symbol scanning
```

### `config/trading_config.yaml` - Trading Parameters

Single source of truth for all parameters. Key settings:

```yaml
trading:
  enabled: true
  execution_mode: "live"   # paper | live | hybrid
  testnet: false           # Mainnet
  max_concurrent_positions: 1
  default_leverage: 10

risk:
  max_risk_per_trade_pct: 0.02      # 2% per trade
  max_daily_drawdown_pct: 0.80      # 60% daily drawdown limit
  max_portfolio_exposure_pct: 0.55  # 55% max deployed
  max_position_concentration_pct: 1.0  # 100% (single coin mode)
```

---

## Running the System

### Paper Trading (Single Symbol)

```powershell
& '.\.conda\python.exe' main.py --mode paper --symbol BTCUSDT --continuous --interval 30
```

### Live Trading (All Symbols, Dynamic Scanner)

```powershell
& '.\.conda\python.exe' main.py --mode live --symbol all --continuous --interval 5
```

The symbol scanner automatically discovers hot symbols every ~5 minutes from:
- Binance movers (top gainers/losers by 24h change)
- CoinGecko trending coins
- CryptoPanic news (if API key configured)
- Always includes blue-chips: BTC, ETH, SOL, XRP

### Live Trading (Single Symbol)

```powershell
& '.\.conda\python.exe' main.py --mode live --symbol BTCUSDT --continuous --interval 5
```

### Single Cycle (Testing)

```powershell
& '.\.conda\python.exe' main.py --mode paper --symbol BTCUSDT
```

### PowerShell Startup Script

```powershell
.\start.ps1  # Launches Ollama + bot
```

---

## Execution Modes

### PAPER Mode
- No real money — simulates orders with realistic slippage
- Uses live Binance market data (mainnet)
- Perfect for testing strategies
- Configurable simulated balance in config

### LIVE Mode (Production)
- Real money on Binance Futures mainnet
- Algo SL/TP orders placed on exchange
- 12 risk checks on every trade
- Kill switches for emergency stops

### HYBRID Mode
- Runs both PAPER and LIVE simultaneously
- Alerts if divergence > 0.5%
- Best for validating execution quality

---

## What Happens During a Trading Cycle

```
1. Symbol Scanner (if --symbol all)
   ✓ Discovers hot symbols from multiple sources
   ✓ Selects top 4-8 symbols per scan

2. Research Coordinator
   ✓ Fetches OHLCV, order book (depth=100), 24h ticker
   ✓ Calculates EMA(9/21/50), VWAP, RSI, MACD, ATR, Bollinger Bands
   ✓ Multi-timeframe HTF bias (1h/15m/5m)
   ✓ WebSocket streaming with REST fallback

3. Trading Decision
   ✓ Evaluates 4 strategies (EMA crossover, RSI pullback, Bollinger squeeze, momentum breakout)
   ✓ Learning system adjusts confidence (6 components)
   ✓ Requires 75%+ confidence + 3+ confluence factors

4. Risk Manager (GLOBAL AUTHORITY)
   ✓ Runs 12 risk checks
   ✓ Approves/Rejects/Modifies trade

5. Execution Agent
   ✓ Places market order + algo SL/TP orders
   ✓ Order retry with exponential backoff

6. Storage & Reporting
   ✓ Persists all data to SQLite
   ✓ SHA-256 hash chain audit trail

7. Emergency Controller (background)
   ✓ Health monitoring every 60s
   ✓ Trailing stop monitor every 5s
   ✓ Kill switch management
   ✓ Orphan reconciliation
```

---

## Backtesting

```powershell
# Standard backtest
& '.\.conda\python.exe' scripts/run_backtest.py --symbol BTCUSDT --start 2025-12-01 --end 2026-02-01 --interval 5m

# Walk-forward validation
& '.\.conda\python.exe' scripts/run_backtest.py --symbol BTCUSDT --start 2025-08-01 --end 2026-02-01 --walk-forward
```

---

## Troubleshooting

### "ModuleNotFoundError"
```powershell
& '.\.conda\python.exe' -m pip install -r requirements.txt
```

### "Binance API Error"
- Check API keys in `.env`
- Verify keys have Futures trading permissions
- Check IP whitelist settings

### "Database Error"
- Delete `data/trading_system.db` and let it recreate on startup
- Check write permissions on the `data/` folder

### "Database locked"
- WAL mode is enabled (should handle concurrent writes)
- Check busy_timeout is set (5000ms default)

---

## Useful Commands

```powershell
# Compile check all Python files
& '.\.conda\python.exe' -m py_compile main.py

# Run tests
& '.\.conda\python.exe' -m pytest tests/

# View database
sqlite3 data/trading_system.db "SELECT * FROM trading_decisions LIMIT 5;"

# Check system status
& '.\.conda\python.exe' -c "from main import *; print('System ready')"
```

---

## Safety Reminders

- **12 risk checks** enforce safety on every trade
- **Kill switches** available (global, symbol, strategy)
- **Consecutive loss cooldown**: 3 losses in 60min → 45min pause
- **Global kill switch**: Auto-activates after 5 consecutive losses (2h shutdown)
- **Trailing stop**: Dynamic SL with breakeven at 0.5% profit
- **Max holding time**: 15 minutes (forced exit)

See `docs/RISK_MANAGEMENT.md` for full risk controls.
