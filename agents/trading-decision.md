# Trading Decision Agent

**Agent ID**: `trading-decision`

**Role**: Analyze research summary, evaluate technical indicators, apply scalping strategies, and propose trading decisions.

---

## Decision Flow

```
Research Summary
       ↓
Strategy Selection (based on market regime)
       ↓
Technical Analysis
       ↓
AI Model Reasoning (GPT Nano → Gemini Flash → Claude Haiku)
       ↓
Trading Decision (LONG/SHORT/NO_TRADE)
```

---

## Strategy Selection

**Based on Market Regime**:
- `trending_up` → EMA Crossover Scalp or Momentum Breakout Scalp
- `trending_down` → EMA Crossover Scalp (short bias)
- `ranging` → VWAP Bounce Scalp or Order Book Imbalance Scalp
- `high_volatility` → NO_TRADE (risk too high)
- `low_liquidity` → NO_TRADE (execution risk)

---

## Scalping Strategies

### 1. EMA Crossover Scalp
**Timeframe**: 5m
**Expected Holding Time**: 180 seconds (3 minutes)

**Entry Conditions (LONG)**:
- EMA(9) crosses above EMA(21)
- Price > EMA(50) (trend confirmation)
- RSI between 40-70 (not overbought)
- Volume > 1.2x average
- Order book imbalance > 0 (buy pressure)

**Entry Conditions (SHORT)**:
- EMA(9) crosses below EMA(21)
- Price < EMA(50)
- RSI between 30-60 (not oversold)
- Volume > 1.2x average
- Order book imbalance < 0 (sell pressure)

**Exit Conditions**:
- Profit target: 0.3% (30 bps)
- Stop loss: 0.2% (20 bps)
- Time limit: 5 minutes max

### 2. VWAP Bounce Scalp
**Timeframe**: 1m
**Expected Holding Time**: 120 seconds (2 minutes)

**Entry Conditions (LONG)**:
- Price touches VWAP - 2σ band (oversold)
- RSI < 35 (oversold confirmation)
- Positive divergence (price lower, RSI higher)
- Volume spike (> 1.5x average)

**Entry Conditions (SHORT)**:
- Price touches VWAP + 2σ band (overbought)
- RSI > 65 (overbought confirmation)
- Negative divergence
- Volume spike

**Exit Conditions**:
- Profit target: 0.25% (25 bps)
- Stop loss: 0.15% (15 bps)
- Time limit: 3 minutes max

### 3. Order Book Imbalance Scalp
**Timeframe**: 1m
**Expected Holding Time**: 90 seconds (1.5 minutes)

**Entry Conditions (LONG)**:
- Order book imbalance > 0.3 (strong buy pressure)
- Price at support level
- Low volatility (ATR < 3% of price)
- Recent price decline (mean reversion setup)

**Entry Conditions (SHORT)**:
- Order book imbalance < -0.3 (strong sell pressure)
- Price at resistance level
- Low volatility
- Recent price rise

**Exit Conditions**:
- Profit target: 0.2% (20 bps)
- Stop loss: 0.15% (15 bps)
- Time limit: 2 minutes max

### 4. Momentum Breakout Scalp
**Timeframe**: 5m
**Expected Holding Time**: 240 seconds (4 minutes)

**Entry Conditions (LONG)**:
- Price breaks above recent high (15m)
- Volume > 2x average (strong momentum)
- MACD histogram positive and increasing
- RSI > 60 (momentum confirmation)
- Order book shows buy pressure

**Entry Conditions (SHORT)**:
- Price breaks below recent low
- Volume > 2x average
- MACD histogram negative and decreasing
- RSI < 40
- Order book shows sell pressure

**Exit Conditions**:
- Profit target: 0.4% (40 bps)
- Stop loss: 0.25% (25 bps)
- Time limit: 6 minutes max

---

## Technical Signal Evaluation

```python
def evaluate_technical_signals(research_summary: Dict) -> Dict:
    """
    Evaluate all technical indicators and return signal strengths.
    """
    indicators = research_summary["technical_indicators"]
    price = research_summary["market_data"]["price"]

    signals = {
        "ema_alignment": evaluate_ema_alignment(indicators, price),
        "rsi_condition": evaluate_rsi(indicators["rsi"]),
        "macd_signal": evaluate_macd(indicators["macd"]),
        "volume_confirmation": evaluate_volume(research_summary),
        "orderbook_pressure": evaluate_orderbook(research_summary["market_data"]["order_book"])
    }

    return signals
```

**Signal Scoring**:
- Each signal: -1 (bearish), 0 (neutral), +1 (bullish)
- Aggregate score: Sum of all signals
- Score > 3: Strong LONG bias
- Score < -3: Strong SHORT bias
- Score between -2 and 2: NO_TRADE (no clear edge)

---

## AI Model Reasoning

**Task**: Probabilistic reasoning about trade outcome

**Prompt Template**:
```
You are a crypto trading analyst. Given the following market conditions, assess the probability of a profitable {direction} trade.

Market Data:
- Symbol: {symbol}
- Current Price: {price}
- Trend: {market_regime}
- EMA Alignment: {ema_alignment}
- RSI: {rsi}
- Order Book Imbalance: {imbalance}
- Recent News: {news_summary}
- Sentiment: {sentiment_score}

Strategy: {strategy_name}
Entry Price: {entry_price}
Take Profit: {take_profit}
Stop Loss: {stop_loss}

Provide:
1. Win probability (0-1)
2. Risk/reward assessment
3. Key factors supporting/opposing the trade
4. Confidence in analysis (0-1)

Respond in JSON format.
```

