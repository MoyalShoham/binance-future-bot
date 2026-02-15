# Database Reference

## Overview

The trading system uses SQLAlchemy ORM with SQLite (development) and PostgreSQL (production) support. WAL mode is enabled for concurrent thread writes.

**Database location**: `data/trading_system.db`

## Quick Commands

```bash
# Initialize database (first time)
python scripts/init_database.py

# Drop and recreate (WARNING: destructive)
python scripts/init_database.py --drop

# Run database tests
python tests/test_database_integration.py

# System auto-initializes on startup
python main.py --mode paper --symbol BTCUSDT
```

## Tables

| Table | Purpose | Key Fields |
|-------|---------|------------|
| **research_summaries** | Market research data | symbol, market_data, technical_indicators, sentiment |
| **trading_decisions** | Trading decisions | decision, confidence, strategy_id, entry_price |
| **risk_approvals** | Risk check results | approval_status, risk_checks, modified_parameters |
| **executions** | Order executions | execution_mode, execution_status, order_details |
| **pnl_ledger** | P&L tracking | entry_price, exit_price, realized_pnl_usdt, is_closed |
| **audit_trail** | Tamper-evident logs | event_type, event_data, current_hash (SHA-256) |
| **performance_metrics** | Aggregated stats | total_trades, win_rate, total_pnl, sharpe_ratio |

### Relationships

```
ResearchSummary (1) --> (N) TradingDecision
TradingDecision (1) --> (1) RiskApproval
RiskApproval (1) --> (1) Execution
Execution (1) --> (1) PnLLedger
```

## Common Queries

```python
from infrastructure.database import DatabaseQueries

with db_session.session_scope() as session:
    queries = DatabaseQueries(session)

    # Latest research
    latest = queries.get_latest_research("BTCUSDT")

    # Open positions
    positions = queries.get_open_positions()

    # P&L and win rate
    total_pnl = queries.calculate_total_pnl(start_date, end_date)
    win_rate = queries.get_win_rate(start_date, end_date)

    # Decisions by date
    decisions = queries.get_decisions_by_date(today_start, today_end)

    # Strategy performance breakdown
    strategy_stats = queries.get_strategy_performance(start_date)

    # Verify audit trail integrity
    is_valid = queries.verify_audit_chain(correlation_id)
```

## Storage & Reporting Agent

Automatically persists all pipeline data during each trading cycle:

```python
storage_agent = StorageReporterAgent("storage-reporter", config, db_session)

# Generate daily report
report = storage_agent.generate_daily_report(target_date)

# Performance metrics
metrics = storage_agent.calculate_performance_metrics(start_date, end_date, "weekly")

# Export reports (JSON, CSV, markdown)
path = storage_agent.export_report(report_data, format="json")
```

## Audit Trail

SHA-256 hash chain for tamper detection:

```
Entry 1: current_hash = SHA256(input_hash + "")
Entry 2: current_hash = SHA256(input_hash + Entry1.current_hash)
Entry 3: current_hash = SHA256(input_hash + Entry2.current_hash)
```

Verify: `queries.verify_audit_chain(correlation_id)` returns False if tampered.

## Configuration

### SQLite (Development)

```yaml
# config/trading_config.yaml
data:
  database:
    type: "sqlite"
    path: "data/trading_system.db"
```

### PostgreSQL (Production)

```yaml
data:
  database:
    type: "postgresql"
    host: "localhost"
    port: 5432
    database: "trading_system"
    user: "trading_user"
    password: "${POSTGRES_PASSWORD}"
```

## File Locations

| File | Purpose |
|------|---------|
| `data/trading_system.db` | Main SQLite database |
| `data/trades.db` | Flat trades database (fast queries) |
| `infrastructure/database/models.py` | SQLAlchemy models |
| `infrastructure/database/session.py` | Session management |
| `infrastructure/database/queries.py` | Query utilities |
| `infrastructure/database/trades_db.py` | Flat trades DB |
| `agents/implementations/storage_reporter.py` | Storage agent |
| `scripts/init_database.py` | DB initialization |
| `tests/test_database_integration.py` | Integration tests |

## Troubleshooting

**Database locked**: WAL mode is enabled. If still locked, check busy_timeout (5000ms) or switch to PostgreSQL.

**Missing tables**: Run `python scripts/init_database.py`

**Reset database**: Delete `data/trading_system.db` and restart. Or: `python scripts/init_database.py --drop`

**Stale positions**: Orphan reconciliation in TrailingStopMonitor handles positions that close on Binance but remain open in DB.
