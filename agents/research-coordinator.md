# Research Coordinator Agent

**Agent ID**: `research-coordinator`

**Role**: Orchestrate sub-agents to gather comprehensive market intelligence and produce normalized Research Summary.

---

## Architecture

**Pattern**: Coordinator with parallel sub-agent execution

```
Research Coordinator
├─ [Parallel Execution via ThreadPoolExecutor]
│  ├─ News Intelligence (Gemini Flash)
│  ├─ Binance Announcements (GPT Nano)
│  ├─ Market Data (Deterministic - no AI)
│  ├─ Sentiment Analyzer (Claude Haiku)
│  └─ On-Chain Flow (GPT Nano)
└─ Aggregates results → Research Summary JSON
```

---

## Sub-Agents

### 1. News Intelligence Agent
- **Model**: Gemini Flash (cheap, fast for news)
- **Sources**:
  - Crypto news APIs (CoinDesk, CoinTelegraph, The Block)
  - General financial news (relevant to crypto markets)
- **Output**: News events with impact level, sentiment, timestamp
- **Example**: "Federal Reserve rate decision - High impact - Positive"

### 2. Binance Announcements Agent
- **Model**: GPT Nano (simple classification)
- **Sources**: Binance official announcements API
- **Monitors**:
  - New listings
  - Delistings
  - Maintenance schedules
  - Futures contract updates
- **Output**: Critical announcements affecting trading

### 3. Market Data Agent (Deterministic)
- **No AI required** - direct data fetching
- **Sources**: Binance API
- **Data Collected**:
  - OHLCV (multi-timeframe: 1m, 5m, 15m, 1h)
  - Order book snapshot (top 10 levels)
  - Funding rate
  - Open interest
  - 24h volume
  - Recent trades
- **Calculations**:
  - Technical indicators (EMA, VWAP, RSI, MACD, ATR)
  - Order book imbalance
  - Volume profile
  - Volatility (standard deviation)

### 4. Sentiment Analyzer Agent
- **Model**: Claude Haiku (nuanced sentiment analysis)
- **Sources**:
  - Twitter/X crypto sentiment
  - Reddit crypto communities
  - Fear & Greed Index
  - Weighted sentiment aggregation
- **Output**: Overall sentiment score (-1 to 1), confidence, sources breakdown

### 5. On-Chain Flow Agent
- **Model**: GPT Nano (pattern recognition)
- **Sources**:
  - Exchange inflow/outflow data
  - Whale transaction alerts
  - Large transfer notifications
- **Output**: Flow classification (accumulation/distribution/neutral)

---

## Execution Flow

```python
def execute_research(symbol: str, timeframe: str) -> Dict:
    """
    Orchestrate parallel sub-agent execution.
    """

    with ThreadPoolExecutor(max_workers=5) as executor:
        # Submit all sub-agents in parallel
        futures = {
            "news": executor.submit(fetch_news_intelligence, symbol),
            "announcements": executor.submit(fetch_binance_announcements, symbol),
            "market_data": executor.submit(fetch_market_data, symbol, timeframe),
            "sentiment": executor.submit(analyze_sentiment, symbol),
            "onchain": executor.submit(analyze_onchain_flow, symbol)
        }

        # Wait for all results
        results = {key: future.result() for key, future in futures.items()}

    # Aggregate into Research Summary
    research_summary = aggregate_research(results)

    # Validate against schema
    validate_research_summary(research_summary)

    return research_summary
```

---

## Output Schema

Conforms to `schemas/research_summary.schema.json`.

Example output:
```json
{
  "symbol": "BTCUSDT",
  "timeframe": "5m",
  "analysis_timestamp": "2026-02-07T10:30:00Z",
  "market_data": {
    "price": 43260.0,
    "volume_24h": 2400000000,
    "price_change_24h_pct": 2.5,
    "funding_rate": 0.0001,
    "open_interest": 8500000000,
    "order_book": {
      "bid_depth": 5000000,
      "ask_depth": 4800000,
      "imbalance_ratio": 0.02
    }
  },
  "technical_indicators": {
    "ema_9": 43250.0,
    "ema_21": 43180.0,
    "ema_50": 42950.0,
    "vwap": 43230.0,
    "rsi": 58.5,
    "macd": {
      "macd_line": 45.2,
      "signal_line": 38.1,
      "histogram": 7.1
    },
    "atr": 280.5,
    "volatility_pct": 2.8
  },
  "sentiment": {
    "overall_score": 0.35,
    "confidence": 0.78,
    "sources": {
      "news_sentiment": 0.45,
      "social_sentiment": 0.28,
      "whale_activity": "accumulation"
    }
  },
  "market_regime": "trending_up",
  "news_events": [
    {
      "headline": "Bitcoin ETF sees $500M inflows",
      "impact": "high",
      "sentiment": "positive",
      "timestamp": "2026-02-07T09:15:00Z"
    }
  ],
  "warnings": [],
  "time_decay_factor": 1.0
}
```

---

## Market Regime Classification

**Classification Logic**:
- **trending_up**: Price above EMA(50), positive momentum, increasing volume
- **trending_down**: Price below EMA(50), negative momentum, increasing volume
- **ranging**: Price oscillating around VWAP, low volatility
- **high_volatility**: ATR > 5% of price, rapid price swings
- **low_liquidity**: Order book depth below threshold, wide spreads

---

## Time Decay Factor

Research freshness indicator:
```python
def calculate_time_decay(analysis_timestamp: datetime) -> float:
    """
    Calculate freshness factor (1.0 = just generated, decays over time).

    For scalping (short-term trades), research decays quickly:
    - 0-1 min: 1.0 (fully fresh)
    - 1-3 min: 0.8
    - 3-5 min: 0.5
    - 5+ min: 0.2 (stale, should re-fetch)
    """
    age_seconds = (datetime.utcnow() - analysis_timestamp).total_seconds()

    if age_seconds < 60:
        return 1.0
    elif age_seconds < 180:
        return 0.8
    elif age_seconds < 300:
        return 0.5
    else:
        return 0.2
```

---

## Error Handling

**Partial Failures**:
- If a sub-agent fails, continue with available data
- Include warnings in research_summary
- Set lower confidence scores

**Complete Failures**:
- If market_data fails (critical), abort
- If 3+ sub-agents fail, abort
- Log errors and return to coordinator

**Timeouts**:
- Each sub-agent has 10-second timeout
- Overall research timeout: 30 seconds

---

## Authority Boundaries

**Can Do**:
- ✅ Query all data sources
- ✅ Run all sub-agents in parallel
- ✅ Aggregate and normalize data
- ✅ Calculate technical indicators
- ✅ Classify market regime

**Cannot Do**:
- ❌ Make trading decisions
- ❌ Modify risk rules
- ❌ Access trading account
- ❌ Execute trades

---

## Integration Points

**Input**: `TradingState` with symbol and timeframe
**Output**: Updated `TradingState` with `research_summary`
**Model Usage**:
- Gemini Flash: News intelligence
- GPT Nano: Announcements, on-chain flow
- Claude Haiku: Sentiment analysis

**Database**: Optionally cache research for 60 seconds to avoid redundant API calls

---

## Testing Requirements

1. Test parallel execution (all sub-agents complete)
2. Test partial failures (1-2 sub-agents fail)
3. Test timeout handling
4. Test schema validation
5. Test time decay calculation
6. Test market regime classification
7. Mock all external API calls for unit tests
