# Emergency Controller Agent

**Agent ID**: `emergency-controller`

**Role**: Monitor system health, detect anomalies, trigger kill switches, and handle emergency situations.

---

## Responsibilities

1. **System Health Monitoring**: API connectivity, database, WebSockets
2. **Anomaly Detection**: Flash crashes, unusual slippage, API errors
3. **Kill Switch Management**: Activate/deactivate all kill switch types
4. **Emergency Responses**: Auto-close positions, halt trading, send alerts
5. **Recovery Procedures**: Restart failed services, re-establish connections

---

## Monitoring Targets

### 1. API Health
```python
def check_binance_api_health() -> Dict:
    """
    Check Binance API connectivity and health.
    """

    checks = {
        "rest_api": False,
        "websocket": False,
        "latency_ms": None,
        "rate_limit_status": None
    }

    try:
        # REST API check
        start = time.time()
        server_time = binance_client.futures_time()
        latency_ms = (time.time() - start) * 1000

        checks["rest_api"] = True
        checks["latency_ms"] = latency_ms

        # Check rate limits
        rate_limits = binance_client.futures_exchange_info()
        checks["rate_limit_status"] = "healthy"

    except Exception as e:
        logger.error("Binance API health check failed", error=str(e))
        checks["rest_api"] = False

    # WebSocket check
    checks["websocket"] = is_websocket_connected()

    return checks
```

### 2. Database Connectivity
```python
def check_database_health() -> bool:
    """
    Verify database is accessible.
    """
    try:
        db.execute("SELECT 1")
        return True
    except Exception as e:
        logger.error("Database health check failed", error=str(e))
        return False
```

### 3. Model API Availability
```python
def check_model_apis() -> Dict:
    """
    Check AI model API availability.
    """
    checks = {
        "openai": False,
        "anthropic": False,
        "google": False
    }

    # Check OpenAI
    try:
        openai_client.models.list()
        checks["openai"] = True
    except:
        pass

    # Check Anthropic
    try:
        anthropic_client.messages.create(
            model="claude-3-haiku-20240307",
            max_tokens=10,
            messages=[{"role": "user", "content": "test"}]
        )
        checks["anthropic"] = True
    except:
        pass

    # Check Google
    try:
        google_client.generate_content("test")
        checks["google"] = True
    except:
        pass

    return checks
```

---

## Anomaly Detection

### 1. Flash Crash Detection
```python
def detect_flash_crash(symbol: str, threshold_pct: float = 0.10) -> bool:
    """
    Detect flash crash: >10% price move in 1 minute.
    """

    # Get last 1 minute of price data
    klines = binance_client.futures_klines(
        symbol=symbol,
        interval="1m",
        limit=2
    )

    if len(klines) < 2:
        return False

    prev_close = float(klines[-2][4])
    current_close = float(klines[-1][4])

    price_change_pct = abs(current_close - prev_close) / prev_close

    if price_change_pct > threshold_pct:
        logger.critical(
            "Flash crash detected",
            symbol=symbol,
            price_change_pct=price_change_pct
        )
        return True

    return False
```

### 2. Unusual Slippage Detection
```python
def detect_unusual_slippage(execution: Dict) -> bool:
    """
    Detect abnormally high slippage (>20 bps).
    """

    slippage_bps = execution["order_details"]["slippage_bps"]

    if slippage_bps > 20:
        logger.warning(
            "Unusual slippage detected",
            execution_id=execution["execution_id"],
            slippage_bps=slippage_bps
        )
        return True

    return False
```

### 3. Repeated API Errors
```python
class APIErrorTracker:
    """
    Track API errors and trigger alerts on repeated failures.
    """

    def __init__(self, threshold: int = 5, window_seconds: int = 60):
        self.errors = []
        self.threshold = threshold
        self.window = window_seconds

    def record_error(self, error: Exception):
        """Record an API error."""
        self.errors.append({
            "error": str(error),
            "timestamp": datetime.utcnow()
        })

        # Clean old errors outside window
        cutoff = datetime.utcnow() - timedelta(seconds=self.window)
        self.errors = [e for e in self.errors if e["timestamp"] > cutoff]

    def should_trigger_alert(self) -> bool:
        """Check if error threshold exceeded."""
        return len(self.errors) >= self.threshold
```

---

## Kill Switch Management

### Kill Switch Types

**1. Global Kill Switch**
```python
class KillSwitchManager:
    """
    Manage all kill switches.
    """

    def __init__(self, db_session):
        self.db = db_session

    def activate_global_kill_switch(self, reason: str, close_positions: bool = False):
        """
        Activate global kill switch - STOP ALL TRADING.
        """

        logger.critical("GLOBAL KILL SWITCH ACTIVATED", reason=reason)

        # Update config
        update_config("trading.enabled", False)
        update_config("risk.kill_switches.global", True)

        # Log to audit trail
        log_audit_event(
            event_type="kill_switch_activated",
            data={"type": "global", "reason": reason}
        )

        # Optionally close all positions
        if close_positions:
            self.close_all_positions(reason="global_kill_switch")

        # Send emergency alerts
        send_emergency_alert(
            severity="CRITICAL",
            message=f"Global kill switch activated: {reason}"
        )

    def deactivate_global_kill_switch(self):
        """Deactivate global kill switch."""
        logger.info("Global kill switch deactivated")
        update_config("trading.enabled", True)
        update_config("risk.kill_switches.global", False)
```

