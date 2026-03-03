# V3 Roadmap: Data-Driven Profitability Overhaul

## Executive Summary

Research across 120+ sources by 4 specialized agents (Alpha, Strategy, Risk/Exit, Validation) reveals **25 high-impact improvements** consolidated into a unified priority matrix. Key corrections from deep research:

1. **Partial profit-taking is mathematically neutral** -- don't implement for automated bot (Risk/Exit Agent disproved)
2. **Liquidation cascade "alpha" is 54% BTC beta** -- use only as defensive regime filter (Tigro Blanc 2026 study)
3. **Fear & Greed Index is useless for 5m scalping** -- daily resolution, wrong timeframe (Alpha Agent)
4. **Signal combinations >> individual signals** -- OI+funding+liquidation ensemble = 87% accuracy (Gate.io research)
5. **Fee drag is #1 PnL killer** -- 100 trades at taker/taker = 15.4% of $65 equity

**Target**: Turn the bot profitable within 2-4 weeks of live trading with $65 equity.

---

## UNIFIED MASTER PRIORITY LIST (All 4 Agents Combined)

### TIER 0: DO IMMEDIATELY (Week 1 -- Highest ROI, Lowest Effort)

| # | Change | Source Agent | Expected Impact | Effort | Evidence |
|---|--------|-------------|-----------------|--------|----------|
| 1 | **Maker entries (limit + 2s GTX timeout, market fallback)** | Risk/Exit | +15-25% net PnL (fee savings) | Medium | HIGH -- fee math is exact |
| 2 | **Buy $5-10 BNB for fee payment** | Risk/Exit | -10% on ALL fees | Trivial | Guaranteed |
| 3 | **Per-strategy SL/TP ATR multipliers** | Strategy | +15-25% profit factor | Low | HIGH -- universally recommended |
| 4 | **MACD (5,13,1) for 5m scalping** | Strategy | +5-11% annual return | Low | HIGH -- multiple backtests confirm |
| 5 | **RSI(7) instead of RSI(14)** | Strategy | 15-20% faster signals | Low | HIGH -- consensus across sources |
| 6 | **Funding rate: binary -> graduated directional** | Alpha | +3-5% win rate | Low | Strong -- already have data |
| 7 | **Volume confirmation on ALL strategies** | Strategy | -20-30% false signals | Low | HIGH -- most agreed-upon filter |
| 8 | **Time-of-day confidence windows** | Alpha (original) | +5% in optimal hours | Trivial | HIGH -- Springer 2024 study |

### TIER 1: DO NEXT (Week 1-2 -- High Impact)

| # | Change | Source Agent | Expected Impact | Effort | Evidence |
|---|--------|-------------|-----------------|--------|----------|
| 9 | **ATR-based trailing stop (replace fixed 0.2%)** | Risk/Exit | +10-20% on winners | Medium | HIGH -- industry standard |
| 10 | **ADX(10) instead of ADX(14)** | Strategy | 10-15% faster trend detection | Low | MEDIUM |
| 11 | **ADX + volume filter on EMA crossover** | Strategy | -10-15% false signals | Low | HIGH |
| 12 | **Widen SL from 1.0x to 1.2x ATR** | Risk/Exit | +5-10% win rate (anti-stop-hunt) | Trivial | HIGH |
| 13 | **BB(14, 1.5) for squeeze detection** | Strategy | More squeeze detections | Low | MEDIUM-HIGH |
| 14 | **Strengthen contra-HTF penalty (-0.15)** | Strategy | +2-5% fewer bad trades | Trivial | HIGH |
| 15 | **Taker Buy/Sell Ratio as confluence** | Alpha | +2-4% win rate | Low | MODERATE |
| 16 | **VWAP as directional filter (not entry)** | Strategy (original) | +3% directional accuracy | Low | MEDIUM |
| 17 | **Time decay on TP (reduce after 6min)** | Risk/Exit | +5-15% risk reduction | Low | MEDIUM-HIGH |

### TIER 2: WEEK 2-3 (Medium Impact, Medium Effort)

