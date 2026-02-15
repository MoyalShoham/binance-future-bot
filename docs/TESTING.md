# Testing Guide - Multi-Agent Trading System

## Quick Start Testing

### Step 1: Environment Setup

Create your `.env` file:
```bash
cp .env.example .env
```

Edit `.env` with your credentials:
```env
# Binance API (Get from: https://www.binance.com/en/my/settings/api-management)
BINANCE_API_KEY=your_api_key_here
BINANCE_API_SECRET=your_api_secret_here

# Optional: AI Model APIs (can test without these)
OPENAI_API_KEY=your_openai_key_here
ANTHROPIC_API_KEY=your_anthropic_key_here
GOOGLE_API_KEY=your_google_key_here
```

**IMPORTANT**:
- For testing, create a **Binance Testnet** account at https://testnet.binancefuture.com/
- Use testnet API keys for safe testing
- Set `testnet: true` in `config/trading_config.yaml`

### Step 2: Install Dependencies

```bash
pip install -r requirements.txt
```

Key dependencies:
- `python-binance` - Binance API
- `pandas` - Data manipulation
- `ta` - Technical indicators
- `langchain` - Agent orchestration
- `structlog` - Logging
- `pyyaml` - Configuration

### Step 3: Configure for Testing

Edit `config/trading_config.yaml`:

```yaml
trading:
  enabled: true
  execution_mode: "paper"  # KEEP THIS FOR TESTING
  testnet: true            # ADD THIS for testnet
  max_concurrent_positions: 1  # Start with 1 position
  default_leverage: 3      # Low leverage for testing

risk:
  max_risk_per_trade_pct: 0.01  # 1% for testing (lower than default)
  max_daily_drawdown_pct: 0.03  # 3% for testing
```

### Step 4: Run First Test (Single Cycle)

```bash
python main.py --mode paper --symbol BTCUSDT
```

**Expected Output**:
```
INFO     Starting Multi-Agent AI Trading System
INFO     Configuration loaded config_file=config/trading_config.yaml
INFO     Environment variables loaded
INFO     Binance client initialized testnet=False
INFO     All agents initialized agent_count=6
INFO     Starting trading cycle symbol=BTCUSDT mode=PAPER
INFO     Research node started correlation_id=<uuid>
INFO     Fetching complete market data symbol=BTCUSDT timeframe=5m
INFO     Market data fetched successfully symbol=BTCUSDT
INFO     Research node completed correlation_id=<uuid>
INFO     Decision node started correlation_id=<uuid>
INFO     Starting trading decision symbol=BTCUSDT market_regime=trending_up
INFO     Decision node completed decision=LONG confidence=0.82
INFO     Risk check node started correlation_id=<uuid>
INFO     Starting risk evaluation symbol=BTCUSDT decision=LONG
INFO     Trade approved as-is processing_time_ms=15.2
INFO     Risk check node completed approval_status=APPROVED
INFO     Execution node started correlation_id=<uuid>
INFO     Order created symbol=BTCUSDT side=BUY order_type=MARKET order_id=12345
INFO     Execution node completed status=FILLED mode=PAPER
INFO     Storage node started correlation_id=<uuid>
INFO     Trading cycle completed correlation_id=<uuid> total_time_ms=1250.5
```

### Step 5: Run Continuous Mode (Testing)

```bash
python main.py --mode paper --symbol BTCUSDT --continuous --interval 120
```

This will run a trading cycle every 120 seconds (2 minutes).

**Press Ctrl+C to stop gracefully.**

---

## Testing Scenarios

### Test 1: Paper Trading - Basic Flow
**Goal**: Verify full pipeline works end-to-end

```bash
python main.py --mode paper --symbol BTCUSDT
```

**Check**:
- ✅ Connects to Binance API
- ✅ Fetches market data
- ✅ Calculates indicators
- ✅ Makes a decision (LONG/SHORT/NO_TRADE)
- ✅ Risk Manager evaluates
- ✅ Simulates execution
- ✅ Logs everything

### Test 2: Different Symbols
**Goal**: Test with multiple symbols

```bash
python main.py --mode paper --symbol ETHUSDT
python main.py --mode paper --symbol BNBUSDT
python main.py --mode paper --symbol SOLUSDT
```

