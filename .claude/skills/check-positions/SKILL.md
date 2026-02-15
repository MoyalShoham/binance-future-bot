# Check Positions

Query open positions from the database and show current status with P&L.

## Steps

1. Run this SQL query against `data/trading_system.db` using the MCP sqlite server or sqlite3 CLI:

```sql
SELECT
  p.id,
  p.symbol,
  p.side,
  p.entry_price,
  p.quantity,
  p.unrealized_pnl_usdt,
  p.leverage,
  p.created_at,
  p.sl_order_id,
  p.tp_order_id
FROM pnl_ledger p
WHERE p.is_closed = 0
ORDER BY p.created_at DESC;
```

2. If there are open positions, also check the current price for each symbol via:

```bash
".conda/python.exe" -c "
from infrastructure.binance_api.client import BinanceClient
import yaml
config = yaml.safe_load(open('config/trading_config.yaml'))
client = BinanceClient(config)
# Replace SYMBOL with actual symbol
ticker = client.client.futures_symbol_ticker(symbol='SYMBOL')
print(f'{ticker[\"symbol\"]}: {ticker[\"price\"]}')
"
```

3. Present results in a table showing: Symbol, Side, Entry Price, Current Price, Unrealized P&L, Leverage, Duration, SL/TP status.

4. If no open positions, report "No open positions."
