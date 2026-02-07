# Quick Start Guide

Get your trading system running in 5 minutes!

## Prerequisites

- Python 3.8+
- Binance account with API keys
- Basic command line knowledge

---

## Step 1: Get Binance API Keys

### Option A: Testnet (Recommended for Testing)
1. Go to https://testnet.binancefuture.com/
2. Register a testnet account
3. Generate API keys
4. Note: Testnet uses fake money - safe for testing!

### Option B: Real Binance Account
1. Go to https://www.binance.com/en/my/settings/api-management
2. Create new API key
3. Enable "Futures" trading
4. **IMPORTANT**: Set IP restrictions for security
5. **DO NOT** enable withdrawals

---

## Step 2: Configure Environment

```bash
# 1. Copy environment template
cp .env.example .env

# 2. Edit .env file
nano .env
# OR
notepad .env
```

Add your API keys:
```env
BINANCE_API_KEY=your_actual_api_key_here
BINANCE_API_SECRET=your_actual_api_secret_here
```

---

## Step 3: Install Dependencies

```bash
pip install -r requirements.txt
```

This installs:
- python-binance (Binance API)
- pandas, ta (Technical analysis)
- langchain (Agent orchestration)
- And more...

---

## Step 4: Configure for Testing

Edit `config/trading_config.yaml`:

```yaml
trading:
  enabled: true
  execution_mode: "paper"  # KEEP THIS - uses fake orders
  max_concurrent_positions: 1

risk:
  max_risk_per_trade_pct: 0.01  # 1% risk for testing
```

---

## Step 5: Run Your First Test!

```bash
python main.py --mode paper --symbol BTCUSDT
```

**What Happens**:
1. ✅ Connects to Binance
2. ✅ Fetches market data (price, indicators)
3. ✅ Analyzes with 4 strategies
4. ✅ Risk Manager evaluates (8 checks)
5. ✅ Simulates execution (paper mode)
6. ✅ Logs everything

**Expected Output**:
```
INFO Starting Multi-Agent AI Trading System
INFO Configuration loaded
INFO Binance client initialized
INFO All agents initialized
INFO Starting trading cycle symbol=BTCUSDT mode=PAPER
INFO Research node started
INFO Decision node started decision=LONG confidence=0.82
INFO Risk check node started
INFO Trade approved as-is
INFO Execution node started
INFO Order created (PAPER) symbol=BTCUSDT side=BUY
INFO Trading cycle completed total_time_ms=1250
```

---

## Step 6: Run Continuous Mode

```bash
python main.py --mode paper --symbol BTCUSDT --continuous --interval 120
```

This runs a trading cycle every 120 seconds (2 minutes).

**Press Ctrl+C to stop.**

---

## Common Commands

```bash
# Single test (BTC)
python main.py --mode paper --symbol BTCUSDT

# Single test (ETH)
python main.py --mode paper --symbol ETHUSDT

# Continuous (every 60 seconds)
python main.py --mode paper --symbol BTCUSDT --continuous --interval 60

# Continuous (every 5 minutes)
python main.py --mode paper --symbol BTCUSDT --continuous --interval 300

# Different symbols
python main.py --mode paper --symbol SOLUSDT
python main.py --mode paper --symbol BNBUSDT
```

---

## Troubleshooting

### "Missing environment variables"
→ Make sure `.env` exists with `BINANCE_API_KEY` and `BINANCE_API_SECRET`

### "Failed to connect to Binance API"
→ Check your API keys are correct
→ Verify Futures trading is enabled on your API key

### "Module not found"
→ Run: `pip install -r requirements.txt`

### BinanceAPIException
→ Check API key permissions
→ Ensure not IP-restricted (or add your IP)

---

## What to Monitor

When running, watch for:
- ✅ **Decision**: LONG, SHORT, or NO_TRADE
- ✅ **Confidence**: 0.70-1.00 (higher = more confident)
- ✅ **Risk Approval**: APPROVED, MODIFIED, or REJECTED
- ✅ **Strategy**: Which of the 4 strategies was used
- ✅ **Execution Status**: FILLED (paper mode always fills)

---

## Next Steps

After successful testing:

1. **Run for 24 hours** in paper mode
2. **Analyze results** - track win rate, drawdowns
3. **Tune parameters** - adjust risk limits, strategies
4. **Test multiple symbols** - ETH, BNB, SOL
5. **Review logs** - understand each decision

---

## Safety First! ⚠️

**Paper Mode (Default)**:
- ✅ Uses FAKE orders
- ✅ No real money at risk
- ✅ Perfect for testing
- ✅ Recommended for at least 1 week

**Live Mode (Advanced)**:
- ⚠️ Uses REAL orders
- ⚠️ Real money at risk
- ⚠️ Only after extensive paper testing
- ⚠️ Start with VERY small sizes

**NEVER**:
- Run live mode without testing
- Use high leverage initially
- Trade without understanding strategies
- Disable risk checks

---

## Need Help?

- 📖 Full Testing Guide: `TESTING_GUIDE.md`
- 📋 Implementation Details: `IMPLEMENTATION_SUMMARY.md`
- 🏗️ Architecture: `PHASE2_COMPLETE.md`
- 📁 Agent Details: `agents/*.md`

---

## Summary

```bash
# Three commands to get started:
cp .env.example .env          # 1. Create environment file
nano .env                     # 2. Add your API keys
python main.py --mode paper --symbol BTCUSDT  # 3. Run!
```

**That's it! Your trading system is running! 🚀**