| # | Change | Source Agent | Expected Impact | Effort | Evidence |
|---|--------|-------------|-----------------|--------|----------|
| 18 | **OI Rate-of-Change + Price Divergence** | Alpha | +5-8% win rate as filter | Medium | Professional desks use it |
| 19 | **Liquidation stream (defensive pause)** | Alpha | Avoid -10% cascade losses | Medium | Strong (defensive only) |
| 20 | **HMM Regime Detection (supplement LLM)** | Validation | HIGH -- enables backtestable regimes | Medium | Industry standard |
| 21 | **Monte Carlo permutation tests** | Validation | Know if edge is real | Low | HIGH |
| 22 | **Deflated Sharpe Ratio** | Validation | Prevent false positives | Low | HIGH |
| 23 | **ATR period 14 -> 10** | Strategy | Faster SL/TP adaptation | Trivial | MEDIUM |
| 24 | **Reduce daily drawdown 60% -> 40%** | Risk/Exit | Survival at $65 | Trivial | Critical for small account |

### TIER 3: WEEK 3-4 (Polish & Validation)

| # | Change | Source Agent | Expected Impact | Effort | Evidence |
|---|--------|-------------|-----------------|--------|----------|
| 25 | **Walk-forward overhaul (double OOS)** | Validation | Gold-standard validation | Medium | HIGH |
| 26 | **Dynamic slippage model (backtest)** | Validation | Realistic backtest results | Medium | HIGH for altcoins |
| 27 | **Long/Short Ratio (top traders)** | Alpha | +1-3% contrarian edge | Low | MODERATE |
| 28 | **Regime-specific strategy enable/disable** | Validation | Prevent wrong-regime trades | Medium | MEDIUM |
| 29 | **WFE consistency checks** | Validation | Catch regime-dependent strategies | Low | MEDIUM |
| 30 | **Adversarial validation diagnostic** | Validation | Know when to distrust OOS | Low | MEDIUM |

### REJECTED / DEFERRED

| Change | Reason | Source |
|--------|--------|--------|
| **Partial profit-taking** | Mathematically neutral for bots, adds complexity | Risk/Exit Agent |
| **Fear & Greed Index** | Daily resolution, useless for 5m scalping | Alpha Agent |
| **Whale wallet tracking** | 4h-daily signal, wrong timeframe | Alpha Agent |
| **VPIN (order flow toxicity)** | Requires HFT infrastructure, too complex | Alpha Agent |
| **ML-based exits (DDQN/LSTM)** | Too complex, unproven at small scale | Risk/Exit Agent |
| **Synthetic data (TimeGAN)** | High complexity, low marginal benefit | Validation Agent |
| **RSI period change (14->9)** | Deferred to Week 4, low standalone impact | Strategy Agent |
| **Dynamic SL/TP recalculation** | Keep disabled -- trailing stop handles same need | Risk/Exit Agent |

---

## DETAILED IMPLEMENTATION SPECS

### Phase 1: Fee Optimization & Indicator Tuning

#### 1.1 Maker Order Entries
**Files**: `infrastructure/execution_modes.py`, `agents/implementations/execution_agent.py`

Current Binance Futures fees (VIP 0):
- Maker: 0.0200% (with BNB: 0.0180%)
- Taker: 0.0500% (with BNB: 0.0450%)
- Round-trip taker/taker: $0.10 per $100 notional
- Round-trip maker/taker + BNB: $0.063 per $100 notional
- **Savings: 37% fee reduction**

Implementation:
1. Entry: Limit at best bid (LONG) or best ask (SHORT) with `timeInForce: GTX` (post-only)
2. Wait 2 seconds
3. If not filled: cancel and send market order
4. Exit: Always market order (risk management must be guaranteed)
5. Hold $5-10 BNB for fee payment (10% discount)

**Caveat**: In fast-moving markets, GTX rejection rate can be 30-60%. The 2s timeout + market fallback handles this.

#### 1.2 MACD Optimization
**Files**: `infrastructure/binance_api/indicators.py` (line 81)

