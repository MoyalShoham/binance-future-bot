# JSON Schema Contract Layer

## Overview

This directory contains **strict JSON schemas** that define ALL inter-agent communication in the trading system.

**Core Principles:**
- **No Free Text**: All agent messages conform to schemas
- **Versioned**: Semantic versioning for forward compatibility
- **Explicit**: Every field has strict type and validation
- **Deterministic**: No `additionalProperties` unless justified
- **Traceable**: Every message includes `correlation_id` for audit trail

---

## Schema Versions

All schemas are currently at version **1.0.0** (semantic versioning).

### Version Format

```
MAJOR.MINOR.PATCH

MAJOR: Breaking changes (incompatible)
MINOR: New features (backward-compatible)
PATCH: Bug fixes (backward-compatible)
```

---

## Required Fields (ALL Schemas)

Every agent-to-agent message MUST include:

```json
{
  "schema_version": "1.0.0",
  "agent_id": "agent-name",
  "correlation_id": "uuid-linking-all-messages-in-cycle",
  "timestamp": "ISO 8601 datetime"
}
```

**Purpose:**
- `schema_version`: Enables version detection and migration
- `agent_id`: Identifies message originator
- `correlation_id`: Links all messages in a trading cycle
- `timestamp`: Precise message generation time

---

## Schema Files

### 1. research_summary.schema.json

**From:** Research Coordinator Agent
**To:** Trading Decision Agent

**Purpose:** Normalized market research (price, indicators, sentiment, news)

**Key Fields:**
- `symbol`: Trading pair (e.g., "BTCUSDT")
- `timeframe`: Analysis timeframe ("1m", "5m", etc.)
- `market_data`: Price, volume, funding rate, order book
- `technical_indicators`: EMA, RSI, MACD, ATR, volatility
- `sentiment`: Overall score + confidence
- `market_regime`: Classified market state
- `news_events`: Significant news with impact/sentiment
- `warnings`: Risk warnings

**Validation:**
- `additionalProperties: false` (strict)
- All numeric fields have `minimum` constraints
- Enums for categorical fields (timeframe, market_regime)

---

### 2. trading_decision.schema.json

**From:** Trading Decision Agent
**To:** Risk Management Agent

**Purpose:** Proposed trade with entry/exit levels

**Key Fields:**
- `decision_id`: UUID for this decision
- `symbol`: Trading pair
- `decision`: Enum ("NO_TRADE", "LONG", "SHORT")
- `confidence`: 0.0-1.0 (decision confidence)
- `strategy_id`: Strategy used (enum: 4 strategies)
- `entry_price`: Proposed entry
- `stop_loss`: Stop loss level
- `take_profit_levels`: Array of TP levels with quantity %
- `position_size_usdt`: Requested size
- `leverage`: Requested leverage (1-20)
- `technical_signals`: Supporting signals
- `risk_metrics`: Risk/reward ratio, win probability

**Validation:**
- `decision` must be enum
- `confidence` must be 0-1
- `leverage` capped at 20
- `take_profit_levels` max 3 items
- Each TP `quantity_pct` must sum to ≤ 1.0

---

### 3. risk_approval.schema.json

**From:** Risk Management Agent
**To:** Execution Agent

**Purpose:** Approval/rejection/modification of trade

**Key Fields:**
- `approval_id`: UUID for this approval
- `decision_id`: Reference to trading decision
- `approval_status`: Enum ("APPROVED", "REJECTED", "MODIFIED")
- `rejection_reason`: String (if REJECTED)
- `modified_parameters`: Object (if MODIFIED)
- `risk_checks`: Object with all 9 check results
- `position_sizing`: Calculated sizing details
- `account_status`: Current account state
- `kill_switches`: Status of all kill switches

**Validation:**
- `approval_status` must be enum
- `risk_checks` has required checks (max_risk_per_trade, max_daily_drawdown, max_portfolio_exposure)
- Each risk check has `passed`, `current_value`, `limit`
- `account_status` has `available_balance`, `total_equity`, `current_exposure`

**Risk Check Structure:**
```json
{
  "risk_check_name": {
    "passed": true/false,
    "current_value": 0.015,
    "limit": 0.02,
    "severity": "info|warning|critical",
    "message": "Explanation"
  }
}
```