**2. Symbol Kill Switch**
```python
def activate_symbol_kill_switch(symbol: str, reason: str):
    """
    Block trading for specific symbol.
    """
    logger.warning(f"Symbol kill switch activated for {symbol}", reason=reason)

    update_config(f"risk.kill_switches.symbols.{symbol}", True)

    # Close open position if exists
    close_position_if_open(symbol, reason=f"symbol_kill_switch: {reason}")
```

**3. Strategy Kill Switch**
```python
def activate_strategy_kill_switch(strategy_id: str, reason: str):
    """
    Disable specific strategy.
    """
    logger.warning(f"Strategy kill switch activated for {strategy_id}", reason=reason)

    update_config(f"risk.kill_switches.strategies.{strategy_id}", True)
```

**4. Volatility Circuit Breaker**
```python
def check_volatility_circuit_breaker(symbol: str) -> bool:
    """
    Check and potentially trigger volatility circuit breaker.
    """

    if detect_flash_crash(symbol, threshold_pct=0.10):
        logger.critical("Volatility circuit breaker triggered", symbol=symbol)

        # Pause trading for 5 minutes
        activate_temporary_pause(
            duration_seconds=300,
            reason="volatility_circuit_breaker"
        )

        return True

    return False
```

---

## Emergency Responses

### Force Close All Positions
```python
def close_all_positions(reason: str):
    """
    Emergency: Close all open positions immediately.
    """

    logger.critical("CLOSING ALL POSITIONS", reason=reason)

    # Get all open positions
    positions = binance_client.futures_position_information()

    for position in positions:
        position_amt = float(position['positionAmt'])

        if position_amt != 0:
            symbol = position['symbol']
            side = "SELL" if position_amt > 0 else "BUY"

            try:
                # Close position with market order
                binance_client.futures_create_order(
                    symbol=symbol,
                    side=side,
                    type="MARKET",
                    quantity=abs(position_amt),
                    reduceOnly=True
                )

                logger.info(f"Closed position {symbol}", quantity=position_amt)

            except Exception as e:
                logger.error(f"Failed to close {symbol}", error=str(e))
```

### Emergency Alert System
```python
def send_emergency_alert(severity: str, message: str):
    """
    Send emergency alerts via all configured channels.
    """

    alert = {
        "severity": severity,
        "message": message,
        "timestamp": datetime.utcnow().isoformat()
    }

    # Console
    if severity == "CRITICAL":
        logger.critical(message)
    else:
        logger.error(message)

    # Log file
    with open("logs/emergency_alerts.log", "a") as f:
        f.write(f"{alert['timestamp']} [{severity}] {message}\n")

    # Email (if configured)
    if config.get("notifications.email.enabled"):
        send_email_alert(alert)

    # Webhook (if configured)
    if config.get("notifications.webhook.enabled"):
        send_webhook_alert(alert)
```

---

## Continuous Monitoring

```python
class ContinuousMonitor:
    """
    Continuously monitor system health and metrics.
    """

    def __init__(self, check_interval_seconds: int = 60):
        self.interval = check_interval_seconds
        self.running = False

    def start(self):
        """Start continuous monitoring."""
        self.running = True

        while self.running:
            self.perform_checks()
            time.sleep(self.interval)

    def perform_checks(self):
        """Perform all health checks."""

        # API health
        api_health = check_binance_api_health()
        if not api_health["rest_api"]:
            handle_api_failure()

        # Database health
        db_health = check_database_health()
        if not db_health:
            handle_database_failure()

        # Risk thresholds
        check_risk_thresholds()

        # Position age
        check_position_age()

        # Flash crash detection (for all active symbols)
        for symbol in get_active_symbols():
            if detect_flash_crash(symbol):
                activate_global_kill_switch(
                    reason=f"Flash crash detected on {symbol}",
                    close_positions=True
                )

    def stop(self):
        """Stop continuous monitoring."""
        self.running = False
```

---

## Recovery Procedures

### Reconnect WebSocket
```python
def reconnect_websocket():
    """
    Attempt to reconnect WebSocket connection.
    """

    logger.info("Attempting to reconnect WebSocket")

    try:
        websocket_client.close()
        time.sleep(5)
        websocket_client.connect()
        logger.info("WebSocket reconnected successfully")
    except Exception as e:
        logger.error("Failed to reconnect WebSocket", error=str(e))
        # Trigger alert after 3 failed attempts
```

---

## Authority Boundaries

**Can Do**:
- ✅ Activate/deactivate all kill switches
- ✅ Force close positions in emergencies
- ✅ Send emergency alerts
- ✅ Monitor system health
- ✅ Detect anomalies

**Cannot Do**:
- ❌ Make trading decisions
- ❌ Approve/reject trades (Risk Manager's role)
- ❌ Modify system configuration permanently

---

## Testing Requirements

1. Test API health monitoring
2. Test flash crash detection
3. Test kill switch activation (all types)
4. Test emergency position closing
5. Test alert system (email, webhook)
6. Test continuous monitoring loop
7. Test recovery procedures (WebSocket reconnect)
8. Test anomaly detection (unusual slippage)
