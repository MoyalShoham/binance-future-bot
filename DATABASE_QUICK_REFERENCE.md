# Database Quick Reference

## Quick Start

### Initialize Database
```bash
# First time setup (creates all tables)
python scripts/init_database.py

# Test database integration
python tests/test_database_integration.py
```

### Run System with Database
```bash
# Database is automatically initialized
python main.py --mode paper --symbol BTCUSDT
```

## Database Tables

| Table | Purpose | Key Fields |
|-------|---------|------------|
| **research_summaries** | Market research data | symbol, market_data, technical_indicators, sentiment |
| **trading_decisions** | Trading decisions | decision, confidence, strategy_id, entry_price |
| **risk_approvals** | Risk check results | approval_status, risk_checks, modified_parameters |
| **executions** | Order executions | execution_mode, execution_status, order_details |
| **pnl_ledger** | P&L tracking | entry_price, exit_price, realized_pnl_usdt, is_closed |
| **audit_trail** | Tamper-evident logs | event_type, event_data, current_hash (SHA-256) |
| **performance_metrics** | Daily/weekly/monthly stats | total_trades, win_rate, total_pnl, sharpe_ratio |

## Common Queries

### Get Latest Research
```python
from infrastructure.database import DatabaseQueries

with db_session.session_scope() as session:
    queries = DatabaseQueries(session)
    latest = queries.get_latest_research("BTCUSDT")
    print(f"Market Regime: {latest.market_regime}")
```

### Get Open Positions
```python
positions = queries.get_open_positions()
for pos in positions:
    print(f"{pos.symbol}: {pos.side} @ {pos.entry_price}")
```

### Calculate P&L
```python
from datetime import datetime, timedelta

start_date = datetime.now() - timedelta(days=7)
end_date = datetime.now()

total_pnl = queries.calculate_total_pnl(start_date, end_date)
win_rate = queries.get_win_rate(start_date, end_date)

print(f"7-day P&L: ${total_pnl:.2f}")
print(f"Win Rate: {win_rate*100:.1f}%")
```

### Get Decisions by Date
```python
from datetime import datetime

today_start = datetime.combine(datetime.today(), datetime.min.time())
today_end = datetime.combine(datetime.today(), datetime.max.time())

decisions = queries.get_decisions_by_date(today_start, today_end)
print(f"Decisions today: {len(decisions)}")
```

### Verify Audit Trail
```python
# Check if audit trail hasn't been tampered with
is_valid = queries.verify_audit_chain(correlation_id)
if not is_valid:
    print("⚠️  WARNING: Audit chain broken - potential tampering!")
```

## Storage & Reporting Agent

### Generate Daily Report
```python
from agents.implementations import StorageReporterAgent

storage_agent = StorageReporterAgent("storage-reporter", config, db_session)

# Generate report for today
report = storage_agent.generate_daily_report()

# Generate report for specific date
from datetime import date
report = storage_agent.generate_daily_report(date(2026, 2, 7))

print(f"Total Trades: {report['summary']['total_trades']}")
print(f"Total P&L: ${report['summary']['total_pnl_usdt']}")
print(f"Win Rate: {report['summary']['win_rate']*100:.1f}%")
```

### Calculate Performance Metrics
```python
from datetime import date

metrics = storage_agent.calculate_performance_metrics(
    start_date=date(2026, 2, 1),
    end_date=date(2026, 2, 7),
    metric_type="weekly"
)

print(f"Total Trades: {metrics['total_trades']}")
print(f"Win Rate: {metrics['win_rate']*100:.1f}%")
print(f"Sharpe Ratio: {metrics.get('sharpe_ratio', 'N/A')}")
```

### Export Report
```python
# Export to JSON
path = storage_agent.export_report(report, format="json")

# Export to Markdown
path = storage_agent.export_report(report, format="markdown")

print(f"Report exported to: {path}")
```

## Database Configuration

### SQLite (Default - Development)
```yaml
# config/trading_config.yaml
data:
  database:
    type: "sqlite"
    path: "data/trading_system.db"
```

### PostgreSQL (Production)
```yaml
# config/trading_config.yaml
data:
  database:
    type: "postgresql"
    host: "localhost"
    port: 5432
    database: "trading_system"
    user: "trading_user"
    password: "${POSTGRES_PASSWORD}"  # From environment variable
```

```bash
# Set PostgreSQL password
export POSTGRES_PASSWORD="your_secure_password"
```

## File Locations

| File | Purpose |
|------|---------|
| `data/trading_system.db` | Main SQLite database (auto-created) |
| `data/test_trading_system.db` | Test database |
| `infrastructure/database/models.py` | SQLAlchemy models |
| `infrastructure/database/session.py` | Session management |
| `infrastructure/database/queries.py` | Query utilities |
| `agents/implementations/storage_reporter.py` | Storage & Reporting agent |
| `scripts/init_database.py` | Database initialization script |
| `tests/test_database_integration.py` | Integration tests |

## Troubleshooting

### Database Locked (SQLite)
```python
# Switch to PostgreSQL for concurrent access, or use WAL mode
import sqlite3
conn = sqlite3.connect("data/trading_system.db")
conn.execute("PRAGMA journal_mode=WAL;")
conn.close()
```

### Missing Tables
```bash
# Recreate tables
python scripts/init_database.py
```

### Reset Database (⚠️ Destructive)
```bash
# Drop all tables and recreate
python scripts/init_database.py --drop
```

### Check Database Size
```bash
# Linux/Mac
ls -lh data/trading_system.db

# Windows (PowerShell)
Get-Item data/trading_system.db | Select-Object Name, Length
```

## Audit Trail

The audit trail uses **SHA-256 hash chaining** to detect tampering:

```
Entry 1: current_hash = SHA256(input_hash + "")
Entry 2: current_hash = SHA256(input_hash + Entry1.current_hash)
Entry 3: current_hash = SHA256(input_hash + Entry2.current_hash)
```

**Verify integrity:**
```python
is_valid = queries.verify_audit_chain(correlation_id)
# Returns False if any entry was modified
```

## Automatic Storage

All trading data is **automatically stored** during the trading pipeline:

```
Research Coordinator → research_summaries
Trading Decision → trading_decisions
Risk Manager → risk_approvals
Execution Agent → executions + pnl_ledger
All Agents → audit_trail (with hash chain)
```

No manual intervention required!

## Report Structure

### Daily Report
```json
{
  "date": "2026-02-07",
  "summary": {
    "total_decisions": 45,
    "total_executions": 32,
    "total_closed_positions": 28,
    "total_pnl_usdt": 125.50,
    "win_rate": 0.70
  },
  "decisions": {
    "LONG": 20,
    "SHORT": 15,
    "NO_TRADE": 10
  },
  "risk_approvals": {
    "APPROVED": 30,
    "REJECTED": 5,
    "MODIFIED": 2
  },
  "trades": [...]
}
```

### Performance Metrics
```json
{
  "total_trades": 150,
  "winning_trades": 105,
  "losing_trades": 45,
  "win_rate": 0.70,
  "total_pnl_usdt": 1250.75,
  "avg_win_usdt": 18.50,
  "avg_loss_usdt": -10.25,
  "sharpe_ratio": 1.85,
  "max_drawdown_pct": 0.04
}
```

## Integration Status

✅ **Database Fully Integrated**
- All 7 tables created
- Storage & Reporting agent implemented
- Automatic persistence enabled
- Audit trail with hash chain
- Query utilities ready
- Reports and metrics working

You can now run the trading system with full database persistence!