---

### 4. execution_intent.schema.json

**From:** Risk Management Agent
**To:** Execution Agent

**Purpose:** Risk-approved execution instructions

**Key Fields:**
- `intent_id`: UUID for this execution intent
- `approval_id`: Reference to risk approval
- `decision_id`: Reference to original decision
- `symbol`: Trading pair
- `side`: Enum ("LONG", "SHORT")
- `execution_mode`: Enum ("PAPER", "LIVE", "HYBRID")
- `position_size_usdt`: Risk-approved size
- `leverage`: Risk-approved leverage
- `entry_price`: Target entry
- `stop_loss`: Risk-approved SL
- `take_profit_levels`: Risk-approved TP levels
- `risk_parameters`: Max risk, risk %, SL distance
- `order_instructions`: Order type, time in force, etc.
- `metadata`: Was modified, modification reason

**Validation:**
- All numeric fields `exclusiveMinimum: true` (must be > 0)
- `take_profit_levels` percentages must be > 0
- `execution_mode` must be enum

---

### 5. execution_result.schema.json

**From:** Execution Agent
**To:** Storage & Reporting Agent

**Purpose:** Order execution outcome

**Key Fields:**
- `execution_id`: UUID for this execution
- `approval_id`: Reference to risk approval
- `decision_id`: Reference to trading decision
- `execution_mode`: Mode used
- `execution_status`: Enum ("FILLED", "PARTIALLY_FILLED", "REJECTED", "FAILED", "CANCELLED")
- `symbol`: Trading pair
- `side`: Direction
- `order_details`: Binance order ID, fill price, quantity, slippage, fees
- `stop_loss_order`: SL order details (if placed)
- `take_profit_orders`: TP order details (if placed)
- `shadow_paper_execution`: Shadow execution (LIVE/HYBRID modes)
- `paper_trading_simulation`: Simulation details (PAPER mode)
- `execution_timeline`: Event timeline
- `errors`: Array of errors encountered
- `idempotency_check`: Duplicate detection

**Validation:**
- `execution_status` must be enum
- `execution_mode` must be enum
- `order_details` has required fields (client_order_id, filled_quantity, avg_fill_price)

---

## Message Flow

Complete trading cycle with schema validation:

```
1. Research Coordinator
   ↓ [research_summary.schema.json]
2. Trading Decision Agent
   ↓ [trading_decision.schema.json]
3. Risk Management Agent
   ↓ [risk_approval.schema.json]
   ↓ [execution_intent.schema.json]
4. Execution Agent
   ↓ [execution_result.schema.json]
5. Storage & Reporting Agent
   (Persists to database)
```

**Correlation ID** links all messages in one cycle.

---

## Schema Validation

### Python Usage

```python
from schemas.validator import SchemaValidator

validator = SchemaValidator()

# Validate message
is_valid = validator.validate_message(
    message=trading_decision,
    message_type="trading_decision",
    strict=True
)

if not is_valid:
    # Schema validation failed
    raise ValueError("Invalid message format")
```

### Strict Mode

When `strict=True`:
- All required fields must be present
- No extra fields allowed (`additionalProperties: false`)
- All type constraints enforced
- All enums validated

When `strict=False`:
- Required fields must be present
- Extra fields allowed (warning logged)
- Type constraints enforced

**Production:** Always use `strict=True`

---

## Database Alignment

JSON schemas are designed to align with SQLAlchemy models:

| Schema | Database Table | JSON Column Mapping |
|--------|----------------|---------------------|
| `research_summary.schema.json` | `research_summaries` | `market_data` → JSON, `technical_indicators` → JSON, `sentiment` → JSON |
| `trading_decision.schema.json` | `trading_decisions` | `technical_signals` → JSON, `risk_metrics` → JSON, `take_profit_levels` → JSON |
| `risk_approval.schema.json` | `risk_approvals` | `risk_checks` → JSON, `position_sizing` → JSON, `account_status` → JSON |
| `execution_result.schema.json` | `executions` | `order_details` → JSON, `execution_timeline` → JSON, `errors` → JSON |

**Storage & Reporting Agent** automatically persists validated messages to database.

---

## Schema Evolution

### Adding New Fields (Minor Version)