Change from:
```python
ta.trend.MACD(df["Close"])  # defaults to (12, 26, 9)
```
To:
```python
ta.trend.MACD(df["Close"], window_slow=13, window_fast=5, window_sign=1)
```

Evidence: Kang study of 19,456 MACD variations found default (12,26,9) produced -3.6% annual returns while optimized setups delivered +11%. Chen & Zhu (2025) confirmed via genetic algorithm.

#### 1.3 RSI Period
**Files**: `infrastructure/binance_api/indicators.py` (line 78)

Change RSI window from 14 to 7. Adjust strategy thresholds:
- `rsi_pullback_scalp`: oversold 40->35, overbought 60->65
- `ema_crossover_scalp`: RSI filter ranges widen to 35-70 (longs), 30-65 (shorts)

#### 1.4 Per-Strategy SL/TP ATR Multipliers
**Files**: `config/trading_config.yaml`, `agents/implementations/trading_decision.py`

```yaml
strategy_sl_tp_overrides:
  ema_crossover_scalp:
    sl_atr_mult: 1.2    # Trend: buffer against stop-hunting
    tp_atr_mult: 2.0    # Trend: let it run
  rsi_pullback_scalp:
    sl_atr_mult: 0.8    # Mean reversion: tight stop
    tp_atr_mult: 1.2    # Mean reversion: tight target
  bollinger_squeeze_scalp:
    sl_atr_mult: 1.5    # Breakout: wide stop (vol expanding)
    tp_atr_mult: 3.0    # Breakout: wide target
  momentum_breakout_scalp:
    sl_atr_mult: 1.5    # Breakout: wide stop
    tp_atr_mult: 2.5    # Breakout: wide target
```

#### 1.5 Volume Confirmation Gate (All Strategies)
**Files**: `agents/implementations/trading_decision.py`

| Strategy | Current Volume Req | New Volume Req |
|----------|-------------------|---------------|
| momentum_breakout_scalp | 1.5x | 1.5x (keep) |
| bollinger_squeeze_scalp | 1.2x | 1.5x (raise) |
| ema_crossover_scalp | None | 1.2x (add) |
| rsi_pullback_scalp | 1.0x | 1.0x (keep) |

#### 1.6 Funding Rate Graduated Signal
**Files**: `agents/implementations/trading_decision.py`, `config/trading_config.yaml`

Current: binary cost filter (reject if > 0.05%)
New: graduated directional confidence modifier

```yaml
funding_rate_signal:
  enabled: true
  # Contrarian thresholds
  crowded_long_threshold: 0.0003   # 0.03% per 8h = crowded longs
  crowded_short_threshold: -0.0002  # -0.02% per 8h = crowded shorts
  # Extreme thresholds (suppress same-direction entries)
  extreme_long_threshold: 0.0008   # 0.08% per 8h
  extreme_short_threshold: -0.0005  # -0.05% per 8h
  confidence_boost: 0.05           # +5% for contrarian
  confidence_penalty: 0.08         # -8% for same-direction at extremes
```

API: Already available via `/fapi/v1/premiumIndex` (field: `lastFundingRate`)

#### 1.7 Time-of-Day Windows
**Files**: `agents/implementations/trading_decision.py`, `config/trading_config.yaml`

```yaml
time_of_day:
  enabled: true
  high_confidence_hours_utc: [13, 14, 15, 16, 21, 22]  # US session + documented edge
  low_confidence_hours_utc: [0, 1, 2, 3, 4]              # Asia dead zone
  boost_pct: 0.05
  penalty_pct: 0.05
```

Source: Springer Nature 2024 -- 21:00-23:00 UTC BTC strategy = 40.64% annualized, Calmar 1.79.

---

### Phase 2: Exit System & Advanced Signals

#### 2.1 ATR-Based Trailing Stop
**Files**: `infrastructure/trailing_stop.py`, `config/trading_config.yaml`

Replace fixed percentage trailing (0.2% step) with ATR-based:

