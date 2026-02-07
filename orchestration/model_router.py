"""
Model Router - Cheap-Model-First Routing Strategy

Routes AI model requests based on task type and confidence thresholds.
Escalates to more expensive models if confidence is too low.
"""

from typing import Dict, Any, Optional, List, Tuple
from enum import Enum
import structlog

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
    NANO = "gpt-4o-nano"
    FLASH = "gemini-1.5-flash"
    HAIKU = "claude-3.5-haiku"
    SONNET = "claude-3.7-sonnet"


class ModelRouter:
    """
    Routes AI requests to appropriate models based on task type and confidence.

    Implements cheap-model-first strategy with automatic escalation.
    """

    # Model configuration: cost per 1K tokens (input)
    MODEL_COSTS = {
        ModelTier.NANO: 0.00015,
        ModelTier.FLASH: 0.000075,
        ModelTier.HAIKU: 0.0008,
        ModelTier.SONNET: 0.003  # Highest tier
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
        """
        Initialize model router.

        Args:
            config: Optional configuration overrides
        """
        self.config = config or {}
        self.confidence_thresholds = self.config.get(
            "confidence_thresholds",
            self.DEFAULT_THRESHOLDS
        )

        # Track model usage statistics
        self.usage_stats = {
            model: {"calls": 0, "escalations": 0, "total_cost": 0.0}
            for model in ModelTier
        }

        logger.info("ModelRouter initialized", thresholds=self.confidence_thresholds)

    def select_model(
        self,
        task_type: TaskType,
        context: str = "decision",
        force_model: Optional[ModelTier] = None
    ) -> ModelTier:
        """
        Select the appropriate model for a task.

        Args:
            task_type: Type of task to perform
            context: Context for confidence threshold (research/decision/risk/emergency)
            force_model: Optional model to force (overrides routing)

        Returns:
            Selected ModelTier
        """
        if force_model:
            logger.info("Model forced by caller", model=force_model, task_type=task_type)
            return force_model

        # Get default model for this task type
        model = self.TASK_DEFAULT_MODELS.get(task_type, ModelTier.HAIKU)

        logger.debug(
            "Model selected",
            task_type=task_type,
            model=model,
            context=context
        )

        return model

    def should_escalate(
        self,
        current_model: ModelTier,
        confidence: float,
        context: str = "decision"
    ) -> Tuple[bool, Optional[ModelTier]]:
        """
        Determine if model escalation is needed based on confidence.

        Args:
            current_model: Current model that produced the result
            confidence: Confidence score from the model (0-1)
            context: Context for threshold lookup

        Returns:
            Tuple of (should_escalate, next_model)
        """
        threshold = self.confidence_thresholds.get(context, 0.75)

        if confidence >= threshold:
            # Confidence is acceptable
            return False, None

        # Find next model in escalation chain
        try:
            current_idx = self.ESCALATION_CHAIN.index(current_model)
            if current_idx < len(self.ESCALATION_CHAIN) - 1:
                next_model = self.ESCALATION_CHAIN[current_idx + 1]
                logger.warning(
                    "Model escalation triggered",
                    current_model=current_model,
                    next_model=next_model,
                    confidence=confidence,
                    threshold=threshold,
                    context=context
                )
                return True, next_model
            else:
                # Already at highest tier
                logger.warning(
                    "Low confidence at highest model tier",
                    model=current_model,
                    confidence=confidence,
                    threshold=threshold
                )
                return False, None
        except ValueError:
            logger.error("Invalid model in escalation chain", model=current_model)
            return False, None

    def route_with_escalation(
        self,
        task_type: TaskType,
        prompt: str,
        context: str = "decision",
        max_escalations: int = 2
    ) -> Dict[str, Any]:
        """
        Route request with automatic escalation on low confidence.

        This is a placeholder that returns routing instructions.
        Actual model calls should be implemented by agents.

        Args:
            task_type: Type of task
            prompt: Prompt to send to model
            context: Context for confidence threshold
            max_escalations: Maximum number of escalation attempts

        Returns:
            Dict with routing instructions
        """
        model = self.select_model(task_type, context)

        routing_plan = {
            "initial_model": model,
            "task_type": task_type,
            "context": context,
            "confidence_threshold": self.confidence_thresholds[context],
            "max_escalations": max_escalations,
            "escalation_chain": []
        }

        # Build escalation chain
        current_idx = self.ESCALATION_CHAIN.index(model)
        for i in range(max_escalations):
            if current_idx + i + 1 < len(self.ESCALATION_CHAIN):
                routing_plan["escalation_chain"].append(
                    self.ESCALATION_CHAIN[current_idx + i + 1]
                )

        logger.info("Routing plan created", plan=routing_plan)
        return routing_plan

    def record_usage(
        self,
        model: ModelTier,
        tokens_used: int,
        was_escalation: bool = False
    ) -> None:
        """
        Record model usage for cost tracking.

        Args:
            model: Model that was used
            tokens_used: Number of tokens consumed
            was_escalation: Whether this was an escalation from cheaper model
        """
        cost = (tokens_used / 1000) * self.MODEL_COSTS[model]

        self.usage_stats[model]["calls"] += 1
        self.usage_stats[model]["total_cost"] += cost

        if was_escalation:
            self.usage_stats[model]["escalations"] += 1

        logger.debug(
            "Model usage recorded",
            model=model,
            tokens=tokens_used,
            cost=cost,
            was_escalation=was_escalation
        )

    def get_usage_stats(self) -> Dict[str, Any]:
        """
        Get model usage statistics.

        Returns:
            Dict with usage stats per model
        """
        total_cost = sum(stats["total_cost"] for stats in self.usage_stats.values())
        total_calls = sum(stats["calls"] for stats in self.usage_stats.values())

        return {
            "by_model": self.usage_stats,
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
