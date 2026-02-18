# New Agent Army - Integration Guide

**Created**: February 17, 2026  
**Total New Agents**: 7  
**Sonnet 4.5 (Deep Analysis)**: 2 agents  
**Haiku 4.5 (Fast Operations)**: 3 agents  
**Haiku 4 (Cost-Effective)**: 2 agents

---

## Agent Roster

### Tier 1: High-Conviction Decision Makers (Sonnet 4.5)

#### 1. **Recommendation Agent** ⭐ [HOT COINS & SIGNALS]
- **Model**: Claude Sonnet 4.5
- **Role**: Scan Binance movers, CoinGecko trends, macro signals → Top 5 hot coin recommendations
- **Output**: 
  - `recommendations[].symbol` (ETHUSDT, SOLUSDT, etc.)
  - `recommendations[].conviction` (0-100)
  - `market_regime` (early_altseason, peak, decline)
- **Update Frequency**: Every 15-30 minutes
- **Integration**: → Trading Decision Agent (optional symbol override)
- **File**: `recommendation_agent.py` | Prompt: `prompts/recommendation_agent.py`

#### 2. **Macro Analyst Agent** 🌍 [GLOBAL MACRO SHIFTS]
- **Model**: Claude Sonnet 4.5
- **Role**: Monitor Fed, yields, geopolitical → Adjust conviction scores for macro regime
- **Output**:
  - `macro_regime` (risk_on_easing, risk_off_hiking, etc.)
  - `conviction_adjustment` (-25% to +25%)
  - `next_catalyst` (event, date, impact)
- **Update Frequency**: 1
  - Real-time on Fed announcements
  - Daily refresh (9am ET)
- **Integration**: → Trading Decision Agent (conviction multiplier)
- **File**: `macro_analyst.py` | Prompt: `prompts/macro_analyst.py`

---

### Tier 2: Market Intelligence (Haiku 4.5)

#### 3. **Sentiment Analyzer Agent** 📊 [REAL-TIME SENTIMENT]
- **Model**: Claude Haiku 4.5
- **Role**: Aggregate news, social, order book sentiment → Composite sentiment score
- **Output**:
  - `composite_sentiment` (-1.0 to +1.0)
  - `confidence` (0-1.0)
  - `drivers[]` (top 3 sentiment drivers)
  - `extreme_readings` (boolean)
- **Update Frequency**: Every minute (real-time)
- **Integration**: → Trading Decision Agent (confluence check)
- **File**: `sentiment_analyzer.py` | Prompt: `prompts/sentiment_analyzer.py`

#### 4. **Risk Adjuster Agent** ⚖️ [DYNAMIC RISK MANAGEMENT]
- **Model**: Claude Haiku 4.5
- **Role**: Adjust SL/TP/size based on vol, drawdown, win rate → Kelly sizing
- **Output**:
  - `adjusted_parameters`:
    - `stop_loss_multiplier` (0.8x in calm, 1.8x in extreme)
    - `take_profit_multiplier` (affects realistic exit levels)
    - `position_size_multiplier` (Kelly-based scaling)
    - `max_leverage_allowed` (2x-5x range)
  - `cooldown_status` (trigger 15min pause after 3 consecutive losses)
- **Update Frequency**: Every 5 minutes
- **Integration**: → Risk Manager (parameter multipliers)
- **File**: `risk_adjuster.py` | Prompt: `prompts/risk_adjuster.py`

---

### Tier 3: Operational Intelligence (Haiku 4)

#### 5. **Volatility Predictor Agent** 📈 [VOL FORECASTING]
- **Model**: Claude Haiku 4
- **Role**: Forecast 1h/4h volatility, classify regime → Position sizing guidance
- **Output**:
  - `current_regime` (calm/elevated/high/extreme)
  - `forecasts.1h_ahead.expected_vol` (0.01 = 1% hourly)
  - `forecasts.4h_ahead.expected_vol`
  - `positioning_guidance` (scale leverage up/down)
