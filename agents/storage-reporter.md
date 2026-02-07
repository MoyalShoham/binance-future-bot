# Storage & Reporting Agent

**Agent ID**: `storage-reporter`

**Role**: Persist all trading pipeline data, maintain audit trail, and generate performance reports.

---

## Responsibilities

1. **Data Persistence**: Store all research, decisions, approvals, executions
2. **Audit Trail**: Maintain immutable log with hash chains
3. **P&L Tracking**: Calculate and track profit/loss metrics
4. **Report Generation**: Daily, weekly, monthly performance reports
5. **Data Export**: Export data in multiple formats (JSON, CSV, Markdown)
6. **Database Maintenance**: Backups, cleanup, optimization

---

## Database Schema

### Tables

**1. research_summaries**
```sql
CREATE TABLE research_summaries (
    id UUID PRIMARY KEY,
    symbol VARCHAR(20) NOT NULL,
    timeframe VARCHAR(10) NOT NULL,
    analysis_timestamp TIMESTAMP NOT NULL,
    market_data JSONB NOT NULL,
    technical_indicators JSONB NOT NULL,
    sentiment JSONB NOT NULL,
    market_regime VARCHAR(50),
    news_events JSONB,
    warnings TEXT[],
    time_decay_factor FLOAT,
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX idx_research_symbol_time ON research_summaries(symbol, analysis_timestamp DESC);
```

**2. trading_decisions**
```sql
CREATE TABLE trading_decisions (
    id UUID PRIMARY KEY,
    research_summary_id UUID REFERENCES research_summaries(id),
    symbol VARCHAR(20) NOT NULL,
    decision VARCHAR(10) NOT NULL,  -- LONG, SHORT, NO_TRADE
    confidence FLOAT NOT NULL,
    strategy_id VARCHAR(50),
    model_used VARCHAR(50),
    reasoning_summary TEXT,
    entry_price DECIMAL(20, 8),
    stop_loss DECIMAL(20, 8),
    take_profit_levels JSONB,
    position_size_usdt DECIMAL(20, 2),
    leverage INT,
    technical_signals JSONB,
    risk_metrics JSONB,
    timestamp TIMESTAMP NOT NULL,
    research_summary_hash VARCHAR(64),
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX idx_decisions_symbol_time ON trading_decisions(symbol, timestamp DESC);
CREATE INDEX idx_decisions_decision ON trading_decisions(decision);
```

**3. risk_approvals**
```sql
CREATE TABLE risk_approvals (
    id UUID PRIMARY KEY,
    decision_id UUID REFERENCES trading_decisions(id),
    approval_status VARCHAR(20) NOT NULL,  -- APPROVED, REJECTED, MODIFIED
    rejection_reason TEXT,
    modified_parameters JSONB,
    risk_checks JSONB NOT NULL,
    position_sizing JSONB,
    account_status JSONB NOT NULL,
    kill_switches JSONB,
    timestamp TIMESTAMP NOT NULL,
    processing_time_ms INT,
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX idx_approvals_status ON risk_approvals(approval_status);
```

**4. executions**
```sql
CREATE TABLE executions (
    id UUID PRIMARY KEY,
    approval_id UUID REFERENCES risk_approvals(id),
    decision_id UUID REFERENCES trading_decisions(id),
    execution_mode VARCHAR(10) NOT NULL,  -- PAPER, LIVE, HYBRID
    execution_status VARCHAR(20) NOT NULL,  -- FILLED, REJECTED, etc.
    symbol VARCHAR(20) NOT NULL,
    side VARCHAR(10) NOT NULL,
    order_details JSONB NOT NULL,
    stop_loss_order JSONB,
    take_profit_orders JSONB,
    shadow_paper_execution JSONB,
    paper_trading_simulation JSONB,
    execution_timeline JSONB,
    errors JSONB,
    timestamp TIMESTAMP NOT NULL,
    processing_time_ms INT,
    idempotency_check JSONB,
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX idx_executions_symbol_time ON executions(symbol, timestamp DESC);
CREATE INDEX idx_executions_status ON executions(execution_status);
CREATE INDEX idx_executions_mode ON executions(execution_mode);
```

**5. pnl_ledger**
```sql
CREATE TABLE pnl_ledger (
    id SERIAL PRIMARY KEY,
    execution_id UUID REFERENCES executions(id),
    symbol VARCHAR(20) NOT NULL,
    side VARCHAR(10) NOT NULL,
    entry_price DECIMAL(20, 8) NOT NULL,
    exit_price DECIMAL(20, 8),
    quantity DECIMAL(20, 8) NOT NULL,
    leverage INT NOT NULL,
    fees_usdt DECIMAL(20, 2),
    realized_pnl_usdt DECIMAL(20, 2),
    unrealized_pnl_usdt DECIMAL(20, 2),
    entry_time TIMESTAMP NOT NULL,
    exit_time TIMESTAMP,
    holding_time_seconds INT,
    is_closed BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX idx_pnl_symbol_entry ON pnl_ledger(symbol, entry_time DESC);
CREATE INDEX idx_pnl_closed ON pnl_ledger(is_closed);
```

