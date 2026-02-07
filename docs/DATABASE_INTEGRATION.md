# Database Integration Guide

## Overview

The trading system uses **SQLAlchemy ORM** for database persistence with support for both SQLite (development) and PostgreSQL (production).

## Database Schema

### Tables

1. **research_summaries** - Market research data from Research Coordinator
2. **trading_decisions** - Trading decisions from Trading Decision Agent
3. **risk_approvals** - Risk approvals/rejections from Risk Manager
4. **executions** - Order executions from Execution Agent
5. **pnl_ledger** - Profit & Loss tracking for all positions
6. **audit_trail** - Immutable audit trail with SHA-256 hash chain
7. **performance_metrics** - Aggregated performance metrics (daily/weekly/monthly)

### Relationships

```
ResearchSummary (1) ──> (N) TradingDecision
TradingDecision (1) ──> (1) RiskApproval
RiskApproval (1) ──> (1) Execution
Execution (1) ──> (1) PnLLedger
```

## Setup

### 1. Initialize Database

**First Time Setup:**
```bash
python scripts/init_database.py
```

**Drop and Recreate (WARNING: Destructive):**
```bash
python scripts/init_database.py --drop
```

This creates all tables in `data/trading_system.db` (SQLite).

### 2. Configuration

Database settings are in `config/trading_config.yaml`:

```yaml
data:
  database:
    type: "sqlite"  # or "postgresql"
    path: "data/trading_system.db"
```

**For PostgreSQL:**
```yaml
data:
  database:
    type: "postgresql"
    host: "localhost"
    port: 5432
    database: "trading_system"
    user: "trading_user"
    password: "${POSTGRES_PASSWORD}"  # From environment
```

## Usage

### Automatic Persistence

The Storage & Reporting Agent automatically persists all trading data:

```python
# In main trading pipeline
storage_reporter = StorageReporterAgent("storage-reporter", config, db_session)

# Automatically stores:
# - Research summaries
# - Trading decisions
# - Risk approvals
# - Execution results
# - P&L updates
# - Audit trail entries
```

### Manual Queries

```python
from infrastructure.database import DatabaseQueries

# Create query utility
with db_session.session_scope() as session:
    queries = DatabaseQueries(session)

    # Get latest research
    latest = queries.get_latest_research("BTCUSDT")

    # Get open positions
    positions = queries.get_open_positions()

    # Calculate P&L
    pnl = queries.calculate_total_pnl(start_date, end_date)

    # Get win rate
    win_rate = queries.get_win_rate(start_date, end_date)
```

## Storage & Reporting Agent

### Core Functionality

1. **Data Persistence**
   - Stores all trading pipeline data
   - Maintains relationships between entities
   - Tracks P&L in real-time

2. **Audit Trail**
   - SHA-256 hash chain for tamper detection
   - Links entries by correlation_id
   - Verifiable audit trail

3. **Reporting**
   - Daily/weekly/monthly reports
   - Performance metrics calculation
   - Strategy breakdown analytics

### Methods

**Storage Methods:**
```python
# Called automatically during trading cycle
_store_research_summary(research_data, session)
_store_trading_decision(decision_data, session)
_store_risk_approval(approval_data, session)
_store_execution_result(execution_data, session)
_update_pnl(execution_data, session)
_store_audit_entry(correlation_id, event_type, event_data, session)
```

**Reporting Methods:**
```python
# Generate reports
report = storage_agent.generate_daily_report(target_date)
metrics = storage_agent.calculate_performance_metrics(start_date, end_date)

# Verify audit trail
is_valid = storage_agent.verify_audit_trail(correlation_id)

# Export reports
path = storage_agent.export_report(report_data, format="json")
```

## Audit Trail

### Hash Chain Verification

Each audit entry includes:
- `input_hash`: SHA-256 of event data
- `previous_hash`: Hash of previous entry
- `current_hash`: SHA-256(input_hash + previous_hash)

**Verify integrity:**
```python
# Check hash chain hasn't been tampered with
is_valid = queries.verify_audit_chain(correlation_id)

if not is_valid:
    logger.error("Hash chain broken - potential tampering detected!")
```

## Database Models

### ResearchSummary
```python
id: str (UUID)
symbol: str
timeframe: str
analysis_timestamp: datetime
market_data: JSON
technical_indicators: JSON
sentiment: JSON
market_regime: str
news_events: JSON
warnings: JSON
```

### TradingDecision
```python
id: str (decision_id)
research_summary_id: str (FK)
symbol: str
decision: str (LONG/SHORT/NO_TRADE)
confidence: float
strategy_id: str
entry_price: float
stop_loss: float
take_profit_levels: JSON
```

### RiskApproval
```python
id: str (approval_id)
decision_id: str (FK)
approval_status: str (APPROVED/REJECTED/MODIFIED)
risk_checks: JSON (all 8 checks)
modified_parameters: JSON
account_status: JSON
```

