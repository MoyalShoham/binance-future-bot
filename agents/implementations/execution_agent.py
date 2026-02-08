"""
Execution Agent Implementation

Interfaces with Binance Futures API to execute approved trades.
Supports paper, live, and hybrid execution modes with idempotent order submission.
"""

from typing import Dict, Any, Optional, List
from datetime import datetime
import uuid
import structlog

from .base_agent import BaseAgent
from infrastructure.execution_modes import OrderExecutor, ExecutionMode, ExecutionStatus
from schemas.validator import SchemaValidator

logger = structlog.get_logger()


class ExecutionAgent(BaseAgent):
    """
    Execution Agent

    Responsibilities:
    - Execute risk-approved trades via Binance API
    - Support PAPER, LIVE, and HYBRID execution modes
    - Place stop loss and take profit orders
    - Track execution quality and slippage
    - Ensure idempotent order submission
    - Produce schema-compliant Execution Result JSON

    Authority Boundaries:
    ✅ Execute approved trades only
    ✅ Place protective stop loss orders
    ✅ Handle order errors and retries
    ❌ Make trading decisions
    ❌ Modify risk-approved parameters (without approval)
    ❌ Bypass Risk Manager approval

    Design:
    - Uses OrderExecutor from infrastructure.execution_modes
    - Validates all outputs against execution_result schema
    - Handles APPROVED, REJECTED, MODIFIED risk approval statuses
    - Places stop loss and take profit orders after entry fill
    - Observable (all orders logged)
    """

    def __init__(
        self,
        agent_id: str,
        config: Dict[str, Any],
        binance_client,
        db_session=None
    ):
        """
        Initialize Execution Agent.

        Args:
            agent_id: Agent identifier
            config: System configuration
            binance_client: Binance API client
            db_session: Database session (optional, for position tracking)
        """
        super().__init__(agent_id, config)

        self.binance_client = binance_client
        self.db_session = db_session
        self.validator = SchemaValidator()

        # Initialize OrderExecutor
        self.order_executor = OrderExecutor(binance_client, config)

        # Configuration
        self.place_stop_loss = config.get("execution", {}).get("place_stop_loss", True)
        self.place_take_profit = config.get("execution", {}).get("place_take_profit", True)
        self.max_retry_attempts = config.get("execution", {}).get("max_retry_attempts", 3)

        logger.info(
            "Execution Agent initialized",
            agent_id=self.agent_id,
            place_stop_loss=self.place_stop_loss,
            place_take_profit=self.place_take_profit
        )

    def execute(self, state: Dict[str, Any]) -> Dict[str, Any]:
        """
        Main execution: Execute risk-approved trade.

        Args:
            state: Pipeline state with risk_approval, trading_decision, mode

        Returns:
            Execution Result JSON (conforms to execution_result.schema.json)
        """
        correlation_id = state.get("correlation_id", str(uuid.uuid4()))
        start_time = datetime.utcnow()

        logger.info(
            "Execution Agent started",
            correlation_id=correlation_id
        )

        try:
            # Extract required data
            risk_approval = state.get("risk_approval")
            trading_decision = state.get("trading_decision")
            mode = state.get("mode", ExecutionMode.PAPER)

            if not risk_approval:
                raise ValueError("No risk approval in state")
            if not trading_decision:
                raise ValueError("No trading decision in state")

            # Check approval status
            approval_status = risk_approval.get("approval_status")

            if approval_status == "REJECTED":
                # Create rejected execution result
                execution_result = self._create_rejected_execution(
                    risk_approval,
                    trading_decision,
                    "Trade rejected by Risk Manager"
                )

                logger.info(
                    "Execution skipped - trade rejected",
                    correlation_id=correlation_id,
                    rejection_reason=risk_approval.get("rejection_reason")
                )

                return execution_result

            # Prepare approval parameters for OrderExecutor
            approval_params = self._prepare_approval_params(
                risk_approval,
                trading_decision,
                approval_status
            )

            # Execute trade via OrderExecutor
            execution_result = self.order_executor.execute_trade(
                approval_params,
                mode
            )

            # If execution successful, place protective orders
            if execution_result["execution_status"] in [ExecutionStatus.FILLED, ExecutionStatus.PARTIALLY_FILLED]:

                # Place stop loss order
                if self.place_stop_loss and mode != ExecutionMode.PAPER:
                    stop_loss_result = self._place_stop_loss_order(
                        execution_result,
                        trading_decision
                    )
                    execution_result["stop_loss_order"] = stop_loss_result

                # Place take profit orders
                if self.place_take_profit and mode != ExecutionMode.PAPER:
                    take_profit_results = self._place_take_profit_orders(
                        execution_result,
                        trading_decision
                    )
                    execution_result["take_profit_orders"] = take_profit_results

            processing_time_ms = (datetime.utcnow() - start_time).total_seconds() * 1000
            execution_result["processing_time_ms"] = processing_time_ms

            # Validate against schema
            is_valid = self.validator.validate_message(
                execution_result,
                "execution_result",
                strict=True
            )

            if not is_valid:
                logger.error("Execution result failed schema validation")
                raise ValueError("Execution result does not conform to schema")

            logger.info(
                "Execution Agent completed",
                correlation_id=correlation_id,
                execution_status=execution_result["execution_status"],
                execution_mode=execution_result["execution_mode"],
                processing_time_ms=processing_time_ms
            )

            return execution_result

        except Exception as e:
            logger.error(
                "Execution Agent failed",
                correlation_id=correlation_id,
                error=str(e),
                exc_info=True
            )
            raise

    # ========== ORDER PREPARATION ==========

    def _prepare_approval_params(
        self,
        risk_approval: Dict[str, Any],
        trading_decision: Dict[str, Any],
        approval_status: str
    ) -> Dict[str, Any]:
        """
        Prepare approval parameters for OrderExecutor.

        Args:
            risk_approval: Risk Manager approval
            trading_decision: Trading decision
            approval_status: APPROVED or MODIFIED

        Returns:
            Approval parameters dict
        """
        symbol = trading_decision["symbol"]
        side = trading_decision["decision"]
        entry_price = trading_decision["entry_price"]

        # Use modified parameters if available, otherwise use original
        if approval_status == "MODIFIED":
            modified_params = risk_approval.get("modified_parameters", {})
            position_size_usdt = modified_params.get(
                "position_size_usdt",
                trading_decision.get("position_size_usdt")
            )
            leverage = modified_params.get(
                "leverage",
                trading_decision.get("leverage")
            )
            stop_loss = modified_params.get(
                "stop_loss",
                trading_decision.get("stop_loss")
            )
        else:  # APPROVED
            position_size_usdt = trading_decision.get("position_size_usdt")
            leverage = trading_decision.get("leverage")
            stop_loss = trading_decision.get("stop_loss")

        approval_params = {
            "approval_id": risk_approval["approval_id"],
            "decision_id": trading_decision["decision_id"],
            "symbol": symbol,
            "side": side,
            "entry_price": entry_price,
            "modified_parameters": {
                "position_size_usdt": position_size_usdt,
                "leverage": leverage,
                "stop_loss": stop_loss
            },
            "timestamp": risk_approval["timestamp"]
        }

        return approval_params

    # ========== PROTECTIVE ORDERS ==========

    def _place_stop_loss_order(
        self,
        execution_result: Dict[str, Any],
        trading_decision: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Place stop loss order to protect position.

        Args:
            execution_result: Entry execution result
            trading_decision: Original trading decision

        Returns:
            Stop loss order result
        """
        try:
            symbol = execution_result["symbol"]
            side = execution_result["side"]
            quantity = execution_result["order_details"]["filled_quantity"]
            stop_price = trading_decision.get("stop_loss")

            if not stop_price:
                logger.warning("No stop loss price defined, skipping")
                return {
                    "binance_order_id": None,
                    "stop_price": None,
                    "status": "SKIPPED"
                }

            # Determine order side (opposite of entry)
            stop_side = "SELL" if side == "LONG" else "BUY"

            # Place STOP_MARKET order
            stop_order = self.binance_client.create_order(
                symbol=symbol,
                side=stop_side,
                order_type="STOP_MARKET",
                quantity=quantity,
                stop_price=stop_price
            )

            logger.info(
                "Stop loss order placed",
                symbol=symbol,
                stop_price=stop_price,
                order_id=stop_order["orderId"]
            )

            return {
                "binance_order_id": str(stop_order["orderId"]),
                "stop_price": stop_price,
                "status": "PLACED"
            }

        except Exception as e:
            logger.error(
                "Failed to place stop loss order",
                symbol=execution_result["symbol"],
                error=str(e)
            )
            return {
                "binance_order_id": None,
                "stop_price": stop_price if stop_price else None,
                "status": "FAILED"
            }

    def _place_take_profit_orders(
        self,
        execution_result: Dict[str, Any],
        trading_decision: Dict[str, Any]
    ) -> List[Dict[str, Any]]:
        """
        Place take profit orders at multiple levels.

        Args:
            execution_result: Entry execution result
            trading_decision: Original trading decision

        Returns:
            List of take profit order results
        """
        take_profit_orders = []

        try:
            symbol = execution_result["symbol"]
            side = execution_result["side"]
            filled_quantity = execution_result["order_details"]["filled_quantity"]

            take_profit_levels = trading_decision.get("take_profit_levels", [])

            if not take_profit_levels:
                logger.warning("No take profit levels defined, skipping")
                return []

            # Determine order side (opposite of entry)
            tp_side = "SELL" if side == "LONG" else "BUY"

            for tp_level in take_profit_levels:
                tp_price = tp_level["price"]
                quantity_pct = tp_level["quantity_pct"]
                tp_quantity = filled_quantity * quantity_pct

                try:
                    # Place TAKE_PROFIT_MARKET order
                    tp_order = self.binance_client.create_order(
                        symbol=symbol,
                        side=tp_side,
                        order_type="TAKE_PROFIT_MARKET",
                        quantity=round(tp_quantity, 3),
                        stop_price=tp_price
                    )

                    logger.info(
                        "Take profit order placed",
                        symbol=symbol,
                        price=tp_price,
                        quantity_pct=quantity_pct,
                        order_id=tp_order["orderId"]
                    )

                    take_profit_orders.append({
                        "binance_order_id": str(tp_order["orderId"]),
                        "price": tp_price,
                        "quantity_pct": quantity_pct,
                        "status": "PLACED"
                    })

                except Exception as e:
                    logger.error(
                        "Failed to place take profit order",
                        price=tp_price,
                        error=str(e)
                    )
                    take_profit_orders.append({
                        "binance_order_id": None,
                        "price": tp_price,
                        "quantity_pct": quantity_pct,
                        "status": "FAILED"
                    })

            return take_profit_orders

        except Exception as e:
            logger.error(
                "Failed to place take profit orders",
                symbol=execution_result["symbol"],
                error=str(e)
            )
            return []

    # ========== ERROR HANDLING ==========

    def _create_rejected_execution(
        self,
        risk_approval: Dict[str, Any],
        trading_decision: Dict[str, Any],
        reason: str
    ) -> Dict[str, Any]:
        """
        Create execution result for rejected trade.

        Args:
            risk_approval: Risk approval
            trading_decision: Trading decision
            reason: Rejection reason

        Returns:
            Rejected execution result
        """
        return {
            "execution_id": str(uuid.uuid4()),
            "approval_id": risk_approval["approval_id"],
            "decision_id": trading_decision["decision_id"],
            "execution_mode": ExecutionMode.PAPER,
            "execution_status": ExecutionStatus.REJECTED,
            "symbol": trading_decision["symbol"],
            "side": trading_decision["decision"],
            "order_details": {
                "binance_order_id": None,
                "client_order_id": None,
                "order_type": "MARKET",
                "requested_quantity": 0,
                "filled_quantity": 0,
                "avg_fill_price": 0,
                "requested_price": trading_decision["entry_price"],
                "slippage_usdt": 0,
                "slippage_bps": 0,
                "fees_usdt": 0,
                "leverage": trading_decision.get("leverage", 1),
                "position_value_usdt": 0
            },
            "errors": [
                {
                    "error_code": "TRADE_REJECTED",
                    "error_message": reason,
                    "retry_count": 0,
                    "timestamp": datetime.utcnow().isoformat() + "Z"
                }
            ],
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "processing_time_ms": 0,
            "idempotency_check": {
                "is_duplicate": False,
                "original_execution_id": None
            }
        }
