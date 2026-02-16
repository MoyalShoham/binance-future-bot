"""
Risk Management Agent - GLOBAL AUTHORITY

This agent has override authority over all trades.
NO other agent may bypass it.

Core Principle: SAFETY OVER SPEED, DETERMINISM OVER CLEVERNESS
"""

from typing import Dict, Any, Optional, Tuple
from datetime import datetime, timedelta
import uuid
import structlog
import math

from .base_agent import BaseAgent
from infrastructure.database import DatabaseQueries, PnLLedger
from schemas.validator import SchemaValidator

logger = structlog.get_logger()


class RiskManagerAgent(BaseAgent):
    """
    Risk Management Agent - Final Gatekeeper

    Authority: GLOBAL - Can approve/reject/modify ALL trades

    Design Principles:
    - Rule-based validation (no LLM for core risk checks)
    - Stateless (portfolio state from database)
    - Fail-safe defaults (when uncertain, be conservative)
    - Observable (log ALL decisions with reasoning)
    - Deterministic (same inputs = same output)

    Responsibilities:
    1. Pre-Trade Risk Validation (8 checks)
    2. Portfolio-Level Controls
    3. Market Safety Controls
    4. Kill Switch Enforcement
    5. Position Sizing (Kelly Criterion, ATR-based)

    Outputs:
    - APPROVED: Trade as-is
    - REJECTED: Block trade entirely
    - MODIFIED: Approve with adjusted parameters
    """

    def __init__(
        self,
        agent_id: str,
        config: Dict[str, Any],
        binance_client,
        db_session
    ):
        """
        Initialize Risk Manager Agent.

        Args:
            agent_id: Agent identifier
            config: System configuration
            binance_client: Binance API client (for account data)
            db_session: Database session (for portfolio state)
        """
        super().__init__(agent_id, config)

        self.binance_client = binance_client
        self.db_session = db_session
        self.queries = DatabaseQueries(db_session.get_session())
        self.validator = SchemaValidator()

        # Load risk limits from config
        risk_config = config.get("risk", {})
        self.max_risk_per_trade_pct = risk_config.get("max_risk_per_trade_pct", 0.02)
        self.max_daily_drawdown_pct = risk_config.get("max_daily_drawdown_pct", 0.05)
        self.max_portfolio_exposure_pct = risk_config.get("max_portfolio_exposure_pct", 0.70)
        self.max_position_concentration_pct = risk_config.get("max_position_concentration_pct", 0.30)
        self.max_correlated_positions = risk_config.get("max_correlated_positions", 3)
        self.volatility_gate_threshold_pct = risk_config.get("volatility_gate_threshold_pct", 0.05)

        # Leverage limits (volatility-adjusted)
        leverage_limits = risk_config.get("leverage_limits", {})
        self.leverage_low_vol = leverage_limits.get("low_volatility", 10)
        self.leverage_med_vol = leverage_limits.get("medium_volatility", 7)
        self.leverage_high_vol = leverage_limits.get("high_volatility", 5)

        # Position sizing config
        sizing_config = risk_config.get("position_sizing", {})
        self.sizing_method = sizing_config.get("method", "kelly_criterion")
        self.kelly_fraction = sizing_config.get("kelly_fraction", 0.5)
        self.atr_multiplier = sizing_config.get("atr_multiplier", 2.0)

        # Kill switches
        kill_switches = risk_config.get("kill_switches", {})
        self.global_kill_switch = kill_switches.get("global", False)
        self.symbol_kill_switches = kill_switches.get("symbols", {})
        self.strategy_kill_switches = kill_switches.get("strategies", {})

        # Volatility circuit breaker
        vol_breaker = kill_switches.get("volatility_circuit_breaker", {})
        self.vol_breaker_enabled = vol_breaker.get("enabled", True)
        self.vol_breaker_trigger_pct = vol_breaker.get("trigger_pct", 0.10)
        self.vol_breaker_cooldown_seconds = vol_breaker.get("cooldown_seconds", 300)

        # Multi-position support
        trading_config = config.get("trading", {})
        self.max_concurrent_positions = trading_config.get("max_concurrent_positions", 3)
        self.per_position_exposure_pct = self.max_portfolio_exposure_pct / self.max_concurrent_positions

        # Funding rate filter
        self.max_funding_rate_pct = risk_config.get("max_funding_rate_pct", 0.0005)

        # Consecutive loss cooldown
        cooldown_config = risk_config.get("consecutive_loss_cooldown", {})
        self.max_consecutive_losses = cooldown_config.get("max_losses", 3)
        self.loss_lookback_minutes = cooldown_config.get("lookback_minutes", 30)

        # Check paper trading config
        paper_config = config.get("execution", {}).get("paper_trading", {})
        simulated_balance_config = paper_config.get("simulated_balance_usdt", 0)

        logger.debug(
            "Risk Manager Agent initialized (GLOBAL AUTHORITY)",
            agent_id=self.agent_id,
            max_risk_per_trade_pct=self.max_risk_per_trade_pct,
            max_daily_drawdown_pct=self.max_daily_drawdown_pct,
            max_portfolio_exposure_pct=self.max_portfolio_exposure_pct,
            simulated_balance_usdt=simulated_balance_config
        )

    def execute(self, state: Dict[str, Any]) -> Dict[str, Any]:
        """
        Main execution: Evaluate trading decision and approve/reject/modify.

        Args:
            state: Trading pipeline state with trading_decision and research_summary

        Returns:
            risk_approval object (APPROVED/REJECTED/MODIFIED)
        """
        logger.debug(
            "Risk Manager evaluation started",
            correlation_id=state.get("correlation_id")
        )

        start_time = datetime.utcnow()

        try:
            # Extract inputs
            trading_decision = state.get("trading_decision")
            research_summary = state.get("research_summary")

            if not trading_decision:
                return self._reject("Missing trading_decision in pipeline state")

            # If decision is NO_TRADE, skip risk checks
            if trading_decision.get("decision") == "NO_TRADE":
                return self._approve_no_trade(trading_decision)

            # Get current account status
            account_status = self._get_account_status()

            # Run all risk checks
            risk_checks = self._run_all_risk_checks(
                trading_decision,
                research_summary,
                account_status
            )

            # Determine approval status
            approval_status, modified_params = self._determine_approval(
                risk_checks,
                trading_decision,
                research_summary,
                account_status
            )

            # Calculate position sizing
            position_sizing = self._calculate_position_sizing(
                trading_decision,
                research_summary,
                account_status
            )

            # Build risk approval response
            risk_approval = self._build_risk_approval(
                approval_status=approval_status,
                decision_id=trading_decision["decision_id"],
                correlation_id=state.get("correlation_id"),
                risk_checks=risk_checks,
                position_sizing=position_sizing,
                account_status=account_status,
                modified_parameters=modified_params,
                processing_time_ms=(datetime.utcnow() - start_time).total_seconds() * 1000
            )

            # Log decision
            logger.debug(
                "Risk Manager decision",
                correlation_id=state.get("correlation_id"),
                approval_status=approval_status,
                decision=trading_decision.get("decision"),
                symbol=trading_decision.get("symbol"),
                checks_passed=sum(1 for c in risk_checks.values() if c.get("passed")),
                checks_total=len(risk_checks)
            )

            return risk_approval

        except Exception as e:
            logger.error(
                "Risk Manager execution failed",
                correlation_id=state.get("correlation_id"),
                error=str(e),
                exc_info=True
            )
            # FAIL SAFE: Reject on error
            return self._reject(f"Risk Manager internal error: {str(e)}")

    # ========== RISK CHECKS (RULE-BASED) ==========

    def _run_all_risk_checks(
        self,
        trading_decision: Dict[str, Any],
        research_summary: Optional[Dict[str, Any]],
        account_status: Dict[str, Any]
    ) -> Dict[str, Dict[str, Any]]:
        """
        Run all 8 risk validation checks.

        Returns:
            Dict of risk check results (each with passed, current_value, limit, message)
        """
        risk_checks = {}

        # Check 1: Kill Switches (HIGHEST PRIORITY)
        risk_checks["kill_switches"] = self._check_kill_switches(
            trading_decision.get("symbol"),
            trading_decision.get("strategy_id")
        )

        # Check 2: Max Daily Drawdown
        risk_checks["max_daily_drawdown"] = self._check_daily_drawdown(account_status)

        # Check 3: Max Risk Per Trade
        risk_checks["max_risk_per_trade"] = self._check_risk_per_trade(
            trading_decision,
            account_status
        )

        # Check 4: Max Portfolio Exposure
        risk_checks["max_portfolio_exposure"] = self._check_portfolio_exposure(
            trading_decision,
            account_status
        )

        # Check 5: Leverage Limit (volatility-adjusted)
        risk_checks["leverage_limit"] = self._check_leverage_limit(
            trading_decision,
            research_summary
        )

        # Check 6: Correlation Check
        risk_checks["correlation_check"] = self._check_correlation_limit(
            trading_decision,
            account_status
        )

        # Check 7: Volatility Gate
        risk_checks["volatility_gate"] = self._check_volatility_gate(research_summary)

        # Check 8: Position Concentration
        risk_checks["position_concentration"] = self._check_position_concentration(
            trading_decision,
            account_status
        )

        # Check 9: Available Margin
        risk_checks["available_margin"] = self._check_available_margin(
            trading_decision,
            account_status
        )

        # Check 10: Duplicate Position (same symbol already open on Binance)
        risk_checks["duplicate_position"] = self._check_duplicate_position(
            trading_decision.get("symbol")
        )

        # Check 11: Funding Rate Filter (block trades that pay expensive funding)
        risk_checks["funding_rate"] = self._check_funding_rate(
            trading_decision,
            research_summary
        )

        # Check 12: Consecutive Loss Cooldown
        risk_checks["consecutive_loss_cooldown"] = self._check_consecutive_losses(
            trading_decision.get("symbol")
        )

        return risk_checks

    def _check_kill_switches(
        self,
        symbol: str,
        strategy_id: str
    ) -> Dict[str, Any]:
        """Check 1: Kill Switches - HIGHEST PRIORITY"""

        # Global kill switch
        if self.global_kill_switch:
            return {
                "passed": False,
                "current_value": 1,
                "limit": 0,
                "severity": "critical",
                "message": "GLOBAL KILL SWITCH ACTIVE - All trading disabled"
            }

        # Symbol-specific kill switch
        if self.symbol_kill_switches.get(symbol, False):
            return {
                "passed": False,
                "current_value": 1,
                "limit": 0,
                "severity": "critical",
                "message": f"Symbol kill switch active for {symbol}"
            }

        # Strategy-specific kill switch
        if self.strategy_kill_switches.get(strategy_id, False):
            return {
                "passed": False,
                "current_value": 1,
                "limit": 0,
                "severity": "critical",
                "message": f"Strategy kill switch active for {strategy_id}"
            }

        return {
            "passed": True,
            "current_value": 0,
            "limit": 0,
            "message": "All kill switches cleared"
        }

    def _check_daily_drawdown(
        self,
        account_status: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Check 2: Max Daily Drawdown"""

        daily_drawdown_pct = account_status.get("daily_drawdown_pct", 0.0)

        # Absolute value for comparison
        abs_drawdown = abs(daily_drawdown_pct)

        passed = abs_drawdown <= self.max_daily_drawdown_pct

        return {
            "passed": passed,
            "current_value": abs_drawdown,
            "limit": self.max_daily_drawdown_pct,
            "severity": "critical" if not passed else "info",
            "message": (
                f"Daily drawdown {abs_drawdown:.2%} exceeds limit {self.max_daily_drawdown_pct:.2%}"
                if not passed
                else f"Daily drawdown within limit ({abs_drawdown:.2%} / {self.max_daily_drawdown_pct:.2%})"
            )
        }

    def _check_risk_per_trade(
        self,
        trading_decision: Dict[str, Any],
        account_status: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Check 3: Max Risk Per Trade"""

        entry_price = trading_decision.get("entry_price", 0)
        stop_loss = trading_decision.get("stop_loss", 0)
        position_size_usdt = trading_decision.get("position_size_usdt", 0)
        total_equity = max(account_status.get("total_equity", 1), 0.01)

        # Calculate risk
        if entry_price == 0 or stop_loss == 0:
            return {
                "passed": False,
                "current_value": 0,
                "limit": self.max_risk_per_trade_pct,
                "severity": "warning",
                "message": "Invalid entry_price or stop_loss (zero values)"
            }

        stop_distance_pct = abs((entry_price - stop_loss) / entry_price)
        risk_usdt = position_size_usdt * stop_distance_pct
        risk_pct = risk_usdt / total_equity

        passed = risk_pct <= self.max_risk_per_trade_pct

        return {
            "passed": passed,
            "current_value": risk_pct,
            "limit": self.max_risk_per_trade_pct,
            "severity": "warning" if not passed else "info",
            "message": (
                f"Risk per trade {risk_pct:.2%} exceeds limit {self.max_risk_per_trade_pct:.2%}"
                if not passed
                else f"Risk per trade within limit ({risk_pct:.2%} / {self.max_risk_per_trade_pct:.2%})"
            ),
            "risk_usdt": risk_usdt,
            "stop_distance_pct": stop_distance_pct
        }

    def _check_portfolio_exposure(
        self,
        trading_decision: Dict[str, Any],
        account_status: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Check 4: Max Portfolio Exposure

        current_exposure is already in margin units (notional / leverage).
        new_position_size is notional — convert to margin before comparing.
        Also enforce per-position cap (max_exposure / max_concurrent_positions).
        """
        current_exposure = account_status.get("current_exposure", 0)
        total_equity = max(account_status.get("total_equity", 1), 0.01)
        new_position_notional = trading_decision.get("position_size_usdt", 0)
        leverage = trading_decision.get("leverage", self.config.get("trading", {}).get("default_leverage", 3))

        # Convert notional to margin for apples-to-apples comparison
        new_position_margin = new_position_notional / leverage

        # Check per-position cap
        per_position_pct = new_position_margin / total_equity
        per_position_limit = self.per_position_exposure_pct
        if per_position_pct > per_position_limit:
            return {
                "passed": False,
                "current_value": per_position_pct,
                "limit": per_position_limit,
                "severity": "warning",
                "message": (
                    f"Single position margin {per_position_pct:.2%} exceeds per-position limit "
                    f"{per_position_limit:.2%} ({self.max_portfolio_exposure_pct:.0%}/{self.max_concurrent_positions})"
                ),
                "current_exposure_usdt": current_exposure,
                "new_exposure_usdt": current_exposure + new_position_margin,
            }

        # Check total portfolio exposure
        total_exposure = current_exposure + new_position_margin
        exposure_pct = total_exposure / total_equity

        passed = exposure_pct <= self.max_portfolio_exposure_pct

        return {
            "passed": passed,
            "current_value": exposure_pct,
            "limit": self.max_portfolio_exposure_pct,
            "severity": "warning" if not passed else "info",
            "message": (
                f"Portfolio exposure {exposure_pct:.2%} would exceed limit {self.max_portfolio_exposure_pct:.2%}"
                if not passed
                else f"Portfolio exposure within limit ({exposure_pct:.2%} / {self.max_portfolio_exposure_pct:.2%})"
            ),
            "current_exposure_usdt": current_exposure,
            "new_exposure_usdt": total_exposure
        }

    def _check_leverage_limit(
        self,
        trading_decision: Dict[str, Any],
        research_summary: Optional[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """Check 5: Leverage Limit

        Uses max of configured leverage limits (low/med/high volatility).
        To re-enable volatility-based branching, set different values in config.
        """

        requested_leverage = trading_decision.get("leverage", 1)

        # Use max of all configured limits — config drives the cap
        max_leverage = max(self.leverage_low_vol, self.leverage_med_vol, self.leverage_high_vol)

        passed = requested_leverage <= max_leverage

        return {
            "passed": passed,
            "current_value": requested_leverage,
            "limit": max_leverage,
            "severity": "warning" if not passed else "info",
            "message": (
                f"Leverage {requested_leverage}x exceeds limit {max_leverage}x"
                if not passed
                else f"Leverage within limit ({requested_leverage}x / {max_leverage}x)"
            ),
        }

    def _check_correlation_limit(
        self,
        trading_decision: Dict[str, Any],
        account_status: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Check 6: Correlation Check"""

        # Simplified: Count positions in same direction
        # In production, would calculate actual correlation matrix

        open_positions_count = account_status.get("open_positions_count", 0)

        # For now, assume all positions could be correlated (conservative)
        passed = open_positions_count < self.max_correlated_positions

        return {
            "passed": passed,
            "current_value": open_positions_count,
            "limit": self.max_correlated_positions,
            "severity": "warning" if not passed else "info",
            "message": (
                f"Open positions count {open_positions_count} at/exceeds correlation limit {self.max_correlated_positions}"
                if not passed
                else f"Open positions within correlation limit ({open_positions_count} / {self.max_correlated_positions})"
            )
        }

    def _check_volatility_gate(
        self,
        research_summary: Optional[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """Check 7: Volatility Gate"""

        if not research_summary:
            return {
                "passed": True,
                "current_value": 0,
                "limit": self.volatility_gate_threshold_pct,
                "message": "No research summary - volatility gate skipped"
            }

        tech_indicators = research_summary.get("technical_indicators", {})
        volatility_pct = tech_indicators.get("volatility_pct", 0.0)

        passed = volatility_pct <= self.volatility_gate_threshold_pct

        return {
            "passed": passed,
            "current_value": volatility_pct,
            "limit": self.volatility_gate_threshold_pct,
            "severity": "warning" if not passed else "info",
            "message": (
                f"Volatility {volatility_pct:.2%} exceeds gate threshold {self.volatility_gate_threshold_pct:.2%}"
                if not passed
                else f"Volatility within gate ({volatility_pct:.2%} / {self.volatility_gate_threshold_pct:.2%})"
            )
        }

    def _check_position_concentration(
        self,
        trading_decision: Dict[str, Any],
        account_status: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Check 8: Position Concentration (margin-based, not notional)"""

        position_size_usdt = trading_decision.get("position_size_usdt", 0)
        total_equity = max(account_status.get("total_equity", 1), 0.01)

        # Use margin (notional / leverage) for concentration check, not raw notional
        leverage = trading_decision.get("leverage", self.config.get("trading", {}).get("default_leverage", 5))
        margin_required = position_size_usdt / max(leverage, 1)
        concentration_pct = margin_required / total_equity

        passed = concentration_pct <= self.max_position_concentration_pct

        return {
            "passed": passed,
            "current_value": concentration_pct,
            "limit": self.max_position_concentration_pct,
            "severity": "warning" if not passed else "info",
            "message": (
                f"Margin concentration {concentration_pct:.2%} exceeds limit {self.max_position_concentration_pct:.2%}"
                if not passed
                else f"Margin concentration within limit ({concentration_pct:.2%} / {self.max_position_concentration_pct:.2%})"
            )
        }

    def _check_duplicate_position(self, symbol: str) -> Dict[str, Any]:
        """Check 10: Reject if there's already an open position for this symbol on Binance."""
        try:
            positions = self.binance_client.get_positions()
            for pos in positions:
                if pos.get("symbol") == symbol:
                    amt = pos.get("position_amount", 0)
                    return {
                        "passed": False,
                        "current_value": abs(amt),
                        "limit": 0,
                        "severity": "critical",
                        "message": f"Already have open position on {symbol}: {amt} units"
                    }
            return {
                "passed": True,
                "current_value": 0,
                "limit": 0,
                "message": f"No existing position on {symbol}"
            }
        except Exception as e:
            logger.warning("Failed to check duplicate positions — BLOCKING trade for safety", error=str(e))
            return {
                "passed": False,
                "current_value": 0,
                "limit": 0,
                "severity": "critical",
                "message": f"Could not verify positions on Binance (API error: {str(e)[:80]}) — rejecting to prevent duplicates"
            }

    def _check_available_margin(
        self,
        trading_decision: Dict[str, Any],
        account_status: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Check 9: Available Margin"""

        position_size_usdt = trading_decision.get("position_size_usdt", 0)
        leverage = trading_decision.get("leverage", 1)
        available_balance = account_status.get("available_balance", 0)

        required_margin = position_size_usdt / leverage

        passed = required_margin <= available_balance

        return {
            "passed": passed,
            "current_value": required_margin,
            "limit": available_balance,
            "severity": "critical" if not passed else "info",
            "message": (
                f"Insufficient margin: required {required_margin:.2f} USDT, available {available_balance:.2f} USDT"
                if not passed
                else f"Sufficient margin ({required_margin:.2f} / {available_balance:.2f} USDT)"
            )
        }

    def _check_funding_rate(
        self,
        trading_decision: Dict[str, Any],
        research_summary: Optional[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """Check 11: Block trades that would pay high funding rates.
        LONGs blocked when funding > threshold (you pay shorts).
        SHORTs blocked when funding < -threshold (you pay longs).
        """
        if not research_summary:
            return {"passed": True, "current_value": 0, "limit": self.max_funding_rate_pct,
                    "message": "No research data — funding check skipped"}

        funding_rate = research_summary.get("market_data", {}).get("funding_rate", 0)
        decision = trading_decision.get("decision", "NO_TRADE")

        # LONG pays funding when rate is positive
        if decision == "LONG" and funding_rate > self.max_funding_rate_pct:
            return {
                "passed": False,
                "current_value": funding_rate,
                "limit": self.max_funding_rate_pct,
                "severity": "warning",
                "message": f"LONG blocked: funding rate {funding_rate:.4%} > {self.max_funding_rate_pct:.4%} (you'd pay shorts)"
            }

        # SHORT pays funding when rate is negative
        if decision == "SHORT" and funding_rate < -self.max_funding_rate_pct:
            return {
                "passed": False,
                "current_value": funding_rate,
                "limit": -self.max_funding_rate_pct,
                "severity": "warning",
                "message": f"SHORT blocked: funding rate {funding_rate:.4%} < -{self.max_funding_rate_pct:.4%} (you'd pay longs)"
            }

        return {
            "passed": True,
            "current_value": funding_rate,
            "limit": self.max_funding_rate_pct,
            "message": f"Funding rate OK: {funding_rate:.4%}"
        }

    def _check_consecutive_losses(self, symbol: str) -> Dict[str, Any]:
        """Check 12: Block trading if too many consecutive losses on this symbol."""
        try:
            with self.db_session.session_scope() as session:
                self.queries.session = session
                consecutive = self.queries.get_consecutive_losses(
                    symbol, self.loss_lookback_minutes
                )

            if consecutive >= self.max_consecutive_losses:
                return {
                    "passed": False,
                    "current_value": consecutive,
                    "limit": self.max_consecutive_losses,
                    "severity": "warning",
                    "message": f"{consecutive} consecutive losses on {symbol} in last {self.loss_lookback_minutes}min — cooling down"
                }

            return {
                "passed": True,
                "current_value": consecutive,
                "limit": self.max_consecutive_losses,
                "message": f"Loss streak OK ({consecutive} / {self.max_consecutive_losses} max)"
            }
        except Exception as e:
            logger.warning("Failed to check consecutive losses", symbol=symbol, error=str(e))
            return {
                "passed": True,
                "current_value": 0,
                "limit": self.max_consecutive_losses,
                "message": "Could not check loss streak (allowing trade)"
            }

    # ========== POSITION SIZING ==========

    def _calculate_position_sizing(
        self,
        trading_decision: Dict[str, Any],
        research_summary: Optional[Dict[str, Any]],
        account_status: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Calculate risk-adjusted position sizing.

        Methods:
        - Kelly Criterion (with safety fraction)
        - ATR-based (volatility-adjusted)
        - Fixed percentage
        """

        if self.sizing_method == "kelly_criterion":
            return self._kelly_criterion_sizing(
                trading_decision,
                research_summary,
                account_status
            )
        elif self.sizing_method == "atr_based":
            return self._atr_based_sizing(
                trading_decision,
                research_summary,
                account_status
            )
        else:  # fixed_percentage
            return self._fixed_percentage_sizing(
                trading_decision,
                account_status
            )

    def _kelly_criterion_sizing(
        self,
        trading_decision: Dict[str, Any],
        research_summary: Optional[Dict[str, Any]],
        account_status: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Kelly Criterion position sizing with full formula: real win_rate AND avg_win/avg_loss."""

        # Extract risk metrics from decision (conservative defaults)
        risk_metrics = trading_decision.get("risk_metrics", {})
        rr_ratio = risk_metrics.get("risk_reward_ratio", 2.0)

        # Try to get REAL stats from database for this strategy
        strategy_id = trading_decision.get("strategy_id", "")
        win_prob = None
        avg_win = None
        avg_loss = None
        try:
            with self.db_session.session_scope() as session:
                self.queries.session = session
                stats = self.queries.get_strategy_full_stats(
                    strategy_id, lookback_days=7, min_trades=10
                )
                if stats:
                    win_prob = stats["win_rate"]
                    avg_win = stats["avg_win"]
                    avg_loss = stats["avg_loss"]
                    # Use real R:R from actual trades instead of decision-level estimate
                    if avg_loss > 0:
                        rr_ratio = avg_win / avg_loss
        except Exception as e:
            logger.warning("Failed to fetch strategy stats from DB", error=str(e))

        # Fall back to conservative defaults if not enough data
        if win_prob is None:
            win_prob = 0.50
            logger.debug("Using conservative 50% win rate (insufficient data)", strategy_id=strategy_id)
        else:
            logger.info(
                "Using real strategy stats from DB",
                strategy_id=strategy_id,
                win_rate=f"{win_prob:.1%}",
                avg_win=f"${avg_win:.3f}" if avg_win else "N/A",
                avg_loss=f"${avg_loss:.3f}" if avg_loss else "N/A",
                real_rr=f"{rr_ratio:.2f}",
            )

        # Full Kelly formula: f* = (p * b - q) / b
        # where p = win probability, q = 1-p, b = avg_win/avg_loss (odds)
        if rr_ratio <= 0:
            rr_ratio = 2.0
        kelly_pct = (win_prob * rr_ratio - (1 - win_prob)) / rr_ratio
        kelly_pct = max(0, kelly_pct)  # Never negative

        # Apply safety fraction (half Kelly for safety)
        adjusted_kelly_pct = kelly_pct * self.kelly_fraction

        # Cap at max risk per trade
        final_risk_pct = min(adjusted_kelly_pct, self.max_risk_per_trade_pct)

        # Calculate position size
        total_equity = max(account_status.get("total_equity", 1), 0.01)
        risk_amount = total_equity * final_risk_pct

        # Calculate stop distance
        entry_price = trading_decision.get("entry_price", 0)
        stop_loss = trading_decision.get("stop_loss", 0)

        if entry_price > 0 and stop_loss > 0:
            stop_distance_pct = abs((entry_price - stop_loss) / entry_price)
            position_size_usdt = risk_amount / stop_distance_pct if stop_distance_pct > 0 else 0
        else:
            position_size_usdt = 0
            stop_distance_pct = 0

        return {
            "method": "kelly_criterion",
            "kelly_fraction": self.kelly_fraction,
            "kelly_pct": kelly_pct,
            "adjusted_kelly_pct": adjusted_kelly_pct,
            "risk_per_trade_usdt": risk_amount,
            "risk_per_trade_pct": final_risk_pct,
            "calculated_position_size_usdt": position_size_usdt,
            "stop_loss_distance_pct": stop_distance_pct,
            "win_probability": win_prob,
            "risk_reward_ratio": rr_ratio
        }

    def _atr_based_sizing(
        self,
        trading_decision: Dict[str, Any],
        research_summary: Optional[Dict[str, Any]],
        account_status: Dict[str, Any]
    ) -> Dict[str, Any]:
        """ATR-based position sizing (volatility-adjusted)."""

        # Get ATR from research summary
        atr = 0
        if research_summary:
            tech_indicators = research_summary.get("technical_indicators", {})
            atr = tech_indicators.get("atr", 0)

        entry_price = trading_decision.get("entry_price", 0)
        total_equity = max(account_status.get("total_equity", 1), 0.01)

        # Stop distance = ATR * multiplier
        stop_distance = atr * self.atr_multiplier
        stop_distance_pct = stop_distance / entry_price if entry_price > 0 else 0

        # Risk amount
        risk_amount = total_equity * self.max_risk_per_trade_pct

        # Position size
        position_size_usdt = risk_amount / stop_distance_pct if stop_distance_pct > 0 else 0

        return {
            "method": "atr_based",
            "atr": atr,
            "atr_multiplier": self.atr_multiplier,
            "risk_per_trade_usdt": risk_amount,
            "risk_per_trade_pct": self.max_risk_per_trade_pct,
            "calculated_position_size_usdt": position_size_usdt,
            "stop_loss_distance_pct": stop_distance_pct
        }

    def _fixed_percentage_sizing(
        self,
        trading_decision: Dict[str, Any],
        account_status: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Fixed percentage position sizing."""

        total_equity = max(account_status.get("total_equity", 1), 0.01)
        risk_amount = total_equity * self.max_risk_per_trade_pct

        entry_price = trading_decision.get("entry_price", 0)
        stop_loss = trading_decision.get("stop_loss", 0)

        if entry_price > 0 and stop_loss > 0:
            stop_distance_pct = abs((entry_price - stop_loss) / entry_price)
            position_size_usdt = risk_amount / stop_distance_pct if stop_distance_pct > 0 else 0
        else:
            position_size_usdt = 0
            stop_distance_pct = 0

        return {
            "method": "fixed_percentage",
            "risk_per_trade_usdt": risk_amount,
            "risk_per_trade_pct": self.max_risk_per_trade_pct,
            "calculated_position_size_usdt": position_size_usdt,
            "stop_loss_distance_pct": stop_distance_pct
        }

    # ========== APPROVAL LOGIC ==========

    def _determine_approval(
        self,
        risk_checks: Dict[str, Dict[str, Any]],
        trading_decision: Dict[str, Any],
        research_summary: Optional[Dict[str, Any]],
        account_status: Dict[str, Any]
    ) -> Tuple[str, Optional[Dict[str, Any]]]:
        """
        Determine approval status based on risk checks.

        Returns:
            (approval_status, modified_parameters)
        """

        # Collect failed checks
        critical_failures = []
        warning_failures = []

        for check_name, check_result in risk_checks.items():
            if not check_result.get("passed"):
                severity = check_result.get("severity", "warning")
                if severity == "critical":
                    critical_failures.append((check_name, check_result))
                else:
                    warning_failures.append((check_name, check_result))

        # REJECT if any critical check failed
        if critical_failures:
            reasons = [f"{name}: {result.get('message')}" for name, result in critical_failures]
            logger.warning(
                "Trade REJECTED - Critical risk check failed",
                symbol=trading_decision.get("symbol"),
                failures=reasons
            )
            return ("REJECTED", None)

        # MODIFY if warnings exist
        if warning_failures:
            modified_params = self._calculate_modifications(
                trading_decision,
                research_summary,
                account_status,
                warning_failures
            )

            logger.debug(
                "Trade MODIFIED - Risk parameters adjusted",
                symbol=trading_decision.get("symbol"),
                modifications=list(modified_params.keys())
            )
            return ("MODIFIED", modified_params)

        # APPROVE - all checks passed
        logger.debug(
            "Trade APPROVED - All risk checks passed",
            symbol=trading_decision.get("symbol")
        )
        return ("APPROVED", None)

    def _calculate_modifications(
        self,
        trading_decision: Dict[str, Any],
        research_summary: Optional[Dict[str, Any]],
        account_status: Dict[str, Any],
        warning_failures: list
    ) -> Dict[str, Any]:
        """Calculate modified parameters to satisfy risk limits."""

        modified_params = {}

        # Get position sizing calculation
        position_sizing = self._calculate_position_sizing(
            trading_decision,
            research_summary,
            account_status
        )

        calculated_size = position_sizing.get("calculated_position_size_usdt", 0)
        requested_size = trading_decision.get("position_size_usdt", 0)

        # Reduce position size if needed
        if calculated_size < requested_size:
            modified_params["position_size_usdt"] = calculated_size

        # Adjust leverage if needed
        for check_name, check_result in warning_failures:
            if check_name == "leverage_limit":
                modified_params["leverage"] = int(check_result.get("limit", 1))

        # Cap position size so margin fits within available balance
        leverage = modified_params.get("leverage", trading_decision.get("leverage", 1))
        available = account_status.get("available_balance", 0)
        total_equity = max(account_status.get("total_equity", 1), 0.01)
        final_size = modified_params.get("position_size_usdt", requested_size)

        # Per-position budget: equity * per_position_exposure_pct * leverage
        per_position_max_notional = total_equity * self.per_position_exposure_pct * leverage
        # Also cap by available margin
        max_notional_from_available = available * leverage * 0.80
        max_notional = min(per_position_max_notional, max_notional_from_available)

        if final_size > max_notional and max_notional > 0:
            modified_params["position_size_usdt"] = round(max_notional, 2)

        # Enforce Binance Futures minimum notional ($100)
        final_size = modified_params.get("position_size_usdt", requested_size)
        if final_size < 100.0:
            modified_params["position_size_usdt"] = 100.0

        return modified_params

    # ========== ACCOUNT STATUS ==========

    def _get_account_status(self) -> Dict[str, Any]:
        """
        Get current account status from Binance API and database.

        Returns:
            Dict with available_balance, total_equity, current_exposure, etc.
        """

        try:
            # Get account balance from Binance
            balance_data = self.binance_client.get_account_balance()

            available_balance = balance_data.get("available_balance", 0)
            total_equity = balance_data.get("total_equity", 0)
            unrealized_pnl = balance_data.get("unrealized_pnl", 0)

            # Get open positions from database
            with self.db_session.session_scope() as session:
                self.queries.session = session
                open_positions = self.queries.get_open_positions()

                # Calculate current exposure
                current_exposure = sum(
                    pos.entry_price * pos.quantity / pos.leverage
                    for pos in open_positions
                )

                # Calculate today's P&L
                today_start = datetime.combine(datetime.today(), datetime.min.time())
                today_end = datetime.now()
                daily_pnl = self.queries.calculate_total_pnl(today_start, today_end)

            # Ensure total_equity is never zero (safety guard)
            total_equity = max(total_equity, 0.01)

            # Calculate daily drawdown
            daily_drawdown_pct = daily_pnl / total_equity if total_equity > 0 else 0

            return {
                "available_balance": available_balance,
                "total_equity": total_equity,
                "current_exposure": current_exposure,
                "unrealized_pnl": unrealized_pnl,
                "daily_pnl": daily_pnl,
                "daily_drawdown_pct": daily_drawdown_pct,
                "open_positions_count": len(open_positions)
            }

        except Exception as e:
            logger.error("Failed to get account status", error=str(e))

            # For paper trading, use simulated balance from config
            paper_config = self.config.get("execution", {}).get("paper_trading", {})
            simulated_balance = paper_config.get("simulated_balance_usdt", 0)

            logger.warning(
                "Using simulated balance for paper trading (API unavailable)",
                simulated_balance=simulated_balance
            )

            # Get open positions from database for exposure calculation
            try:
                with self.db_session.session_scope() as session:
                    self.queries.session = session
                    open_positions = self.queries.get_open_positions()
                    current_exposure = sum(
                        pos.entry_price * pos.quantity / pos.leverage
                        for pos in open_positions
                    )
                    today_start = datetime.combine(datetime.today(), datetime.min.time())
                    today_end = datetime.now()
                    daily_pnl = self.queries.calculate_total_pnl(today_start, today_end)
            except Exception as db_error:
                logger.warning("Failed to get position data from database", error=str(db_error))
                current_exposure = 0
                daily_pnl = 0
                open_positions = []

            # Calculate total equity (simulated balance + unrealized P&L)
            total_equity = simulated_balance + daily_pnl
            available_balance = total_equity - current_exposure

            # FAIL SAFE: Return simulated paper trading defaults or conservative values
            return {
                "available_balance": max(0, available_balance) if simulated_balance > 0 else 0,
                "total_equity": max(1, total_equity) if simulated_balance > 0 else 1,
                "current_exposure": current_exposure,
                "unrealized_pnl": 0,
                "daily_pnl": daily_pnl,
                "daily_drawdown_pct": daily_pnl / total_equity if total_equity > 0 else 0,
                "open_positions_count": len(open_positions)
            }

    # ========== RESPONSE BUILDERS ==========

    def _build_risk_approval(
        self,
        approval_status: str,
        decision_id: str,
        correlation_id: str,
        risk_checks: Dict[str, Dict[str, Any]],
        position_sizing: Dict[str, Any],
        account_status: Dict[str, Any],
        modified_parameters: Optional[Dict[str, Any]],
        processing_time_ms: float
    ) -> Dict[str, Any]:
        """Build risk approval response (conforms to risk_approval.schema.json)."""

        risk_approval = {
            "schema_version": "1.0.0",
            "agent_id": "risk-manager",
            "correlation_id": correlation_id,
            "timestamp": datetime.utcnow().isoformat(),
            "approval_id": str(uuid.uuid4()),
            "decision_id": decision_id,
            "approval_status": approval_status,
            "risk_checks": risk_checks,
            "position_sizing": position_sizing,
            "account_status": account_status,
            "kill_switches": {
                "global_trading_enabled": not self.global_kill_switch,
                "symbol_enabled": True,  # Set dynamically based on symbol_kill_switches
                "strategy_enabled": True,  # Set dynamically based on strategy_kill_switches
                "volatility_breaker_active": False  # Would be set by circuit breaker logic
            },
            "processing_time_ms": processing_time_ms
        }

        # Add rejection reason if rejected
        if approval_status == "REJECTED":
            failed_checks = [
                f"{name}: {result.get('message')}"
                for name, result in risk_checks.items()
                if not result.get("passed") and result.get("severity") == "critical"
            ]
            risk_approval["rejection_reason"] = "; ".join(failed_checks)

        # Add modified parameters if modified
        if approval_status == "MODIFIED" and modified_parameters:
            risk_approval["modified_parameters"] = modified_parameters

        return risk_approval

    def _approve_no_trade(self, trading_decision: Dict[str, Any]) -> Dict[str, Any]:
        """Approve NO_TRADE decisions (skip risk checks)."""

        return {
            "schema_version": "1.0.0",
            "agent_id": "risk-manager",
            "correlation_id": trading_decision.get("correlation_id", str(uuid.uuid4())),
            "timestamp": datetime.utcnow().isoformat(),
            "approval_id": str(uuid.uuid4()),
            "decision_id": trading_decision.get("decision_id"),
            "approval_status": "APPROVED",
            "risk_checks": {},
            "account_status": self._get_account_status(),
            "processing_time_ms": 0
        }

    def _reject(self, reason: str) -> Dict[str, Any]:
        """Reject trade with reason."""

        return {
            "schema_version": "1.0.0",
            "agent_id": "risk-manager",
            "correlation_id": str(uuid.uuid4()),
            "timestamp": datetime.utcnow().isoformat(),
            "approval_id": str(uuid.uuid4()),
            "decision_id": "unknown",
            "approval_status": "REJECTED",
            "rejection_reason": reason,
            "risk_checks": {},
            "account_status": {"available_balance": 0, "total_equity": 1, "current_exposure": 0},
            "processing_time_ms": 0
        }
