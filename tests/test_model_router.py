"""
Test Suite for ModelRouter - LLM Invocation Engine

Tests model selection, invocation, JSON parsing, escalation, retry, and cost tracking.
All LLM calls are mocked.
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
import json
from unittest.mock import Mock, MagicMock, patch
from types import SimpleNamespace

from orchestration.model_router import ModelRouter, ModelTier, TaskType


# ========== FIXTURES ==========

@pytest.fixture
def router_config():
    """Standard test config."""
    return {
        "enabled": True,
        "primary_provider": "anthropic",
        "confidence_thresholds": {
            "research": 0.70,
            "decision": 0.75,
            "risk": 0.80,
            "emergency": 0.90,
        },
        "timeouts": {
            "haiku_seconds": 15,
            "sonnet_seconds": 30,
        },
    }


@pytest.fixture
def router(router_config):
    """Create a ModelRouter instance."""
    return ModelRouter(router_config)


@pytest.fixture
def mock_llm_response():
    """Factory for creating mock LLM responses."""
    def _make(content_dict, input_tokens=100, output_tokens=50):
        resp = Mock()
        resp.content = json.dumps(content_dict)
        resp.usage_metadata = {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
        }
        resp.response_metadata = {}
        return resp
    return _make


# ========== MODEL SELECTION TESTS ==========

class TestModelSelection:

    def test_anthropic_primary_biases_to_haiku(self, router):
        """When primary_provider=anthropic, NANO/FLASH tasks become HAIKU."""
        assert router.select_model(TaskType.CLASSIFICATION) == ModelTier.HAIKU
        assert router.select_model(TaskType.SIMPLE_REASONING) == ModelTier.HAIKU
        assert router.select_model(TaskType.PATTERN_MATCHING) == ModelTier.HAIKU
        assert router.select_model(TaskType.SENTIMENT_ANALYSIS) == ModelTier.HAIKU

    def test_haiku_tasks_stay_haiku(self, router):
        """HAIKU-default tasks stay HAIKU with anthropic primary."""
        assert router.select_model(TaskType.RISK_REASONING) == ModelTier.HAIKU
        assert router.select_model(TaskType.COMPLEX_DECISION) == ModelTier.HAIKU

    def test_sonnet_tasks_stay_sonnet(self, router):
        """SONNET-default tasks stay SONNET regardless of primary."""
        assert router.select_model(TaskType.CRITICAL_DECISION) == ModelTier.SONNET
        assert router.select_model(TaskType.ANOMALY_DETECTION) == ModelTier.SONNET

    def test_force_model_overrides(self, router):
        """force_model should override all selection logic."""
        assert router.select_model(
            TaskType.CLASSIFICATION, force_model=ModelTier.SONNET
        ) == ModelTier.SONNET

    def test_non_anthropic_primary_uses_defaults(self, router_config):
        """When primary is not anthropic, use default task-model mapping."""
        router_config["primary_provider"] = "openai"
        router = ModelRouter(router_config)
        assert router.select_model(TaskType.CLASSIFICATION) == ModelTier.NANO
        assert router.select_model(TaskType.PATTERN_MATCHING) == ModelTier.FLASH


# ========== ESCALATION TESTS ==========

class TestEscalation:

    def test_no_escalation_above_threshold(self, router):
        """Should not escalate when confidence >= threshold."""
        should, next_model = router.should_escalate(ModelTier.HAIKU, 0.80, "decision")
        assert should is False
        assert next_model is None

    def test_escalation_below_threshold(self, router):
        """Should escalate when confidence < threshold."""
        should, next_model = router.should_escalate(ModelTier.HAIKU, 0.50, "decision")
        assert should is True
        assert next_model == ModelTier.SONNET

    def test_no_escalation_at_highest_tier(self, router):
        """Should not escalate from SONNET (highest tier)."""
        should, next_model = router.should_escalate(ModelTier.SONNET, 0.30, "decision")
        assert should is False
        assert next_model is None

    def test_escalation_chain_order(self, router):
        """Escalation follows NANO -> FLASH -> HAIKU -> SONNET."""
        _, next_nano = router.should_escalate(ModelTier.NANO, 0.0, "decision")
        assert next_nano == ModelTier.FLASH

        _, next_flash = router.should_escalate(ModelTier.FLASH, 0.0, "decision")
        assert next_flash == ModelTier.HAIKU

        _, next_haiku = router.should_escalate(ModelTier.HAIKU, 0.0, "decision")
        assert next_haiku == ModelTier.SONNET

    def test_context_specific_thresholds(self, router):
        """Different contexts have different confidence thresholds."""
        # research threshold = 0.70
        should_research, _ = router.should_escalate(ModelTier.HAIKU, 0.72, "research")
        assert should_research is False

        # emergency threshold = 0.90
        should_emergency, _ = router.should_escalate(ModelTier.HAIKU, 0.72, "emergency")
        assert should_emergency is True


# ========== JSON PARSING TESTS ==========

class TestJsonParsing:

    def test_parse_clean_json(self, router):
        """Parse clean JSON string."""
        result = router._parse_json_response('{"confidence": 0.8, "data": "test"}')
        assert result == {"confidence": 0.8, "data": "test"}

    def test_parse_json_with_markdown_fences(self, router):
        """Strip markdown code fences before parsing."""
        result = router._parse_json_response('```json\n{"confidence": 0.8}\n```')
        assert result == {"confidence": 0.8}

    def test_parse_json_with_plain_fences(self, router):
        """Strip plain ``` fences."""
        result = router._parse_json_response('```\n{"confidence": 0.5}\n```')
        assert result == {"confidence": 0.5}

    def test_parse_json_embedded_in_text(self, router):
        """Extract JSON from surrounding text."""
        result = router._parse_json_response('Here is the result: {"confidence": 0.9} done.')
        assert result == {"confidence": 0.9}

    def test_parse_invalid_json_returns_none(self, router):
        """Return None for completely unparseable content."""
        result = router._parse_json_response('This is not JSON at all')
        assert result is None


# ========== INVOKE TESTS ==========

class TestInvoke:

    def test_invoke_disabled_returns_unavailable(self, router_config):
        """When enabled=false, invoke returns llm_available=False."""
        router_config["enabled"] = False
        router = ModelRouter(router_config)
        result = router.invoke(TaskType.CLASSIFICATION, "sys", "user")
        assert result["llm_available"] is False

    @patch.object(ModelRouter, '_invoke_with_retry')
    def test_invoke_success(self, mock_retry, router):
        """Successful invocation returns parsed response."""
        mock_retry.return_value = {
            "parsed": {"confidence": 0.85, "analysis": "bullish"},
            "raw_content": '{"confidence": 0.85, "analysis": "bullish"}',
            "model_used": "claude-3-5-haiku-20241022",
            "tokens": 150,
        }

        result = router.invoke(
            TaskType.PATTERN_MATCHING, "system prompt", "user prompt",
            context="research"
        )

        assert result["llm_available"] is True
        assert result["response"]["confidence"] == 0.85
        assert result["model_used"] == "claude-3-5-haiku-20241022"
        assert result["tokens"] == 150

    @patch.object(ModelRouter, '_invoke_with_retry')
    def test_invoke_escalation_on_low_confidence(self, mock_retry, router):
        """Low confidence triggers escalation to next tier."""
        # First call returns low confidence, second returns high
        mock_retry.side_effect = [
            {
                "parsed": {"confidence": 0.30},
                "raw_content": '{"confidence": 0.30}',
                "model_used": "claude-3-5-haiku-20241022",
                "tokens": 100,
            },
            {
                "parsed": {"confidence": 0.90},
                "raw_content": '{"confidence": 0.90}',
                "model_used": "claude-sonnet-4-20250514",
                "tokens": 200,
            },
        ]

        result = router.invoke(
            TaskType.COMPLEX_DECISION, "sys", "user",
            context="decision", max_escalations=1
        )

        assert result["llm_available"] is True
        assert result["confidence"] == 0.90
        assert result["model_used"] == "claude-sonnet-4-20250514"
        assert result["escalation_count"] == 1
        assert mock_retry.call_count == 2

    @patch.object(ModelRouter, '_invoke_with_retry')
    def test_invoke_max_escalations_respected(self, mock_retry, router):
        """Escalation count does not exceed max_escalations."""
        mock_retry.return_value = {
            "parsed": {"confidence": 0.10},
            "raw_content": '{"confidence": 0.10}',
            "model_used": "claude-3-5-haiku-20241022",
            "tokens": 100,
        }

        result = router.invoke(
            TaskType.COMPLEX_DECISION, "sys", "user",
            context="decision", max_escalations=0
        )

        # Should NOT escalate since max_escalations=0
        assert mock_retry.call_count == 1
        assert result["confidence"] == 0.10

    @patch.object(ModelRouter, '_invoke_with_retry')
    def test_invoke_error_returns_unavailable(self, mock_retry, router):
        """Exception during invoke returns llm_available=False."""
        mock_retry.side_effect = Exception("API timeout")

        result = router.invoke(
            TaskType.CLASSIFICATION, "sys", "user",
            max_escalations=0
        )

        assert result["llm_available"] is False
        assert "API timeout" in result["error"]

    @patch.object(ModelRouter, '_invoke_with_retry')
    def test_invoke_escalation_on_error(self, mock_retry, router):
        """Error on first tier triggers escalation to next tier."""
        mock_retry.side_effect = [
            Exception("Rate limited"),
            {
                "parsed": {"confidence": 0.80},
                "raw_content": '{"confidence": 0.80}',
                "model_used": "claude-sonnet-4-20250514",
                "tokens": 200,
            },
        ]

        result = router.invoke(
            TaskType.COMPLEX_DECISION, "sys", "user",
            context="decision", max_escalations=1
        )

        assert result["llm_available"] is True
        assert result["confidence"] == 0.80

    @patch.object(ModelRouter, '_invoke_with_retry')
    def test_invoke_unparseable_response(self, mock_retry, router):
        """Unparseable LLM response returns response=None."""
        mock_retry.return_value = {
            "parsed": None,
            "raw_content": "I cannot produce JSON",
            "model_used": "claude-3-5-haiku-20241022",
            "tokens": 50,
        }

        result = router.invoke(TaskType.CLASSIFICATION, "sys", "user")
        assert result["llm_available"] is True
        assert result["response"] is None


# ========== COST TRACKING TESTS ==========

class TestCostTracking:

    def test_record_usage(self, router):
        """Usage recording updates stats correctly."""
        router.record_usage(ModelTier.HAIKU, 1000, was_escalation=False)
        stats = router.get_usage_stats()

        assert stats["total_calls"] == 1
        assert stats["total_cost_usd"] > 0
        assert stats["by_model"]["claude-3-5-haiku-20241022"]["calls"] == 1

    def test_record_escalation(self, router):
        """Escalation flag is tracked."""
        router.record_usage(ModelTier.SONNET, 500, was_escalation=True)
        stats = router.get_usage_stats()

        assert stats["by_model"]["claude-sonnet-4-20250514"]["escalations"] == 1

    def test_reset_stats(self, router):
        """Reset clears all stats."""
        router.record_usage(ModelTier.HAIKU, 1000)
        router.reset_stats()
        stats = router.get_usage_stats()

        assert stats["total_calls"] == 0
        assert stats["total_cost_usd"] == 0

    def test_avg_cost_per_call(self, router):
        """Average cost per call calculated correctly."""
        router.record_usage(ModelTier.HAIKU, 1000)
        router.record_usage(ModelTier.HAIKU, 1000)
        stats = router.get_usage_stats()

        assert stats["total_calls"] == 2
        assert stats["avg_cost_per_call"] == stats["total_cost_usd"] / 2


# ========== MODEL CREATION TESTS ==========

class TestModelCreation:

    @patch.dict('os.environ', {'ANTHROPIC_API_KEY': 'test-key'})
    def test_create_haiku_model(self, router):
        """Create Haiku model with API key."""
        model = router._create_model(ModelTier.HAIKU)
        assert model is not None

    @patch.dict('os.environ', {'ANTHROPIC_API_KEY': 'test-key'})
    def test_model_caching(self, router):
        """Same model tier returns cached instance."""
        model1 = router._create_model(ModelTier.HAIKU)
        model2 = router._create_model(ModelTier.HAIKU)
        assert model1 is model2

    @patch.dict('os.environ', {}, clear=True)
    def test_missing_api_key_raises(self, router):
        """Missing API key raises ValueError."""
        router._model_cache.clear()
        with pytest.raises(ValueError, match="ANTHROPIC_API_KEY not set"):
            router._create_model(ModelTier.HAIKU)
