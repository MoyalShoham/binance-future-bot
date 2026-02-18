# Recommendation Agent

**Agent ID**: `recommendation-agent`

**Model**: Claude Sonnet 4.5 (deep analysis for high-conviction signals)

**Role**: Scan Binance movers, CoinGecko trending, and macro conditions to recommend high-probability entry opportunities.

---

## Architecture

**Pattern**: Autonomous scanner with AI synthesis

```
Symbol Scanner
├─ Binance movers (24h gainers/losers)
├─ CoinGecko trending (social momentum)
├─ News aggregators (macro events)
├─ On-chain metrics (whale activity, inflows)
└─ AI Synthesis (Sonnet 4.5)
    └─ Outputs: Top 3-5 recommendations with conviction scores
```

---

## Responsibilities

✅ **Data Collection**
- Fetch top gainers/losers from Binance 24h
- Pull trending coins from CoinGecko
- Monitor macro events (Fed, geopolitical, regulatory)
- Scan on-chain metrics (exchange inflows, whale activity)

✅ **Analysis**
- Cross-validate signals across multiple sources
- Assess macro tailwinds (bull market, altseason indicators)
- Evaluate trend strength and momentum
- Calculate conviction score (1-100)

✅ **Recommendation Output**
- Top symbol to trade (e.g., "ETHUSDT")
- Conviction score (85%)
- Reasoning (why now)
- Time window (how long valid)
- Risk warnings

❌ Does NOT make trading decisions
❌ Does NOT suggest position sizes
❌ Does NOT override risk manager

---

## Input Data

```json
{
  "market_state": {
    "btc_dominance": 0.45,
    "altseason_score": 0.72,
    "overall_sentiment": "bullish"
  },
  "top_movers": {
    "gainers_24h": [
      {"symbol": "ETHUSDT", "change": 0.08, "volume": 1.2e9},
      {"symbol": "ADAUSDT", "change": 0.12, "volume": 5.0e8}
    ],
    "volume_surges": [
      {"symbol": "DOTUSDT", "volume_change": 3.5}
    ]
  },
  "macro_events": [
    {"event": "Fed rate hold", "impact": "positive", "timestamp": "2026-02-17"}
  ],
  "on_chain": {
    "exchange_inflows": [
      {"symbol": "ETH", "inflow_volume": 5000, "type": "selling_pressure"}
    ],
    "whale_activity": [
      {"symbol": "SOL", "activity": "accumulation"}
    ]
  }
}
```

---

## Output Schema

```json
{
  "recommendations": [
    {
      "symbol": "ETHUSDT",
      "conviction": 87,
      "reasoning": "Altseason indicators + ETF inflows + bullish macro",
      "time_window": "4 hours",
      "risk_level": "medium",
      "macro_tailwinds": ["Fed supportive", "ETH supply pressure declining"],
      "potential_trade_setup": "Breakout above $2800 resistance"
    }
  ],
  "market_regime": "early_altseason",
  "scanning_timestamp": "2026-02-17T10:30:00Z"
}
```

---

## Integration Points

- **Input**: Market state from Research Coordinator
- **Output**: Fed to Trading Decision Agent as optional symbol override
- **Frequency**: Every 15-30 minutes during trading hours
- **Backoff**: If 2+ recommendations fail (low conviction trades), skip next cycle

---

## Tuning Parameters

| Parameter | Value | Notes |
|-----------|-------|-------|
| Min conviction | 75% | Filters low-confidence scans |
| Max top_n | 5 | Return top 5 symbols max |
| Macro override | -20% conviction | Negative macro = lower scores |
| Altseason boost | +15% | Extra boost if altseason confirmed |
| Recency weight | 0.7 | Recent moves weighted more |

---

## Known Gotchas

- **Data staleness**: Always check timestamp; CoinGecko updates every 5 min
- **Survivorship bias**: Some coins may delist; cross-check with Binance active symbols
- **Hype trap**: High conviction ≠ guaranteed profit. Risk manager still applies
- **Macro lag**: News impacts price with 15-60min delay; timing matters

