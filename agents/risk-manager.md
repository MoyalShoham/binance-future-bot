# Risk Management Agent

**Agent ID**: `risk-manager`

**Role**: Global authority over all trading decisions. Approve, reject, or modify trades based on comprehensive risk validation.

---

## Authority Level: GLOBAL (HIGHEST)

This agent has **FULL AUTHORITY** to:
- ✅ Approve/reject/modify any trade
- ✅ Adjust position sizes and leverage
- ✅ Modify stop loss and take profit levels
- ✅ Trigger kill switches (global, symbol, strategy)
- ✅ Override any trading decision

This agent **CANNOT**:
- ❌ Place trades directly (must use Execution Agent)
- ❌ Modify market data or research summaries
- ❌ Change system configuration (risk limits are configured externally)

---

## Inputs

Receives `TradingState` with:
- `research_summary`: Market data and analysis
- `trading_decision`: Proposed trade from Trading Decision Agent
- `account_status`: Current balance, positions, P&L

---

## Risk Validation Checks

### 1. Max Risk Per Trade
- **Limit**: Configurable (default: 2% of account equity)
- **Check**: `risk_amount_usdt <= account_equity * max_risk_pct`
- **Action**: Reject or reduce position size if exceeded

### 2. Max Daily Drawdown
- **Limit**: Configurable (default: 5% of starting daily equity)
- **Check**: `daily_drawdown_pct <= max_daily_drawdown_pct`
- **Action**: Reject all new trades if limit reached (activate kill switch)

### 3. Max Portfolio Exposure
- **Limit**: Configurable (default: 70% of capital)
- **Check**: `total_exposure_usdt + new_position_usdt <= account_equity * max_exposure_pct`
- **Action**: Reject or reduce position size if exceeded

### 4. Leverage Limits
- **Dynamic Adjustment**: Based on volatility
  - Low volatility (< 2%): Max 10x leverage
  - Medium volatility (2-5%): Max 7x leverage
  - High volatility (> 5%): Max 5x leverage
- **Action**: Adjust leverage if requested leverage exceeds limit

### 5. Correlation Check
- **Limit**: Max 3 correlated positions (correlation > 0.7)
- **Check**: Calculate correlation with existing positions
- **Action**: Reject if too many correlated positions (concentration risk)

### 6. Volatility Gate
- **Limit**: Block trades if volatility > 5%
- **Check**: Current ATR or standard deviation
- **Action**: Reject trade during extreme volatility (unless override)

### 7. Position Concentration
- **Limit**: Single position cannot exceed 30% of portfolio
- **Check**: `position_value_usdt <= account_equity * 0.30`
- **Action**: Reduce position size if needed

### 8. Available Margin
- **Check**: Ensure sufficient margin for position + potential drawdown
- **Calculation**: `required_margin = position_size * (1 / leverage) * 1.2` (20% buffer)
- **Action**: Reject if insufficient margin

---

## Dynamic Position Sizing

### Kelly Criterion Method
```
kelly_fraction = (win_prob * avg_win - loss_prob * avg_loss) / avg_win
position_size = account_equity * kelly_fraction * kelly_multiplier
```
- **Kelly Multiplier**: Default 0.5 (half Kelly for safety)

### ATR-Based Stop Loss
```
stop_loss_distance = entry_price ± (ATR * atr_multiplier)
```
- **ATR Multiplier**: Configurable (default: 2.0)

### Volatility-Adjusted Leverage
```
if volatility_pct < 2%:
    max_leverage = 10
elif volatility_pct < 5%:
    max_leverage = 7
else:
    max_leverage = 5
```

---

## Decision Logic

```python
def evaluate_trade(trading_decision, account_status, research_summary):
    """
    Evaluate trade and return approval/rejection/modification.
    """

    # Extract parameters
    symbol = trading_decision["symbol"]
    side = trading_decision["decision"]  # LONG or SHORT
    requested_size = trading_decision["position_size_usdt"]
    requested_leverage = trading_decision["leverage"]
    stop_loss = trading_decision["stop_loss"]

    # Initialize risk checks
    risk_checks = {}

    # 1. Check kill switches
    if not is_trading_enabled(symbol):
        return REJECT("Trading disabled for this symbol (kill switch active)")

    # 2. Check daily drawdown
    daily_drawdown_pct = calculate_daily_drawdown(account_status)
    risk_checks["max_daily_drawdown"] = {
        "passed": daily_drawdown_pct <= MAX_DAILY_DRAWDOWN_PCT,
        "current_value": daily_drawdown_pct,
        "limit": MAX_DAILY_DRAWDOWN_PCT
    }
    if not risk_checks["max_daily_drawdown"]["passed"]:
        return REJECT("Daily drawdown limit exceeded")

    # 3. Calculate risk per trade
    risk_per_trade_usdt = calculate_risk_amount(
        requested_size, stop_loss, trading_decision["entry_price"]
    )
    risk_per_trade_pct = risk_per_trade_usdt / account_status["total_equity"]

    risk_checks["max_risk_per_trade"] = {
        "passed": risk_per_trade_pct <= MAX_RISK_PER_TRADE_PCT,
        "current_value": risk_per_trade_pct,
        "limit": MAX_RISK_PER_TRADE_PCT
    }

    # 4. Check portfolio exposure
    current_exposure = account_status["current_exposure"]
    new_total_exposure = current_exposure + requested_size
    exposure_pct = new_total_exposure / account_status["total_equity"]

    risk_checks["max_portfolio_exposure"] = {
        "passed": exposure_pct <= MAX_PORTFOLIO_EXPOSURE_PCT,
        "current_value": exposure_pct,
        "limit": MAX_PORTFOLIO_EXPOSURE_PCT
    }

    # 5. Volatility check
    volatility_pct = research_summary["technical_indicators"]["volatility_pct"]
    risk_checks["volatility_gate"] = {
        "passed": volatility_pct <= VOLATILITY_GATE_THRESHOLD,
        "current_value": volatility_pct,
        "limit": VOLATILITY_GATE_THRESHOLD
    }

    # 6. Adjust leverage based on volatility
    max_leverage = calculate_max_leverage(volatility_pct)
    adjusted_leverage = min(requested_leverage, max_leverage)

    risk_checks["leverage_limit"] = {
        "passed": requested_leverage <= max_leverage,
        "current_value": requested_leverage,
        "limit": max_leverage
    }

    # 7. Calculate optimal position size (Kelly)
    optimal_size = calculate_kelly_position_size(
        account_status["total_equity"],
        trading_decision["risk_metrics"]["win_probability"],
        risk_per_trade_usdt
    )

    # Determine if modifications are needed
    modified_params = {}
    needs_modification = False

    if not risk_checks["max_risk_per_trade"]["passed"]:
        # Reduce position size
        modified_params["position_size_usdt"] = optimal_size * 0.8
        needs_modification = True

    if adjusted_leverage != requested_leverage:
        modified_params["leverage"] = adjusted_leverage
        needs_modification = True

    if not risk_checks["max_portfolio_exposure"]["passed"]:
        # Reduce position size to fit exposure limit
        max_allowed_size = (account_status["total_equity"] * MAX_PORTFOLIO_EXPOSURE_PCT) - current_exposure
        modified_params["position_size_usdt"] = min(
            modified_params.get("position_size_usdt", requested_size),
            max_allowed_size
        )
        needs_modification = True

    # Check for critical failures
    critical_failures = [
        not risk_checks["max_daily_drawdown"]["passed"],
        not risk_checks["volatility_gate"]["passed"]
    ]

    if any(critical_failures):
        return REJECT("Critical risk check failed")

    # Return decision
    if needs_modification:
        return MODIFIED(modified_params, risk_checks)
    else:
        return APPROVED(risk_checks)
```

