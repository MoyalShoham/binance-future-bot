# Risk Management System

**Last Updated**: 2026-03-03
**Status**: Production (LIVE trading on Binance Futures mainnet)

## Overview

The Risk Management Agent is the **GLOBAL AUTHORITY** in the trading system. NO trade can be executed without its explicit approval.

**Design Philosophy:**
- **Safety Over Speed**: Conservative defaults, fail-safe on errors
- **Determinism Over Cleverness**: Rule-based validation, reproducible decisions
- **Observability**: Every decision logged with full reasoning

---

## Architecture

```
Trading Decision Agent
        ↓ (proposes trade)
Risk Management Agent  ← [GLOBAL AUTHORITY CHECKPOINT]
        ↓ (3 possible outcomes)
        ├─ APPROVED → Execute as-is
        ├─ MODIFIED → Execute with adjusted parameters
        └─ REJECTED → Block trade entirely
```

### Authority Boundaries

**Can Do:**
- Approve trades without modification
- Reject any trade for any reason
- Modify position size, leverage, stop loss, take profit levels
- Activate kill switches (global, symbol, strategy)
- Override any agent's recommendations

**Cannot Do:**
- Create new trading decisions (only evaluate existing ones)
- Execute trades directly (sends approval to Execution Agent)
- Be bypassed by any other agent

---

## Risk Validation System

### 12 Core Risk Checks (Rule-Based)

Every trade undergoes **12 sequential risk checks**. All checks are **deterministic** (no LLM-based decisions).

#### 1. Kill Switches (CRITICAL - Highest Priority)

**Purpose:** Emergency trade blocking

**Types:**
- **Global**: Disables ALL trading system-wide (manual or auto-triggered)
- **Symbol**: Blocks specific trading pairs (e.g., BTCUSDT)
- **Strategy**: Disables specific strategies
- **Volatility Circuit Breaker**: Auto-triggered on extreme moves (>10% in 1 min)

**Result:** REJECT if any kill switch is active

**Configuration:**
```yaml
risk:
  kill_switches:
    global: false
    symbols: {}    # {"BTCUSDT": true}
    strategies: {}  # {"ema_crossover_scalp": true}
    volatility_circuit_breaker:
      enabled: true
      trigger_pct: 0.10  # 10% move in 1 minute
      cooldown_seconds: 300  # 5 minutes
```

---

#### 2. Max Daily Drawdown (CRITICAL)

**Purpose:** Prevent catastrophic daily losses

**Rule:** Total realized P&L for today must not exceed limit

**Formula:**
```
daily_pnl = sum(realized_pnl for trades closed today)
daily_drawdown_pct = daily_pnl / account_equity
```

**Limit:** 60% (config: `max_daily_drawdown_pct: 0.80` — the value is subtracted from 1.0, so 0.80 = 20% remaining = 80% of equity preserved, effectively 60% max drawdown for a micro account)

**Result:** REJECT if daily drawdown exceeds limit

**Configuration:**
```yaml
risk:
  max_daily_drawdown_pct: 0.80  # 60% daily drawdown limit
```

---

#### 3. Max Risk Per Trade (WARNING)

**Purpose:** Limit maximum loss on any single trade

**Rule:** Risk amount (position size x stop distance) must not exceed % of equity

**Formula:**
```
stop_distance_pct = abs((entry_price - stop_loss) / entry_price)
risk_usdt = position_size_usdt × stop_distance_pct
risk_pct = risk_usdt / account_equity
```

**Limit:** 2%

**Result:** MODIFY if `risk_pct > 2%` (reduce position size)

**Configuration:**
```yaml
risk:
  max_risk_per_trade_pct: 0.02  # 2%
```

---

#### 4. Max Portfolio Exposure (WARNING)

**Purpose:** Prevent over-leverage across all positions

**Rule:** Total capital deployed (all open positions + new trade) must not exceed % of equity

**Formula:**
```
current_exposure = sum(position_size for all open positions)
new_exposure = current_exposure + new_position_size
exposure_pct = new_exposure / account_equity
```

**Limit:** 55%

**Result:** MODIFY if `exposure_pct > 55%` (reduce position size)

**Configuration:**
```yaml
risk:
  max_portfolio_exposure_pct: 0.55  # 55%
```

---

#### 5. Leverage Limit (WARNING - Volatility-Adjusted)

**Purpose:** Reduce leverage in volatile markets