**6. audit_trail**
```sql
CREATE TABLE audit_trail (
    id SERIAL PRIMARY KEY,
    correlation_id UUID NOT NULL,
    agent_id VARCHAR(50) NOT NULL,
    event_type VARCHAR(50) NOT NULL,
    event_data JSONB NOT NULL,
    input_hash VARCHAR(64),
    previous_hash VARCHAR(64),  -- For hash chain
    current_hash VARCHAR(64) NOT NULL,
    timestamp TIMESTAMP NOT NULL,
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX idx_audit_correlation ON audit_trail(correlation_id);
CREATE INDEX idx_audit_agent ON audit_trail(agent_id);
CREATE INDEX idx_audit_time ON audit_trail(timestamp DESC);
```

**7. performance_metrics**
```sql
CREATE TABLE performance_metrics (
    id SERIAL PRIMARY KEY,
    date DATE NOT NULL,
    metric_type VARCHAR(50) NOT NULL,  -- daily, weekly, monthly
    total_trades INT,
    winning_trades INT,
    losing_trades INT,
    win_rate FLOAT,
    total_pnl_usdt DECIMAL(20, 2),
    avg_win_usdt DECIMAL(20, 2),
    avg_loss_usdt DECIMAL(20, 2),
    largest_win_usdt DECIMAL(20, 2),
    largest_loss_usdt DECIMAL(20, 2),
    sharpe_ratio FLOAT,
    max_drawdown_pct FLOAT,
    avg_holding_time_seconds INT,
    strategy_breakdown JSONB,
    model_accuracy JSONB,
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX idx_metrics_date ON performance_metrics(date DESC);
CREATE INDEX idx_metrics_type ON performance_metrics(metric_type);
```

---

## Audit Trail with Hash Chain

```python
class AuditTrail:
    """
    Immutable audit trail with SHA-256 hash chaining.
    """

    def __init__(self, db_session):
        self.db = db_session
        self.previous_hash = self._get_last_hash()

    def _get_last_hash(self) -> str:
        """Get the hash of the last audit entry."""
        last_entry = self.db.query(AuditTrailEntry).order_by(
            AuditTrailEntry.id.desc()
        ).first()

        return last_entry.current_hash if last_entry else "GENESIS"

    def log_event(
        self,
        correlation_id: str,
        agent_id: str,
        event_type: str,
        event_data: Dict,
        input_hash: Optional[str] = None
    ) -> str:
        """
        Log an event with hash chain.

        Returns:
            Current hash
        """

        # Create hash chain entry
        entry_data = {
            "correlation_id": correlation_id,
            "agent_id": agent_id,
            "event_type": event_type,
            "event_data": event_data,
            "input_hash": input_hash,
            "previous_hash": self.previous_hash,
            "timestamp": datetime.utcnow().isoformat()
        }

        # Compute current hash
        current_hash = hashlib.sha256(
            json.dumps(entry_data, sort_keys=True).encode()
        ).hexdigest()

        entry_data["current_hash"] = current_hash

        # Store in database
        audit_entry = AuditTrailEntry(**entry_data)
        self.db.add(audit_entry)
        self.db.commit()

        # Update previous hash for next entry
        self.previous_hash = current_hash

        return current_hash
```

---

## P&L Calculation

```python
class PnLCalculator:
    """
    Calculate realized and unrealized P&L.
    """

    def register_position(self, execution: Dict):
        """
        Register new position in P&L ledger.
        """
        entry = PnLLedger(
            execution_id=execution["execution_id"],
            symbol=execution["symbol"],
            side=execution["side"],
            entry_price=execution["order_details"]["avg_fill_price"],
            quantity=execution["order_details"]["filled_quantity"],
            leverage=execution["order_details"]["leverage"],
            fees_usdt=execution["order_details"]["fees_usdt"],
            entry_time=execution["timestamp"],
            is_closed=False
        )
        self.db.add(entry)
        self.db.commit()

    def update_unrealized_pnl(self, symbol: str):
        """
        Update unrealized P&L for open position.
        """
        position = self.db.query(PnLLedger).filter_by(
            symbol=symbol,
            is_closed=False
        ).first()

        if not position:
            return

        current_price = fetch_current_price(symbol)

        if position.side == "LONG":
            pnl = (current_price - position.entry_price) * position.quantity
        else:  # SHORT
            pnl = (position.entry_price - current_price) * position.quantity

        # Subtract fees
        pnl -= position.fees_usdt

        position.unrealized_pnl_usdt = pnl
        position.updated_at = datetime.utcnow()
        self.db.commit()

    def close_position(self, symbol: str, exit_price: float, exit_fees: float):
        """
        Close position and calculate realized P&L.
        """
        position = self.db.query(PnLLedger).filter_by(
            symbol=symbol,
            is_closed=False
        ).first()

        if not position:
            raise ValueError(f"No open position for {symbol}")

        if position.side == "LONG":
            pnl = (exit_price - position.entry_price) * position.quantity
        else:
            pnl = (position.entry_price - exit_price) * position.quantity

        # Subtract all fees
        pnl -= (position.fees_usdt + exit_fees)

        # Update position
        position.exit_price = exit_price
        position.exit_time = datetime.utcnow()
        position.holding_time_seconds = int(
            (position.exit_time - position.entry_time).total_seconds()
        )
        position.realized_pnl_usdt = pnl
        position.is_closed = True
        self.db.commit()

        return pnl
```

