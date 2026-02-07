# Execution Agent

**Agent ID**: `execution-agent`

**Role**: Interface with Binance Futures API to execute approved trades in paper, live, or hybrid mode.

---

## Responsibilities

1. **Order Placement**: Execute market/limit orders via Binance API
2. **Stop Loss Management**: Place and monitor stop loss orders
3. **Take Profit Management**: Place and monitor take profit orders
4. **Position Monitoring**: Track open positions and P&L
5. **Execution Quality**: Measure slippage, fees, fill quality
6. **Idempotency**: Prevent duplicate order submissions

---

## Execution Flow

```
Risk Approval (APPROVED/MODIFIED)
       ↓
Pre-Trade Validation Hook (blocking)
       ↓
Order Execution (Paper/Live/Hybrid)
       ↓
Stop Loss & Take Profit Placement
       ↓
Position Registration
       ↓
Post-Execution Audit Hook (non-blocking)
       ↓
Execution Result
```

---

## Order Placement Logic

### Market Orders (Primary)
```python
def place_market_order(
    symbol: str,
    side: str,  # "BUY" or "SELL"
    quantity: float,
    leverage: int,
    client_order_id: str
) -> Dict:
    """
    Place market order on Binance Futures.
    """

    # Set leverage
    binance_client.futures_change_leverage(
        symbol=symbol,
        leverage=leverage
    )

    # Place order
    order = binance_client.futures_create_order(
        symbol=symbol,
        side=side,
        type="MARKET",
        quantity=quantity,
        newClientOrderId=client_order_id
    )

    return order
```

### Stop Loss Orders
```python
def place_stop_loss(
    symbol: str,
    side: str,  # Opposite of entry: "SELL" for LONG, "BUY" for SHORT
    quantity: float,
    stop_price: float,
    client_order_id: str
) -> Dict:
    """
    Place stop-market order.
    """

    order = binance_client.futures_create_order(
        symbol=symbol,
        side=side,
        type="STOP_MARKET",
        stopPrice=stop_price,
        quantity=quantity,
        reduceOnly=True,  # Only closes existing position
        newClientOrderId=f"{client_order_id}_SL"
    )

    return order
```

### Take Profit Orders
```python
def place_take_profit(
    symbol: str,
    side: str,
    quantity: float,
    price: float,
    client_order_id: str
) -> Dict:
    """
    Place take-profit limit order.
    """

    order = binance_client.futures_create_order(
        symbol=symbol,
        side=side,
        type="TAKE_PROFIT_MARKET",
        stopPrice=price,
        quantity=quantity,
        reduceOnly=True,
        newClientOrderId=f"{client_order_id}_TP"
    )

    return order
```

---

## Error Handling

### Rate Limits
```python
@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=10),
    retry=retry_if_exception_type(RateLimitExceeded)
)
def place_order_with_retry(order_params):
    """
    Retry order placement with exponential backoff on rate limit.
    """
    return binance_client.futures_create_order(**order_params)
```

### Partial Fills
```python
def handle_partial_fill(order: Dict) -> str:
    """
    Handle partially filled orders.

    Options:
    1. Wait for full fill (up to 10 seconds)
    2. Cancel remaining and accept partial
    3. Cancel entire order if fill < 80%
    """

    filled_qty = float(order["executedQty"])
    total_qty = float(order["origQty"])
    fill_pct = filled_qty / total_qty

    if fill_pct >= 0.8:
        # Accept partial fill, cancel remainder
        binance_client.futures_cancel_order(
            symbol=order["symbol"],
            orderId=order["orderId"]
        )
        return "PARTIALLY_FILLED"
    else:
        # Cancel entire order
        binance_client.futures_cancel_order(
            symbol=order["symbol"],
            orderId=order["orderId"]
        )
        return "REJECTED"
```

### Network Errors
```python
def handle_network_error(e: Exception, order_params: Dict) -> Dict:
    """
    Handle network errors during order placement.

    1. Check if order was actually placed (query by client_order_id)
    2. If placed, return existing order
    3. If not placed, retry
    """

    client_order_id = order_params["newClientOrderId"]

    # Query existing order
    try:
        existing_order = binance_client.futures_get_order(
            symbol=order_params["symbol"],
            origClientOrderId=client_order_id
        )
        logger.warning("Order was placed despite network error")
        return existing_order
    except:
        # Order not placed, safe to retry
        logger.info("Order not placed, retrying")
        raise  # Trigger retry
```

---

## Position Monitoring

### Track Open Positions
```python
class PositionTracker:
    """
    Tracks all open positions for monitoring and P&L calculation.
    """

    def __init__(self):
        self.positions = {}  # symbol -> position_info

    def register_position(self, execution_result: Dict):
        """
        Register new position after execution.
        """
        symbol = execution_result["symbol"]
        self.positions[symbol] = {
            "execution_id": execution_result["execution_id"],
            "side": execution_result["side"],
            "entry_price": execution_result["order_details"]["avg_fill_price"],
            "quantity": execution_result["order_details"]["filled_quantity"],
            "leverage": execution_result["order_details"]["leverage"],
            "stop_loss": execution_result["stop_loss_order"]["stop_price"],
            "take_profit": [tp["price"] for tp in execution_result["take_profit_orders"]],
            "entry_time": execution_result["timestamp"],
            "expected_exit_time": calculate_expected_exit_time(execution_result)
        }

    def get_current_pnl(self, symbol: str) -> float:
        """
        Calculate current unrealized P&L for position.
        """
        position = self.positions[symbol]
        current_price = fetch_current_price(symbol)

        if position["side"] == "LONG":
            pnl = (current_price - position["entry_price"]) * position["quantity"]
        else:  # SHORT
            pnl = (position["entry_price"] - current_price) * position["quantity"]

        return pnl
```