| Parameter | Current | New |
|-----------|---------|-----|
| Trail activation | 0.5% fixed | 0.75x ATR |
| Trail step | 0.2% fixed | Recalculate every 5s |
| Trail distance | N/A | 1.0x ATR behind high-water mark |
| Breakeven trigger | 0.5% | 0.6-0.7% (slightly wider to avoid noise) |

Benefits: Automatically widens in volatile periods, tightens in calm periods.

#### 2.2 ADX Optimization
**Files**: `infrastructure/binance_api/indicators.py` (line 114)

- Change ADX window from 14 to 10 (detects trend onset 4-6 candles earlier)
- Keep threshold at 20 for confluence gate
- For HTF (1h): keep ADX(14) with threshold 25

#### 2.3 Taker Buy/Sell Ratio
**Files**: `agents/implementations/research_coordinator.py`, `agents/implementations/trading_decision.py`

New data source: `GET /futures/data/takerlongshortRatio` with `period=5m`
- Ratio > 1.05: boost LONG confidence +3%
- Ratio < 0.95: boost SHORT confidence +3%
- Ratio moving against signal direction: reduce confidence

#### 2.4 Open Interest Rate-of-Change
**Files**: `agents/implementations/research_coordinator.py`, `agents/implementations/trading_decision.py`

New data source: `GET /fapi/v1/openInterest` (poll every 60s)
- OI +3% in 15min + price -0.3% = bearish divergence (boost SHORT +5%)
- OI +3% in 15min + price +0.3% = bullish confirmation (boost LONG +5%)
- OI -3% in 15min + price rising = short covering, weak rally (penalize LONG)

#### 2.5 Time Decay on TP
**Files**: `infrastructure/trailing_stop.py`

| Time Elapsed | TP Adjustment |
|-------------|---------------|
| 0-6 min | Full TP target |
| 6-9 min | 75% of original TP |
| 9-12 min | 50% of original TP (take any profit) |
| 12-15 min | Close at market |

#### 2.6 Liquidation Stream (Defensive)
**Files**: `infrastructure/websocket_manager.py`, `agents/implementations/emergency_controller.py`

Subscribe to `wss://fstream.binance.com/ws/!forceOrder@arr`
- Track 5-min rolling liquidation USD volume
- If spike > 3x rolling 1h average: PAUSE new entries
- Track side bias (long vs short liquidations) for directional hint after cascade settles
- **NOT a trading signal** -- purely defensive regime filter

---

### Phase 3: HMM Regime Detection & Validation

#### 3.1 HMM Regime Detector
**Files**: `infrastructure/regime_detector.py` (new parallel system)

```python
from hmmlearn.hmm import GaussianHMM

model = GaussianHMM(
    n_components=3,          # Low vol, Trending, High vol
    covariance_type="full",
    n_iter=200,
    random_state=42,
)
# Features: [log_returns_5bar, realized_vol_20bar, volume_ratio_20bar]
model.fit(features_train)
```

Benefits over LLM-only:
- Deterministic, free, backtestable, fast (<1ms vs 2-5s)
- Re-train weekly on rolling 30-60 days of 5m data
- Documented: 50% volatility reduction, 15-17pp drawdown improvement (SSRN)

#### 3.2 Monte Carlo Permutation Tests
**Files**: `backtesting/metrics.py`

- 5,000 permutations of trade PnL sequence
- p-value < 0.05 = genuine edge
- Bootstrap 95% CI for Sharpe, max drawdown, total return
- Add to walk-forward output

#### 3.3 Deflated Sharpe Ratio
**Files**: `backtesting/metrics.py`

Bailey & Lopez de Prado correction for multiple testing:
- Track N = total strategy/parameter combinations tested
- DSR > 0.95 = genuine alpha (95% confidence)
- With 4 strategies x ~20 param combos, N~20-80

---

## KEY HONEST INSIGHTS FROM RESEARCH

