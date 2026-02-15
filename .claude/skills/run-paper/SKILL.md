# Run Paper Trading

Start the bot in paper trading mode.

## Steps

1. Run the bot in paper trading mode:

```bash
".conda/python.exe" main.py --mode paper --symbol BTCUSDT --continuous --interval 30
```

This runs paper trading on BTCUSDT with a 30-second interval between cycles.

**Flags:**
- `--mode paper` - Simulated orders, no real money
- `--symbol BTCUSDT` - Trade BTCUSDT (or use `all` for dynamic symbol selection)
- `--continuous` - Keep running cycles
- `--interval 30` - 30 seconds between cycles

To change the symbol or interval, modify the command before running.
