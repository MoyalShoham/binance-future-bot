# Multi-Agent Trading System - Setup Guide

## Quick Setup (Recommended)

### Option 1: Automated Setup (Windows Batch)

1. **Double-click** `setup_conda_env.bat`
2. Wait 3-5 minutes for installation
3. Done! ✅

### Option 2: Automated Setup (PowerShell)

1. **Right-click** `setup_conda_env.ps1` → Run with PowerShell
2. Wait 3-5 minutes for installation
3. Done! ✅

### Option 3: Manual Setup

Open **Anaconda Prompt** or **Command Prompt** and run:

```bash
# Navigate to project directory
cd C:\Users\User\Desktop\binance-future-bot

# Create conda environment
conda create -n trading-system python=3.11 -y

# Activate environment
conda activate trading-system

# Upgrade pip
python -m pip install --upgrade pip

# Install dependencies
pip install -r requirements.txt
```

---

## Running the System

### Quick Test (Single Cycle)

**Method 1: Use the run script**
- Double-click `run_trading_system.bat`

**Method 2: Manual command**
```bash
# Activate environment first
conda activate trading-system

# Run single cycle
python main.py --mode paper --symbol BTCUSDT
```

### Continuous Trading (Every 5 Minutes)

```bash
conda activate trading-system
python main.py --mode paper --symbol BTCUSDT --continuous --interval 300
```

### Test Different Symbols

```bash
# Bitcoin
python main.py --mode paper --symbol BTCUSDT

# Ethereum
python main.py --mode paper --symbol ETHUSDT

# Solana
python main.py --mode paper --symbol SOLUSDT
```

---

## What Happens During a Trading Cycle

```
1. Research Coordinator Agent
   ✓ Fetches BTCUSDT market data from Binance testnet
   ✓ Calculates technical indicators (EMA, RSI, MACD, ATR)
   ✓ Analyzes market sentiment
   ✓ Classifies market regime

2. Trading Decision Agent
   ✓ Evaluates 4 scalping strategies
   ✓ Selects best signal (if confidence >= 70%)
   ✓ Calculates entry/exit levels
   ✓ Estimates position size

3. Risk Management Agent (GLOBAL AUTHORITY)
   ✓ Runs 9 risk checks
   ✓ Validates against kill switches
   ✓ Approves/Rejects/Modifies trade

4. Execution Agent
   ✓ Simulates order execution (paper mode)
   ✓ Calculates realistic slippage
   ✓ Places stop loss & take profit orders

5. Storage & Reporting Agent
   ✓ Saves all data to database
   ✓ Creates audit trail
   ✓ Generates reports

6. Emergency Controller (background)
   ✓ Monitors system health
   ✓ Checks kill switches
   ✓ Detects anomalies
```

---

## Execution Modes

### PAPER Mode (Current - Safe)
- **No real money**
- Simulates orders with realistic slippage
- Perfect for testing
- Uses Binance testnet API

### LIVE Mode (After Testing)
- **Real money** - use with caution!
- Actual orders on Binance
- Includes shadow paper execution for comparison
- Requires thorough testing first

### HYBRID Mode (Advanced)
- Runs both PAPER and LIVE simultaneously
- Alerts if divergence > 0.5%
- Best for validating execution quality

---

## Configuration Files

### `.env` - API Credentials
```bash
# Already configured with your keys ✓
BINANCE_API_KEY=your_testnet_key
BINANCE_API_SECRET=your_testnet_secret
OPENAI_API_KEY=your_openai_key
ANTHROPIC_API_KEY=your_claude_key
GOOGLE_API_KEY=your_gemini_key
```

### `config/trading_config.yaml` - Trading Parameters
```yaml
trading:
  enabled: true
  execution_mode: "paper"  # Start with paper!
  testnet: true            # Use Binance testnet

risk:
  max_risk_per_trade_pct: 0.02     # 2% max per trade
  max_daily_drawdown_pct: 0.05     # 5% daily drawdown limit
  max_portfolio_exposure_pct: 0.70 # 70% max deployed
```

---

## Troubleshooting

### "conda: command not found"
- Make sure Anaconda/Miniconda is installed
- Use Anaconda Prompt instead of regular CMD

### "ModuleNotFoundError"
- Activate environment: `conda activate trading-system`
- Reinstall: `pip install -r requirements.txt`

### "Binance API Error"
- Check your testnet API keys in `.env`
- Verify keys are from Binance Futures Testnet (not spot testnet)

### "Database Error"
- Delete `data/trading_system.db` and let it recreate
- Check permissions on the `data/` folder

### "Import Error: No module named 'langchain'"
- Environment not activated
- Run: `conda activate trading-system`

---

## Useful Commands

```bash
# Check environment
conda env list

# Activate environment
conda activate trading-system

# Deactivate environment
conda deactivate

# Update dependencies
pip install -r requirements.txt --upgrade

# Run tests
pytest tests/

# View database
sqlite3 data/trading_system.db "SELECT * FROM trading_decisions LIMIT 5;"

# Clean logs
rm -rf logs/*

# Check system status
python -c "from main import *; print('System ready ✓')"
```

---

## Next Steps After Setup

1. **Run single test cycle** - Verify everything works
2. **Check the logs** - Review console output
3. **Inspect database** - See stored data
4. **Run continuous mode** - Let it run for 1 hour
5. **Analyze results** - Check strategy performance
6. **Tune parameters** - Adjust risk limits if needed
7. **Consider live trading** - Only after thorough testing!

---

## Safety Reminders

✅ **Currently in PAPER mode** - No real money at risk
✅ **Using testnet** - Safe Binance environment
✅ **Kill switches enabled** - Emergency stop available
✅ **Risk limits enforced** - 9 checks on every trade

⚠️ **Before going LIVE:**
- Test in paper mode for at least 1 week
- Verify all strategies work as expected
- Start with small position sizes (10% of target)
- Monitor continuously for first 24 hours

---

## Support

- Documentation: `IMPLEMENTATION_PROGRESS.md`
- Risk system: `docs/RISK_MANAGEMENT.md`
- Database guide: `docs/DATABASE_INTEGRATION.md`

---

**Ready to trade? Run:**
```bash
conda activate trading-system
python main.py --mode paper --symbol BTCUSDT
```

Good luck! 🚀
