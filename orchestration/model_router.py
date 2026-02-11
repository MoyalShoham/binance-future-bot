"""
Model Router - Cheap-Model-First Routing Strategy with LLM Invocation

Routes AI model requests based on task type and confidence thresholds.
Escalates to more expensive models if confidence is too low.
Actually invokes LLMs via LangChain ChatModel classes.
"""

import os
import re
import json
from typing import Dict, Any, Optional, Tuple
from enum import Enum
import structlog
from dotenv import load_dotenv

load_dotenv()

from langchain_anthropic import ChatAnthropic
from langchain_openai import ChatOpenAI
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_ollama import ChatOllama
from langchain_core.messages import SystemMessage, HumanMessage
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

logger = structlog.get_logger()


class TaskType(str, Enum):
    """Types of AI tasks requiring model routing."""
    CLASSIFICATION = "classification"
    PATTERN_MATCHING = "pattern_matching"
    SIMPLE_REASONING = "simple_reasoning"
    RISK_REASONING = "risk_reasoning"
    COMPLEX_DECISION = "complex_decision"
    CRITICAL_DECISION = "critical_decision"
    SENTIMENT_ANALYSIS = "sentiment_analysis"
    ANOMALY_DETECTION = "anomaly_detection"


class ModelTier(str, Enum):
    """Model tiers ordered by cost (low to high)."""
    OLLAMA = "ollama-local"
    NANO = "gpt-4o-mini"
    FLASH = "gemini-2.5-flash"
    HAIKU = "claude-3-5-haiku-20241022"
    SONNET = "claude-sonnet-4-20250514"


# Map tier to provider for display
TIER_PROVIDER = {
    ModelTier.OLLAMA: "ollama",
    ModelTier.NANO: "openai",
    ModelTier.FLASH: "google",
    ModelTier.HAIKU: "anthropic",
    ModelTier.SONNET: "anthropic",
}


