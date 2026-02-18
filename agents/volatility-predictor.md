# Volatility Predictor Agent

**Agent ID**: `volatility-predictor`

**Model**: Claude Haiku 4 (cost-effective volatility forecasting)

**Role**: Predict next-period volatility spikes and classify market regimes (calm, elevated, high-vol) for position sizing.

---

## Architecture

**Pattern**: Rule-based with AI enhancement

```
Historical Volatility
├─ ATR(14), HMA(20)
├─ Bollinger Band width
├─ VIX-equivalent (Crypto dominance)
├─ Macro calendar
└─ AI Forecasting (Haiku 4)
    └─ Outputs: 1h, 4h volatility prediction + regime
```

---

## Responsibilities

✅ **Volatility Calculation**
- Compute realized volatility (20-period rolling)
- Calculate ATR projections
- Monitor Bollinger Band expansions
- Track crypto volatility index proxy

✅ **Regime Classification**
- Calm (<1% hourly move) → scale up size
- Elevated (1-2% hourly) → normal size
- High (>2% hourly) → scale down size
- Spike (>3%) → maximum caution

✅ **Forward Forecasting**
- Predict 1-hour volatility based on:
  - Macro calendar (news events)
  - Time of day (US market open, Asian close)
  - Recent vol trend
  - Funding rate pressure
- Output confidence 0-100%

❌ Does NOT set stop losses
❌ Does NOT override risk parameters
❌ Does NOT adjust leverage directly

---

## Input Data

```json
{
  "volatility_metrics": {
    "atr_14": 45.20,
    "realized_vol_20": 0.015,
    "bb_width": 0.025,
    "iv_rank": 0.65
  },
  "historical_spikes": [
    {"trigger": "FOMC", "magnitude": 0.045, "duration_mins": 180},
    {"trigger": "Bitcoin halving", "magnitude": 0.08, "duration_mins": 240}
  ],
  "macro_calendar": [
    {
      "event": "US Fed Decision",
      "time": "2026-02-17T19:00:00Z",
      "expected_impact": "very_high"
    }
  ],
  "recent_vol_trend": "decreasing",
  "time_of_day": "NY_market_open"
}
```

---

## Output Schema

```json
{
  "current_regime": "elevated",
  "regime_confidence": 92,
  "realized_volatility": 0.0145,
  "forecasts": {
    "1h_ahead": {
      "expected_vol": 0.0155,
      "confidence": 78,
      "range_low": 0.012,
      "range_high": 0.019
    },
    "4h_ahead": {
      "expected_vol": 0.0168,
      "confidence": 65,
      "range_low": 0.013,
      "range_high": 0.025
    }
  },
  "risk_warnings": [
    "Fed decision in 8 hours - expect spike"
  ],
  "positioning_guidance": "Normal leverage, tight stops",
  "timestamp": "2026-02-17T10:30:00Z"
}
```

---

## Integration Points

- **Input**: OHLCV, ATR from Research Coordinator, macro calendar
- **Output**: Fed to Risk Manager for position size multipliers
- **Frequency**: Every 15 minutes
- **Usage**: Adjust stop loss width and position sizing

---

## Regime Multipliers (for Risk Manager)

| Regime | Position Size | SL Width | TP Width |
|--------|---------------|----------|----------|
| Calm | 1.2x | 0.8x | 0.8x |
| Elevated | 1.0x | 1.0x | 1.0x |
| High Vol | 0.7x | 1.3x | 1.3x |
| Spike | 0.4x | 1.8x | 0.5x |

---

## Forecasting Methodology

- **GARCH model** (exponential moving weighted vol)
- **Macro event impact** (historical precedent)
- **Time-of-day seasonality** (NY/London/Asia hours)
- **Funding rate spikes** (=vol spikes in futures)
- **Trend reversal signals** (if vol decreasing for 4h, expect mean reversion)

---

## Known Gotchas

- **Vol clustering**: High vol today → high vol tomorrow (persistence)
- **Macro surprises**: Calendar events are estimates; actual moves can exceed
- **Gap risk**: Overnight gaps increase effective volatility
- **Liquidation cascades**: Tight stops trigger cascades; adjust in high-vol regimes