### Test 3: Risk Rejection
**Goal**: Trigger risk rejection by modifying config

Edit `config/trading_config.yaml`:
```yaml
risk:
  max_risk_per_trade_pct: 0.0001  # Very low - should reject most trades
```

Run:
```bash
python main.py --mode paper --symbol BTCUSDT
```

**Expected**: Risk Manager should REJECT or heavily MODIFY the trade.

### Test 4: High Volatility NO_TRADE
**Goal**: Test NO_TRADE decision in high volatility

Wait for high volatility market conditions or test with volatile pairs.

**Expected**: Trading Decision should return NO_TRADE if volatility > 5%.

### Test 5: Continuous Trading
**Goal**: Test system stability over time

```bash
python main.py --mode paper --symbol BTCUSDT --continuous --interval 60
```

**Let it run for 10 minutes** (10 cycles).

**Monitor**:
- Memory usage
- Error rate
- Decision quality
- Risk approvals/rejections

---

## Troubleshooting

### Issue: "Missing environment variables"
**Solution**: Make sure `.env` exists and has `BINANCE_API_KEY` and `BINANCE_API_SECRET`

### Issue: "Failed to connect to Binance API"
**Solution**:
- Check your API keys are correct
- Ensure Binance API is accessible (not blocked by firewall)
- Try testnet first: https://testnet.binancefuture.com/

### Issue: "Module not found"
**Solution**: Install dependencies:
```bash
pip install -r requirements.txt
```

### Issue: BinanceAPIException errors
**Solution**:
- Check API key permissions (need Futures trading enabled)
- Verify API key is not IP-restricted
- Check rate limits

### Issue: "Risk Manager agent not registered"
**Solution**: Make sure you're on the latest commit:
```bash
git pull origin feature/phase2-integration
```

---

## Verification Checklist

After running tests, verify:

- [ ] **Connection**: System connects to Binance successfully
- [ ] **Market Data**: Fetches current price, order book, indicators
- [ ] **Indicators**: Calculates EMA, RSI, MACD, etc.
- [ ] **Regime Classification**: Correctly identifies market regime
- [ ] **Strategy Selection**: Chooses appropriate strategy
- [ ] **Decision Making**: Makes LONG/SHORT/NO_TRADE decision
- [ ] **Risk Checks**: All 8 risk checks execute
- [ ] **Position Sizing**: Calculates Kelly Criterion correctly
- [ ] **Leverage Adjustment**: Adjusts based on volatility
- [ ] **Approval Logic**: APPROVED/REJECTED/MODIFIED correctly
- [ ] **Paper Execution**: Simulates order with slippage
- [ ] **Logging**: All events logged clearly
- [ ] **Error Handling**: Gracefully handles errors
- [ ] **Shutdown**: Ctrl+C stops gracefully

---

## Performance Benchmarks

Expected performance (single cycle):
- **Total Time**: 1-3 seconds
- **Research**: 500-1000ms (parallel sub-agents)
- **Decision**: 200-500ms
- **Risk Check**: 10-50ms
- **Execution**: 100-300ms (paper mode)
- **Storage**: 10-50ms

Slower performance might indicate:
- Slow Binance API response
- Network latency
- AI model API calls (if integrated)

---

## Next Steps After Testing

Once testing is successful:

1. **Run for 24 hours in paper mode** to gather data
2. **Analyze results**: Win rate, drawdown, strategy performance
3. **Tune parameters**: Adjust risk limits, strategy parameters
4. **Add more symbols**: Test with multiple pairs
5. **Consider live trading**: Only after extensive paper testing

---

## Safety Reminders

⚠️ **NEVER**:
- Run in `live` mode without extensive paper testing
- Use high leverage (>5x) until proven successful
- Trade without understanding the strategies
- Ignore risk warnings or red flags
- Disable risk checks or kill switches

✅ **ALWAYS**:
- Start with paper trading
- Use small position sizes
- Monitor daily
- Keep kill switches accessible
- Understand each trade decision

---

## Support

If you encounter issues:
1. Check logs for error messages
2. Review `IMPLEMENTATION_SUMMARY.md`
3. Check agent definitions in `agents/` folder
4. Review configuration in `config/trading_config.yaml`

Happy Testing! 🚀
