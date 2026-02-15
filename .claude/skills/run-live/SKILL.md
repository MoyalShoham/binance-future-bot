# Run Live Trading

Start the bot in live trading mode with real Binance orders.

## Steps

1. **IMPORTANT**: Before running, confirm with the user that they want to start LIVE trading with real money. This is irreversible.

2. Ask the user which mode they want:
   - Single symbol: `--symbol BTCUSDT`
   - All symbols (dynamic scanner): `--symbol all`

3. Run the bot:

```bash
".conda/python.exe" main.py --mode live --symbol all --continuous --interval 5
```

**Flags:**
- `--mode live` - Real orders on Binance Futures
- `--symbol all` - Dynamic symbol selection via scanner
- `--continuous` - Keep running cycles
- `--interval 5` - 5 seconds between cycles

**Safety checks before starting:**
- Verify `.env` has valid BINANCE_API_KEY and BINANCE_API_SECRET
- Verify config has reasonable risk limits (check `config/trading_config.yaml`)
- Verify kill switches are not activated