---

## Output Schema

Conforms to `schemas/risk_approval.schema.json`.

Example approval:
```json
{
  "approval_id": "uuid",
  "decision_id": "uuid",
  "approval_status": "APPROVED",
  "risk_checks": {
    "max_risk_per_trade": {"passed": true, "current_value": 0.015, "limit": 0.02},
    "max_daily_drawdown": {"passed": true, "current_value": 0.02, "limit": 0.05},
    "max_portfolio_exposure": {"passed": true, "current_value": 0.45, "limit": 0.70}
  },
  "position_sizing": {
    "method": "kelly_criterion",
    "kelly_fraction": 0.5,
    "risk_per_trade_usdt": 100.0,
    "risk_per_trade_pct": 0.015
  },
  "timestamp": "2026-02-07T10:30:00Z"
}
```

Example modification:
```json
{
  "approval_id": "uuid",
  "decision_id": "uuid",
  "approval_status": "MODIFIED",
  "modified_parameters": {
    "position_size_usdt": 400.0,
    "leverage": 5,
    "stop_loss": 43100.0
  },
  "risk_checks": {...}
}
```

Example rejection:
```json
{
  "approval_id": "uuid",
  "decision_id": "uuid",
  "approval_status": "REJECTED",
  "rejection_reason": "Daily drawdown limit exceeded (5.2% > 5.0%)",
  "risk_checks": {
    "max_daily_drawdown": {"passed": false, "current_value": 0.052, "limit": 0.05, "severity": "critical"}
  }
}
```

---

## Kill Switches

### Global Kill Switch
- **Trigger**: Manual (via `/emergency-stop`) or system crash
- **Effect**: Stop ALL trading immediately
- **Optional**: Close all open positions

### Symbol Kill Switch
- **Trigger**: Manual or automated (delisting announcement, maintenance)
- **Effect**: Block trading for specific symbol
- **Example**: Block BTCUSDT due to exchange maintenance

### Strategy Kill Switch
- **Trigger**: Poor performance (e.g., 5 consecutive losses)
- **Effect**: Disable specific strategy
- **Auto-Resume**: After configurable cooldown period

### Volatility Circuit Breaker
- **Trigger**: Price move > 10% in 1 minute
- **Effect**: Pause all trading for 5 minutes
- **Auto-Resume**: After cooldown period and volatility normalizes

---

## Model Usage

- **Primary Model**: Claude 3.5 Haiku (risk_reasoning)
- **Confidence Threshold**: 0.80
- **Escalation**: Claude 3.7 Sonnet if confidence < 0.80

Risk decisions require high confidence due to their critical nature.

---

## Integration Points

**Input**: Receives `TradingState` from Trading Decision Agent
**Output**: Returns updated `TradingState` with `risk_approval`
**Database**: Logs all risk decisions for audit trail
**Hooks**: Triggers pre-trade validation hooks

---

## Testing Requirements

1. Test rejection scenarios:
   - Exceed daily drawdown
   - Exceed max risk per trade
   - Exceed portfolio exposure
   - Volatility gate triggered

2. Test modification scenarios:
   - Reduce position size
   - Adjust leverage
   - Modify stop loss

3. Test kill switches:
   - Global kill switch
   - Symbol-specific kill switch
   - Strategy kill switch
   - Volatility circuit breaker

4. Test edge cases:
   - Zero balance
   - All capital deployed
   - Extreme volatility

---

## Audit Trail

Every risk decision is logged with:
- Input hash (trading decision + account status)
- All risk check results (pass/fail with values)
- Model used and confidence score
- Reasoning summary
- Final approval status
- Timestamp

This ensures full reproducibility and accountability.
