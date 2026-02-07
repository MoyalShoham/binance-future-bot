"""
JSON Schema Validator for Agent Messages

Validates all inter-agent communication against defined JSON schemas.
Ensures deterministic, type-safe message passing.
"""

import json
import hashlib
from pathlib import Path
from typing import Dict, Any, Optional
from datetime import datetime
from jsonschema import validate, ValidationError, Draft7Validator
from jsonschema.exceptions import SchemaError


class SchemaValidator:
    """Validates messages against JSON schemas with audit trail support."""

    SCHEMA_DIR = Path(__file__).parent
    SCHEMA_VERSION = "1.0.0"

    # Schema file mapping
    SCHEMAS = {
        "agent_message": "agent_message.schema.json",
        "research_summary": "research_summary.schema.json",
        "trading_decision": "trading_decision.schema.json",
        "risk_approval": "risk_approval.schema.json",
        "execution_result": "execution_result.schema.json"
    }

    def __init__(self):
        self._schemas: Dict[str, Dict] = {}
        self._load_schemas()

    def _load_schemas(self) -> None:
        """Load all JSON schemas from disk."""
        for schema_name, schema_file in self.SCHEMAS.items():
            schema_path = self.SCHEMA_DIR / schema_file
            if not schema_path.exists():
                raise FileNotFoundError(f"Schema file not found: {schema_path}")

            with open(schema_path, 'r') as f:
                schema = json.load(f)

            # Validate the schema itself
            try:
                Draft7Validator.check_schema(schema)
            except SchemaError as e:
                raise ValueError(f"Invalid schema {schema_name}: {e}")

            self._schemas[schema_name] = schema

    def validate_message(
        self,
        message: Dict[str, Any],
        message_type: str,
        strict: bool = True
    ) -> tuple[bool, Optional[str]]:
        """
        Validate a message against its schema.

        Args:
            message: The message dict to validate
            message_type: Type of message (e.g., 'research_summary')
            strict: If True, raise exception on validation failure

        Returns:
            Tuple of (is_valid, error_message)

        Raises:
            ValidationError: If strict=True and validation fails
        """
        if message_type not in self._schemas:
            error = f"Unknown message type: {message_type}"
            if strict:
                raise ValueError(error)
            return False, error

        schema = self._schemas[message_type]

        try:
            validate(instance=message, schema=schema)
            return True, None
        except ValidationError as e:
            error_msg = f"Validation failed for {message_type}: {e.message} at {'.'.join(str(p) for p in e.path)}"
            if strict:
                raise ValidationError(error_msg)
            return False, error_msg

    def validate_agent_message_wrapper(
        self,
        message: Dict[str, Any],
        strict: bool = True
    ) -> tuple[bool, Optional[str]]:
        """
        Validate the base agent_message wrapper.

        All messages must conform to agent_message schema.
        """
        return self.validate_message(message, "agent_message", strict)

    def compute_input_hash(self, data: Dict[str, Any]) -> str:
        """
        Compute SHA-256 hash of input data for audit trail.

        Args:
            data: Input data dict

        Returns:
            Hex-encoded SHA-256 hash
        """
        # Sort keys for deterministic hashing
        json_str = json.dumps(data, sort_keys=True, ensure_ascii=True)
        return hashlib.sha256(json_str.encode()).hexdigest()

    def create_agent_message(
        self,
        agent_id: str,
        message_type: str,
        payload: Dict[str, Any],
        metadata: Optional[Dict[str, Any]] = None,
        correlation_id: Optional[str] = None,
        parent_message_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Create a properly formatted agent message.

        Args:
            agent_id: Sender agent identifier
            message_type: Type of message
            payload: Message-specific data
            metadata: Optional metadata (model, confidence, etc.)
            correlation_id: UUID for tracking related messages
            parent_message_id: Reference to parent message

        Returns:
            Formatted agent message dict
        """
        message = {
            "agent_id": agent_id,
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "schema_version": self.SCHEMA_VERSION,
            "message_type": message_type,
            "payload": payload
        }

        if metadata:
            message["metadata"] = metadata
        if correlation_id:
            message["correlation_id"] = correlation_id
        if parent_message_id:
            message["parent_message_id"] = parent_message_id

        # Validate before returning
        self.validate_agent_message_wrapper(message, strict=True)

        return message

    def validate_full_message(
        self,
        message: Dict[str, Any],
        strict: bool = True
    ) -> tuple[bool, Optional[str]]:
        """
        Validate both the wrapper and the payload against their schemas.

        Args:
            message: Complete agent message
            strict: If True, raise exception on validation failure

        Returns:
            Tuple of (is_valid, error_message)
        """
        # First validate the wrapper
        valid, error = self.validate_agent_message_wrapper(message, strict=False)
        if not valid:
            if strict:
                raise ValidationError(f"Wrapper validation failed: {error}")
            return False, error

        # Then validate the payload
        message_type = message.get("message_type")
        payload = message.get("payload", {})

        if message_type in self._schemas:
            return self.validate_message(payload, message_type, strict)

        # Message type doesn't have a specific schema (e.g., error messages)
        return True, None

    def get_schema(self, message_type: str) -> Dict[str, Any]:
        """Get a schema by message type."""
        if message_type not in self._schemas:
            raise ValueError(f"Unknown message type: {message_type}")
        return self._schemas[message_type].copy()


# Global validator instance
_validator: Optional[SchemaValidator] = None


def get_validator() -> SchemaValidator:
    """Get or create the global schema validator instance."""
    global _validator
    if _validator is None:
        _validator = SchemaValidator()
    return _validator


# Convenience functions
def validate_message(message: Dict[str, Any], message_type: str, strict: bool = True) -> tuple[bool, Optional[str]]:
    """Validate a message against its schema."""
    return get_validator().validate_message(message, message_type, strict)


def validate_full_message(message: Dict[str, Any], strict: bool = True) -> tuple[bool, Optional[str]]:
    """Validate both wrapper and payload."""
    return get_validator().validate_full_message(message, strict)


def compute_hash(data: Dict[str, Any]) -> str:
    """Compute SHA-256 hash for audit trail."""
    return get_validator().compute_input_hash(data)


def create_message(
    agent_id: str,
    message_type: str,
    payload: Dict[str, Any],
    **kwargs
) -> Dict[str, Any]:
    """Create a properly formatted agent message."""
    return get_validator().create_agent_message(agent_id, message_type, payload, **kwargs)
