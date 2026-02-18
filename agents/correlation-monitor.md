# Correlation Monitor Agent

**Agent ID**: `correlation-monitor`

**Model**: Claude Haiku 4 (fast inter-asset correlation analysis)

**Role**: Monitor correlations between BTC, major alts, and indices to avoid over-correlated positions.

---

## Architecture

**Pattern**: Real-time correlation matrix with anomaly detection

```
Price Data (BTC, ETH, SOL, ADA, XRP, etc.)
├─ Compute rolling correlation matrix (30-period)
├─ Detect correlation regime shifts
├─ Identify low-corr trading pairs
├─ Monitor tail risk (tail correlation)
└─ AI Analysis (Haiku 4)
    └─ Outputs: Diversification score + red flags
```

---

## Responsibilities

✅ **Correlation Tracking**
- 15-min rolling correlations (BTC vs major alts)
- Sector-level correlations (L1s vs DEX tokens)
- Index correlations (crypto vol vs SPY, DXY)

✅ **Regime Detection**
- Normal correlation
- High correlation (risk concentration)
- Decoupling (opportunity for pairs trading)
- Tail correlation (crisis mode)

✅ **Portfolio Optimization Hints**
- Recommend uncorrelated symbols for diversification
- Flag when portfolio becomes too clustered
- Suggest hedge positions

❌ Does NOT execute hedges
❌ Does NOT override trading decisions
❌ Does NOT adjust position sizes directly

---

## Input Data

```json
{
  "price_timeseries": {
    "BTC": [45000, 45100, 45050, ...],
    "ETH": [2500, 2510, 2505, ...],
    "SOL": [125, 126, 125.5, ...],
    "ADA": [0.95, 0.96, 0.95, ...],
    "XRP": [2.50, 2.51, 2.50, ...]
  },
  "current_positions": [
    {"symbol": "ETHUSDT", "size": 0.15},
    {"symbol": "SOLUSDT", "size": 0.25}
  ],
  "time_window": "30m"
}
```

---

## Output Schema

```json
{
  "correlation_matrix": {
    "BTC_ETH": 0.85,
    "BTC_SOL": 0.72,
    "ETH_SOL": 0.68,
    "BTC_ADA": 0.45,
    "ETH_ADA": 0.38
  },
  "regime": "high_correlation",
  "regime_confidence": 0.88,
  "portfolio_diversity_score": 0.62,
  "current_position_correlation": {
    "ETH_SOL": 0.68,
    "correlation_risk": "moderate"
  },
  "recommendations": [
    {
      "action": "Add ADA position",
      "reason": "Low correlation to ETH (0.38) and SOL (0.45)",
      "expected_portfolio_diversity": 0.78
    }
  ],
  "risk_flags": [
    "High BTC-ETH correlation (0.85) - portfolio clustered"
  ],
  "tail_correlation": 0.92,
  "crisis_flag": false,
  "timestamp": "2026-02-17T10:30:00Z"
}
```

---

## Integration Points

- **Input**: Price feeds from Research Coordinator, portfolio state from Storage
- **Output**: Fed to Trading Decision & Risk Manager
- **Frequency**: Every 30 minutes
- **Usage**: Diversification checks, hedging recommendations

---

## Thresholds

| Metric | Low | Medium | High |
|--------|-----|--------|------|
| Avg correlation | <0.5 | 0.5-0.7 | >0.7 |
| Portfolio diversity | >0.7 | 0.5-0.7 | <0.5 |
| Tail correlation | <0.7 | 0.7-0.85 | >0.85 |
| Position concentration | <70% | 70-85% | >85% |

---

## Statistical Methods

- **Rolling Pearson correlation** (30-period minimum)
- **Spearman rank correlation** (non-linear relationships)
- **Tail correlation** (extreme moves; upper/lower quantiles)
- **Regime detection** (Markov switching if available)

---

## Use Cases

1. **Avoid concentration risk**: Don't add another L1 if already holding ETH + SOL
2. **Pair trading**: If correlation suddenly drops, spread trade opportunity
3. **Crisis hedge**: When tail correlation → 0.95, reduce notional exposure
4. **Rebalancing**: Suggest when two positions become >0.85 correlated

---

## Known Gotchas

- **Short lookbacks**: With only 30 min data, correlation estimates noisy
- **Structural breaks**: Correlations regime-shift during bull/bear markets
- **Thin symbols**: Low-volume alts have erratic correlation; use only for major 20 coins
- **Lagged effects**: Correlation lag = time for information diffusion; altseason decouples BTC from alts with delay