- **Update Frequency**: Every 15 minutes
- **Integration**: → Risk Adjuster (vol multiplier input)
- **File**: `volatility_predictor.py` | Prompt: `prompts/volatility_predictor.py`

#### 6. **Correlation Monitor Agent** 🔗 [PORTFOLIO DIVERSIFICATION]
- **Model**: Claude Haiku 4
- **Role**: Monitor inter-asset correlations → Flag concentration risk
- **Output**:
  - `portfolio_diversity_score` (0-1.0; >0.6 is healthy)
  - `regime` (low_corr/moderate/high_corr)
  - `recommendations[]` (add uncorrelated assets)
  - `tail_correlation` (crisis scenario correlation)
  - `risk_flags[]` (if >0.85, crisis vulnerability)
- **Update Frequency**: Every 30 minutes
- **Integration**: → Risk Manager (concentration checks)
- **File**: `correlation_monitor.py` | Prompt: `prompts/correlation_monitor.py`

#### 7. **Entry Optimizer Agent** 🎯 [MICRO-LEVEL ENTRY TUNING]
- **Model**: Claude Haiku 4
- **Role**: Fine-tune entry prices within ±0.5% of suggested → Max fill probability
- **Output**:
  - `optimal_entry` ($2799.75)
  - `fill_probability` (0-1.0)
  - `entry_window` (how long window is valid)
  - `expected_slippage` (0.005 = 0.5%)
  - `microstructure_signals[]` (iceberg alerts, etc.)
- **Update Frequency**: Real-time (on trade approval)
- **Integration**: → Execution Agent (order placement)
- **File**: `entry_optimizer.py` | Prompt: `prompts/entry_optimizer.py`

#### 8. **Liquidation Predictor Agent** ⚠️ [CASCADE FORECASTING]
- **Model**: Claude Haiku 4
- **Role**: Calculate liq levels, predict cascades → Warn before entering danger zones
- **Output**:
  - `position_liquidation_levels[]` (distance %, liq price)
  - `market_liquidation_map[]` (clusters with cascade_risk)
  - `entry_safety_score` (0-1.0; <0.35 = danger)
  - `cascade_analysis` (estimated trigger, speed, volume)
  - `funding_rate_risk` (annualized %)
- **Update Frequency**: Every minute
- **Integration**: → Risk Manager (entry safety checks)
- **File**: `liquidation_predictor.py` | Prompt: `prompts/liquidation_predictor.py`

---

## Pipeline Integration Diagram

```
MARKET DATA SOURCES
├─ Binance API (prices, OI, funding)
├─ CoinGecko (trending, social)
├─ CryptoPanic (news)
├─ Federal Reserve (policy, yields)
└─ On-chain (whale activity, flows)
    │
    ├───────────────────────────────────────────────────┐
    │                                                   │
    v                                                   v
[Recommendation Agent]◄─(Sonnet 4.5)               [Macro Analyst]◄─(Sonnet 4.5)
   + hot coins                                        + regime shift
   + conviction scores                                + conviction adj
    │                                                   │
    └──────────────────┬───────────────────────────────┘
                       │
       ┌───────────────┴───────────────┐
       │                               │
       v                               v
[Sentiment Analyzer]◄─(Haiku 4.5) [Volatility Predictor]◄─(Haiku 4)
+ composite sentiment              + vol forecast
+ confidence                       + regime
                                   + multipliers
       │                               │
       └───────────────────┬───────────┘
                           │
    ┌──────────────────────┴─────────────────────┐
    │                                            │
    v                                            v
[Trading Decision Agent]         [Risk Adjuster]◄─(Haiku 4.5)
   (existing, now enhanced)          + SL/TP scaling
                                     + position sizing
                                     + Kelly sizing
                                     │
                                     v
                            [Correlation Monitor]◄─(Haiku 4)
                               + diversity check
                               + concentration flags
                                     │
    │                                │
    └──────────────┬─────────────────┘
                   │
                   v
           [Risk Manager]
         (existing, with more inputs)
                   │
                   ├─── Approved ───────┐
                   │                    │
                   v                    v
            [Entry Optimizer]◄─(Haiku 4)   [Liquidation Predictor]◄─(Haiku 4)
             + optimal entry              + liq calculations
             + fill probability           + cascade warnings
             + timing                     │
             │                            │
             └────────┬───────────────────┘
                      │
                      v
            [Execution Agent]
         (existing, enhanced data)
            + order placement
            + PAPER/LIVE/HYBRID
```