**Model Routing**:
1. Start with GPT Nano (cheap, fast)
2. If confidence < 0.75, escalate to Gemini Flash
3. If confidence < 0.75, escalate to Claude Haiku

---

## Decision Logic

```python
def make_trading_decision(research_summary: Dict) -> Dict:
    """
    Main decision logic.
    """

    # Check market regime
    regime = research_summary["market_regime"]
    if regime in ["high_volatility", "low_liquidity"]:
        return create_no_trade_decision("Unfavorable market regime")

    # Check research freshness
    if research_summary["time_decay_factor"] < 0.5:
        return create_no_trade_decision("Research data too stale")

    # Select strategy
    strategy = select_strategy(regime)

    # Evaluate technical signals
    signals = evaluate_technical_signals(research_summary)
    signal_score = sum(signals.values())

    # Check if signals are strong enough
    if abs(signal_score) < 3:
        return create_no_trade_decision("Insufficient technical edge")

    # Determine direction
    direction = "LONG" if signal_score > 0 else "SHORT"

    # Calculate entry, stop, take profit
    entry_price = research_summary["market_data"]["price"]
    stop_loss, take_profit = calculate_levels(entry_price, direction, strategy)

    # Get AI model reasoning
    ai_reasoning = get_ai_reasoning(
        research_summary,
        strategy,
        direction,
        entry_price,
        stop_loss,
        take_profit
    )

    # Check AI confidence
    if ai_reasoning["confidence"] < 0.75:
        return create_no_trade_decision("AI confidence too low")

    # Create trading decision
    return create_trading_decision(
        symbol=research_summary["symbol"],
        decision=direction,
        confidence=ai_reasoning["confidence"],
        strategy_id=strategy["id"],
        entry_price=entry_price,
        stop_loss=stop_loss,
        take_profit=take_profit,
        reasoning=ai_reasoning["reasoning"],
        technical_signals=signals,
        risk_metrics=ai_reasoning["risk_metrics"]
    )
```

---

## Position Sizing Proposal

**Initial Size Calculation**:
```python
def calculate_position_size(
    entry_price: float,
    stop_loss: float,
    account_equity: float,
    risk_per_trade_pct: float = 0.02
) -> float:
    """
    Calculate position size based on risk per trade.

    This is a PROPOSAL - Risk Manager has final authority.
    """
    risk_amount = account_equity * risk_per_trade_pct
    stop_distance_pct = abs(entry_price - stop_loss) / entry_price
    position_size = risk_amount / stop_distance_pct

    return position_size
```

**Leverage Proposal**:
- Low volatility (< 2%): Propose 8-10x
- Medium volatility (2-5%): Propose 5-7x
- High volatility (> 5%): NO_TRADE

*Note*: Risk Manager will adjust based on comprehensive risk checks.

---

## Output Schema

Conforms to `schemas/trading_decision.schema.json`.

Example LONG decision:
```json
{
  "decision_id": "uuid",
  "symbol": "BTCUSDT",
  "decision": "LONG",
  "confidence": 0.82,
  "expected_holding_time_seconds": 180,
  "strategy_id": "ema_crossover_scalp",
  "model_used": "gemini-1.5-flash",
  "reasoning_summary": "Strong bullish EMA crossover with volume confirmation. RSI in healthy range (58). Positive sentiment (0.35) and accumulation signals. Risk/reward favorable at 1.5:1.",
  "entry_price": 43260.0,
  "stop_loss": 43173.0,
  "take_profit_levels": [
    {"price": 43390.0, "quantity_pct": 0.5},
    {"price": 43520.0, "quantity_pct": 0.5}
  ],
  "position_size_usdt": 500.0,
  "leverage": 7,
  "technical_signals": {
    "ema_alignment": "bullish",
    "rsi_condition": "neutral",
    "macd_signal": "bullish_cross",
    "volume_confirmation": true,
    "orderbook_pressure": "buy_pressure"
  },
  "risk_metrics": {
    "risk_reward_ratio": 1.5,
    "win_probability": 0.68,
    "max_adverse_excursion_pct": 0.003
  },
  "timestamp": "2026-02-07T10:30:15Z",
  "research_summary_hash": "abc123..."
}
```

Example NO_TRADE decision:
```json
{
  "decision_id": "uuid",
  "symbol": "BTCUSDT",
  "decision": "NO_TRADE",
  "confidence": 0.95,
  "expected_holding_time_seconds": 0,
  "strategy_id": null,
  "model_used": "gpt-4o-nano",
  "reasoning_summary": "High volatility detected (6.2%). Market regime unfavorable for scalping. Waiting for stability.",
  "timestamp": "2026-02-07T10:30:15Z"
}
```

---

## Authority Boundaries

**Can Do**:
- ✅ Analyze market data and indicators
- ✅ Select appropriate strategies
- ✅ Propose trading decisions (LONG/SHORT/NO_TRADE)
- ✅ Calculate entry/stop/target levels
- ✅ Propose position size and leverage

**Cannot Do**:
- ❌ Execute trades (must go through Risk Manager)
- ❌ Modify risk parameters
- ❌ Override Risk Manager decisions
- ❌ Access trading account directly

---

## Testing Requirements

1. Test all 4 strategies with historical data
2. Test NO_TRADE conditions (high volatility, low liquidity, stale data)
3. Test AI model routing and escalation
4. Test signal scoring logic
5. Test position sizing calculations
6. Test schema validation
7. Test edge cases (missing data, extreme values)
