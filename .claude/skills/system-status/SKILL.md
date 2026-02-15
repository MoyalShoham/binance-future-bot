# System Status

Check the health of all system components.

## Steps

Run the following checks and report results:

### 1. Binance API

```bash
".conda/python.exe" -c "
from binance.client import Client
import os
from dotenv import load_dotenv
load_dotenv()
client = Client(os.getenv('BINANCE_API_KEY',''), os.getenv('BINANCE_API_SECRET',''))
try:
    status = client.get_system_status()
    print(f'Binance API: {\"OK\" if status[\"status\"] == 0 else \"DOWN\"}')
    ticker = client.futures_symbol_ticker(symbol='BTCUSDT')
    print(f'BTC Price: \${float(ticker[\"price\"]):,.2f}')
    account = client.futures_account_balance()
    usdt = next((a for a in account if a['asset'] == 'USDT'), None)
    if usdt:
        print(f'USDT Balance: \${float(usdt[\"balance\"]):,.2f}')
except Exception as e:
    print(f'Binance API: ERROR - {e}')
"
```

### 2. Database

```bash
sqlite3 data/trading_system.db "SELECT 'Tables: ' || COUNT(*) FROM sqlite_master WHERE type='table'; SELECT 'Open positions: ' || COUNT(*) FROM pnl_ledger WHERE is_closed=0; SELECT 'Total trades: ' || COUNT(*) FROM pnl_ledger WHERE is_closed=1;"
```

### 3. Ollama (Local LLM)

```bash
curl -s http://localhost:11434/api/tags | ".conda/python.exe" -c "import sys,json; data=json.load(sys.stdin); models=[m['name'] for m in data.get('models',[])]; print(f'Ollama: {len(models)} models loaded') if models else print('Ollama: no models'); [print(f'  - {m}') for m in models]" 2>/dev/null || echo "Ollama: NOT RUNNING"
```

### 4. WebSocket

```bash
".conda/python.exe" -c "
try:
    from infrastructure.websocket_manager import WebSocketManager
    print('WebSocket module: OK')
except Exception as e:
    print(f'WebSocket module: ERROR - {e}')
"
```

### 5. Config

```bash
".conda/python.exe" -c "
import yaml
config = yaml.safe_load(open('config/trading_config.yaml'))
mode = config['trading']['execution_mode']
enabled = config['trading']['enabled']
kill = config['risk']['kill_switches']['global']
print(f'Config: mode={mode}, enabled={enabled}, kill_switch={kill}')
"
```

Present all results in a clear status dashboard format.