---

## Integration Checklist

### Phase 1: Add Agent Classes (this step)
- [x] Create agent implementation files (8 new agents)
- [x] Create system prompts (8 new agents)
- [x] Create markdown specifications
- [ ] Update `agents/implementations/__init__.py` (next step)

### Phase 2: Wire Into Coordinator
- [ ] Update `orchestration/coordinator.py` to instantiate new agents
- [ ] Add new agent configurations to `config/trading_config.yaml`
- [ ] Update state flow to handle new outputs

### Phase 3: Connect to Existing Agents
- [ ] Update Trading Decision Agent to accept:
  - Recommendation suggestions
  - Macro conviction adjustments
  - Sentiment confluence checks
- [ ] Update Risk Manager to accept:
  - Volatility multipliers
  - Correlation concentration checks
  - Liquidation entry safety scores
  - Risk adjustment parameters (SL/TP/size)
- [ ] Update Execution Agent to accept:
  - Optimized entry prices
  - Liquidation cascade warnings

### Phase 4: Testing & Validation
- [ ] Unit tests for each new agent
- [ ] Integration tests (agent outputs feed downstream correctly)
- [ ] Backtesting with new agent signals
- [ ] Paper trading validation (1-2 weeks)

---

## Configuration (Add to trading_config.yaml)

```yaml
agents:
  recommendation:
    min_conviction: 75
    max_top_n: 5
    altseason_boost: 0.15
    model: "sonnet-4-5"
    
  macro_analyst:
    max_conviction_adjustment: 0.25
    model: "sonnet-4-5"
    
  sentiment_analyzer:
    min_sample_size: 1000
    news_recency_min: 30  # minutes
    extreme_threshold: 0.85
    model: "haiku-4-5"
    
  volatility_predictor:
    calm_threshold: 0.01
    elevated_threshold: 0.02
    high_threshold: 0.03
    model: "haiku-4"
    
  correlation_monitor:
    high_correlation_threshold: 0.7
    tail_correlation_threshold: 0.85
    model: "haiku-4"
    
  entry_optimizer:
    max_entry_deviation: 0.005  # ±0.5%
    min_fill_probability: 0.6
    model: "haiku-4"
    
  liquidation_predictor:
    min_safety_distance: 0.08  # 8%
    model: "haiku-4"
    
  risk_adjuster:
    max_daily_drawdown: -0.06
    model: "haiku-4-5"
```

---

## Model Usage & Costs

| Agent | Model | Use Case | Cost/call | Frequency | Daily Est. |
|-------|-------|----------|-----------|-----------|-----------|
| Recommendation | Sonnet 4.5 | Deep movers analysis | $0.003 | 48x/day | $0.14 |
| Macro Analyst | Sonnet 4.5 | Global macro synthesis | $0.003 | 10x/day | $0.03 |
| Sentiment | Haiku 4.5 | News+social aggregation | $0.0008 | 1440x/day | $1.15 |
| Volatility | Haiku 4 | Vol forecasting | $0.0003 | 96x/day | $0.03 |
| Correlation | Haiku 4 | Portfolio diversification | $0.0003 | 48x/day | $0.01 |
| Entry | Haiku 4 | Micro entries (on-demand) | $0.0003 | 10x/day | $0.003 |
| Liquidation | Haiku 4 | Cascade forecasting | $0.0003 | 1440x/day | $0.43 |
| Risk Adjuster | Haiku 4.5 | Dynamic risk scaling | $0.0008 | 288x/day | $0.23 |
| | | | | **TOTAL** | **~$2.00/day** |

