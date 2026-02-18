# Risk Adjuster Agent

**Agent ID**: `risk-adjuster`

**Model**: Claude Haiku 4.5 (dynamic risk management)

**Role**: Dynamically adjust risk parameters (stop loss, take profit, position size) based on real-time market conditions and system health.

---

## Architecture

**Pattern**: Rule-based with adaptive multipliers

```
Risk Parameters (from config)
├─ Current positions
├─ Market volatility
├─ Drawdown tracking
├─ Funding rate pressure
├─ System health metrics
└─ AI Adaptation (Haiku 4.5)
    └─ Outputs: Adjusted SL/TP, position size multipliers
```

---

## Responsibilities

✅ **Parameter Adjustment**
- Scale stop loss width based on volatility
- Adjust take profit levels dynamically
- Scale position size multipliers
- Tighten limits during high drawdown

✅ **Risk Guardrails**
- Never exceed max_risk_per_trade (1%)
- Enforce daily drawdown limits
- Apply consecutive loss cooldowns
- Monitor correlations for concentration

✅ **Feedback Mechanisms**
- Learn from win rate (Kelly sizing)
- Penalize low-confidence strategies
- Increase caution after consecutive losses
- Raise limits during high-conviction/low-vol periods

❌ Does NOT override hard limits from Risk Manager
❌ Does NOT execute trades
❌ Does NOT modify core risk thresholds

---

## Input Data

```json
{
  "market_conditions": {
    "volatility_regime": "elevated",
    "volatility_multiplier": 1.3,
    "funding_rate": 0.00045,
    "liquidation_pressure": "moderate"
  },
  "system_state": {
    "daily_pnl": -0.8,
    "daily_pnl_percent": -1.2,
    "max_daily_drawdown": -1.5,
    "consecutive_losses": 2,
    "cumulative_confidence": 0.78
  },
  "position_state": {
    "open_positions": 2,
    "avg_leverage": 4.5,
    "portfolio_correlation": 0.72,
    "max_concentration": 0.35
  },
  "strategy_performance": {
    "win_rate": 0.58,
    "win_rate_sample_size": 127,
    "avg_rr_ratio": 2.8,
    "kelly_percent": 0.065
  }
}
```

---

## Output Schema

```json
{
  "adjusted_parameters": {
    "stop_loss_multiplier": 1.2,
    "take_profit_multiplier": 0.9,
    "position_size_multiplier": 0.8,
    "max_leverage_allowed": 4.0
  },
  "reasoning": [
    "Elevated volatility (+30%) -> widen stops",
    "Consecutive losses 2x -> reduce size 20%",
    "Portfolio corr 0.72 -> moderate diversification"
  ],
  "kelly_recommendation": {
    "edge_percent": 6.5,
    "recommended_size": 0.065,
    "current_size": 0.08,
    "action": "reduce_slightly"
  },
  "cooldown_status": {
    "active": false,
    "reason": null
  },
  "warning_flags": [
    "Daily drawdown approaching -2%",
    "Funding rate spike incoming (on-chain data)"
  ],
  "recommendations": [
    {
      "condition": "Next loss -> trigger 15min cooldown",
      "impact": "Prevents death spiral"
    }
  ],
  "timestamp": "2026-02-17T10:30:00Z"
}
```

---

## Integration Points

- **Input**: Market volatility from Volatility Predictor, system state from Storage
- **Output**: Fed to Risk Manager & Execution Agent
- **Frequency**: Every 5 minutes (or on major events)
- **Usage**: Real-time parameter adjustments

---

## Adjustment Factors

### Volatility-based (Vol Multiplier)

| Regime | SL Width | TP Width | Position Size |
|--------|----------|----------|---------------|
| Calm (<1%) | 0.8x | 0.8x | 1.2x |
| Elevated (1-2%) | 1.0x | 1.0x | 1.0x |
| High (2-3%) | 1.3x | 1.3x | 0.7x |
| Extreme (>3%) | 1.8x | 0.5x | 0.4x |

### Drawdown-based

```
If daily_drawdown > -1%: position_size_multiplier = 0.95
If daily_drawdown > -2%: position_size_multiplier = 0.85
If daily_drawdown > -3%: position_size_multiplier = 0.70
If daily_drawdown > -4%: hard stop (no new trades)
```

### Consecutive Loss Cooldowns

```
2 losses in 30min -> warning
3 losses in 30min -> 15min pause
4 losses in 60min -> 30min pause
5 losses in 120min -> 2hr pause
```

### Kelly Sizing

```
kelly_percent = win_rate - (1 - win_rate) / avg_rr_ratio
position_size = 0.5 * kelly_percent (half kelly for safety)
```

---

## Real-time Monitoring

- **Liquidation pressure**: If cascade-style liquidations detected, reduce leverage
- **Funding rate spikes**: If >0.1%, tighten stops and reduce size
- **Macro events**: If major news imminent, increase caution
- **System anomalies**: If API latency high, reduce size and widen SL

---

## Known Gotchas

- **Kelly overfitting**: Win rate can oscillate; use min 30-trade sample
- **Regime shifts**: Parameters good only for current regime; recalculate on shift
- **Liquidation cascades**: Looser stops = safer in high-vol, but also slower exits
- **Drawdown recovery**: Each -1% requires +1.01% gain to recover (compounding)

---

## Configuration (Adaptive Ranges)

```yaml
# These can be adjusted dynamically but stay within these bounds
min_leverage: 2
max_leverage: 5  # from config
min_position_size_multiplier: 0.3
max_position_size_multiplier: 1.5
min_consecutive_loss_cooldown: 15min
max_consecutive_loss_cooldown: 2hr
```