**Rule:** Leverage must not exceed limit based on current volatility

**Volatility Tiers (current production — all set to 10x):**
- **Low Volatility** (< 2%): Max 10x leverage
- **Medium Volatility** (2-5%): Max 10x leverage
- **High Volatility** (> 5%): Max 10x leverage

**Result:** MODIFY if leverage exceeds tier limit

**Configuration:**
```yaml
risk:
  leverage_limits:
    low_volatility: 10
    medium_volatility: 10
    high_volatility: 10
```

---

#### 6. Volatility Gate (WARNING)

**Purpose:** Block trades during extreme volatility

**Rule:** Current volatility must not exceed threshold

**Limit:** 5%

**Result:** REJECT if `volatility_pct > 5%`

**Configuration:**
```yaml
risk:
  volatility_gate_threshold_pct: 0.05  # 5%
```

---

#### 7. Position Concentration (WARNING)

**Purpose:** Prevent oversized single positions

**Rule:** No single position should exceed % of total equity

**Limit:** 100% (single coin mode — only 1 concurrent position)

**Result:** MODIFY if `concentration_pct > 100%`

**Configuration:**
```yaml
risk:
  max_position_concentration_pct: 1.0  # 100% (single coin mode)
```

---

#### 8. Available Margin (CRITICAL)

**Purpose:** Ensure sufficient margin for position

**Rule:** Required margin must not exceed available balance

**Formula:**
```
required_margin = position_size_usdt / leverage
available_balance = (from Binance API)
```

**Result:** REJECT if `required_margin > available_balance`

---

#### 9. Duplicate Position Guard (CRITICAL)

**Purpose:** Prevent opening a second position on same symbol

**Rule:** Check Binance API for existing open position on the symbol

**Result:** REJECT if position already exists for this symbol

---

#### 10. Funding Rate Filter (WARNING)

**Purpose:** Avoid trades that pay high funding rates (crowded side)

**Rule:** Block LONGs when funding rate > threshold, block SHORTs when funding rate < -threshold

**Limit:** 0.05% per 8h funding period

**Result:** REJECT if trade direction aligns with extreme funding

**Configuration:**
```yaml
risk:
  max_funding_rate_pct: 0.0005  # 0.05%
```

---

#### 11. Consecutive Loss Cooldown (CRITICAL)

**Purpose:** Prevent tilt trading after losing streaks

**Rule:** If N consecutive losses within lookback window, pause trading

**Tiers:**
- **Symbol cooldown**: 3 consecutive losses in 60min → 45min pause for that symbol
- **Global kill switch**: 5 consecutive losses → 2h global trading shutdown (auto-resets)

**Result:** REJECT if cooldown is active

**Configuration:**
```yaml
risk:
  consecutive_loss_cooldown:
    max_losses: 3
    lookback_minutes: 60
    cooldown_minutes: 45
    global_kill_losses: 5
    global_kill_cooldown_hours: 2
```

---

#### 12. Fee Filter (Pre-Trade Profitability Check)

**Purpose:** Reject trades where expected profit doesn't justify fees

**Rule:** Expected profit must exceed N times the round-trip fees

**Formula:**
```
round_trip_fees = position_size × (maker_bps + taker_bps) × 2
expected_profit = position_size × tp_distance_pct
must_pass: expected_profit >= round_trip_fees × fee_buffer_multiplier
```

**Limit:** 3.0x round-trip fees

**Result:** REJECT if expected profit < 3x fees

**Configuration:**
```yaml
execution:
  fees:
    maker_bps: 2    # 0.02%
    taker_bps: 5    # 0.05%
    pre_trade_fee_filter:
      enabled: true
      fee_buffer_multiplier: 3.0
```

---

## Position Sizing

### Current Method: Fixed Percentage

**Formula:**
```
margin = account_equity × position_pct
notional = margin × leverage
quantity = notional / entry_price
```

**Limits:**
- Min position: 13% of equity as margin ($65 × 0.13 × 10x = ~$85 notional)
- Max position: 15% of equity as margin ($65 × 0.15 × 10x = ~$98 notional)
- Min notional floor: $100 (Binance minimum, auto round-up)

**Configuration:**
```yaml
risk:
  position_sizing:
    method: "fixed_percentage"
    min_position_pct: 0.13  # 13% of equity as margin
    max_position_pct: 0.15  # 15% of equity as margin
    kelly_fraction: 0.5     # For Kelly method (not currently used)
    atr_multiplier: 2.0     # For ATR method (not currently used)
```

