# Entry Optimizer Agent

**Agent ID**: `entry-optimizer`

**Model**: Claude Haiku 4 (fast entry point analysis)

**Role**: Fine-tune entry prices within approved trade setups to maximize risk-reward and fill probability.

---

## Architecture

**Pattern**: Micro-timeframe entry optimization

```
Trading Decision (Trade approved)
├─ Order book depth analysis
├─ Support/resistance levels
├─ Chart pattern (divergences, breakouts)
├─ Market microstructure (VWAP, POC)
└─ AI Optimization (Haiku 4)
    └─ Outputs: Optimal entry price, fill probability, time window
```

---

## Responsibilities

✅ **Entry Analysis**
- Analyze order book depth (top 20 levels)
- Identify micro-level support/resistance
- Detect divergences on 1-5 min chart
- Calculate VWAP and point of control (POC)

✅ **Price Optimization**
- Suggest optimal entry within ±0.5% of suggested price
- Calculate fill probability at each level
- Estimate slippage impact

✅ **Timing**
- Identify best entry window (next 5-15 minutes)
- Watch for momentum shifts
- Time breakouts with volume confirmation

❌ Does NOT override position size
❌ Does NOT change stop loss
❌ Does NOT reject approved trades

---

## Input Data

```json
{
  "trading_decision": {
    "symbol": "ETHUSDT",
    "direction": "LONG",
    "suggested_entry": 2800.0,
    "suggested_sl": 2790.0,
    "suggested_tp": 2830.0
  },
  "order_book": {
    "bids": [
      {"price": 2799.50, "amount": 50},
      {"price": 2799.40, "amount": 75},
      {"price": 2799.30, "amount": 120}
    ],
    "asks": [
      {"price": 2800.10, "amount": 60},
      {"price": 2800.20, "amount": 90},
      {"price": 2800.30, "amount": 140}
    ]
  },
  "recent_candles": [
    {"open": 2795, "high": 2805, "low": 2794, "close": 2801},
    {"open": 2800, "high": 2803, "low": 2799, "close": 2802}
  ],
  "vwap": 2798.50,
  "poc": 2799.00
}
```

---

## Output Schema

```json
{
  "optimal_entry": 2799.75,
  "entry_reasoning": "Just above POC, within order book supply",
  "fill_probability": 0.87,
  "expected_slippage": 0.005,
  "entry_window": {
    "start": "2026-02-17T10:31:00Z",
    "duration_seconds": 300,
    "confidence": 0.82
  },
  "alternative_entries": [
    {
      "price": 2799.20,
      "probability": 0.65,
      "reason": "Support level + divergence"
    }
  ],
  "timing_hint": "Wait 2 minutes for volume spike confirmation",
  "microstructure_signals": [
    "Bid-ask imbalance favoring buys (2:1 ratio)"
  ],
  "timestamp": "2026-02-17T10:30:00Z"
}
```

---

## Integration Points

- **Input**: Trading Decision approval, real-time order book, price feeds
- **Output**: Fed to Execution Agent for order placement
- **Frequency**: Real-time (on trade approval)
- **Latency requirement**: <500ms for optimal effectiveness

---

## Entry Strategies

| Strategy | Condition | Entry Price |
|----------|-----------|------------|
| Aggressive | Momentum strong, volume confirms | Ask side |
| Balanced | Normal conditions | VWAP or POC |
| Patience | Low conviction setup | Bid side + support |
| Micro-pullback | Brief dip in approved direction | Support level |

---

## Order Book Analysis

- **Imbalance ratio**: Sum of buy side / sum of sell side
- **Depth**: Total liquidity at each level
- **Icebergs**: Watch for large orders that refresh
- **Clustering**: Levels with +3 orders = key support/resistance

---

## Fill Probability Calculation

```
P(fill) = 1 - (amount_above_entry / total_seller_volume_to_entry)
```

---

## Known Gotchas

- **Iceberg orders**: Binance shows only visible portion; real depth unknown
- **Flash fills**: Large market orders can deplete levels in milliseconds
- **Manipulation**: Watch for spoofing (fake orders withdrawn before fill)
- **Queue position**: Even at same price, your position in queue affects fill
- **Slippage**: On breakout entries, expect 2-3x normal slippage