---

## Report Generation

### Daily Report
```python
def generate_daily_report(date: datetime.date) -> Dict:
    """
    Generate comprehensive daily trading report.
    """

    # Query all executions for the day
    executions = db.query(Execution).filter(
        func.date(Execution.timestamp) == date
    ).all()

    # Query P&L entries
    closed_positions = db.query(PnLLedger).filter(
        func.date(PnLLedger.exit_time) == date,
        PnLLedger.is_closed == True
    ).all()

    # Calculate metrics
    total_trades = len(closed_positions)
    winning_trades = sum(1 for p in closed_positions if p.realized_pnl_usdt > 0)
    losing_trades = sum(1 for p in closed_positions if p.realized_pnl_usdt < 0)

    win_rate = winning_trades / total_trades if total_trades > 0 else 0

    total_pnl = sum(p.realized_pnl_usdt for p in closed_positions)
    avg_win = sum(p.realized_pnl_usdt for p in closed_positions if p.realized_pnl_usdt > 0) / winning_trades if winning_trades > 0 else 0
    avg_loss = sum(p.realized_pnl_usdt for p in closed_positions if p.realized_pnl_usdt < 0) / losing_trades if losing_trades > 0 else 0

    # Strategy breakdown
    strategy_breakdown = calculate_strategy_performance(closed_positions)

    # Model accuracy
    model_accuracy = calculate_model_accuracy(date)

    report = {
        "date": date.isoformat(),
        "total_trades": total_trades,
        "winning_trades": winning_trades,
        "losing_trades": losing_trades,
        "win_rate": round(win_rate, 4),
        "total_pnl_usdt": round(total_pnl, 2),
        "avg_win_usdt": round(avg_win, 2),
        "avg_loss_usdt": round(avg_loss, 2),
        "strategy_breakdown": strategy_breakdown,
        "model_accuracy": model_accuracy
    }

    return report
```

### Export to CSV
```python
def export_to_csv(data: List[Dict], filepath: str):
    """
    Export data to CSV format.
    """
    import pandas as pd

    df = pd.DataFrame(data)
    df.to_csv(filepath, index=False)
```

### Export to Markdown
```python
def export_daily_report_md(report: Dict, filepath: str):
    """
    Export daily report as Markdown.
    """

    md = f"""# Daily Trading Report - {report['date']}

## Summary

- **Total Trades**: {report['total_trades']}
- **Win Rate**: {report['win_rate']:.2%}
- **Total P&L**: ${report['total_pnl_usdt']:.2f}
- **Avg Win**: ${report['avg_win_usdt']:.2f}
- **Avg Loss**: ${report['avg_loss_usdt']:.2f}

## Strategy Performance

| Strategy | Trades | Win Rate | P&L |
|----------|--------|----------|-----|
"""

    for strategy, metrics in report['strategy_breakdown'].items():
        md += f"| {strategy} | {metrics['trades']} | {metrics['win_rate']:.2%} | ${metrics['pnl']:.2f} |\n"

    md += f"\n## Model Accuracy\n\n"
    for model, accuracy in report['model_accuracy'].items():
        md += f"- **{model}**: {accuracy:.2%}\n"

    with open(filepath, 'w') as f:
        f.write(md)
```

---

## Authority Boundaries

**Can Do**:
- ✅ Store all pipeline data
- ✅ Generate reports
- ✅ Export data in multiple formats
- ✅ Calculate P&L metrics
- ✅ Maintain audit trail

**Cannot Do**:
- ❌ Modify trading decisions
- ❌ Execute trades
- ❌ Change risk parameters
- ❌ Delete audit trail entries

---

## Testing Requirements

1. Test database schema creation
2. Test data insertion for all tables
3. Test audit trail hash chain
4. Test P&L calculations (long and short)
5. Test report generation (daily, weekly, monthly)
6. Test export functions (JSON, CSV, Markdown)
7. Test database backups
8. Test data integrity constraints
