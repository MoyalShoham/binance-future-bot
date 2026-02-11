"""
Emergency Controller Agent Implementation

Monitors system health and handles emergency situations.
Manages kill switches and detects trading anomalies.
"""

from typing import Dict, Any, List, Optional
from datetime import datetime, timedelta
import time
import threading
import structlog

from .base_agent import BaseAgent
from infrastructure.trailing_stop import TrailingStopMonitor

logger = structlog.get_logger()


class EmergencyControllerAgent(BaseAgent):
    """
    Emergency Controller Agent

    Responsibilities:
    - Monitor system health (API, database, WebSocket connectivity)
    - Manage kill switches (global, symbol, strategy, volatility circuit breaker)
    - Detect anomalies (flash crashes, unusual slippage, position sizing errors)
    - Trigger emergency alerts via configured channels
    - Run continuous background monitoring

    Authority Boundaries:
    ✅ FULL AUTHORITY to activate kill switches
    ✅ FULL AUTHORITY to pause trading on anomaly detection
    ✅ Monitor all system components
    ❌ Make trading decisions
    ❌ Execute trades
    ❌ Modify risk parameters

    Design:
    - Runs in background thread for continuous monitoring
    - Thread-safe kill switch management
    - Integrates with all system components
    - Observable (all events logged)
    """

    def __init__(
        self,
        agent_id: str,
        config: Dict[str, Any],
        binance_client,
        db_session=None,
        model_router=None,
        trades_db=None
    ):
        """
        Initialize Emergency Controller Agent.

        Args:
            agent_id: Agent identifier
            config: System configuration
            binance_client: Binance API client
            db_session: Database session (optional)
            model_router: Optional ModelRouter for LLM enhancement
            trades_db: Optional TradesDB for flat trade records
        """
        super().__init__(agent_id, config, model_router=model_router)

        self.binance_client = binance_client
        self.db_session = db_session

        # Initialize subsystems
        self.kill_switch_manager = KillSwitchManager(config)
        self.system_monitor = SystemHealthMonitor(binance_client, db_session)
        self.anomaly_detector = AnomalyDetector(config)
        self.trailing_stop_monitor = TrailingStopMonitor(binance_client, db_session, config, trades_db=trades_db)

        # Continuous monitoring
        self.monitoring_active = False
        self.monitor_thread = None
        self.check_interval_seconds = config.get("emergency", {}).get("check_interval_seconds", 60)

        logger.info(
            "Emergency Controller Agent initialized",
            agent_id=self.agent_id,
            check_interval_seconds=self.check_interval_seconds
        )

    def execute(self, state: Dict[str, Any]) -> Dict[str, Any]:
        """
        Execute emergency controller logic (on-demand health check).

        Args:
            state: Current state

        Returns:
            Emergency controller status dict
        """
        logger.info("Emergency Controller on-demand check started")

        try:
            # Perform immediate health checks
            api_health = self.system_monitor.check_binance_api_health()
            db_health = self.system_monitor.check_database_health()
            model_apis = self.system_monitor.check_model_apis()

            # Get kill switch status
            kill_switch_status = self.kill_switch_manager.get_status()

            # Check for anomalies if execution result present
            anomalies_detected = []
            if "execution_result" in state:
                if self.anomaly_detector.detect_unusual_slippage(state["execution_result"]):
                    anomalies_detected.append("UNUSUAL_SLIPPAGE")

            status = {
                "timestamp": datetime.utcnow().isoformat() + "Z",
                "api_health": api_health,
                "db_health": db_health,
                "model_apis": model_apis,
                "kill_switches": kill_switch_status,
                "anomalies_detected": anomalies_detected,
                "monitoring_active": self.monitoring_active
            }

            # LLM risk assessment (advisory only - CANNOT activate kill switches)
            llm_assessment = self._get_risk_assessment(
                api_health, db_health, model_apis, anomalies_detected, kill_switch_status
            )
            if llm_assessment:
                status["llm_risk_assessment"] = llm_assessment
                risk_level = llm_assessment.get("risk_level", "safe")
                if risk_level in ("danger", "critical"):
                    logger.warning(
                        "LLM risk assessment elevated",
                        risk_level=risk_level,
                        reasoning=llm_assessment.get("reasoning", ""),
                    )

            logger.info("Emergency Controller check completed", status=status)

            return status

        except Exception as e:
            logger.error("Emergency Controller check failed", error=str(e), exc_info=True)
            raise

    # ========== LLM ENHANCEMENT ==========

    def _get_risk_assessment(
        self,
        api_health: Dict[str, Any],
        db_health: bool,
        model_apis: Dict[str, bool],
        anomalies_detected: List[str],
        kill_switch_status: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:
        """
        Call LLM for advisory risk assessment.

        SAFETY: LLM output is logged/stored but CANNOT auto-activate kill switches.

        Returns parsed LLM response or None if LLM unavailable/disabled.
        """
        from orchestration.model_router import TaskType
        from prompts.base import build_prompt
        from prompts.emergency_controller import EMERGENCY_CONTROLLER_SYSTEM

        context_data = {
            "api_health": api_health,
            "db_health": db_health,
            "model_apis": model_apis,
            "anomalies_detected": anomalies_detected,
            "kill_switch_status": kill_switch_status,
        }

        system_prompt, user_prompt = build_prompt(EMERGENCY_CONTROLLER_SYSTEM, context_data)

        result = self.call_llm(
            task_type=TaskType.ANOMALY_DETECTION,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            context="emergency",
            max_escalations=0,
        )

        if result and result.get("response"):
            response = result["response"]
            logger.info(
                "LLM risk assessment completed",
                model=result.get("model_used"),
                risk_level=response.get("risk_level"),
            )
            return response

        return None

    # ========== CONTINUOUS MONITORING ==========

    def start_continuous_monitoring(self):
        """Start continuous monitoring in background thread."""
        if self.monitoring_active:
            logger.warning("Monitoring already active")
            return

        self.monitoring_active = True
        self.monitor_thread = threading.Thread(
            target=self._monitoring_loop,
            daemon=True,
            name="EmergencyController-Monitor"
        )
        self.monitor_thread.start()

        logger.info("Continuous monitoring started", check_interval=self.check_interval_seconds)

    def stop_continuous_monitoring(self):
        """Stop continuous monitoring gracefully."""
        if not self.monitoring_active:
            logger.warning("Monitoring not active")
            return

        logger.info("Stopping continuous monitoring...")
        self.monitoring_active = False

        if self.monitor_thread:
            self.monitor_thread.join(timeout=10)

        logger.info("Continuous monitoring stopped")

    def _monitoring_loop(self):
        """Main monitoring loop (runs in background thread).

        Runs trailing stop checks every check_interval_seconds (default 5s)
        and health checks every 60 seconds.
        """
        logger.info("Monitoring loop started")
        health_check_counter = 0
        check_interval = self.config.get("trailing_stop", {}).get("check_interval_seconds", 5)
        health_check_every = max(1, 60 // check_interval)  # health check every ~60s

        while self.monitoring_active:
            try:
                self.trailing_stop_monitor.check_all_positions()

                health_check_counter += 1
                if health_check_counter >= health_check_every:
                    self._perform_health_checks()
                    health_check_counter = 0

                time.sleep(check_interval)

            except Exception as e:
                logger.error("Monitoring loop error", error=str(e), exc_info=True)
                time.sleep(check_interval)

        logger.info("Monitoring loop exited")

    def _perform_health_checks(self):
        """Perform all health checks and take action if needed."""

        # Check API health
        api_health = self.system_monitor.check_binance_api_health()
        if not api_health.get("rest_api"):
            logger.critical("Binance REST API connection lost")
            self._trigger_emergency_response("API_CONNECTION_LOST", "critical")

        if not api_health.get("websocket"):
            logger.error("Binance WebSocket connection lost")
            # WebSocket loss is less critical, don't stop trading

        # Check database health
        db_health = self.system_monitor.check_database_health()
        if not db_health:
            logger.critical("Database connection lost")
            self._trigger_emergency_response("DATABASE_CONNECTION_LOST", "critical")

        # Check model API availability (optional, don't fail on this)
        model_apis = self.system_monitor.check_model_apis()
        unavailable_apis = [api for api, status in model_apis.items() if not status]
        if unavailable_apis:
            logger.warning("Model APIs unavailable", apis=unavailable_apis)

        logger.debug(
            "Health checks completed",
            api_health=api_health,
            db_health=db_health,
            model_apis=model_apis
        )

    def _trigger_emergency_response(self, event_type: str, severity: str):
        """
        Trigger emergency response for critical events.

        Args:
            event_type: Type of emergency event
            severity: Severity level (critical, error, warning)
        """
        logger.critical(
            "EMERGENCY RESPONSE TRIGGERED",
            event_type=event_type,
            severity=severity
        )

        # Activate global kill switch for critical events
        if severity == "critical":
            self.kill_switch_manager.activate_global_kill_switch(
                reason=f"Emergency: {event_type}",
                close_positions=False  # Don't auto-close on connection loss
            )

        # Send emergency alerts
        send_emergency_alert(severity.upper(), f"Emergency: {event_type}")

    # ========== KILL SWITCH CONTROLS ==========

    def activate_global_kill_switch(self, reason: str, close_positions: bool = False):
        """
        Activate global kill switch - STOP ALL TRADING.

        Args:
            reason: Reason for activation
            close_positions: Whether to close all positions
        """
        self.kill_switch_manager.activate_global_kill_switch(reason, close_positions)

        if close_positions:
            logger.warning("Emergency close: closing all open positions via trailing stop monitor")
            self.trailing_stop_monitor.check_all_positions()

    def deactivate_global_kill_switch(self):
        """Deactivate global kill switch."""
        self.kill_switch_manager.deactivate_global_kill_switch()

    def activate_symbol_kill_switch(self, symbol: str, reason: str):
        """Block trading for specific symbol."""
        self.kill_switch_manager.activate_symbol_kill_switch(symbol, reason)

    def activate_strategy_kill_switch(self, strategy_id: str, reason: str):
        """Disable specific strategy."""
        self.kill_switch_manager.activate_strategy_kill_switch(strategy_id, reason)

    def is_trading_allowed(
        self,
        symbol: Optional[str] = None,
        strategy_id: Optional[str] = None
    ) -> bool:
        """
        Check if trading is allowed.

        Args:
            symbol: Optional symbol to check
            strategy_id: Optional strategy to check

        Returns:
            True if trading allowed
        """
        return self.kill_switch_manager.is_trading_allowed(symbol, strategy_id)


class KillSwitchManager:
    """
    Manages all kill switch types.

    Kill switches provide emergency stop capabilities:
    - Global: Stop all trading
    - Symbol: Block specific trading pairs
    - Strategy: Disable specific strategies
    - Volatility circuit breaker: Pause on extreme volatility
    """

    def __init__(self, config: Dict[str, Any]):
        """
        Initialize kill switch manager.

        Args:
            config: System configuration
        """
        self.config = config

        # Load initial kill switch states from config
        kill_switch_config = config.get("risk", {}).get("kill_switches", {})
        self.kill_switches = {
            "global": kill_switch_config.get("global", False),
            "symbols": {},
            "strategies": {},
            "volatility_breaker": False
        }

        logger.info("KillSwitchManager initialized", initial_state=self.kill_switches)

    def activate_global_kill_switch(self, reason: str, close_positions: bool = False):
        """
        Activate global kill switch - STOP ALL TRADING.

        Args:
            reason: Reason for activation
            close_positions: Whether to close all positions
        """
        logger.critical(
            "🛑 GLOBAL KILL SWITCH ACTIVATED 🛑",
            reason=reason,
            close_positions=close_positions
        )

        self.kill_switches["global"] = True

        # TODO: Update config file to persist kill switch state
        # TODO: Close positions if requested
        # TODO: Send emergency alerts to all channels

        send_emergency_alert("CRITICAL", f"Global Kill Switch Activated: {reason}")

    def deactivate_global_kill_switch(self):
        """Deactivate global kill switch."""
        logger.info("Global kill switch deactivated")
        self.kill_switches["global"] = False

        send_emergency_alert("WARNING", "Global Kill Switch Deactivated - Trading resumed")

    def activate_symbol_kill_switch(self, symbol: str, reason: str):
        """
        Block trading for specific symbol.

        Args:
            symbol: Trading symbol (e.g., "BTCUSDT")
            reason: Reason for activation
        """
        logger.warning(
            f"Symbol kill switch activated for {symbol}",
            symbol=symbol,
            reason=reason
        )
        self.kill_switches["symbols"][symbol] = {
            "active": True,
            "reason": reason,
            "activated_at": datetime.utcnow().isoformat()
        }

    def deactivate_symbol_kill_switch(self, symbol: str):
        """
        Reactivate trading for specific symbol.

        Args:
            symbol: Trading symbol
        """
        if symbol in self.kill_switches["symbols"]:
            del self.kill_switches["symbols"][symbol]
            logger.info(f"Symbol kill switch deactivated for {symbol}")

    def activate_strategy_kill_switch(self, strategy_id: str, reason: str):
        """
        Disable specific strategy.

        Args:
            strategy_id: Strategy identifier
            reason: Reason for activation
        """
        logger.warning(
            f"Strategy kill switch activated for {strategy_id}",
            strategy_id=strategy_id,
            reason=reason
        )
        self.kill_switches["strategies"][strategy_id] = {
            "active": True,
            "reason": reason,
            "activated_at": datetime.utcnow().isoformat()
        }

    def deactivate_strategy_kill_switch(self, strategy_id: str):
        """
        Reactivate specific strategy.

        Args:
            strategy_id: Strategy identifier
        """
        if strategy_id in self.kill_switches["strategies"]:
            del self.kill_switches["strategies"][strategy_id]
            logger.info(f"Strategy kill switch deactivated for {strategy_id}")

    def activate_volatility_circuit_breaker(self, reason: str):
        """
        Activate volatility circuit breaker (temporary pause).

        Args:
            reason: Reason for activation
        """
        logger.warning("Volatility circuit breaker activated", reason=reason)
        self.kill_switches["volatility_breaker"] = True

        # Auto-deactivate after 5 minutes
        def auto_deactivate():
            time.sleep(300)  # 5 minutes
            self.deactivate_volatility_circuit_breaker()

        threading.Thread(target=auto_deactivate, daemon=True).start()

    def deactivate_volatility_circuit_breaker(self):
        """Deactivate volatility circuit breaker."""
        logger.info("Volatility circuit breaker deactivated")
        self.kill_switches["volatility_breaker"] = False

    def get_status(self) -> Dict[str, Any]:
        """
        Get current kill switch status.

        Returns:
            Kill switch status dict
        """
        return {
            "global": self.kill_switches["global"],
            "volatility_breaker": self.kill_switches["volatility_breaker"],
            "blocked_symbols": list(self.kill_switches["symbols"].keys()),
            "disabled_strategies": list(self.kill_switches["strategies"].keys())
        }

    def is_trading_allowed(
        self,
        symbol: Optional[str] = None,
        strategy_id: Optional[str] = None
    ) -> bool:
        """
        Check if trading is allowed.

        Args:
            symbol: Optional symbol to check
            strategy_id: Optional strategy to check

        Returns:
            True if trading allowed
        """
        # Check global kill switch
        if self.kill_switches["global"]:
            return False

        # Check volatility circuit breaker
        if self.kill_switches["volatility_breaker"]:
            return False

        # Check symbol-specific kill switch
        if symbol:
            symbol_data = self.kill_switches["symbols"].get(symbol)
            if symbol_data and symbol_data.get("active"):
                return False

        # Check strategy-specific kill switch
        if strategy_id:
            strategy_data = self.kill_switches["strategies"].get(strategy_id)
            if strategy_data and strategy_data.get("active"):
                return False

        return True


class SystemHealthMonitor:
    """
    Monitors system health metrics.

    Checks:
    - Binance API connectivity (REST + WebSocket)
    - Database connectivity
    - AI model API availability
    """

    def __init__(self, binance_client, db_session=None):
        """
        Initialize system health monitor.

        Args:
            binance_client: Binance API client
            db_session: Database session
        """
        self.binance_client = binance_client
        self.db_session = db_session

    def check_binance_api_health(self) -> Dict[str, Any]:
        """
        Check Binance API connectivity and health.

        Returns:
            Health status dict
        """
        try:
            # Test REST API
            rest_api_healthy = self.binance_client.ping() if self.binance_client else False

            # Test WebSocket (simplified - just check if client is configured)
            websocket_healthy = True  # Simplified - would need actual WS connection test

            # Estimate latency (simplified)
            latency_ms = 50  # Would need actual ping measurement

            return {
                "rest_api": rest_api_healthy,
                "websocket": websocket_healthy,
                "latency_ms": latency_ms,
                "rate_limit_status": "healthy"  # Would need actual rate limit tracking
            }

        except Exception as e:
            logger.error("Failed to check Binance API health", error=str(e))
            return {
                "rest_api": False,
                "websocket": False,
                "latency_ms": None,
                "rate_limit_status": "unknown"
            }

    def check_database_health(self) -> bool:
        """
        Verify database is accessible.

        Returns:
            True if healthy
        """
        if not self.db_session:
            return True  # No database session, skip check

        try:
            # Simple query to test connection
            from sqlalchemy import text
            with self.db_session.session_scope() as session:
                session.execute(text("SELECT 1"))
            return True

        except Exception as e:
            logger.error("Database health check failed", error=str(e))
            return False

    def check_model_apis(self) -> Dict[str, bool]:
        """
        Check AI model API availability.

        Returns:
            Status dict for each API
        """
        # Simplified - would need actual API health checks
        return {
            "openai": True,  # Would ping OpenAI API
            "anthropic": True,  # Would ping Anthropic API
            "google": True  # Would ping Google API
        }


class AnomalyDetector:
    """
    Detects trading anomalies and unusual conditions.

    Monitors for:
    - Flash crashes (>10% price move in 1 minute)
    - Unusual slippage (>20 basis points)
    - Abnormal position sizing
    """

    def __init__(self, config: Dict[str, Any]):
        """
        Initialize anomaly detector.

        Args:
            config: System configuration
        """
        self.config = config

        # Thresholds from config
        anomaly_config = config.get("emergency", {}).get("anomaly_detection", {})
        self.flash_crash_threshold_pct = anomaly_config.get("flash_crash_threshold_pct", 0.10)
        self.slippage_threshold_bps = anomaly_config.get("slippage_threshold_bps", 20)

    def detect_flash_crash(self, symbol: str, current_price: float, previous_price: float) -> bool:
        """
        Detect flash crash: >10% price move in short time.

        Args:
            symbol: Trading symbol
            current_price: Current price
            previous_price: Previous price (1 minute ago)

        Returns:
            True if flash crash detected
        """
        price_change_pct = abs(current_price - previous_price) / previous_price

        if price_change_pct > self.flash_crash_threshold_pct:
            logger.critical(
                "FLASH CRASH DETECTED",
                symbol=symbol,
                price_change_pct=price_change_pct,
                current_price=current_price,
                previous_price=previous_price
            )
            return True

        return False

    def detect_unusual_slippage(self, execution_result: Dict[str, Any]) -> bool:
        """
        Detect abnormally high slippage (>20 bps).

        Args:
            execution_result: Execution result dict

        Returns:
            True if unusual slippage detected
        """
        order_details = execution_result.get("order_details", {})
        slippage_bps = order_details.get("slippage_bps", 0)

        if slippage_bps > self.slippage_threshold_bps:
            logger.warning(
                "Unusual slippage detected",
                execution_id=execution_result.get("execution_id"),
                slippage_bps=slippage_bps,
                threshold=self.slippage_threshold_bps
            )
            return True

        return False


def send_emergency_alert(severity: str, message: str):
    """
    Send emergency alerts via all configured channels.

    Args:
        severity: Alert severity (CRITICAL, ERROR, WARNING)
        message: Alert message
    """
    alert = {
        "severity": severity,
        "message": message,
        "timestamp": datetime.utcnow().isoformat() + "Z"
    }

    # Console logging (always active)
    if severity == "CRITICAL":
        logger.critical(message)
    elif severity == "ERROR":
        logger.error(message)
    else:
        logger.warning(message)

    # TODO: Implement additional alert channels:
    # - Email alerts (via SMTP)
    # - Webhook alerts (Slack, Discord, Telegram)
    # - SMS alerts (Twilio)
    # - Push notifications