### Execution
```python
id: str (execution_id)
approval_id: str (FK)
execution_mode: str (PAPER/LIVE/HYBRID)
execution_status: str (FILLED/PARTIAL/REJECTED/FAILED)
symbol: str
order_details: JSON
shadow_paper_execution: JSON
```

### PnLLedger
```python
id: int (auto-increment)
execution_id: str (FK)
symbol: str
entry_price: float
exit_price: float
realized_pnl_usdt: float
unrealized_pnl_usdt: float
is_closed: bool
```

### AuditTrail
```python
id: int (auto-increment)
correlation_id: str (groups related events)
agent_id: str
event_type: str
event_data: JSON
input_hash: str (SHA-256)
previous_hash: str
current_hash: str (SHA-256)
```

### PerformanceMetrics
```python
id: int (auto-increment)
date: datetime
metric_type: str (daily/weekly/monthly)
total_trades: int
win_rate: float
total_pnl_usdt: float
sharpe_ratio: float
max_drawdown_pct: float
```

## Query Examples

### Get Trading History
```python
# Get all decisions for today
from datetime import datetime
today_start = datetime.combine(datetime.today(), datetime.min.time())
today_end = datetime.combine(datetime.today(), datetime.max.time())

decisions = queries.get_decisions_by_date(today_start, today_end)
```

### Calculate Performance
```python
# Calculate win rate for last 7 days
from datetime import timedelta
start_date = datetime.now() - timedelta(days=7)
end_date = datetime.now()

win_rate = queries.get_win_rate(start_date, end_date)
total_pnl = queries.calculate_total_pnl(start_date, end_date)
```

### Get Strategy Performance
```python
# Get performance breakdown by strategy
from datetime import timedelta
start_date = datetime.now() - timedelta(days=30)

strategy_stats = queries.get_strategy_performance(start_date)
# Returns:
# {
#   "ema_crossover_scalp": {
#     "total_trades": 45,
#     "decisions": {"LONG": 25, "SHORT": 20}
#   },
#   ...
# }
```

## Maintenance

### Backup Database
```bash
# SQLite
cp data/trading_system.db backups/trading_system_$(date +%Y%m%d).db

# PostgreSQL
pg_dump trading_system > backups/trading_system_$(date +%Y%m%d).sql
```

### Check Database Size
```python
import os
db_path = "data/trading_system.db"
size_mb = os.path.getsize(db_path) / (1024 * 1024)
print(f"Database size: {size_mb:.2f} MB")
```

### Optimize Database
```python
# For SQLite
with db_session.session_scope() as session:
    session.execute("VACUUM")
```

## Production Considerations

### Switch to PostgreSQL

1. **Install PostgreSQL:**
```bash
# Ubuntu/Debian
sudo apt install postgresql postgresql-contrib

# macOS
brew install postgresql
```

2. **Create Database:**
```sql
CREATE DATABASE trading_system;
CREATE USER trading_user WITH PASSWORD 'secure_password';
GRANT ALL PRIVILEGES ON DATABASE trading_system TO trading_user;
```

3. **Update Configuration:**
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

4. **Set Environment Variable:**
```bash
export POSTGRES_PASSWORD="secure_password"
```

5. **Initialize:**
```bash
python scripts/init_database.py
```

### Performance Optimization

**For SQLite:**
- Enable WAL mode: `PRAGMA journal_mode=WAL;`
- Increase cache size: `PRAGMA cache_size=-64000;` (64MB)

**For PostgreSQL:**
- Create indexes on frequently queried columns (already defined in models)
- Use connection pooling (already configured)
- Regular VACUUM and ANALYZE

## Troubleshooting

### Database Locked (SQLite)
```
sqlite3.OperationalError: database is locked
```
**Solution:** Use WAL mode or switch to PostgreSQL for concurrent access.

### Missing Tables
```
sqlalchemy.exc.OperationalError: no such table
```
**Solution:** Run `python scripts/init_database.py`

### Migration Errors
If schema changes, use Alembic for migrations:
```bash
pip install alembic
alembic init alembic
alembic revision --autogenerate -m "Add new column"
alembic upgrade head
```

## Integration with Main Pipeline

The database is automatically initialized in `main.py`:

```python
# Initialize database
db_session = init_database(config)

# Pass to Storage & Reporting Agent
storage_agent = StorageReporterAgent("storage-reporter", config, db_session)

# Cleanup on shutdown
db_session.close()
```

## Summary

✅ **7 tables** for complete data persistence
✅ **SQLAlchemy ORM** for easy querying
✅ **Automatic storage** via Storage & Reporting Agent
✅ **Audit trail** with hash chain verification
✅ **Performance metrics** calculation
✅ **SQLite** (dev) and **PostgreSQL** (production) support
✅ **Relationship mapping** between all entities

The database integration is **production-ready** and provides complete persistence for all trading system data.