class ModelRouter:
    """
    Routes AI requests to appropriate models based on task type and confidence.

    Implements cheap-model-first strategy with automatic escalation.
    Actually invokes LLMs via LangChain and parses JSON responses.
    """

    # Model configuration: cost per 1K tokens (input)
    MODEL_COSTS = {
        ModelTier.OLLAMA: 0.0,  # Free - local inference
        ModelTier.NANO: 0.00015,
        ModelTier.FLASH: 0.000075,
        ModelTier.HAIKU: 0.0008,
        ModelTier.SONNET: 0.003
    }

    # Default model for each task type
    TASK_DEFAULT_MODELS = {
        TaskType.CLASSIFICATION: ModelTier.NANO,
        TaskType.PATTERN_MATCHING: ModelTier.FLASH,
        TaskType.SIMPLE_REASONING: ModelTier.NANO,
        TaskType.SENTIMENT_ANALYSIS: ModelTier.FLASH,
        TaskType.RISK_REASONING: ModelTier.HAIKU,
        TaskType.COMPLEX_DECISION: ModelTier.HAIKU,
        TaskType.CRITICAL_DECISION: ModelTier.SONNET,
        TaskType.ANOMALY_DETECTION: ModelTier.SONNET
    }

    # Model escalation chain
    ESCALATION_CHAIN = [
        ModelTier.NANO,
        ModelTier.FLASH,
        ModelTier.HAIKU,
        ModelTier.SONNET
    ]

    # Confidence thresholds by context
    DEFAULT_THRESHOLDS = {
        "research": 0.70,
        "decision": 0.75,
        "risk": 0.80,
        "emergency": 0.90
    }

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        self.config = config or {}
        self.confidence_thresholds = self.config.get(
            "confidence_thresholds",
            self.DEFAULT_THRESHOLDS
        )
        self.enabled = self.config.get("enabled", True)
        self.primary_provider = self.config.get("primary_provider", "anthropic")

        # Timeout settings
        timeouts = self.config.get("timeouts", {})
        self._haiku_timeout = timeouts.get("haiku_seconds", 15)
        self._sonnet_timeout = timeouts.get("sonnet_seconds", 30)
        self._ollama_timeout = timeouts.get("ollama_seconds", 30)

        # Track model usage statistics
        self.usage_stats = {
            model: {"calls": 0, "escalations": 0, "total_cost": 0.0}
            for model in ModelTier
        }

        # Cache model instances to avoid re-creating on every call
        self._model_cache: Dict[ModelTier, Any] = {}

        logger.debug(
            "ModelRouter initialized",
            enabled=self.enabled,
            primary_provider=self.primary_provider,
            thresholds=self.confidence_thresholds
        )

    def _create_model(self, tier: ModelTier) -> Any:
        """Create a LangChain ChatModel instance for the given tier."""
        if tier in self._model_cache:
            return self._model_cache[tier]

        model = None
        if tier == ModelTier.OLLAMA:
            ollama_model = self.config.get("ollama_model", "qwen2.5:7b")
            ollama_base_url = self.config.get("ollama_base_url", "http://localhost:11434")
            model = ChatOllama(
                model=ollama_model,
                base_url=ollama_base_url,
                temperature=0.1,
                num_predict=2048,
                format="json",
                timeout=self._ollama_timeout,
            )
        elif tier == ModelTier.HAIKU:
            api_key = os.getenv("ANTHROPIC_API_KEY")
            if not api_key:
                raise ValueError("ANTHROPIC_API_KEY not set")
            model = ChatAnthropic(
                model="claude-3-5-haiku-20241022",
                api_key=api_key,
                temperature=0.1,
                max_tokens=2048,
                timeout=self._haiku_timeout,
            )
        elif tier == ModelTier.SONNET:
            api_key = os.getenv("ANTHROPIC_API_KEY")
            if not api_key:
                raise ValueError("ANTHROPIC_API_KEY not set")
            model = ChatAnthropic(
                model="claude-sonnet-4-20250514",
                api_key=api_key,
                temperature=0.2,
                max_tokens=4096,
                timeout=self._sonnet_timeout,
            )
        elif tier == ModelTier.NANO:
            api_key = os.getenv("OPENAI_API_KEY")
            if not api_key:
                raise ValueError("OPENAI_API_KEY not set")
            model = ChatOpenAI(
                model="gpt-4o-mini",
                api_key=api_key,
                temperature=0.1,
                max_tokens=2048,
                timeout=15,
            )
        elif tier == ModelTier.FLASH:
            api_key = os.getenv("GOOGLE_API_KEY")
            if not api_key:
                raise ValueError("GOOGLE_API_KEY not set")
            model = ChatGoogleGenerativeAI(
                model="gemini-2.5-flash",
                google_api_key=api_key,
                temperature=0.1,
                max_output_tokens=8192,
                disable_streaming=True,
            )

        if model is None:
            raise ValueError(f"Unknown model tier: {tier}")

        self._model_cache[tier] = model
        return model

    def _parse_json_response(self, content: str) -> Optional[Dict[str, Any]]:
        """Parse JSON from LLM response, stripping think tags and markdown fences."""
        text = content.strip()

        # Strip DeepSeek-R1 <think>...</think> reasoning blocks
        text = re.sub(r'<think>[\s\S]*?</think>', '', text).strip()

        # Strip markdown code fences
        if text.startswith("```"):
            # Remove opening fence (```json or ```)
            text = re.sub(r'^```(?:json)?\s*\n?', '', text)
            # Remove closing fence
            text = re.sub(r'\n?```\s*$', '', text)
            text = text.strip()

        try:
            return json.loads(text)
        except json.JSONDecodeError:
            # Try to extract JSON object from the text
            match = re.search(r'\{[\s\S]*\}', text)
            if match:
                try:
                    return json.loads(match.group())
                except json.JSONDecodeError:
                    pass
            logger.warning("Failed to parse JSON from LLM response", content=text[:200])
            return None

    def _invoke_model(
        self,
        tier: ModelTier,
        system_prompt: str,
        user_prompt: str,
    ) -> Dict[str, Any]:
        """
        Invoke a single model and return parsed result.

        Returns dict with: response, model_used, tokens, raw_content
        Raises on failure (caller handles retry/escalation).
        """
        model = self._create_model(tier)
        messages = [
            SystemMessage(content=system_prompt),
            HumanMessage(content=user_prompt),
        ]

        result = model.invoke(messages)
        raw_content = result.content if hasattr(result, 'content') else str(result)

        # Extract token usage if available
        tokens = 0
        if hasattr(result, 'usage_metadata') and result.usage_metadata:
            tokens = (
                result.usage_metadata.get('input_tokens', 0)
                + result.usage_metadata.get('output_tokens', 0)
            )
        elif hasattr(result, 'response_metadata'):
            usage = result.response_metadata.get('usage', {})
            tokens = usage.get('input_tokens', 0) + usage.get('output_tokens', 0)

        parsed = self._parse_json_response(raw_content)

        return {
            "parsed": parsed,
            "raw_content": raw_content,
            "model_used": tier.value,
            "tokens": tokens,
        }

    def select_model(
        self,
        task_type: TaskType,
        context: str = "decision",
        force_model: Optional[ModelTier] = None
    ) -> ModelTier:
        """Select the appropriate model for a task."""
        if force_model:
            return force_model

        # If primary is ollama, route everything locally
        if self.primary_provider == "ollama":
            return ModelTier.OLLAMA

        # If primary is google, route everything to Flash
        if self.primary_provider == "google":
            return ModelTier.FLASH

        # If primary is anthropic, bias towards Haiku for most tasks
        if self.primary_provider == "anthropic":
            default = self.TASK_DEFAULT_MODELS.get(task_type, ModelTier.HAIKU)
            if default in (ModelTier.NANO, ModelTier.FLASH):
                return ModelTier.HAIKU
            return default

        return self.TASK_DEFAULT_MODELS.get(task_type, ModelTier.HAIKU)

    def should_escalate(
        self,
        current_model: ModelTier,
        confidence: float,
        context: str = "decision"
    ) -> Tuple[bool, Optional[ModelTier]]:
        """Determine if model escalation is needed based on confidence."""
        threshold = self.confidence_thresholds.get(context, 0.75)

        if confidence >= threshold:
            return False, None

        try:
            current_idx = self.ESCALATION_CHAIN.index(current_model)
            if current_idx < len(self.ESCALATION_CHAIN) - 1:
                next_model = self.ESCALATION_CHAIN[current_idx + 1]
                logger.warning(
                    "Model escalation triggered",
                    current_model=current_model.value,
                    next_model=next_model.value,
                    confidence=confidence,
                    threshold=threshold,
                    context=context
                )
                return True, next_model
            else:
                logger.warning(
                    "Low confidence at highest model tier",
                    model=current_model.value,
                    confidence=confidence,
                    threshold=threshold
                )
                return False, None
        except ValueError:
            return False, None

    def invoke(
        self,
        task_type: TaskType,
        system_prompt: str,
        user_prompt: str,
        context: str = "decision",
        max_escalations: int = 1,
    ) -> Dict[str, Any]:
        """
        Invoke an LLM with automatic escalation on low confidence.

        Args:
            task_type: Type of task for model selection
            system_prompt: System prompt with agent role/instructions
            user_prompt: User prompt with context data
            context: Context for confidence threshold (research/decision/risk/emergency)
            max_escalations: Maximum number of escalation attempts

        Returns:
            Dict with keys: response (parsed dict), model_used (str),
            confidence (float), tokens (int), llm_available (bool)
        """
        if not self.enabled:
            return {"llm_available": False, "error": "LLM disabled in config"}

        current_tier = self.select_model(task_type, context)
        escalation_count = 0

        while True:
            try:
                result = self._invoke_with_retry(current_tier, system_prompt, user_prompt)

                parsed = result["parsed"]
                tokens = result["tokens"]

                # Record usage
                self.record_usage(current_tier, tokens, was_escalation=escalation_count > 0)

                if parsed is None:
                    logger.warning("LLM returned unparseable response", model=current_tier.value)
                    return {
                        "llm_available": True,
                        "response": None,
                        "model_used": current_tier.value,
                        "confidence": 0.0,
                        "tokens": tokens,
                        "raw_content": result.get("raw_content", ""),
                    }

                # Extract confidence from response
                confidence = parsed.get("confidence", 0.5)

                # Check if escalation needed
                if escalation_count < max_escalations:
                    needs_escalation, next_tier = self.should_escalate(
                        current_tier, confidence, context
                    )
                    if needs_escalation and next_tier:
                        escalation_count += 1
                        current_tier = next_tier
                        continue

                return {
                    "llm_available": True,
                    "response": parsed,
                    "model_used": current_tier.value,
                    "confidence": confidence,
                    "tokens": tokens,
                    "escalation_count": escalation_count,
                }

            except Exception as e:
                logger.error(
                    "LLM invocation failed",
                    model=current_tier.value,
                    error=str(e),
                    task_type=task_type.value,
                )

                # Try escalation on error
                if escalation_count < max_escalations:
                    try:
                        current_idx = self.ESCALATION_CHAIN.index(current_tier)
                        if current_idx < len(self.ESCALATION_CHAIN) - 1:
                            escalation_count += 1
                            current_tier = self.ESCALATION_CHAIN[current_idx + 1]
                            logger.info("Escalating after error", next_model=current_tier.value)
                            continue
                    except ValueError:
                        pass

                return {"llm_available": False, "error": str(e)}

    def _invoke_with_retry(
        self,
        tier: ModelTier,
        system_prompt: str,
        user_prompt: str,
    ) -> Dict[str, Any]:
        """Invoke model with retry logic (3 attempts, exponential backoff)."""

        @retry(
            stop=stop_after_attempt(3),
            wait=wait_exponential(multiplier=1, min=1, max=10),
            reraise=True,
        )
        def _do_invoke():
            return self._invoke_model(tier, system_prompt, user_prompt)

        return _do_invoke()

    def record_usage(
        self,
        model: ModelTier,
        tokens_used: int,
        was_escalation: bool = False
    ) -> None:
        """Record model usage for cost tracking."""
        cost = (tokens_used / 1000) * self.MODEL_COSTS.get(model, 0.001)

        self.usage_stats[model]["calls"] += 1
        self.usage_stats[model]["total_cost"] += cost

        if was_escalation:
            self.usage_stats[model]["escalations"] += 1

        logger.debug(
            "Model usage recorded",
            model=model.value,
            tokens=tokens_used,
            cost=round(cost, 6),
            was_escalation=was_escalation
        )

    def get_usage_stats(self) -> Dict[str, Any]:
        """Get model usage statistics."""
        total_cost = sum(stats["total_cost"] for stats in self.usage_stats.values())
        total_calls = sum(stats["calls"] for stats in self.usage_stats.values())

        return {
            "by_model": {k.value: v for k, v in self.usage_stats.items()},
            "total_cost_usd": round(total_cost, 6),
            "total_calls": total_calls,
            "avg_cost_per_call": round(total_cost / total_calls, 6) if total_calls > 0 else 0
        }

    def reset_stats(self) -> None:
        """Reset usage statistics."""
        for model in ModelTier:
            self.usage_stats[model] = {
                "calls": 0,
                "escalations": 0,
                "total_cost": 0.0
            }
        logger.info("Model usage stats reset")
