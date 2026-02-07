"""
Execution Agent Implementation

Interfaces with Binance Futures API to execute approved trades.
"""

from typing import Dict, Any, Optional
from datetime import datetime
import uuid
import structlog

from .base_agent import BaseAgent
from orchestration.state_manager import TradingState, ExecutionMode
from infrastructure.execution_modes import OrderExecutor

logger = structlog.get_logger()


class ExecutionAgent(BaseAgent):
    """
    Execution Agent: Order placement and position management.
    """

    def __init__(self, config: Dict[str, Any]):
        super().__init__("execution-agent", config)

        # TODO: Initialize Binance client
        self.binance_client = None  # Mock for now

        self.order_executor = OrderExecutor(self.binance_client, config)
        self.position_tracker = PositionTracker()

    def execute(self, state: TradingState) -> Dict[str, Any]:
        """
        Execute approved trade.

        Args:
            state: Trading state with risk_approval

        Returns:
            Execution result conforming to execution_result schema
        """
        start_time = datetime.utcnow()

        risk_approval = state.get("risk_approval")
        if not risk_approval:
            raise ValueError("No risk approval in state")

        trading_decision = state.get("trading_decision")
        if not trading_decision:
            raise ValueError("No trading decision in state")

        approval_status = risk_approval["approval_status"]

        self.logger.info(
            "Starting execution",
            approval_status=approval_status,
            mode=state.get("mode"),
            correlation_id=state.get("correlation_id")
        )

        # Check approval status
        if approval_status == "REJECTED":
            return self._create_rejected_execution(
                risk_approval,
                trading_decision,
                "Trade rejected by Risk Manager"
            )

        # Get execution parameters
        if approval_status == "MODIFIED":
            modified_params = risk_approval["modified_parameters"]
            symbol = trading_decision["symbol"]
            side = trading_decision["decision"]
            entry_price = trading_decision["entry_price"]

            # Use modified parameters
            position_size_usdt = modified_params.get("position_size_usdt")
            leverage = modified_params.get("leverage")
            stop_loss = modified_params.get("stop_loss", trading_decision["stop_loss"])
        else:  # APPROVED
            symbol = trading_decision["symbol"]
            side = trading_decision["decision"]
            entry_price = trading_decision["entry_price"]
            position_size_usdt = trading_decision["position_size_usdt"]
            leverage = trading_decision["leverage"]
            stop_loss = trading_decision["stop_loss"]

        # Prepare approval dict for executor
        approval_for_executor = {
            "approval_id": risk_approval["id"],
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

        # Execute trade
        mode = state.get("mode", ExecutionMode.PAPER)
        execution_result = self.order_executor.execute_trade(
            approval_for_executor,
            mode
        )

        # Register position if filled
        if execution_result["execution_status"] in ["FILLED", "PARTIALLY_FILLED"]:
            self.position_tracker.register_position(execution_result)

        processing_time_ms = (datetime.utcnow() - start_time).total_seconds() * 1000

        self.logger.info(
            "Execution completed",
            status=execution_result["execution_status"],
            processing_time_ms=processing_time_ms
        )

        # Validate output
        self.validate_output(execution_result, "execution_result")

        return execution_result

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
            "approval_id": risk_approval["id"],
            "decision_id": trading_decision["decision_id"],
            "execution_mode": "PAPER",
            "execution_status": "REJECTED",
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
                "leverage": 0,
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


class PositionTracker:
    """
    Tracks open positions for monitoring and P&L calculation.
    """

    def __init__(self):
        self.positions = {}  # symbol -> position_info

    def register_position(self, execution_result: Dict[str, Any]):
        """
        Register new position after execution.

        Args:
            execution_result: Execution result
        """
        symbol = execution_result["symbol"]
        order_details = execution_result["order_details"]

        self.positions[symbol] = {
            "execution_id": execution_result["execution_id"],
            "side": execution_result["side"],
            "entry_price": order_details["avg_fill_price"],
            "quantity": order_details["filled_quantity"],
            "leverage": order_details["leverage"],
            "stop_loss": execution_result.get("stop_loss_order", {}).get("stop_price"),
            "entry_time": execution_result["timestamp"]
        }

        logger.info("Position registered", symbol=symbol, side=execution_result["side"])

    def get_position(self, symbol: str) -> Optional[Dict[str, Any]]:
        """
        Get position info for symbol.

        Args:
            symbol: Trading symbol

        Returns:
            Position info or None
        """
        return self.positions.get(symbol)

    def close_position(self, symbol: str):
        """
        Mark position as closed.

        Args:
            symbol: Trading symbol
        """
        if symbol in self.positions:
            del self.positions[symbol]
            logger.info("Position closed", symbol=symbol)

    def get_all_positions(self) -> Dict[str, Dict[str, Any]]:
        """
        Get all open positions.

        Returns:
            Dict of all positions
        """
        return self.positions.copy()
