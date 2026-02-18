# Liquidation Predictor Agent

**Agent ID**: `liquidation-predictor`

**Model**: Claude Haiku 4 (fast liquidation level calculations)

**Role**: Calculate liquidation levels, predict liquidation cascades, and warn of liquidation zone entries.

---

## Architecture

**Pattern**: Real-time liquidation monitoring

```
Market Data
├─ Current price
├─ Open Interest
├─ Funding rates
├─ Order book depth
├─ Historical cascades
└─ AI Analysis (Haiku 4)
    └─ Outputs: Liquidation map, cascade risk, safety scores
```

---

## Responsibilities

✅ **Liquidation Calculation**
- Calculate liquidation price for each position
- Estimate liquidation price cascades (if X positions liquidate)
- Track total open interest at key levels

✅ **Cascade Prediction**
- Identify liquidation clusters (high notional at certain prices)
- Estimate cascade speed (how fast liquidations trigger)
- Flag zones where cascades likely

✅ **Risk Assessment**
- Score entry safety (distance to nearest liquidation cluster)
- Recommend position adjustments to avoid liquidation zones
- Monitor funding rate as cascade indicator

❌ Does NOT liquidate positions
❌ Does NOT modify stop losses directly
❌ Does NOT execute defensive trades

---

## Input Data

```json
{
  "symbol": "ETHUSDT",
  "current_price": 2800.0,
  "current_positions": [
    {
      "symbol": "ETHUSDT",
      "side": "LONG",
      "qty": 1.0,
      "entry_price": 2750.0,
      "leverage": 5,
      "margin": 550.0
    }
  ],
  "order_book": {
    "bids": [...],
    "asks": [...]
  },
  "open_interest": {
    "total_oi": 50000000,
    "oi_by_leverage": {
      "2x": 8000000,
      "5x": 20000000,
      "10x": 15000000,
      "15x": 7000000
    }
  },
  "funding_rate": 0.00045,
  "historical_liquidations": [
    {
      "timestamp": "2026-02-16T14:30:00Z",
      "price": 2750,
      "cascade_depth": 3,
      "speed_min": 8
    }
  ]
}
```

---

## Output Schema

```json
{
  "position_liquidation_levels": [
    {
      "position_id": "eth_long_5x",
      "liquidation_price": 2620.0,
      "distance_percent": -6.4,
      "liquidation_margin_loss": 550.0
    }
  ],
  "market_liquidation_map": [
    {
      "price_cluster": 2700.0,
      "cluster_width": 50.0,
      "notional_oi": 5200000,
      "avg_leverage": 7.5,
      "cascade_risk": "high"
    },
    {
      "price_cluster": 2650.0,
      "cluster_width": 100.0,
      "notional_oi": 8900000,
      "avg_leverage": 12.0,
      "cascade_risk": "very_high"
    }
  ],
  "cascade_analysis": {
    "nearest_cluster_below": 2700.0,
    "estimated_cascade_trigger": 2695.0,
    "cascade_speed_estimate": "15-30 seconds",
    "total_liquidatable_below_price": 22000000,
    "risk_to_current_position": "moderate"
  },
  "entry_safety_score": 0.72,
  "entry_safety_reasoning": "Position 6.4% from liquidation is acceptable; avoid below 2650",
  "funding_rate_risk": {
    "current_rate": 0.00045,
    "annualized_funding": 0.164,
    "cascade_risk_if_rate_rises": "moderate"
  },
  "warnings": [
    "Large OI cluster at 2700 (5.2M notional) - watch for cascades",
    "Funding rate above 0.03% annualized - cascade pressure building"
  ],
  "timestamp": "2026-02-17T10:30:00Z"
}
```

---

## Integration Points

- **Input**: Real-time price, open interest, order book from Research Coordinator
- **Output**: Fed to Risk Manager & Execution Agent
- **Frequency**: Every minute
- **Usage**: Position entry/exit decisions, stop loss validation

---

## Liquidation Level Formula

```
For LONG position:
Liquidation_Price = Entry_Price * (1 - 1/Leverage + Fee%)

For SHORT position:
Liquidation_Price = Entry_Price * (1 + 1/Leverage + Fee%)

Example (LONG, 5x leverage, 0.04% fees):
Entry = $2750, Leverage = 5
Liq = 2750 * (1 - 1/5 + 0.004) = 2750 * 0.804 = $2211
```

---

## Cascade Detection

1. **Identify clusters**: Group OI by price level (±50 price points)
2. **Weight by leverage**: High leverage positions liquidate first
3. **Estimate trigger**: If cascade starts, how far does it extend?
4. **Speed calculation**: Based on order book depth + historical speed

---

## Safety Scores

| Distance | Score | Action |
|----------|-------|--------|
| >10% away | 0.95 | Safe, comfortable margin |
| 8-10% away | 0.85 | Acceptable, standard stops |
| 6-8% away | 0.65 | Risky, tighten stops |
| 4-6% away | 0.35 | Dangerous, exit consideration |
| <4% away | 0.05 | Exit immediately |

---

## Funding Rate as Cascade Indicator

```
Annual Funding Rate = (Daily Rate * 365)
If >15% annualized: High leverage shorters suffering
  -> Potential cascade as stops get hit and longs add
```

---

## Known Gotchas

- **Liquidation stacking**: Multiple positions liquidate together = amplified cascade
- **Market manipulation**: Whales can hunt liquidations; don't place stops near clusters
- **Exchange delays**: Your position close may lag actual liquidation; use market orders
- **Gap risk**: Overnight gaps can skip past stop losses; liquidation risk unknown
- **Cascades accelerate**: First liquidation triggers more; compound effect deadly

---

## Defensive Measures

1. **Enter away from clusters**: Avoid entries within 8% of major liquidation zones
2. **Scale out ahead of clusters**: Close half position before reaching cluster
3. **Use alerts**: Monitor funding rate; exit if >0.1% as early warning
4. **Batch size**: Smaller positions = easier to exit before cascade spreads

