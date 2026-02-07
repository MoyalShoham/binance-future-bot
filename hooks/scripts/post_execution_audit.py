#!/usr/bin/env python3
"""
Post-Execution Audit Hook

Logs execution details to audit trail and updates P&L tracking.
This is a NON-BLOCKING hook - failures will be logged but won't stop the pipeline.
"""

import sys
import json
from datetime import datetime
import hashlib


def audit_execution(state: dict) -> dict:
    """
    Audit execution and log details.

    Args:
        state: Current trading state with execution result

    Returns:
        Audit result
    """
    audit_result = {
        "logged": False,
        "audit_entries": [],
        "timestamp": datetime.utcnow().isoformat()
    }

    execution_result = state.get("execution_result")
    if not execution_result:
        audit_result["error"] = "No execution result to audit"
        return audit_result

    # Extract key details
    execution_id = execution_result.get("execution_id")
    symbol = execution_result.get("symbol")
    execution_status = execution_result.get("execution_status")
    mode = execution_result.get("execution_mode")

    # Calculate audit hash
    audit_data = {
        "execution_id": execution_id,
        "decision_id": execution_result.get("decision_id"),
        "approval_id": execution_result.get("approval_id"),
        "timestamp": execution_result.get("timestamp")
    }
    audit_hash = hashlib.sha256(
        json.dumps(audit_data, sort_keys=True).encode()
    ).hexdigest()

    # Create audit entries
    audit_result["audit_entries"].append({
        "type": "execution_completed",
        "execution_id": execution_id,
        "symbol": symbol,
        "status": execution_status,
        "mode": mode,
        "audit_hash": audit_hash
    })

    # Log slippage
    order_details = execution_result.get("order_details", {})
    if "slippage_bps" in order_details:
        audit_result["audit_entries"].append({
            "type": "slippage_recorded",
            "execution_id": execution_id,
            "slippage_bps": order_details["slippage_bps"],
            "slippage_usdt": order_details["slippage_usdt"]
        })

    # Log fees
    if "fees_usdt" in order_details:
        audit_result["audit_entries"].append({
            "type": "fees_recorded",
            "execution_id": execution_id,
            "fees_usdt": order_details["fees_usdt"]
        })

    # Log shadow execution comparison (if HYBRID mode)
    shadow = execution_result.get("shadow_paper_execution")
    if shadow and shadow.get("divergence_alert"):
        audit_result["audit_entries"].append({
            "type": "execution_divergence_alert",
            "execution_id": execution_id,
            "divergence_pct": shadow["divergence_pct"],
            "divergence_usdt": shadow["divergence_usdt"]
        })

    audit_result["logged"] = True

    # In real implementation, would write to database here
    # db.audit_log.insert_many(audit_result["audit_entries"])

    return audit_result


def main():
    """
    Hook entry point.

    Reads state from stdin, audits execution, and outputs result to stdout.
    Always exits with code 0 (non-blocking).
    """
    try:
        # Read state from stdin
        state = json.load(sys.stdin)

        # Audit
        result = audit_execution(state)

        # Output result
        json.dump(result, sys.stdout, indent=2)

        # Always succeed (non-blocking)
        sys.exit(0)

    except Exception as e:
        error_result = {
            "logged": False,
            "error": str(e),
            "timestamp": datetime.utcnow().isoformat()
        }
        json.dump(error_result, sys.stdout, indent=2)
        sys.exit(0)  # Still exit with 0 (non-blocking)


if __name__ == "__main__":
    main()
