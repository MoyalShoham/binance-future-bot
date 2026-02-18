# Macro Analyst Agent

**Agent ID**: `macro-analyst`

**Model**: Claude Sonnet 4.5 (deep macro reasoning for regime classification)

**Role**: Synthesize macroeconomic data to detect regime shifts and provide directional conviction scores.

---

## Architecture

**Pattern**: Event-driven macro intelligence

```
Macro Data Sources
├─ Federal Reserve decisions & statements
├─ Treasury yield curves
├─ DXY (Dollar Index)
├─ VIX equivalent (crypto vol index)
├─ Geopolitical events
├─ Regulatory announcements
└─ AI Synthesis (Sonnet 4.5)
    └─ Outputs: Macro regime, conviction, impact on crypto
```

---

## Responsibilities

✅ **Data Collection**
- Monitor Federal Reserve calendar & decisions
- Track Treasury yields (2yr, 10yr spread)
- Monitor DXY (inverse correlation to crypto)
- Scan geopolitical events (supply chain, wars, sanctions)
- Track regulatory news (SEC, CFTC, international)

✅ **Regime Classification**
- Risk-on (loose policy, low rates) -> Bullish crypto
- Risk-off (tight policy, high rates) -> Bearish crypto
- Currency crisis (weak USD) -> Bullish BTC
- Stagflation (inflation + growth slowdown) -> Mixed

✅ **Conviction Scoring**
- Macro tailwinds: +% to conviction
- Macro headwinds: -% to conviction
- Neutral macro: Keep existing conviction unchanged

❌ Does NOT make trading decisions
❌ Does NOT override Trading Decision Agent
❌ Does NOT ignore Technical + Sentiment signals

---

## Input Data

```json
{
  "fed_policy": {
    "current_rate": 0.045,
    "recent_decision": "hold",
    "forward_guidance": "easing_coming",
    "market_expectation_next_cut": "Q2_2026"
  },
  "treasury_yields": {
    "yield_2y": 0.038,
    "yield_10y": 0.042,
    "tsy_2y10y_spread": 0.004,
    "yield_trend": "decreasing"
  },
  "currency_metrics": {
    "dxy": 103.5,
    "dxy_trend": "weakening",
    "btc_usd_correlation": -0.72
  },
  "geopolitical": [
    {
      "event": "Israel-Hamas ceasefire",
      "impact": "risk_on",
      "market_implied_prob": 0.65
    }
  ],
  "regulatory": [
    {
      "agency": "SEC",
      "announcement": "Approved crypto spot ETFs",
      "impact": "bullish",
      "timestamp": "2026-02-17"
    }
  ],
  "macro_indicators": {
    "unemployment": 0.039,
    "inflation_yoy": 0.031,
    "real_rates": 0.014,
    "gdp_growth": 0.023
  }
}
```

---

## Output Schema

```json
{
  "macro_regime": "risk_on_easing",
  "regime_confidence": 0.82,
  "crypto_directional_bias": "bullish",
  "conviction_adjustment": 0.12,
  "conviction_reasoning": "Fed easing + weak dollar + risk appetite recovery",
  "key_drivers": [
    {
      "factor": "Fed rate cut cycle beginning",
      "impact": "+8%",
      "timeline": "Q2 2026"
    },
    {
      "factor": "DXY weakening trend",
      "impact": "+6%",
      "timeline": "Immediate"
    },
    {
      "factor": "Geopolitical stability improving",
      "impact": "+4%",
      "timeline": "Already priced in"
    }
  ],
  "risk_factors": [
    {
      "factor": "Inflation still 3.1% YoY",
      "impact": "-3%",
      "timeline": "If accelerates"
    }
  ],
  "regime_history": {
    "previous_regime": "risk_off_hiking",
    "regime_change_confidence": 0.78,
    "estimated_duration": "8-12 weeks"
  },
  "comparable_periods": [
    {
      "historical_period": "Mar-Jun 2021",
      "description": "Fed easing + weak USD",
      "crypto_performance": "+120% (Ethereum)"
    }
  ],
  "trading_implications": {
    "position_sizing": "1.1x normal (slight aggression)",
    "risk_tolerance": "elevated",
    "hedge_recommendations": null
  },
  "next_catalyst": {
    "date": "2026-03-18",
    "event": "FOMC meeting",
    "expected_impact": "high",
    "directional_bias": "bullish_if_dovish"
  },
  "timestamp": "2026-02-17T10:30:00Z",
  "data_freshness": "Real-time"
}
```

---

## Integration Points

- **Input**: Fed calendar, yields, macro data APIs
- **Output**: Fed to Trading Decision & Recommendation agents
- **Frequency**: On macro events (minutes), daily refresh (once/day)
- **Usage**: Long-term conviction adjustment, regime override

---

## Macro Regime Framework

| Regime | Rate Trend | Dollar | Risk Appetite | Crypto Profile |
|--------|-----------|--------|---------------|---|
| Risk-on easing | Falling | Weak | Strong | Bullish altseason |
| Risk-on hiking | Rising | Strong | Strong | BTC dominance |
| Risk-off easing | Falling | Weak | Weak | Safe haven (BTC) |
| Risk-off hiking | Rising | Strong | Weak | Most bearish |

---

## Conviction Adjustment Algorithm

```
Base_Conviction = Trading_Decision_System_Output

If Fed_Easing: +10%
If Fed_Hiking: -10%
If DXY_Trending_Down: +8%
If DXY_Trending_Up: -8%
If Real_Rates_Negative: +6%
If Real_Rates_Rising: -6%
If Geopolitical_Stable: +4%
If Geopolitical_Crisis: -8%

Clamped to [-25%, +25%] range
```

---

## Data Sources

- **Federal Reserve**: FRED API (St. Louis Fed)
- **Treasury Yields**: Yahoo Finance API
- **Geopolitical**: Reuters, Bloomberg alerts (if available)
- **Regulatory**: Official SEC/CFTC websites
- **Macro Indicators**: Trading Economics API

---

## Known Gotchas

- **Market leads macro**: Price moves before official data releases
- **Expectations vs reality**: If market expects cut but hike happens, dump
- **Correlation breakdown**: In regime shifts, crypto-macro correlation changes
- **Supply shocks**: Ukraine war, Taiwan tensions can override macro models
- **Lag effects**: Macro changes take 4-8 weeks to hit financial assets

---

## Crisis Scenarios (Override All Systems)

```
If War_Declared or Major_Terrorist_Attack:
  -> Exit ALL positions immediately (risk-off shock)
  
If Major_Bank_Failure:
  -> Exit ALL positions (systemic risk)
  
If US_Credit_Crisis or Debt_Default:
  -> Mixed (BTC → safe haven, alts → crash)
  
If Major_Regulatory_Ban:
  -> Exit affected coins immediately
```

