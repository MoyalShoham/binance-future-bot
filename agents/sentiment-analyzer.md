# Sentiment Analyzer Agent

**Agent ID**: `sentiment-analyzer`

**Model**: Claude Haiku 4.5 (fast, cost-effective sentiment classification)

**Role**: Synthesize market sentiment from news, social media, and order book dynamics to provide directional bias.

---

## Architecture

**Pattern**: Event-driven sentiment aggregator

```
News Feeds
├─ CryptoPanic (crypto news)
├─ Twitter/X sentiment (trending hashtags)
├─ Reddit sentiment (r/crypto, r/trading)
├─ Binance community (bullish/bearish ratio)
└─ Order book analysis (buy/sell pressure)
    └─ AI Synthesis (Haiku 4.5)
        └─ Outputs: Composite sentiment score + drivers
```

---

## Responsibilities

✅ **Sentiment Collection**
- Aggregate news events from CryptoPanic (last 30 mins)
- Pull social sentiment from community indices
- Analyze order book imbalance (buy vs sell wall ratio)
- Track fear/greed index (if available)

✅ **Synthesis & Classification**
- Score each sentiment source (-1.0 to +1.0)
- Assign confidence weight to each source
- Identify sentiment conflicts (e.g., news bullish, order book bearish)
- Flag extreme readings (>0.9 bullish/bearish)

✅ **Output**
- Composite sentiment score
- Confidence level
- Key drivers (top 3)
- Conflicting signals
- Regime interpretation

❌ Does NOT make trading decisions
❌ Does NOT override Risk Manager
❌ Does NOT adjust position sizes

---

## Input Data

```json
{
  "news_events": [
    {
      "title": "Bitcoin ETF inflow surge",
      "source": "CryptoPanic",
      "sentiment": "positive",
      "impact": "high",
      "timestamp": "2026-02-17T10:15:00Z"
    }
  ],
  "social_sentiment": {
    "twitter_trend": {"score": 0.65, "sample_size": 5000},
    "reddit_sentiment": {"score": 0.58, "subreddit": "cryptocurrency"},
    "community_index": 0.72
  },
  "order_book": {
    "buy_wall_ratio": 1.3,
    "sell_pressure_ratio": 0.9,
    "large_order_activity": "accumulation"
  },
  "fear_greed_index": 58
}
```

---

## Output Schema

```json
{
  "composite_sentiment": 0.71,
  "confidence": 0.83,
  "sentiment_label": "bullish",
  "drivers": [
    "Positive news (inflows)",
    "Social momentum increasing",
    "Buy pressure on order book"
  ],
  "conflicting_signals": [],
  "extreme_readings": false,
  "regime_interpretation": "Strong accumulation phase",
  "sentiment_summary": {
    "news": 0.75,
    "social": 0.68,
    "technical": 0.70,
    "macro": 0.65
  },
  "timestamp": "2026-02-17T10:30:00Z"
}
```

---

## Integration Points

- **Input**: News feeds, social APIs, order book from Research Coordinator
- **Output**: Fed to Trading Decision & Risk Manager for confirmation checks
- **Frequency**: Every minute (real-time)
- **Usage**: Confluence check (high conviction trades need sentiment alignment)

---

## Tuning Parameters

| Parameter | Value | Notes |
|-----------|-------|-------|
| Min sample size | 1000 | Social sentiment must have min volume |
| News recency | 30 min | Only recent news counts |
| Extreme threshold | 0.85/-0.85 | Flag unusual readings |
| Confidence weight | news=0.4, social=0.3, technical=0.3 | Composite calculation |

---

## Real-time Monitoring

- **Watch for sudden shifts**: Sentiment flip (bullish → bearish in <5 min)
- **Detect wash trading**: Check for coordinated fake news
- **Social bot farms**: Cross-validate with verified accounts only
- **Black swan events**: Flag if sentiment > 0.9 with low confidence

