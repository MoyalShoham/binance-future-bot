"""
Base prompt components shared across all trading agents.

Patterns adapted from everything-claude-code:
- Structured JSON output enforcement
- Confidence scoring with reasoning
- Iterative self-verification before responding
- Graceful error recovery with safe defaults
- Selective context (token efficiency)
"""

import json
from typing import Dict, Any, Tuple


STRUCTURED_OUTPUT_INSTRUCTION = """
## Output Format
You MUST respond with valid JSON only. No markdown, no explanations outside the JSON.
Do not wrap your response in ```json``` code fences. Return raw JSON only.
"""

CONFIDENCE_SCORING_INSTRUCTION = """
## Confidence Scoring
Include a "confidence" field (0.0-1.0) in your response:
- 0.9-1.0: Very high confidence - multiple strong confirming signals, clear pattern
- 0.7-0.89: High confidence - clear signal with some confirmation
- 0.5-0.69: Moderate confidence - mixed or ambiguous signals
- 0.3-0.49: Low confidence - weak or conflicting signals
- 0.0-0.29: Very low confidence - insufficient data or contradictions
Include "confidence_reasoning" explaining your confidence level.
"""

ITERATIVE_VERIFICATION_INSTRUCTION = """
## Self-Verification (before responding)
1. Verify all numerical values are within reasonable ranges for crypto markets
2. Check that your reasoning is consistent with your conclusion
3. Ensure your confidence level matches the strength of evidence
4. Confirm your response is valid JSON with all required fields
"""

ERROR_RECOVERY_INSTRUCTION = """
## Error Recovery
If you cannot complete the analysis due to missing or invalid data:
- Set confidence to 0.0
- Set the primary output to the safest default (e.g., NO_TRADE, "safe")
- Explain the issue in "warnings" or "confidence_reasoning"
- Never fabricate data points or indicators
"""

SELECTIVE_CONTEXT_NOTE = """
## Context Boundaries
You are provided with pre-processed market data. Focus only on data relevant
to your specific role. Do not attempt analysis outside your agent's authority.
"""


def build_prompt(
    system_template: str,
    context_data: Dict[str, Any],
) -> Tuple[str, str]:
    """
    Build system and user prompts from template and context data.

    Args:
        system_template: Agent-specific system prompt
        context_data: Dict of context data to serialize as user prompt

    Returns:
        Tuple of (system_prompt, user_prompt)
    """
    system = system_template
    system += "\n" + STRUCTURED_OUTPUT_INSTRUCTION
    system += "\n" + CONFIDENCE_SCORING_INSTRUCTION
    system += "\n" + ITERATIVE_VERIFICATION_INSTRUCTION
    system += "\n" + ERROR_RECOVERY_INSTRUCTION
    system += "\n" + SELECTIVE_CONTEXT_NOTE

    # Serialize context data as JSON for the user prompt
    user = json.dumps(context_data, indent=2, default=str)

    return system, user