```json
{
  "schema_version": "1.1.0",  // Increment MINOR
  "new_optional_field": "..."
}
```

**Requirements:**
- New field must be **optional**
- Old agents ignore new field (backward-compatible)
- Update schema version constant

### Breaking Changes (Major Version)

```json
{
  "schema_version": "2.0.0",  // Increment MAJOR
  "renamed_field": "..."  // Previously "old_field"
}
```

**Requirements:**
- All agents must be updated simultaneously
- Deploy with coordination
- Consider migration scripts

---

## Testing Schemas

### Validate Example Messages

```bash
# Test research summary
python schemas/test_schema.py research_summary examples/research_summary.json

# Test trading decision
python schemas/test_schema.py trading_decision examples/trading_decision.json

# Test risk approval
python schemas/test_schema.py risk_approval examples/risk_approval.json
```

### Validate in Tests

```python
import pytest
from schemas.validator import SchemaValidator

def test_trading_decision_schema():
    validator = SchemaValidator()

    valid_decision = {
        "schema_version": "1.0.0",
        "agent_id": "trading-decision",
        "correlation_id": "uuid-1234",
        "timestamp": "2026-02-08T00:00:00Z",
        "decision_id": "uuid-5678",
        "symbol": "BTCUSDT",
        "decision": "LONG",
        "confidence": 0.85,
        "strategy_id": "ema_crossover_scalp"
    }

    is_valid = validator.validate_message(
        valid_decision,
        "trading_decision",
        strict=True
    )

    assert is_valid == True
```

---

## Common Patterns

### Enums

All categorical fields use enums for strict validation:

```json
"decision": {
  "type": "string",
  "enum": ["NO_TRADE", "LONG", "SHORT"]
}
```

**Benefits:**
- Prevents typos
- Enforces valid values
- Self-documenting

### Numeric Constraints

All numeric fields have appropriate constraints:

```json
"confidence": {
  "type": "number",
  "minimum": 0,
  "maximum": 1
}

"leverage": {
  "type": "integer",
  "minimum": 1,
  "maximum": 20
}

"price": {
  "type": "number",
  "minimum": 0,
  "exclusiveMinimum": true  // Must be > 0 (not >= 0)
}
```

### Nested Objects

Nested objects also have `additionalProperties: false`:

```json
"market_data": {
  "type": "object",
  "additionalProperties": false,
  "required": ["price", "volume_24h"],
  "properties": {
    "price": {"type": "number", "minimum": 0},
    "volume_24h": {"type": "number", "minimum": 0}
  }
}
```

---

## Schema Compliance Checklist

When creating/updating schemas:

- [ ] Version number correct
- [ ] Required fields: `schema_version`, `agent_id`, `correlation_id`, `timestamp`
- [ ] `additionalProperties: false` on all objects
- [ ] All enums defined
- [ ] All numeric fields have `minimum`/`maximum`
- [ ] Prices use `exclusiveMinimum: true` (must be > 0)
- [ ] Array items have `minItems`/`maxItems`
- [ ] Field descriptions present
- [ ] Aligns with database JSON columns
- [ ] Example message validates

---

## FAQ

### Q: Why no `additionalProperties`?

**A:** Strict validation prevents typos and unexpected fields. If you need flexibility, explicitly allow it and document why.

### Q: Can I send custom fields?

**A:** No. All fields must be in schema. If you need a new field, update the schema and increment version.

### Q: What if schemas conflict with database?

**A:** Schemas are the source of truth. Update database models to match schemas, not the reverse.

### Q: How do I test schema changes?

**A:** Run `pytest tests/test_schemas.py` and verify all agents still validate.

---

## Summary

✅ **5 Schemas**: research_summary, trading_decision, risk_approval, execution_intent, execution_result
✅ **Strict Validation**: `additionalProperties: false` enforced
✅ **Versioned**: Semantic versioning for evolution
✅ **Traceable**: Every message has `correlation_id`
✅ **Type-Safe**: All fields have explicit types and constraints
✅ **Database-Aligned**: JSON columns match schema structure
✅ **Tested**: Schema validator with strict mode

The schema layer provides **deterministic, type-safe communication** between all agents. Treat schemas as **contracts** - breaking them breaks the system.