### Alternative Methods (Available)

- **Kelly Criterion**: Uses real DB win rate, half-Kelly for safety. Falls back to 50% win rate if <30 trades.
- **ATR-Based**: Position size inversely proportional to ATR-derived stop distance.

---

## Approval Decision Logic

```
┌─────────────────────────────────────┐
│  Run All 12 Risk Checks             │
└────────────┬────────────────────────┘
             │
             ├─ Any CRITICAL failures?
             │  └─ YES → REJECT
             │
             ├─ Any WARNING failures?
             │  └─ YES → MODIFY (adjust parameters)
             │
             └─ All checks passed?
                └─ YES → APPROVE
```

### APPROVED (All Checks Passed)
Trade proceeds exactly as proposed by Trading Decision Agent.

### REJECTED (Critical Failure)
Trade is completely blocked. Critical failures: kill switch active, daily drawdown exceeded, insufficient margin, duplicate position, consecutive loss cooldown.

### MODIFIED (Warning Failures)
Trade is approved with adjusted parameters: reduced position size, reduced leverage, adjusted stop loss.

---

## Stateless Design

**The Risk Manager is STATELESS.** All state comes from:

1. **Database** (via DatabaseQueries): Open/closed positions, today's P&L, historical trades
2. **Binance API** (via BinanceFuturesClient): Available balance, total equity, unrealized P&L
3. **Trading Decision** (from pipeline): Proposed trade parameters
4. **Research Summary** (from pipeline): Market volatility, technical indicators

**Benefit:** Same inputs → Same output (reproducible, auditable)

---

## Current Production Risk Limits

| Parameter | Value |
|-----------|-------|
| Max risk per trade | 2% |
| Max daily drawdown | 60% (config: 0.80) |
| Max portfolio exposure | 55% |
| Max concentration/symbol | 100% (single coin mode) |
| Max concurrent positions | 1 |
| Default leverage | 10x (all volatility tiers) |
| Min position size | 13% of equity (margin) |
| Max position size | 15% of equity (margin) |
| Min notional floor | $100 (Binance minimum, auto round-up) |
| Min R:R ratio | 1.5:1 |
| Min SL distance | 0.3% |
| Fee filter | 3.0x round-trip fees |
| Funding rate limit | 0.05% |
| Consecutive loss cooldown | 3 losses in 60min → 45min pause |
| Global kill switch | 5 consecutive losses → 2h shutdown |
| Volatility gate | 5% |
| Min confidence | 75% |
| Confluence gate | 3+ of 5 factors required |

---

## Safety Mechanisms

### Fail-Safe Defaults

- **On Error:** REJECT (never approve on exception)
- **Missing Data:** Use conservative assumptions
- **API Failure:** Assume zero balance (blocks trades)
- **Database Error:** Cannot verify P&L → REJECT

### Audit Trail

All risk decisions are persisted to database via Storage & Reporting Agent:
- Approval ID, Decision ID
- All risk check results
- Modified parameters (if any)
- Rejection reason (if rejected)
- Processing time

---

## FAQ

**Q: Can I disable specific risk checks?**
A: No. All checks are mandatory for safety. You can adjust limits in config, but cannot disable checks.

**Q: What happens if Risk Manager fails?**
A: The system **REJECTS the trade**. Fail-safe principle: when uncertain, be conservative.

**Q: Can the Execution Agent bypass Risk Manager?**
A: **NO.** Risk Manager has GLOBAL AUTHORITY. No agent can bypass it.

**Q: How do I activate the global kill switch?**
A: Set `risk.kill_switches.global: true` in config and restart. Or it auto-activates after 5 consecutive losses.

**Q: Can I use LLMs for risk decisions?**
A: **NO** for core risk checks. LLMs are used only for regime classification (every 15min). Risk checks are rule-based only for determinism.

---

## Summary

- **Global Authority**: No trade executes without Risk Manager approval
- **12 Risk Checks**: Kill switches, drawdown, per-trade risk, exposure, leverage, volatility gate, concentration, margin, duplicate guard, funding rate, consecutive loss cooldown, fee filter
- **3 Outcomes**: APPROVED, REJECTED, MODIFIED
- **Stateless Design**: All state from DB/API (reproducible)
- **Fail-Safe**: REJECT on error
- **Observable**: Every decision logged with full reasoning