### What the Alpha Agent discovered:
> "There are no magic alpha signals in crypto derivatives in 2026. The most rigorous study (Tigro Blanc) showed liquidation cascade 'alpha' is 54% BTC beta. The real edge comes from signal combinations and knowing when NOT to trade."

### What the Risk/Exit Agent discovered:
> "Partial profit-taking does NOT improve mathematical expectancy for automated bots. The 'win rate boost' is a psychological illusion. Keep full position with ATR trailing."

> "At $65, the small account paradox is real: Binance's $100 minimum means actual risk per trade is 5-8% of equity regardless of risk settings."

### What the Strategy Agent discovered:
> "Multi-timeframe alignment improves win rate from 45% to 60-75% (strongest evidence). Your MTF is already well-configured. Further tuning is marginal."

### What the Validation Agent discovered:
> "After only 1,000 backtests, the expected maximum Sharpe Ratio is 3.26 even with ZERO true alpha (False Strategy Theorem). DSR is essential."

---

## EXPECTED IMPACT (Conservative)

| Metric | Current | After Tier 0-1 | After All Tiers |
|--------|---------|----------------|-----------------|
| Trade frequency | ~15-20/day | ~8-12/day | ~6-10/day |
| Win rate | 35.7% | 48-55% | 55-65% |
| TP hit rate | 9.4% | 30-40% | 40-55% |
| Avg winner | $0.63 | $0.90-1.20 | $1.20-1.80 |
| Avg loser | -$0.68 | -$0.45 | -$0.35 |
| Fee drag/trade | $0.10 | $0.063 | $0.055 |
| Daily PnL | -$5-10 | $1-4 | $4-10 |
| Monthly PnL | -$200 | $30-100 | $100-250 |

---

## KEY RESEARCH SOURCES (120+)

### Academic / Institutional
- arXiv:2602.00776 -- OFI in Cryptocurrency Microstructure
- arXiv:2602.10785 -- Walk-Forward Crypto Optimization (81 window combos)
- Bailey & Lopez de Prado 2014 -- Deflated Sharpe Ratio (SSRN 2460551)
- Bailey et al. -- Probability of Backtest Overfitting (SSRN 2326253)
- Springer Nature 2024 -- Intraday crypto time-of-day patterns
- Springer Digital Finance 2024 -- Regime-switching crypto forecasting
- SSRN -- HMM-LSTM Framework (50% vol reduction)
- MDPI -- Regime-switching factor investing (50-60% excess returns)
- Tigro Blanc Feb 2026 -- Liquidation cascade alpha disproven (54% beta)
- SSRN -- October 2025 $19B liquidation cascade anatomy
- Almgren-Chriss -- Square-root market impact model
- EFMA 2025 -- Order flow and cryptocurrency returns

### Quantitative / Practitioner
- MC2 Finance -- MACD 5min settings (19,456 variations study)
- MC2 Finance -- RSI scalping optimization
- QuantStart -- HMM regime detection, backtesting methodology
- QuantInsti -- Regime-adaptive trading
- LuxAlgo -- ATR stop-loss, volume scalping, volatility stops
- FMZQuant -- ATR dynamic trailing strategy
- QuantifiedStrategies -- ATR trailing, MACD+RSI (73%), MACD+BB (78%)
- CryptoProfitCalc -- EMA crossover crypto 2025 guide
- TheRobustTrader -- ADX settings optimization

### Crypto-Specific
- Gate.io -- OI+funding+liquidation ensemble (87% accuracy)
- Amberdata -- Funding rate arbitrage (Sharpe 3-6), liquidation analysis
- CoinGlass -- Funding rate arbitrage guide
- CryptoQuant -- Taker buy/sell ratio data
- Binance API -- Open Interest, Funding Rate, Taker Volume, Liquidation streams

### Risk & Exit
- Van Tharp Institute -- Position sizing, expectancy
- BabyPips -- Scaling out mathematical analysis
- HyroTrader 2026 -- Crypto scalping fee optimization
- Binance Dev Community -- Post-only GTX order behavior
- Kelly Criterion (Wikipedia, CQF) -- Fractional Kelly for small accounts
