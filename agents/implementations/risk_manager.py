"""
Risk Manager Agent Implementation

Global authority agent with power to approve, reject, or modify all trades.
"""

from typing import Dict, Any, Optional, Tuple
from datetime import datetime
import uuid
import structlog

from .base_agent import BaseAgent
from orchestration.state_manager import TradingState

logger = structlog.get_logger()


class RiskManagerAgent(BaseAgent):
    """
    Risk Manager: Global authority over all trading decisions.

    Has FULL AUTHORITY to:
    - Approve trades
    - Reject trades
    - Modify trade parameters (size, leverage, stops)
    - Activate kill switches
    """

    def __init__(self, config: Dict[str, Any], binance_client=None):
        super().__init__("risk-manager", config)

        self.binance_client = binance_client

        # Load risk limits from config
        risk_config = config.get("risk", {})
        self.max_risk_per_trade_pct = risk_config.get("max_risk_per_trade_pct", 0.02)
        self.max_daily_drawdown_pct = risk_config.get("max_daily_drawdown_pct", 0.05)
        self.max_portfolio_exposure_pct = risk_config.get("max_portfolio_exposure_pct", 0.70)
        self.max_position_concentration_pct = risk_config.get("max_position_concentration_pct", 0.30)
        self.max_correlated_positions = risk_config.get("max_correlated_positions", 3)
        self.volatility_gate_threshold = risk_config.get("volatility_gate_threshold_pct", 0.05)

        # Leverage limits
        leverage_limits = risk_config.get("leverage_limits", {})
        self.leverage_low_vol = leverage_limits.get("low_volatility", 10)
        self.leverage_med_vol = leverage_limits.get("medium_volatility", 7)
        self.leverage_high_vol = leverage_limits.get("high_volatility", 5)

        # Position sizing
        pos_sizing = risk_config.get("position_sizing", {})
        self.position_sizing_method = pos_sizing.get("method", "kelly_criterion")
        self.kelly_fraction = pos_sizing.get("kelly_fraction", 0.5)
        self.atr_multiplier = pos_sizing.get("atr_multiplier", 2.0)

        # Kill switches
        self.kill_switches = risk_config.get("kill_switches", {})

        # Track daily metrics
        self.daily_start_equity = None
        self.daily_pnl = 0.0

    def execute(self, state: TradingState) -> Dict[str, Any]:
        """
        Execute risk evaluation and return approval decision.

        Args:
            state: Trading state with trading_decision

        Returns:
            Risk approval conforming to risk_approval schema
        """
        start_time = datetime.utcnow()

        trading_decision = state.get("trading_decision")
        if not trading_decision:
            raise ValueError("No trading decision in state")

        research_summary = state.get("research_summary")
        if not research_summary:
            raise ValueError("No research summary in state")

        symbol = trading_decision["symbol"]
        decision = trading_decision["decision"]

        self.logger.info(
            "Starting risk evaluation",
            symbol=symbol,
            decision=decision,
            correlation_id=state.get("correlation_id")
        )

        # Check if NO_TRADE (skip risk checks)
        if decision == "NO_TRADE":
            return self._create_risk_approval(
                trading_decision,
                approval_status="REJECTED",
                rejection_reason="Trading decision was NO_TRADE",
                risk_checks={},
                account_status={}
            )

        # Get account status
        account_status = self._get_account_status()

        # Initialize daily equity tracking
        if self.daily_start_equity is None:
            self.daily_start_equity = account_status["total_equity"]

        # Run all risk checks
        risk_checks = self._run_all_risk_checks(
            trading_decision,
            research_summary,
            account_status
        )

        # Check for critical failures
        critical_failures = [
            check for check in risk_checks.values()
            if isinstance(check, dict) and not check.get("passed") and check.get("severity") == "critical"
        ]

        if critical_failures:
            # Reject trade
            rejection_reason = "; ".join([
                f"{name}: {check.get('message', 'Failed')}"
                for name, check in risk_checks.items()
                if isinstance(check, dict) and not check.get("passed")
            ])

            return self._create_risk_approval(
                trading_decision,
                approval_status="REJECTED",
                rejection_reason=rejection_reason,
                risk_checks=risk_checks,
                account_status=account_status
            )

        # Calculate position sizing
        position_sizing = self._calculate_position_sizing(
            trading_decision,
            research_summary,
            account_status
        )

        # Check if modifications are needed
        needs_modification, modified_params = self._check_modifications_needed(
            trading_decision,
            research_summary,
            risk_checks,
            position_sizing,
            account_status
        )

        processing_time_ms = (datetime.utcnow() - start_time).total_seconds() * 1000

        if needs_modification:
            self.logger.info(
                "Trade approved with modifications",
                modifications=modified_params,
                processing_time_ms=processing_time_ms
            )

            return self._create_risk_approval(
                trading_decision,
                approval_status="MODIFIED",
                modified_parameters=modified_params,
                risk_checks=risk_checks,
                account_status=account_status,
                position_sizing=position_sizing,
                processing_time_ms=processing_time_ms
            )
        else:
            self.logger.info(
                "Trade approved as-is",
                processing_time_ms=processing_time_ms
            )

            return self._create_risk_approval(
                trading_decision,
                approval_status="APPROVED",
                risk_checks=risk_checks,
                account_status=account_status,
                position_sizing=position_sizing,
                processing_time_ms=processing_time_ms
            )

    def _get_account_status(self) -> Dict[str, Any]:
        """
        Get current account status.

        Returns:
            Account status dict
        """
        if self.binance_client:
            try:
                balance = self.binance_client.get_account_balance()
                positions = self.binance_client.get_positions()

                # Calculate current exposure
                current_exposure = sum(
                    abs(pos["position_amount"] * pos["entry_price"])
                    for pos in positions
                )

                # Calculate daily drawdown
                daily_drawdown_pct = 0.0
                if self.daily_start_equity:
                    daily_drawdown = self.daily_start_equity - balance["total_equity"]
                    daily_drawdown_pct = daily_drawdown / self.daily_start_equity

                return {
                    "available_balance": balance["available_balance"],
                    "total_equity": balance["total_equity"],
                    "current_exposure": current_exposure,
                    "unrealized_pnl": balance["unrealized_pnl"],
                    "daily_pnl": self.daily_pnl,
                    "daily_drawdown_pct": daily_drawdown_pct,
                    "open_positions_count": len(positions)
                }
            except Exception as e:
                self.logger.error("Failed to get account status", error=str(e))

        # Fallback to mock data
        return {
            "available_balance": 10000.0,
            "total_equity": 10000.0,
            "current_exposure": 0.0,
            "unrealized_pnl": 0.0,
            "daily_pnl": 0.0,
            "daily_drawdown_pct": 0.0,
            "open_positions_count": 0
        }

    def _run_all_risk_checks(
        self,
        trading_decision: Dict[str, Any],
        research_summary: Dict[str, Any],
        account_status: Dict[str, Any]
    ) -> Dict[str, Dict[str, Any]]:
        """
        Run all risk validation checks.

        Args:
            trading_decision: Trading decision
            research_summary: Research summary
            account_status: Account status

        Returns:
            Dict of risk check results
        """
        risk_checks = {}

        # 1. Check kill switches
        risk_checks["kill_switches"] = self._check_kill_switches(
            trading_decision["symbol"],
            trading_decision.get("strategy_id")
        )

        # 2. Max daily drawdown
        risk_checks["max_daily_drawdown"] = self._check_daily_drawdown(account_status)

        # 3. Max risk per trade
        risk_checks["max_risk_per_trade"] = self._check_risk_per_trade(
            trading_decision,
            account_status
        )

        # 4. Max portfolio exposure
        risk_checks["max_portfolio_exposure"] = self._check_portfolio_exposure(
            trading_decision,
            account_status
        )

        # 5. Leverage limits
        risk_checks["leverage_limit"] = self._check_leverage_limit(
            trading_decision,
            research_summary
        )

        # 6. Volatility gate
        risk_checks["volatility_gate"] = self._check_volatility_gate(research_summary)

        # 7. Position concentration
        risk_checks["position_concentration"] = self._check_position_concentration(
            trading_decision,
            account_status
        )

        # 8. Available margin
        risk_checks["available_margin"] = self._check_available_margin(
            trading_decision,
            account_status
        )

        return risk_checks

    def _check_kill_switches(self, symbol: str, strategy_id: Optional[str]) -> Dict[str, Any]:
        """Check if kill switches allow trading."""
        # Global kill switch
        if self.kill_switches.get("global", False):
            return {
                "passed": False,
                "current_value": True,
                "limit": False,
                "severity": "critical",
                "message": "Global kill switch is active"
            }

        # Symbol kill switch
        if self.kill_switches.get("symbols", {}).get(symbol, False):
            return {
                "passed": False,
                "current_value": True,
                "limit": False,
                "severity": "critical",
                "message": f"Symbol kill switch active for {symbol}"
            }

        # Strategy kill switch
        if strategy_id and self.kill_switches.get("strategies", {}).get(strategy_id, False):
            return {
                "passed": False,
                "current_value": True,
                "limit": False,
                "severity": "critical",
                "message": f"Strategy kill switch active for {strategy_id}"
            }

        return {
            "passed": True,
            "current_value": False,
            "limit": False,
            "message": "All kill switches disabled"
        }

    def _check_daily_drawdown(self, account_status: Dict[str, Any]) -> Dict[str, Any]:
        """Check daily drawdown limit."""
        daily_drawdown_pct = account_status["daily_drawdown_pct"]

        return {
            "passed": daily_drawdown_pct <= self.max_daily_drawdown_pct,
            "current_value": daily_drawdown_pct,
            "limit": self.max_daily_drawdown_pct,
            "severity": "critical" if daily_drawdown_pct > self.max_daily_drawdown_pct else "info",
            "message": f"Daily drawdown: {daily_drawdown_pct:.2%}"
        }

    def _check_risk_per_trade(
        self,
        trading_decision: Dict[str, Any],
        account_status: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Check risk per trade limit."""
        entry_price = trading_decision["entry_price"]
        stop_loss = trading_decision["stop_loss"]
        position_size = trading_decision["position_size_usdt"]

        # Calculate risk amount
        stop_distance_pct = abs(entry_price - stop_loss) / entry_price
        risk_amount_usdt = position_size * stop_distance_pct
        risk_pct = risk_amount_usdt / account_status["total_equity"]

        return {
            "passed": risk_pct <= self.max_risk_per_trade_pct,
            "current_value": risk_pct,
            "limit": self.max_risk_per_trade_pct,
            "severity": "warning" if risk_pct > self.max_risk_per_trade_pct else "info",
            "message": f"Risk per trade: {risk_pct:.2%}"
        }

    def _check_portfolio_exposure(
        self,
        trading_decision: Dict[str, Any],
        account_status: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Check portfolio exposure limit."""
        current_exposure = account_status["current_exposure"]
        new_position_size = trading_decision["position_size_usdt"]
        total_exposure = current_exposure + new_position_size
        exposure_pct = total_exposure / account_status["total_equity"]

        return {
            "passed": exposure_pct <= self.max_portfolio_exposure_pct,
            "current_value": exposure_pct,
            "limit": self.max_portfolio_exposure_pct,
            "severity": "warning" if exposure_pct > self.max_portfolio_exposure_pct else "info",
            "message": f"Portfolio exposure: {exposure_pct:.2%}"
        }

    def _check_leverage_limit(
        self,
        trading_decision: Dict[str, Any],
        research_summary: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Check leverage limit based on volatility."""
        requested_leverage = trading_decision["leverage"]
        volatility_pct = research_summary["technical_indicators"].get("volatility_pct", 3.0)

        # Determine max leverage based on volatility
        if volatility_pct < 2.0:
            max_leverage = self.leverage_low_vol
        elif volatility_pct < 5.0:
            max_leverage = self.leverage_med_vol
        else:
            max_leverage = self.leverage_high_vol

        return {
            "passed": requested_leverage <= max_leverage,
            "current_value": requested_leverage,
            "limit": max_leverage,
            "severity": "warning" if requested_leverage > max_leverage else "info",
            "message": f"Leverage: {requested_leverage}x (max: {max_leverage}x at {volatility_pct:.1f}% vol)"
        }

    def _check_volatility_gate(self, research_summary: Dict[str, Any]) -> Dict[str, Any]:
        """Check volatility gate threshold."""
        volatility_pct = research_summary["technical_indicators"].get("volatility_pct", 0.0) / 100

        return {
            "passed": volatility_pct <= self.volatility_gate_threshold,
            "current_value": volatility_pct,
            "limit": self.volatility_gate_threshold,
            "severity": "critical" if volatility_pct > self.volatility_gate_threshold else "info",
            "message": f"Volatility: {volatility_pct:.2%}"
        }

    def _check_position_concentration(
        self,
        trading_decision: Dict[str, Any],
        account_status: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Check position concentration limit."""
        position_size = trading_decision["position_size_usdt"]
        concentration_pct = position_size / account_status["total_equity"]

        return {
            "passed": concentration_pct <= self.max_position_concentration_pct,
            "current_value": concentration_pct,
            "limit": self.max_position_concentration_pct,
            "severity": "warning" if concentration_pct > self.max_position_concentration_pct else "info",
            "message": f"Position concentration: {concentration_pct:.2%}"
        }

    def _check_available_margin(
        self,
        trading_decision: Dict[str, Any],
        account_status: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Check available margin with buffer."""
        position_size = trading_decision["position_size_usdt"]
        leverage = trading_decision["leverage"]
        required_margin = (position_size / leverage) * 1.2  # 20% buffer

        available = account_status["available_balance"]

        return {
            "passed": available >= required_margin,
            "current_value": available,
            "limit": required_margin,
            "severity": "critical" if available < required_margin else "info",
            "message": f"Available margin: ${available:.2f} (required: ${required_margin:.2f})"
        }

    def _calculate_position_sizing(
        self,
        trading_decision: Dict[str, Any],
        research_summary: Dict[str, Any],
        account_status: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Calculate optimal position sizing."""
        entry_price = trading_decision["entry_price"]
        stop_loss = trading_decision["stop_loss"]
        risk_metrics = trading_decision.get("risk_metrics", {})

        # Stop loss distance
        stop_distance_pct = abs(entry_price - stop_loss) / entry_price

        # Kelly Criterion sizing
        if self.position_sizing_method == "kelly_criterion":
            win_prob = risk_metrics.get("win_probability", 0.5)
            rr_ratio = risk_metrics.get("risk_reward_ratio", 1.5)

            # Kelly formula: (win_prob * rr_ratio - (1 - win_prob)) / rr_ratio
            kelly_pct = max(0, (win_prob * rr_ratio - (1 - win_prob)) / rr_ratio)
            kelly_fraction_adjusted = kelly_pct * self.kelly_fraction

            risk_amount = account_status["total_equity"] * min(kelly_fraction_adjusted, self.max_risk_per_trade_pct)
        else:
            # Fixed percentage
            risk_amount = account_status["total_equity"] * self.max_risk_per_trade_pct

        # Calculate position size
        position_size = risk_amount / stop_distance_pct

        # Cap at max concentration
        max_size = account_status["total_equity"] * self.max_position_concentration_pct
        position_size = min(position_size, max_size)

        return {
            "method": self.position_sizing_method,
            "kelly_fraction": self.kelly_fraction if self.position_sizing_method == "kelly_criterion" else None,
            "risk_per_trade_usdt": risk_amount,
            "risk_per_trade_pct": risk_amount / account_status["total_equity"],
            "stop_loss_distance_pct": stop_distance_pct,
            "calculated_position_size_usdt": round(position_size, 2)
        }

    def _check_modifications_needed(
        self,
        trading_decision: Dict[str, Any],
        research_summary: Dict[str, Any],
        risk_checks: Dict[str, Any],
        position_sizing: Dict[str, Any],
        account_status: Dict[str, Any]
    ) -> Tuple[bool, Dict[str, Any]]:
        """
        Check if modifications are needed and return modified parameters.

        Returns:
            Tuple of (needs_modification, modified_params)
        """
        modified_params = {}
        needs_modification = False

        # Check position size
        requested_size = trading_decision["position_size_usdt"]
        calculated_size = position_sizing["calculated_position_size_usdt"]

        if calculated_size < requested_size * 0.95:  # More than 5% reduction
            modified_params["position_size_usdt"] = calculated_size
            needs_modification = True

        # Check leverage
        leverage_check = risk_checks.get("leverage_limit", {})
        if not leverage_check.get("passed"):
            modified_params["leverage"] = leverage_check["limit"]
            needs_modification = True

        # Check exposure
        exposure_check = risk_checks.get("max_portfolio_exposure", {})
        if not exposure_check.get("passed"):
            # Reduce size to fit exposure limit
            current_exposure = account_status["current_exposure"]
            max_new_size = (account_status["total_equity"] * self.max_portfolio_exposure_pct) - current_exposure
            modified_params["position_size_usdt"] = min(
                modified_params.get("position_size_usdt", requested_size),
                max_new_size
            )
            needs_modification = True

        return needs_modification, modified_params

    def _create_risk_approval(
        self,
        trading_decision: Dict[str, Any],
        approval_status: str,
        risk_checks: Dict[str, Any],
        account_status: Dict[str, Any],
        rejection_reason: Optional[str] = None,
        modified_parameters: Optional[Dict[str, Any]] = None,
        position_sizing: Optional[Dict[str, Any]] = None,
        processing_time_ms: float = 0.0
    ) -> Dict[str, Any]:
        """Create risk approval object."""
        approval = {
            "id": str(uuid.uuid4()),
            "decision_id": trading_decision["decision_id"],
            "approval_status": approval_status,
            "risk_checks": risk_checks,
            "account_status": account_status,
            "kill_switches": {
                "global_trading_enabled": not self.kill_switches.get("global", False),
                "symbol_enabled": not self.kill_switches.get("symbols", {}).get(trading_decision["symbol"], False),
                "strategy_enabled": True,  # Simplified for now
                "volatility_breaker_active": False
            },
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "processing_time_ms": processing_time_ms
        }

        if rejection_reason:
            approval["rejection_reason"] = rejection_reason

        if modified_parameters:
            approval["modified_parameters"] = modified_parameters

        if position_sizing:
            approval["position_sizing"] = position_sizing

        # Validate output
        self.validate_output(approval, "risk_approval")

        return approval
