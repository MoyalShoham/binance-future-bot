# Check P&L

Show daily, weekly, and all-time P&L summary with win rate statistics.

## Steps

1. Run these SQL queries against `data/trading_system.db`:

**Today's P&L:**
```sql
SELECT
  COUNT(*) as total_trades,
  SUM(CASE WHEN realized_pnl_usdt > 0 THEN 1 ELSE 0 END) as wins,
  SUM(CASE WHEN realized_pnl_usdt <= 0 THEN 1 ELSE 0 END) as losses,
  ROUND(SUM(realized_pnl_usdt), 4) as total_pnl,
  ROUND(AVG(realized_pnl_usdt), 4) as avg_pnl,
  ROUND(MAX(realized_pnl_usdt), 4) as best_trade,
  ROUND(MIN(realized_pnl_usdt), 4) as worst_trade
FROM pnl_ledger
WHERE is_closed = 1
  AND closed_at >= date('now', 'start of day');
```

**Last 7 days:**
```sql
SELECT
  COUNT(*) as total_trades,
  SUM(CASE WHEN realized_pnl_usdt > 0 THEN 1 ELSE 0 END) as wins,
  ROUND(SUM(realized_pnl_usdt), 4) as total_pnl,
  ROUND(CAST(SUM(CASE WHEN realized_pnl_usdt > 0 THEN 1 ELSE 0 END) AS FLOAT) / NULLIF(COUNT(*), 0) * 100, 1) as win_rate_pct
FROM pnl_ledger
WHERE is_closed = 1
  AND closed_at >= date('now', '-7 days');
```

**All-time:**
```sql
SELECT
  COUNT(*) as total_trades,
  SUM(CASE WHEN realized_pnl_usdt > 0 THEN 1 ELSE 0 END) as wins,
  ROUND(SUM(realized_pnl_usdt), 4) as total_pnl,
  ROUND(CAST(SUM(CASE WHEN realized_pnl_usdt > 0 THEN 1 ELSE 0 END) AS FLOAT) / NULLIF(COUNT(*), 0) * 100, 1) as win_rate_pct
FROM pnl_ledger
WHERE is_closed = 1;
```

**P&L by strategy:**
```sql
SELECT
  d.strategy_id,
  COUNT(*) as trades,
  ROUND(SUM(p.realized_pnl_usdt), 4) as total_pnl,
  ROUND(CAST(SUM(CASE WHEN p.realized_pnl_usdt > 0 THEN 1 ELSE 0 END) AS FLOAT) / COUNT(*) * 100, 1) as win_rate_pct
FROM pnl_ledger p
JOIN executions e ON p.execution_id = e.id
JOIN risk_approvals r ON e.approval_id = r.id
JOIN trading_decisions d ON r.decision_id = d.id
WHERE p.is_closed = 1
GROUP BY d.strategy_id
ORDER BY total_pnl DESC;
```

2. Present results in a formatted summary with sections for Today, 7-Day, All-Time, and By Strategy. Include open position count and unrealized P&L if any exist.
