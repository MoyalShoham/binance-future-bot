# Validate Config

Validate the structure and values in `config/trading_config.yaml`.

## Steps

1. Run the validation script:

```bash
".conda/python.exe" -c "
import yaml, sys

with open('config/trading_config.yaml') as f:
    config = yaml.safe_load(f)

errors = []
warnings = []

# Required top-level sections
for section in ['trading', 'risk', 'models', 'execution', 'data', 'strategies', 'trailing_stop']:
    if section not in config:
        errors.append(f'Missing required section: {section}')

# Risk limits sanity checks
risk = config.get('risk', {})
if risk.get('max_risk_per_trade_pct', 0) > 0.05:
    warnings.append(f'max_risk_per_trade_pct={risk[\"max_risk_per_trade_pct\"]} is >5% - very aggressive')
if risk.get('max_daily_drawdown_pct', 0) > 0.15:
    warnings.append(f'max_daily_drawdown_pct={risk[\"max_daily_drawdown_pct\"]} is >15% - very aggressive')
if risk.get('max_portfolio_exposure_pct', 0) > 0.80:
    warnings.append(f'max_portfolio_exposure_pct={risk[\"max_portfolio_exposure_pct\"]} is >80% - very aggressive')

# Leverage sanity
lev = risk.get('leverage_limits', {})
for tier, val in lev.items():
    if val > 20:
        warnings.append(f'leverage_limits.{tier}={val} is >20x - very high')

# Trading section
trading = config.get('trading', {})
if not trading.get('enabled'):
    warnings.append('trading.enabled is false - bot will not trade')
mode = trading.get('execution_mode', '')
if mode not in ('paper', 'live', 'hybrid'):
    errors.append(f'Invalid execution_mode: {mode}')

# Strategies
strategies = config.get('strategies', {})
enabled = strategies.get('enabled', [])
if not enabled:
    errors.append('No strategies enabled')

# Trailing stop
ts = config.get('trailing_stop', {})
if ts.get('enabled') and ts.get('max_holding_time_seconds', 0) < 60:
    warnings.append('max_holding_time_seconds < 60s - very short')

# Database
db = config.get('data', {}).get('database', {})
if not db.get('type'):
    errors.append('No database type configured')

print('=== Config Validation ===')
print(f'Mode: {mode}')
print(f'Strategies enabled: {len(enabled)} ({", ".join(enabled)})')
print(f'Leverage: {lev}')
print(f'Risk per trade: {risk.get(\"max_risk_per_trade_pct\", \"?\")}')
print(f'Daily drawdown: {risk.get(\"max_daily_drawdown_pct\", \"?\")}')
print()

if errors:
    print(f'ERRORS ({len(errors)}):')
    for e in errors: print(f'  - {e}')
if warnings:
    print(f'WARNINGS ({len(warnings)}):')
    for w in warnings: print(f'  - {w}')
if not errors and not warnings:
    print('All checks passed')

sys.exit(1 if errors else 0)
"
```

2. Report the results: mode, enabled strategies, risk limits, and any errors/warnings found.
