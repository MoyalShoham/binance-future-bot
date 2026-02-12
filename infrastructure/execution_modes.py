"""
Execution Modes: Paper, Live, and Hybrid Trading

Implements three execution modes with idempotent order submission and shadow execution.
"""

import uuid
import hashlib
from typing import Dict, Any, Optional
from datetime import datetime
from enum import Enum
import structlog

logger = structlog.get_logger()


class ExecutionMode(str, Enum):
    """Trading execution modes."""
    PAPER = "PAPER"
    LIVE = "LIVE"
    HYBRID = "HYBRID"


class ExecutionStatus(str, Enum):
    """Order execution status."""
    FILLED = "FILLED"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    REJECTED = "REJECTED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class OrderExecutor:
    """
    Handles order execution across different modes.

    Implements:
    - Paper trading with realistic slippage simulation
    - Live trading with real Binance API calls
    - Hybrid mode with live execution + shadow paper comparison
    - Idempotent order submission to prevent duplicates
    """

    # Fallback rules (used if Binance API is unavailable)
    FALLBACK_SYMBOL_RULES = {
        "XRPUSDT": {"min_qty": 0.1, "step_size": 0.1, "min_notional": 5.0, "price_precision": 4},
        "LINKUSDT": {"min_qty": 0.01, "step_size": 0.01, "min_notional": 5.0, "price_precision": 3},
        "DOGEUSDT": {"min_qty": 1, "step_size": 1, "min_notional": 5.0, "price_precision": 5},
        "1000SHIBUSDT": {"min_qty": 1, "step_size": 1, "min_notional": 5.0, "price_precision": 6},
        "1000FLOKIUSDT": {"min_qty": 1, "step_size": 1, "min_notional": 5.0, "price_precision": 5},
        "ADAUSDT": {"min_qty": 1, "step_size": 1, "min_notional": 5.0, "price_precision": 4},
        "DOTUSDT": {"min_qty": 0.1, "step_size": 0.1, "min_notional": 5.0, "price_precision": 3},
        "AVAXUSDT": {"min_qty": 0.1, "step_size": 0.1, "min_notional": 5.0, "price_precision": 3},
        "BTCUSDT": {"min_qty": 0.001, "step_size": 0.001, "min_notional": 5.0, "price_precision": 1},
        "ETHUSDT": {"min_qty": 0.001, "step_size": 0.001, "min_notional": 5.0, "price_precision": 2},
    }
    DEFAULT_RULES = {"min_qty": 0.001, "step_size": 0.001, "min_notional": 5.0, "price_precision": 2}

    def __init__(self, binance_client, config: Dict[str, Any]):
        """
        Initialize order executor.

        Args:
            binance_client: Binance API client instance
            config: Execution configuration
        """
        self.binance_client = binance_client
        self.config = config

        # Track executed orders for idempotency
        self.executed_orders = {}  # client_order_id -> execution_result

        # Paper trading configuration
        self.paper_config = {
            "base_slippage_bps": config.get("paper_trading", {}).get("base_slippage_bps", 5),
            "market_impact_model": config.get("paper_trading", {}).get("market_impact_model", "square_root"),
            "maker_fee_bps": config.get("fees", {}).get("maker_bps", 2),
            "taker_fee_bps": config.get("fees", {}).get("taker_bps", 5)
        }

        # Hybrid mode configuration
        self.hybrid_config = {
            "divergence_threshold_pct": config.get("hybrid", {}).get("divergence_threshold_pct", 0.005),
            "alert_on_divergence": config.get("hybrid", {}).get("alert_on_divergence", True)
        }

        # Load real symbol rules from Binance API, fallback to hardcoded
        self.SYMBOL_RULES = dict(self.FALLBACK_SYMBOL_RULES)
        self._load_exchange_info()

        logger.info("OrderExecutor initialized", mode=config.get("execution_mode"))

    def _load_exchange_info(self):
        """Fetch real symbol precision rules from Binance at startup."""
        try:
            configured_symbols = self.config.get("trading", {}).get("symbols", [])
            if not configured_symbols:
                configured_symbols = list(self.FALLBACK_SYMBOL_RULES.keys())

            exchange_rules = self.binance_client.get_symbol_info(configured_symbols)

            for symbol, rules in exchange_rules.items():
                self.SYMBOL_RULES[symbol] = {
                    "min_qty": rules["min_qty"],
                    "step_size": rules["step_size"],
                    "min_notional": rules["min_notional"],
                    "price_precision": rules.get("price_precision", 2),
                }

            logger.info(
                "Exchange info loaded from Binance API",
                symbols_updated=len(exchange_rules)
            )
        except Exception as e:
            logger.warning(
                "Failed to load exchange info, using fallback rules",
                error=str(e)
            )

    def ensure_symbol_rules(self, symbol: str):
        """Load exchange rules for a symbol if not already cached."""
        if symbol in self.SYMBOL_RULES:
            return
        try:
            exchange_rules = self.binance_client.get_symbol_info([symbol])
            if symbol in exchange_rules:
                rules = exchange_rules[symbol]
                self.SYMBOL_RULES[symbol] = {
                    "min_qty": rules["min_qty"],
                    "step_size": rules["step_size"],
                    "min_notional": rules["min_notional"],
                    "price_precision": rules.get("price_precision", 2),
                }
                logger.debug("Loaded exchange rules for new symbol", symbol=symbol)
        except Exception as e:
            logger.warning("Failed to load rules for symbol, using defaults", symbol=symbol, error=str(e))

    def execute_trade(
        self,
        approval: Dict[str, Any],
        mode: ExecutionMode
    ) -> Dict[str, Any]:
        """
        Execute a trade based on the specified mode.

        Args:
            approval: Risk-approved trading parameters
            mode: Execution mode (PAPER/LIVE/HYBRID)

        Returns:
            Execution result conforming to execution_result schema
        """
        # Ensure we have exchange rules for this symbol
        self.ensure_symbol_rules(approval["symbol"])

        # Generate idempotent client order ID
        client_order_id = self._generate_client_order_id(approval)

        # Check for duplicate submission
        if client_order_id in self.executed_orders:
            logger.warning(
                "Duplicate order submission detected",
                client_order_id=client_order_id,
                original_execution=self.executed_orders[client_order_id]["execution_id"]
            )
            return {
                **self.executed_orders[client_order_id],
                "idempotency_check": {
                    "is_duplicate": True,
                    "original_execution_id": self.executed_orders[client_order_id]["execution_id"]
                }
            }

        # Route to appropriate execution mode
        if mode == ExecutionMode.PAPER:
            result = self._execute_paper_trade(approval, client_order_id)
        elif mode == ExecutionMode.LIVE:
            result = self._execute_live_trade(approval, client_order_id, with_shadow=True)
        elif mode == ExecutionMode.HYBRID:
            result = self._execute_hybrid_trade(approval, client_order_id)
        else:
            raise ValueError(f"Invalid execution mode: {mode}")

        # Store for idempotency check
        self.executed_orders[client_order_id] = result

        return result

    def _generate_client_order_id(self, approval: Dict[str, Any]) -> str:
        """
        Generate idempotent client order ID.

        Uses hash of (decision_id + approval_id + timestamp) to ensure uniqueness
        while preventing accidental duplicate submissions.

        Args:
            approval: Risk approval containing decision_id and approval_id

        Returns:
            Unique client order ID
        """
        decision_id = approval["decision_id"]
        approval_id = approval["approval_id"]
        timestamp = approval["timestamp"]

        # Create deterministic hash
        hash_input = f"{decision_id}:{approval_id}:{timestamp}"
        order_hash = hashlib.sha256(hash_input.encode()).hexdigest()[:16]

        client_order_id = f"TRADE_{order_hash}"

        return client_order_id

    def _execute_paper_trade(
        self,
        approval: Dict[str, Any],
        client_order_id: str
    ) -> Dict[str, Any]:
        """
        Execute paper trade with realistic slippage simulation.

        Args:
            approval: Risk-approved parameters
            client_order_id: Idempotent order ID

        Returns:
            Execution result
        """
        execution_id = str(uuid.uuid4())
        start_time = datetime.utcnow()

        logger.info(
            "Executing paper trade",
            execution_id=execution_id,
            client_order_id=client_order_id
        )

        try:
            # Extract parameters
            symbol = approval["symbol"]
            side = approval["side"]
            position_size_usdt = approval["modified_parameters"]["position_size_usdt"]
            entry_price = approval["entry_price"]
            leverage = approval["modified_parameters"]["leverage"]

            # Calculate quantity with proper rounding
            quantity = position_size_usdt / entry_price
            quantity = self._round_quantity(symbol, quantity)
            rules = self.SYMBOL_RULES.get(symbol, self.DEFAULT_RULES)
            if quantity < rules["min_qty"]:
                quantity = rules["min_qty"]

            # Simulate slippage
            simulated_slippage_bps = self._calculate_simulated_slippage(
                position_size_usdt,
                symbol
            )

            # Calculate fill price with slippage
            if side == "LONG":
                fill_price = entry_price * (1 + simulated_slippage_bps / 10000)
            else:  # SHORT
                fill_price = entry_price * (1 - simulated_slippage_bps / 10000)

            # Calculate fees
            fees_usdt = position_size_usdt * (self.paper_config["taker_fee_bps"] / 10000)

            # Calculate slippage amount
            slippage_usdt = abs(fill_price - entry_price) * quantity

            processing_time_ms = (datetime.utcnow() - start_time).total_seconds() * 1000

            result = {
                "execution_id": execution_id,
                "approval_id": approval["approval_id"],
                "decision_id": approval["decision_id"],
                "execution_mode": ExecutionMode.PAPER,
                "execution_status": ExecutionStatus.FILLED,
                "symbol": symbol,
                "side": side,
                "order_details": {
                    "binance_order_id": None,  # No real order
                    "client_order_id": client_order_id,
                    "order_type": "MARKET",
                    "requested_quantity": quantity,
                    "filled_quantity": quantity,
                    "avg_fill_price": fill_price,
                    "requested_price": entry_price,
                    "slippage_usdt": slippage_usdt,
                    "slippage_bps": simulated_slippage_bps,
                    "fees_usdt": fees_usdt,
                    "leverage": leverage,
                    "position_value_usdt": position_size_usdt
                },
                "paper_trading_simulation": {
                    "simulated_slippage_bps": simulated_slippage_bps,
                    "simulated_fees_usdt": fees_usdt,
                    "market_impact_model": self.paper_config["market_impact_model"]
                },
                "execution_timeline": [
                    {
                        "timestamp": start_time.isoformat(),
                        "event": "order_submitted",
                        "details": "Paper trade simulation started"
                    },
                    {
                        "timestamp": datetime.utcnow().isoformat(),
                        "event": "order_filled",
                        "details": f"Simulated fill at {fill_price}"
                    }
                ],
                "timestamp": datetime.utcnow().isoformat() + "Z",
                "processing_time_ms": processing_time_ms,
                "idempotency_check": {
                    "is_duplicate": False,
                    "original_execution_id": None
                }
            }

            logger.info(
                "Paper trade executed",
                execution_id=execution_id,
                fill_price=fill_price,
                slippage_bps=simulated_slippage_bps
            )

            return result

        except Exception as e:
            logger.error("Paper trade failed", error=str(e), execution_id=execution_id)
            return self._create_failed_execution(
                execution_id,
                approval,
                client_order_id,
                ExecutionMode.PAPER,
                str(e)
            )

    def _execute_live_trade(
        self,
        approval: Dict[str, Any],
        client_order_id: str,
        with_shadow: bool = False
    ) -> Dict[str, Any]:
        """
        Execute live trade via Binance API.

        Args:
            approval: Risk-approved parameters
            client_order_id: Idempotent order ID
            with_shadow: If True, also execute shadow paper trade for comparison

        Returns:
            Execution result with optional shadow comparison
        """
        execution_id = str(uuid.uuid4())
        start_time = datetime.utcnow()

        logger.info(
            "Executing live trade",
            execution_id=execution_id,
            client_order_id=client_order_id,
            with_shadow=with_shadow
        )

        try:
            # Extract parameters
            symbol = approval["symbol"]
            side = approval["side"]
            position_size_usdt = approval["modified_parameters"]["position_size_usdt"]
            entry_price = approval["entry_price"]
            leverage = approval["modified_parameters"]["leverage"]

            # Set leverage
            self.binance_client.change_leverage(symbol=symbol, leverage=leverage)

            # Calculate quantity and round to symbol's step size
            quantity = position_size_usdt / entry_price
            quantity = self._round_quantity(symbol, quantity)

            # Enforce minimum quantity and notional
            rules = self.SYMBOL_RULES.get(symbol, self.DEFAULT_RULES)
            if quantity < rules["min_qty"]:
                quantity = rules["min_qty"]
            notional = quantity * entry_price
            if notional < rules["min_notional"]:
                quantity = self._round_quantity(symbol, rules["min_notional"] / entry_price + rules["step_size"])

            logger.info("Order quantity calculated", symbol=symbol, quantity=quantity, notional=quantity * entry_price)

            # Place market order
            order = self.binance_client.create_order(
                symbol=symbol,
                side="BUY" if side == "LONG" else "SELL",
                order_type="MARKET",
                quantity=quantity,
                client_order_id=client_order_id
            )

            # Parse order result
            binance_order_id = order["orderId"]
            filled_quantity = float(order["executedQty"])
            avg_fill_price = float(order.get("avgPrice", 0))
            # Binance may return avgPrice=0 for MARKET orders - use entry_price as fallback
            if avg_fill_price == 0:
                avg_fill_price = entry_price

            # Calculate slippage and fees
            slippage_usdt = abs(avg_fill_price - entry_price) * filled_quantity
            slippage_bps = (slippage_usdt / max(position_size_usdt, 0.01)) * 10000

            # Estimate fees (will be confirmed later)
            fees_usdt = position_size_usdt * (self.paper_config["taker_fee_bps"] / 10000)

            processing_time_ms = (datetime.utcnow() - start_time).total_seconds() * 1000

            result = {
                "execution_id": execution_id,
                "approval_id": approval["approval_id"],
                "decision_id": approval["decision_id"],
                "execution_mode": ExecutionMode.LIVE,
                "execution_status": ExecutionStatus.FILLED if filled_quantity >= quantity * 0.99 else ExecutionStatus.PARTIALLY_FILLED,
                "symbol": symbol,
                "side": side,
                "order_details": {
                    "binance_order_id": str(binance_order_id),
                    "client_order_id": client_order_id,
                    "order_type": "MARKET",
                    "requested_quantity": quantity,
                    "filled_quantity": filled_quantity,
                    "avg_fill_price": avg_fill_price,
                    "requested_price": entry_price,
                    "slippage_usdt": slippage_usdt,
                    "slippage_bps": slippage_bps,
                    "fees_usdt": fees_usdt,
                    "leverage": leverage,
                    "position_value_usdt": position_size_usdt
                },
                "execution_timeline": [
                    {
                        "timestamp": start_time.isoformat(),
                        "event": "order_submitted",
                        "details": "Live order sent to Binance"
                    },
                    {
                        "timestamp": datetime.utcnow().isoformat(),
                        "event": "order_filled",
                        "details": f"Filled at {avg_fill_price}"
                    }
                ],
                "timestamp": datetime.utcnow().isoformat() + "Z",
                "processing_time_ms": processing_time_ms,
                "idempotency_check": {
                    "is_duplicate": False,
                    "original_execution_id": None
                }
            }

            # Execute shadow paper trade if requested
            if with_shadow:
                shadow_result = self._execute_paper_trade(approval, f"{client_order_id}_shadow")
                result["shadow_paper_execution"] = self._compare_executions(
                    avg_fill_price,
                    shadow_result["order_details"]["avg_fill_price"],
                    position_size_usdt
                )

            logger.info(
                "Live trade executed",
                execution_id=execution_id,
                binance_order_id=binance_order_id,
                fill_price=avg_fill_price
            )

            return result

        except Exception as e:
            logger.error("Live trade failed", error=str(e), execution_id=execution_id)
            return self._create_failed_execution(
                execution_id,
                approval,
                client_order_id,
                ExecutionMode.LIVE,
                str(e)
            )

    def _execute_hybrid_trade(
        self,
        approval: Dict[str, Any],
        client_order_id: str
    ) -> Dict[str, Any]:
        """
        Execute hybrid trade: live + shadow paper with divergence monitoring.

        Args:
            approval: Risk-approved parameters
            client_order_id: Idempotent order ID

        Returns:
            Execution result with divergence analysis
        """
        logger.info("Executing hybrid trade", client_order_id=client_order_id)

        # Execute live trade with shadow
        result = self._execute_live_trade(approval, client_order_id, with_shadow=True)

        # Change mode to HYBRID
        result["execution_mode"] = ExecutionMode.HYBRID

        # Check for divergence alert
        if "shadow_paper_execution" in result:
            if result["shadow_paper_execution"]["divergence_alert"]:
                logger.warning(
                    "Execution quality divergence detected",
                    divergence_pct=result["shadow_paper_execution"]["divergence_pct"],
                    execution_id=result["execution_id"]
                )

        return result

    def _round_quantity(self, symbol: str, quantity: float) -> float:
        """Round quantity to symbol's step size and enforce minimum."""
        import math
        rules = self.SYMBOL_RULES.get(symbol, self.DEFAULT_RULES)
        step = rules["step_size"]
        # Floor to step size
        rounded = math.floor(quantity / step) * step
        # Round to avoid floating point issues
        decimals = len(str(step).rstrip('0').split('.')[-1]) if '.' in str(step) else 0
        rounded = round(rounded, decimals)
        return rounded

    # Tier-based base slippage: top-tier coins have tighter spreads
    SLIPPAGE_TIERS = {
        # Tier 1: BTC/ETH — tightest spreads
        "BTCUSDT": 3, "ETHUSDT": 3,
        # Tier 2: Major alts — decent liquidity
        "XRPUSDT": 5, "SOLUSDT": 5, "BNBUSDT": 4, "DOGEUSDT": 6,
        "ADAUSDT": 6, "AVAXUSDT": 6, "DOTUSDT": 6, "LINKUSDT": 5,
    }
    DEFAULT_SLIPPAGE_BPS = 10  # Mid-cap alts: wider spreads

    def _calculate_simulated_slippage(
        self,
        position_size_usdt: float,
        symbol: str
    ) -> float:
        """
        Calculate realistic simulated slippage for paper trading.

        Uses per-symbol base slippage (tier-based) + market impact model.

        Args:
            position_size_usdt: Position size in USDT
            symbol: Trading symbol

        Returns:
            Slippage in basis points
        """
        # Per-symbol base slippage (more realistic than flat 5 bps for all)
        base_slippage = self.SLIPPAGE_TIERS.get(symbol, self.DEFAULT_SLIPPAGE_BPS)

        # Market impact model
        if self.paper_config["market_impact_model"] == "linear":
            impact = (position_size_usdt / 10000) * 0.5
        elif self.paper_config["market_impact_model"] == "square_root":
            impact = (position_size_usdt / 10000) ** 0.5
        else:
            impact = 0

        total_slippage_bps = base_slippage + impact

        return min(total_slippage_bps, 50)  # Cap at 50 bps (0.5%)

    def _compare_executions(
        self,
        live_price: float,
        shadow_price: float,
        position_size: float
    ) -> Dict[str, Any]:
        """
        Compare live and shadow paper execution.

        Args:
            live_price: Actual fill price from live execution
            shadow_price: Simulated fill price from paper execution
            position_size: Position size in USDT

        Returns:
            Comparison dict with divergence metrics
        """
        safe_live_price = max(live_price, 0.0001)
        divergence_usdt = abs(live_price - shadow_price) * (position_size / safe_live_price)
        divergence_pct = abs(live_price - shadow_price) / safe_live_price

        threshold = self.hybrid_config["divergence_threshold_pct"]
        divergence_alert = divergence_pct > threshold

        return {
            "shadow_fill_price": shadow_price,
            "shadow_slippage_usdt": divergence_usdt,
            "divergence_usdt": divergence_usdt,
            "divergence_pct": divergence_pct,
            "divergence_alert": divergence_alert
        }

    def _create_failed_execution(
        self,
        execution_id: str,
        approval: Dict[str, Any],
        client_order_id: str,
        mode: ExecutionMode,
        error_message: str
    ) -> Dict[str, Any]:
        """Create execution result for failed execution."""
        leverage = approval.get("modified_parameters", {}).get("leverage", 1)
        return {
            "execution_id": execution_id,
            "approval_id": approval["approval_id"],
            "decision_id": approval["decision_id"],
            "execution_mode": mode,
            "execution_status": ExecutionStatus.FAILED,
            "symbol": approval["symbol"],
            "side": approval["side"],
            "order_details": {
                "binance_order_id": None,
                "client_order_id": client_order_id,
                "order_type": "MARKET",
                "requested_quantity": 0,
                "filled_quantity": 0,
                "avg_fill_price": 0,
                "requested_price": approval["entry_price"],
                "slippage_usdt": 0,
                "slippage_bps": 0,
                "fees_usdt": 0,
                "leverage": leverage,
                "position_value_usdt": 0
            },
            "errors": [
                {
                    "error_code": "EXECUTION_FAILED",
                    "error_message": error_message,
                    "retry_count": 0,
                    "timestamp": datetime.utcnow().isoformat()
                }
            ],
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "processing_time_ms": 0,
            "idempotency_check": {
                "is_duplicate": False,
                "original_execution_id": None
            }
        }
