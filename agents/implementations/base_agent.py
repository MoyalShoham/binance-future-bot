"""
Base Agent Class

Abstract base class for all trading agents.
"""

from abc import ABC, abstractmethod
from typing import Dict, Any, Optional
from datetime import datetime
import structlog
import uuid

from schemas.validator import get_validator, compute_hash

logger = structlog.get_logger()


class BaseAgent(ABC):
    """
    Abstract base class for all agents.

    Provides common functionality:
    - State management
    - Schema validation
    - Logging
    - Error handling
    - Performance tracking
    """

    def __init__(self, agent_id: str, config: Dict[str, Any]):
        """
        Initialize base agent.

        Args:
            agent_id: Unique agent identifier
            config: Agent configuration
        """
        self.agent_id = agent_id
        self.config = config
        self.validator = get_validator()
        self.logger = logger.bind(agent_id=agent_id)

        self.logger.info("Agent initialized")

    @abstractmethod
    def execute(self, state: Dict[str, Any]) -> Dict[str, Any]:
        """
        Execute agent logic.

        Args:
            state: Current trading state

        Returns:
            Agent output (specific to each agent)
        """
        pass

    def create_agent_message(
        self,
        message_type: str,
        payload: Dict[str, Any],
        correlation_id: Optional[str] = None,
        parent_message_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Create a properly formatted agent message.

        Args:
            message_type: Type of message
            payload: Message payload
            correlation_id: UUID for tracking related messages
            parent_message_id: Reference to parent message
            metadata: Optional metadata

        Returns:
            Formatted agent message
        """
        return self.validator.create_agent_message(
            agent_id=self.agent_id,
            message_type=message_type,
            payload=payload,
            correlation_id=correlation_id,
            parent_message_id=parent_message_id,
            metadata=metadata
        )

    def validate_output(self, output: Dict[str, Any], schema_type: str) -> None:
        """
        Validate agent output against schema.

        Args:
            output: Output data to validate
            schema_type: Schema type (e.g., 'research_summary')

        Raises:
            ValidationError: If validation fails
        """
        self.validator.validate_message(output, schema_type, strict=True)

    def compute_input_hash(self, data: Dict[str, Any]) -> str:
        """
        Compute SHA-256 hash of input data.

        Args:
            data: Input data

        Returns:
            Hex-encoded hash
        """
        return compute_hash(data)

    def track_performance(self, func):
        """
        Decorator to track agent performance.

        Measures execution time and logs metrics.
        """
        def wrapper(*args, **kwargs):
            start_time = datetime.utcnow()

            try:
                result = func(*args, **kwargs)

                processing_time_ms = (datetime.utcnow() - start_time).total_seconds() * 1000

                self.logger.info(
                    "Agent execution completed",
                    processing_time_ms=processing_time_ms
                )

                return result

            except Exception as e:
                processing_time_ms = (datetime.utcnow() - start_time).total_seconds() * 1000

                self.logger.error(
                    "Agent execution failed",
                    error=str(e),
                    processing_time_ms=processing_time_ms
                )

                raise

        return wrapper

    def handle_error(
        self,
        error: Exception,
        context: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Handle agent errors and create error message.

        Args:
            error: Exception that occurred
            context: Error context

        Returns:
            Error message dict
        """
        error_message = {
            "agent_id": self.agent_id,
            "error_type": type(error).__name__,
            "error_message": str(error),
            "context": context,
            "timestamp": datetime.utcnow().isoformat()
        }

        self.logger.error("Agent error", error=error_message)

        return error_message
