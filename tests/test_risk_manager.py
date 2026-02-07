"""
Test Suite for Risk Management Agent

Tests all risk checks, approval logic, and safety mechanisms.
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
import yaml
from datetime import datetime
from unittest.mock import Mock, MagicMock
import uuid

from agents.implementations.risk_manager import RiskManagerAgent


# ========== FIXTURES ==========

@pytest.fixture
def config():
    """Load test configuration."""
    with open("config/trading_config.yaml", 'r') as f:
        return yaml.safe_load(f)


@pytest.fixture
def mock_binance_client():
    """Mock Binance client."""
    client = Mock()
    client.get_account_balance.return_value = {
        "available_balance": 5000.0,
        "total_wallet_balance": 10000.0,
        "total_unrealized_profit": 0.0
    }
    return client


@pytest.fixture
def mock_db_session():
    """Mock database session."""
    session = Mock()
    session.get_session.return_value = Mock()
    session.session_scope = MagicMock()
    return session


@pytest.fixture
def risk_manager(config, mock_binance_client, mock_db_session):
    """Create Risk Manager instance."""
    return RiskManagerAgent(
        agent_id="risk-manager",
        config=config,
        binance_client=mock_binance_client,
        db_session=mock_db_session
    )


@pytest.fixture
def valid_trading_decision():
    """Valid trading decision for testing."""
    return {
        "schema_version": "1.0.0",
        "agent_id": "trading-decision",
        "correlation_id": str(uuid.uuid4()),
        "timestamp": datetime.utcnow().isoformat(),
        "decision_id": str(uuid.uuid4()),
        "symbol": "BTCUSDT",
        "decision": "LONG",
        "confidence": 0.85,
        "strategy_id": "ema_crossover_scalp",
        "model_used": "claude-3.5-haiku",
        "entry_price": 43250.0,
        "stop_loss": 43100.0,
        "take_profit_levels": [
            {"price": 43400.0, "quantity_pct": 0.5},
            {"price": 43550.0, "quantity_pct": 0.5}
        ],
        "position_size_usdt": 500.0,
        "leverage": 5,
        "risk_metrics": {
            "risk_reward_ratio": 2.0,
            "win_probability": 0.65
        }
    }


@pytest.fixture
def valid_research_summary():
    """Valid research summary for testing."""
    return {
        "schema_version": "1.0.0",
        "agent_id": "research-coordinator",
        "correlation_id": str(uuid.uuid4()),
        "timestamp": datetime.utcnow().isoformat(),
        "symbol": "BTCUSDT",
        "timeframe": "5m",
        "analysis_timestamp": datetime.utcnow().isoformat(),
        "market_data": {
            "price": 43250.0,
            "volume_24h": 25000000000,
            "funding_rate": 0.0001
        },
        "technical_indicators": {
            "volatility_pct": 0.03,
            "atr": 250.0
        },
        "sentiment": {
            "overall_score": 0.7,
            "confidence": 0.8
        }
    }


# ========== TESTS ==========

def test_risk_manager_initialization(risk_manager):
    """Test Risk Manager initializes correctly."""
    assert risk_manager.agent_id == "risk-manager"
    assert risk_manager.max_risk_per_trade_pct == 0.02
    assert risk_manager.max_daily_drawdown_pct == 0.05
    assert risk_manager.max_portfolio_exposure_pct == 0.70


def test_kill_switch_global(config, mock_binance_client, mock_db_session):
    """Test global kill switch rejects all trades."""
    # Enable global kill switch
    config["risk"]["kill_switches"]["global"] = True

    risk_manager = RiskManagerAgent(
        agent_id="risk-manager",
        config=config,
        binance_client=mock_binance_client,
        db_session=mock_db_session
    )

    check_result = risk_manager._check_kill_switches("BTCUSDT", "ema_crossover_scalp")

    assert check_result["passed"] == False
    assert check_result["severity"] == "critical"
    assert "GLOBAL KILL SWITCH" in check_result["message"]


def test_kill_switch_symbol(config, mock_binance_client, mock_db_session):
    """Test symbol-specific kill switch."""
    # Enable symbol kill switch
    config["risk"]["kill_switches"]["symbols"] = {"BTCUSDT": True}

    risk_manager = RiskManagerAgent(
        agent_id="risk-manager",
        config=config,
        binance_client=mock_binance_client,
        db_session=mock_db_session
    )

    check_result = risk_manager._check_kill_switches("BTCUSDT", "ema_crossover_scalp")

    assert check_result["passed"] == False
    assert "Symbol kill switch" in check_result["message"]


def test_daily_drawdown_check_pass(risk_manager):
    """Test daily drawdown check passes within limit."""
    account_status = {
        "daily_drawdown_pct": 0.02  # 2% (within 5% limit)
    }

    check_result = risk_manager._check_daily_drawdown(account_status)

    assert check_result["passed"] == True
    assert check_result["current_value"] == 0.02
    assert check_result["limit"] == 0.05


def test_daily_drawdown_check_fail(risk_manager):
    """Test daily drawdown check fails when exceeding limit."""
    account_status = {
        "daily_drawdown_pct": -0.06  # -6% (exceeds 5% limit)
    }

    check_result = risk_manager._check_daily_drawdown(account_status)

    assert check_result["passed"] == False
    assert check_result["severity"] == "critical"


def test_risk_per_trade_check_pass(risk_manager, valid_trading_decision):
    """Test risk per trade check passes."""
    account_status = {
        "total_equity": 10000.0
    }

    # Position: 500 USDT, Stop distance: 150/43250 = 0.35%
    # Risk: 500 * 0.0035 = 1.75 USDT = 0.0175% of 10k (< 2% limit)

    check_result = risk_manager._check_risk_per_trade(
        valid_trading_decision,
        account_status
    )

    assert check_result["passed"] == True
    assert check_result["risk_pct"] <= 0.02


def test_risk_per_trade_check_fail(risk_manager, valid_trading_decision):
    """Test risk per trade check fails when risk too high."""
    # Increase position size to trigger failure
    valid_trading_decision["position_size_usdt"] = 10000.0  # Very large position

    account_status = {
        "total_equity": 10000.0
    }

    check_result = risk_manager._check_risk_per_trade(
        valid_trading_decision,
        account_status
    )

    assert check_result["passed"] == False


def test_portfolio_exposure_check_pass(risk_manager, valid_trading_decision):
    """Test portfolio exposure check passes."""
    account_status = {
        "current_exposure": 3000.0,  # 30% already exposed
        "total_equity": 10000.0
    }

    # New position: 500 USDT
    # Total: 3500 / 10000 = 35% (< 70% limit)

    check_result = risk_manager._check_portfolio_exposure(
        valid_trading_decision,
        account_status
    )

    assert check_result["passed"] == True
    assert check_result["current_value"] <= 0.70


def test_portfolio_exposure_check_fail(risk_manager, valid_trading_decision):
    """Test portfolio exposure check fails when too high."""
    account_status = {
        "current_exposure": 7000.0,  # 70% already exposed
        "total_equity": 10000.0
    }

    # New position would exceed 70% limit

    check_result = risk_manager._check_portfolio_exposure(
        valid_trading_decision,
        account_status
    )

    assert check_result["passed"] == False


def test_leverage_limit_low_volatility(risk_manager, valid_trading_decision, valid_research_summary):
    """Test leverage limit with low volatility allows higher leverage."""
    valid_research_summary["technical_indicators"]["volatility_pct"] = 0.015  # 1.5% (low)
    valid_trading_decision["leverage"] = 8  # Should pass for low vol (max 10x)

    check_result = risk_manager._check_leverage_limit(
        valid_trading_decision,
        valid_research_summary
    )

    assert check_result["passed"] == True
    assert check_result["limit"] == 10


def test_leverage_limit_high_volatility(risk_manager, valid_trading_decision, valid_research_summary):
    """Test leverage limit with high volatility restricts leverage."""
    valid_research_summary["technical_indicators"]["volatility_pct"] = 0.08  # 8% (high)
    valid_trading_decision["leverage"] = 8  # Should fail for high vol (max 5x)

    check_result = risk_manager._check_leverage_limit(
        valid_trading_decision,
        valid_research_summary
    )

    assert check_result["passed"] == False
    assert check_result["limit"] == 5  # High volatility limit


def test_volatility_gate_pass(risk_manager, valid_research_summary):
    """Test volatility gate passes with normal volatility."""
    valid_research_summary["technical_indicators"]["volatility_pct"] = 0.03  # 3% (< 5% limit)

    check_result = risk_manager._check_volatility_gate(valid_research_summary)

    assert check_result["passed"] == True


def test_volatility_gate_fail(risk_manager, valid_research_summary):
    """Test volatility gate fails with high volatility."""
    valid_research_summary["technical_indicators"]["volatility_pct"] = 0.08  # 8% (> 5% limit)

    check_result = risk_manager._check_volatility_gate(valid_research_summary)

    assert check_result["passed"] == False


def test_position_concentration_check_pass(risk_manager, valid_trading_decision):
    """Test position concentration check passes."""
    account_status = {
        "total_equity": 10000.0
    }

    # Position: 500 USDT = 5% of equity (< 30% limit)

    check_result = risk_manager._check_position_concentration(
        valid_trading_decision,
        account_status
    )

    assert check_result["passed"] == True


def test_position_concentration_check_fail(risk_manager, valid_trading_decision):
    """Test position concentration check fails."""
    valid_trading_decision["position_size_usdt"] = 4000.0  # 40% of equity

    account_status = {
        "total_equity": 10000.0
    }

    check_result = risk_manager._check_position_concentration(
        valid_trading_decision,
        account_status
    )

    assert check_result["passed"] == False


def test_available_margin_check_pass(risk_manager, valid_trading_decision):
    """Test available margin check passes."""
    account_status = {
        "available_balance": 5000.0
    }

    # Required margin: 500 / 5 = 100 USDT (< 5000 available)

    check_result = risk_manager._check_available_margin(
        valid_trading_decision,
        account_status
    )

    assert check_result["passed"] == True


def test_available_margin_check_fail(risk_manager, valid_trading_decision):
    """Test available margin check fails with insufficient balance."""
    account_status = {
        "available_balance": 50.0  # Not enough margin
    }

    check_result = risk_manager._check_available_margin(
        valid_trading_decision,
        account_status
    )

    assert check_result["passed"] == False
    assert check_result["severity"] == "critical"


def test_kelly_criterion_sizing(risk_manager, valid_trading_decision, valid_research_summary):
    """Test Kelly Criterion position sizing."""
    account_status = {
        "total_equity": 10000.0
    }

    sizing = risk_manager._kelly_criterion_sizing(
        valid_trading_decision,
        valid_research_summary,
        account_status
    )

    assert sizing["method"] == "kelly_criterion"
    assert sizing["kelly_fraction"] == 0.5
    assert sizing["calculated_position_size_usdt"] > 0
    assert "risk_per_trade_pct" in sizing


def test_approval_all_checks_pass(risk_manager, valid_trading_decision, valid_research_summary, mock_db_session):
    """Test trade approval when all checks pass."""
    # Mock database queries
    mock_session = Mock()
    mock_session.query().filter().all.return_value = []  # No open positions
    mock_db_session.session_scope.return_value.__enter__.return_value = mock_session

    state = {
        "correlation_id": str(uuid.uuid4()),
        "trading_decision": valid_trading_decision,
        "research_summary": valid_research_summary
    }

    result = risk_manager.execute(state)

    assert result["approval_status"] == "APPROVED" or result["approval_status"] == "MODIFIED"
    assert "risk_checks" in result
    assert "account_status" in result


def test_rejection_critical_failure(config, mock_binance_client, mock_db_session, valid_trading_decision, valid_research_summary):
    """Test trade rejection on critical failure."""
    # Enable global kill switch to force rejection
    config["risk"]["kill_switches"]["global"] = True

    risk_manager = RiskManagerAgent(
        agent_id="risk-manager",
        config=config,
        binance_client=mock_binance_client,
        db_session=mock_db_session
    )

    state = {
        "correlation_id": str(uuid.uuid4()),
        "trading_decision": valid_trading_decision,
        "research_summary": valid_research_summary
    }

    result = risk_manager.execute(state)

    assert result["approval_status"] == "REJECTED"
    assert "rejection_reason" in result


def test_modification_warning_failure(risk_manager, valid_trading_decision, valid_research_summary, mock_db_session):
    """Test trade modification when warnings exist."""
    # Set very high requested leverage to trigger modification
    valid_trading_decision["leverage"] = 15  # Exceeds medium vol limit

    # Mock database queries
    mock_session = Mock()
    mock_session.query().filter().all.return_value = []
    mock_db_session.session_scope.return_value.__enter__.return_value = mock_session

    state = {
        "correlation_id": str(uuid.uuid4()),
        "trading_decision": valid_trading_decision,
        "research_summary": valid_research_summary
    }

    result = risk_manager.execute(state)

    # Should be modified (leverage reduced) or rejected
    assert result["approval_status"] in ["MODIFIED", "REJECTED"]


def test_no_trade_approval(risk_manager):
    """Test NO_TRADE decisions are approved without risk checks."""
    trading_decision = {
        "decision_id": str(uuid.uuid4()),
        "decision": "NO_TRADE",
        "symbol": "BTCUSDT"
    }

    result = risk_manager._approve_no_trade(trading_decision)

    assert result["approval_status"] == "APPROVED"
    assert result["risk_checks"] == {}


def test_schema_compliance(risk_manager, valid_trading_decision, valid_research_summary, mock_db_session):
    """Test that risk approval output conforms to schema."""
    # Mock database queries
    mock_session = Mock()
    mock_session.query().filter().all.return_value = []
    mock_db_session.session_scope.return_value.__enter__.return_value = mock_session

    state = {
        "correlation_id": str(uuid.uuid4()),
        "trading_decision": valid_trading_decision,
        "research_summary": valid_research_summary
    }

    result = risk_manager.execute(state)

    # Check required fields
    assert "schema_version" in result
    assert "agent_id" in result
    assert result["agent_id"] == "risk-manager"
    assert "correlation_id" in result
    assert "timestamp" in result
    assert "approval_id" in result
    assert "decision_id" in result
    assert "approval_status" in result
    assert "risk_checks" in result
    assert "account_status" in result


# ========== RUN TESTS ==========

if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