### Time-Based Exits
```python
def check_time_exits():
    """
    Check if positions have exceeded expected holding time.

    For scalping, force close positions after max holding time.
    """
    for symbol, position in position_tracker.positions.items():
        elapsed = (datetime.utcnow() - position["entry_time"]).total_seconds()

        if elapsed > position["expected_exit_time"] + 60:  # +60s grace period
            logger.warning(f"Position {symbol} exceeded max holding time, force closing")
            force_close_position(symbol, reason="time_limit_exceeded")
```

---

## Execution Quality Metrics

### Slippage Measurement
```python
def calculate_slippage(
    requested_price: float,
    actual_price: float,
    quantity: float,
    side: str
) -> Dict:
    """
    Calculate slippage metrics.
    """

    if side == "LONG":
        # For longs, higher price = worse fill (positive slippage)
        slippage_usdt = (actual_price - requested_price) * quantity
    else:  # SHORT
        # For shorts, lower price = worse fill (positive slippage)
        slippage_usdt = (requested_price - actual_price) * quantity

    slippage_bps = (slippage_usdt / (requested_price * quantity)) * 10000

    return {
        "slippage_usdt": slippage_usdt,
        "slippage_bps": slippage_bps,
        "is_favorable": slippage_usdt < 0  # Negative slippage = better than expected
    }
```

### Fill Quality Score
```python
def calculate_fill_quality(execution_result: Dict) -> float:
    """
    Score execution quality (0-1).

    Factors:
    - Slippage (lower = better)
    - Fill speed (faster = better)
    - Full vs partial fill
    """

    slippage_bps = execution_result["order_details"]["slippage_bps"]
    processing_time_ms = execution_result["processing_time_ms"]

    # Slippage score (0-5 bps = 1.0, 10+ bps = 0)
    slippage_score = max(0, 1 - (slippage_bps / 10))

    # Speed score (< 100ms = 1.0, > 1000ms = 0)
    speed_score = max(0, 1 - (processing_time_ms / 1000))

    # Fill score
    if execution_result["execution_status"] == "FILLED":
        fill_score = 1.0
    elif execution_result["execution_status"] == "PARTIALLY_FILLED":
        fill_score = 0.5
    else:
        fill_score = 0.0

    # Weighted average
    quality_score = (slippage_score * 0.5 + speed_score * 0.3 + fill_score * 0.2)

    return quality_score
```

---

## Output Schema

Conforms to `schemas/execution_result.schema.json`.

Example successful execution:
```json
{
  "execution_id": "uuid",
  "approval_id": "uuid",
  "decision_id": "uuid",
  "execution_mode": "LIVE",
  "execution_status": "FILLED",
  "symbol": "BTCUSDT",
  "side": "LONG",
  "order_details": {
    "binance_order_id": "12345678",
    "client_order_id": "TRADE_abc123...",
    "order_type": "MARKET",
    "requested_quantity": 0.115,
    "filled_quantity": 0.115,
    "avg_fill_price": 43265.0,
    "requested_price": 43260.0,
    "slippage_usdt": 0.575,
    "slippage_bps": 1.15,
    "fees_usdt": 2.49,
    "leverage": 7,
    "position_value_usdt": 497.545
  },
  "stop_loss_order": {
    "binance_order_id": "12345679",
    "stop_price": 43173.0,
    "status": "PLACED"
  },
  "take_profit_orders": [
    {
      "binance_order_id": "12345680",
      "price": 43390.0,
      "quantity_pct": 0.5,
      "status": "PLACED"
    },
    {
      "binance_order_id": "12345681",
      "price": 43520.0,
      "quantity_pct": 0.5,
      "status": "PLACED"
    }
  ],
  "shadow_paper_execution": {
    "shadow_fill_price": 43262.0,
    "shadow_slippage_usdt": 0.23,
    "divergence_usdt": 0.345,
    "divergence_pct": 0.0008,
    "divergence_alert": false
  },
  "execution_timeline": [
    {
      "timestamp": "2026-02-07T10:30:16.100Z",
      "event": "order_submitted",
      "details": "Market order submitted to Binance"
    },
    {
      "timestamp": "2026-02-07T10:30:16.250Z",
      "event": "order_filled",
      "details": "Order fully filled"
    },
    {
      "timestamp": "2026-02-07T10:30:16.400Z",
      "event": "stop_loss_placed",
      "details": "Stop loss order placed"
    },
    {
      "timestamp": "2026-02-07T10:30:16.550Z",
      "event": "take_profit_placed",
      "details": "Take profit orders placed"
    }
  ],
  "timestamp": "2026-02-07T10:30:16.600Z",
  "processing_time_ms": 500,
  "idempotency_check": {
    "is_duplicate": false,
    "original_execution_id": null
  }
}
```

---

## Authority Boundaries

**Can Do**:
- ✅ Execute approved trades only
- ✅ Place stop loss and take profit orders
- ✅ Monitor open positions
- ✅ Force close positions on time limit
- ✅ Cancel orders if needed

**Cannot Do**:
- ❌ Decide on trades (must receive approval)
- ❌ Modify risk parameters
- ❌ Override Risk Manager decisions
- ❌ Withdraw or deposit funds

---

## Testing Requirements

1. Test paper trading execution (slippage simulation)
2. Test live execution with small amounts
3. Test idempotency (duplicate order detection)
4. Test stop loss and take profit placement
5. Test partial fill handling
6. Test network error recovery
7. Test rate limit handling (exponential backoff)
8. Test time-based position exits
9. Test execution quality scoring
10. Test shadow execution comparison (hybrid mode)
