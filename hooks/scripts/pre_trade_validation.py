#!/usr/bin/env python3
"""
Pre-Trade Validation Hook

Validates all preconditions before allowing trade execution.
This is a CRITICAL BLOCKING hook - execution will not proceed if validation fails.
"""

import sys
import json
from datetime import datetime


def validate_pre_trade(state: dict) -> dict:
    """
    Validate pre-trade conditions.

    Args:
        state: Current trading state

    Returns:
        Validation result with pass/fail and messages
    """
    validation_result = {
        "passed": True,
        "checks": {},
        "timestamp": datetime.utcnow().isoformat()
    }

    # Check 1: Risk Manager approval exists
    risk_approval = state.get("risk_approval")
    if not risk_approval:
        validation_result["checks"]["risk_approval_exists"] = {
            "passed": False,
            "message": "No Risk Manager approval found"
        }
        validation_result["passed"] = False
    elif risk_approval.get("approval_status") not in ["APPROVED", "MODIFIED"]:
        validation_result["checks"]["risk_approval_valid"] = {
            "passed": False,
            "message": f"Risk approval status is {risk_approval.get('approval_status')}"
        }
        validation_result["passed"] = False
    else:
        validation_result["checks"]["risk_approval_exists"] = {
            "passed": True,
            "message": "Risk approval valid"
        }

    # Check 2: Execution mode is valid
    mode = state.get("mode")
    if mode not in ["PAPER", "LIVE", "HYBRID"]:
        validation_result["checks"]["execution_mode_valid"] = {
            "passed": False,
            "message": f"Invalid execution mode: {mode}"
        }
        validation_result["passed"] = False
    else:
        validation_result["checks"]["execution_mode_valid"] = {
            "passed": True,
            "message": f"Execution mode: {mode}"
        }

    # Check 3: No critical errors in pipeline
    errors = state.get("errors", [])
    critical_errors = [e for e in errors if e.get("severity") == "critical"]
    if critical_errors:
        validation_result["checks"]["no_critical_errors"] = {
            "passed": False,
            "message": f"Found {len(critical_errors)} critical errors"
        }
        validation_result["passed"] = False
    else:
        validation_result["checks"]["no_critical_errors"] = {
            "passed": True,
            "message": "No critical errors"
        }

    # Check 4: Kill switches
    # (In real implementation, would check database/config for kill switch status)
    validation_result["checks"]["kill_switches"] = {
        "passed": True,
        "message": "All kill switches disabled (placeholder check)"
    }

    return validation_result


def main():
    """
    Hook entry point.

    Reads state from stdin, validates, and outputs result to stdout.
    Exit code 0 = validation passed, Exit code 1 = validation failed
    """
    try:
        # Read state from stdin
        state = json.load(sys.stdin)

        # Validate
        result = validate_pre_trade(state)

        # Output result
        json.dump(result, sys.stdout, indent=2)

        # Exit with appropriate code
        sys.exit(0 if result["passed"] else 1)

    except Exception as e:
        error_result = {
            "passed": False,
            "error": str(e),
            "timestamp": datetime.utcnow().isoformat()
        }
        json.dump(error_result, sys.stdout, indent=2)
        sys.exit(1)


if __name__ == "__main__":
    main()