---

## Data Flow Examples

### Example 1: Hot Coin Recommendation to Trade

```
1. Recommendation Agent scans:
   - ETHUSDT: +8% gain, strong volume → conviction 87%
   
2. Macro Analyst adds:
   - Fed easing signals → +12% conviction adjustment
   - Final conviction: 87 × 1.12 = 98 (very high)
   
3. Trading Decision receives suggestion:
   - IF (recommendation.conviction > 85) AND (technical_setup matches):
     → Approve ETHUSDT long setup
   
4. Risk Manager validates:
   - Correlation check: Is portfolio overexposed to L1 tokens? (Correlation Monitor)
   - Liquidation check: Would entry be safe? (Liquidation Predictor ✓)
   - Size calculation: Kelly sizing from recent wins (Risk Adjuster)
   → Approve 1% risk sizing
   
5. Entry Optimizer refines:
   - Current price: $2800
   - Order book shows support at $2799.75
   - Recommendation: Enter $2799.75 with 87% fill probability
   
6. Execution Agent places order:
   - Market order at $2799.75 (or limit if patience)
   → TRADE EXECUTED
```

### Example 2: Volatility Spike + Risk Adjustment

```
1. Volatility Predictor detects:
   - Realized vol: 0.015 (elevated)
   - Forecast: 0.02 in 1 hour (high regime expected)
   - Recommendation: 1.3x SL width, 0.7x position size
   
2. Risk Adjuster amplifies:
   - Daily drawdown: -1.2% (already)
   - Consecutive losses: 1
   - Kelly sizing: 50% of edge (conservative)
   - Final multipliers:
     - SL width: 1.3x (from vol) × 0.95 (from drawdown) = 1.23x
     - Position size: 0.7x (from vol) × 0.95 (from drawdown) × 0.8 (from Kelly) = 0.53x
   → Reduce next trade to 53% of normal size
   
3. Risk Manager enforces:
   - All subsequent trades use reduced sizing
   - If drawdown hits -2%, further reduction to 40% size
   → PROTECTION ENGAGED
```

---

## Day 1 Activation

```bash
# 1. Update __init__.py to export new agents
$ vim agents/implementations/__init__.py

# 2. Test imports
$ python -c "from agents.implementations import RecommendationAgent; print('✓')"

# 3. Update coordinator to load new agents
$ vim orchestration/coordinator.py

# 4. Paper trade for 1 week
$ python main.py --mode paper --symbol BTCUSDT --continuous

# 5. Monitor new agent outputs
# Check: logs/trading_system.log for agent_name_start, agent_name_complete
# Example: "recommendation_analysis_start", "sentiment_analysis_complete"

# 6. Tune parameters in config/trading_config.yaml
# Adjust min_conviction, thresholds, etc. based on paper results

# 7. Go live with confidence
$ python main.py --mode live --symbol all --continuous
```

---

## Notes on Model Selection

- **Sonnet 4.5**: Used for strategic decisions (recommendations, macro) - deep reasoning ✓
- **Haiku 4.5**: Used for fast operations (sentiment, risk adjustment) - speed ✓
- **Haiku 4**: Used for operational tasks (vol, entries, liquidation) - cost-effective ✓

Total daily API cost: ~$2/day (cheap!) vs. value (8 specialized eyes on market)

---

## Future Enhancements

1. **Market Microstructure Agent** (Haiku 4) - Detect spoofing, iceberg orders, wash trades
2. **Options Gamma Agent** (Haiku 4) - Track BTC/ETH options open interest for sentiment
3. **Social Whale Tracker** (Haiku 4) - Monitor whale wallets for accumulation/distribution
4. **Funding Rate Predictor** (Haiku 4) - Forecast funding spikes 1-4 hours ahead
5. **Drawdown Recovery Agent** (Haiku 4.5) - After -5% daily loss, suggest recovery trades

---

**Status**: Ready to integrate ✓  
**Testing**: Pending Phase 2 wiring  
**Production**: Target: February 24, 2026

