# DB Query

Run a custom SQL query against the trading database.

## Steps

1. The user provides a SQL query (or a natural language description of what they want to query).

2. If the user provided natural language, convert it to SQL using this schema:

   **Tables**: research_summaries, trading_decisions, risk_approvals, executions, pnl_ledger, audit_trail, performance_metrics

   **Key columns**:
   - `pnl_ledger`: id, execution_id, symbol, side, entry_price, exit_price, quantity, leverage, realized_pnl_usdt, unrealized_pnl_usdt, fees_usdt, is_closed, created_at, closed_at, sl_order_id, tp_order_id
   - `trading_decisions`: id, research_summary_id, symbol, decision, confidence, strategy_id, entry_price, stop_loss, take_profit_levels, created_at
   - `risk_approvals`: id, decision_id, approval_status, risk_checks, modified_parameters, created_at
   - `executions`: id, approval_id, execution_mode, execution_status, symbol, side, order_details, created_at

3. Run the query using the MCP sqlite server or fall back to:

```bash
sqlite3 -header -column data/trading_system.db "YOUR SQL QUERY HERE"
```

4. Present the results in a formatted table. For large result sets, limit to 50 rows and note the total count.
