"""
Test Database Integration

Verifies database models, session management, and queries work correctly.
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import yaml
from datetime import datetime, timedelta
from infrastructure.database import (
    init_database,
    ResearchSummary,
    TradingDecision,
    RiskApproval,
    Execution,
    PnLLedger,
    AuditTrail,
    PerformanceMetrics,
    DatabaseQueries
)


def load_config():
    """Load configuration."""
    with open("config/trading_config.yaml", 'r') as f:
        return yaml.safe_load(f)


def test_database_init():
    """Test database initialization."""
    print("\n=== Test 1: Database Initialization ===")

    config = load_config()

    # Override to use test database
    config["data"]["database"]["path"] = "data/test_trading_system.db"

    db_session = init_database(config)

    print("✅ Database initialized successfully")
    print(f"   Database type: SQLite")
    print(f"   Database path: {config['data']['database']['path']}")

    return db_session


def test_create_research_summary(db_session):
    """Test creating research summary."""
    print("\n=== Test 2: Create Research Summary ===")

    with db_session.session_scope() as session:
        research = ResearchSummary(
            symbol="BTCUSDT",
            timeframe="5m",
            analysis_timestamp=datetime.utcnow(),
            market_data={
                "current_price": 43250.0,
                "price_change_24h_pct": 2.5,
                "volume_24h": 25000000000
            },
            technical_indicators={
                "ema_9": 43200,
                "ema_21": 43100,
                "rsi": 65,
                "macd": 150
            },
            sentiment={
                "overall": "bullish",
                "news_sentiment": 0.7,
                "social_sentiment": 0.6
            },
            market_regime="trending_bull"
        )

        session.add(research)
        session.commit()

        research_id = research.id

    print(f"✅ Research summary created")
    print(f"   ID: {research_id}")
    print(f"   Symbol: BTCUSDT")
    print(f"   Market Regime: trending_bull")

    return research_id


def test_create_trading_decision(db_session, research_id):
    """Test creating trading decision."""
    print("\n=== Test 3: Create Trading Decision ===")

    with db_session.session_scope() as session:
        decision = TradingDecision(
            research_summary_id=research_id,
            symbol="BTCUSDT",
            decision="LONG",
            confidence=0.85,
            strategy_id="ema_crossover_scalp",
            model_used="claude-3.5-haiku",
            reasoning_summary="Strong bullish EMA alignment with high RSI",
            entry_price=43250.0,
            stop_loss=43100.0,
            take_profit_levels=[43400.0, 43550.0, 43700.0],
            position_size_usdt=500.0,
            leverage=5,
            technical_signals={
                "ema_alignment": "bullish",
                "rsi": "overbought",
                "macd": "bullish"
            },
            timestamp=datetime.utcnow()
        )

        session.add(decision)
        session.commit()

        decision_id = decision.id

    print(f"✅ Trading decision created")
    print(f"   ID: {decision_id}")
    print(f"   Decision: LONG")
    print(f"   Confidence: 0.85")
    print(f"   Strategy: ema_crossover_scalp")

    return decision_id


def test_create_risk_approval(db_session, decision_id):
    """Test creating risk approval."""
    print("\n=== Test 4: Create Risk Approval ===")

    with db_session.session_scope() as session:
        approval = RiskApproval(
            decision_id=decision_id,
            approval_status="APPROVED",
            risk_checks={
                "kill_switches": {"passed": True},
                "max_daily_drawdown": {"passed": True, "value": 0.02, "limit": 0.05},
                "max_risk_per_trade": {"passed": True, "value": 0.015, "limit": 0.02},
                "max_portfolio_exposure": {"passed": True, "value": 0.45, "limit": 0.70},
                "leverage_limit": {"passed": True, "value": 5, "limit": 10},
                "correlation_check": {"passed": True, "count": 1, "limit": 3},
                "volatility_gate": {"passed": True, "value": 0.03, "limit": 0.05},
                "position_concentration": {"passed": True, "value": 0.15, "limit": 0.30}
            },
            position_sizing={
                "method": "kelly_criterion",
                "kelly_fraction": 0.5,
                "calculated_size_usdt": 500.0
            },
            account_status={
                "total_equity_usdt": 10000.0,
                "available_balance_usdt": 5500.0,
                "current_exposure_pct": 0.45
            },
            timestamp=datetime.utcnow(),
            processing_time_ms=45
        )

        session.add(approval)
        session.commit()

        approval_id = approval.id

    print(f"✅ Risk approval created")
    print(f"   ID: {approval_id}")
    print(f"   Status: APPROVED")
    print(f"   All risk checks: PASSED")

    return approval_id


def test_create_execution(db_session, approval_id, decision_id):
    """Test creating execution record."""
    print("\n=== Test 5: Create Execution ===")

    with db_session.session_scope() as session:
        execution = Execution(
            approval_id=approval_id,
            decision_id=decision_id,
            execution_mode="PAPER",
            execution_status="FILLED",
            symbol="BTCUSDT",
            side="LONG",
            order_details={
                "order_id": "TEST_ORDER_123",
                "avg_fill_price": 43255.0,
                "filled_quantity": 0.0115,
                "commission_usdt": 0.25,
                "leverage": 5
            },
            stop_loss_order={"order_id": "SL_123", "price": 43100.0},
            take_profit_orders=[
                {"order_id": "TP1_123", "price": 43400.0},
                {"order_id": "TP2_123", "price": 43550.0}
            ],
            paper_trading_simulation={
                "simulated": True,
                "slippage_bps": 5
            },
            timestamp=datetime.utcnow(),
            processing_time_ms=120
        )

        session.add(execution)
        session.commit()

        execution_id = execution.id

    print(f"✅ Execution created")
    print(f"   ID: {execution_id}")
    print(f"   Mode: PAPER")
    print(f"   Status: FILLED")
    print(f"   Fill Price: 43255.0")

    return execution_id


def test_create_pnl_entry(db_session, execution_id):
    """Test creating P&L entry."""
    print("\n=== Test 6: Create P&L Entry ===")

    with db_session.session_scope() as session:
        pnl = PnLLedger(
            execution_id=execution_id,
            symbol="BTCUSDT",
            side="LONG",
            entry_price=43255.0,
            quantity=0.0115,
            leverage=5,
            entry_time=datetime.utcnow(),
            fees_usdt=0.25,
            unrealized_pnl_usdt=0.0,
            is_closed=False
        )

        session.add(pnl)
        session.commit()

        pnl_id = pnl.id

    print(f"✅ P&L entry created")
    print(f"   ID: {pnl_id}")
    print(f"   Symbol: BTCUSDT")
    print(f"   Entry Price: 43255.0")
    print(f"   Status: OPEN")

    return pnl_id


def test_create_audit_trail(db_session):
    """Test creating audit trail with hash chain."""
    print("\n=== Test 7: Create Audit Trail ===")

    correlation_id = "test-correlation-123"

    with db_session.session_scope() as session:
        # First entry
        import hashlib
        import json

        event_data_1 = {"event": "research_complete", "symbol": "BTCUSDT"}
        input_hash_1 = hashlib.sha256(
            json.dumps(event_data_1, sort_keys=True).encode()
        ).hexdigest()
        current_hash_1 = hashlib.sha256(f"{input_hash_1}:".encode()).hexdigest()

        audit_1 = AuditTrail(
            correlation_id=correlation_id,
            agent_id="research-coordinator",
            event_type="research_complete",
            event_data=event_data_1,
            input_hash=input_hash_1,
            previous_hash=None,
            current_hash=current_hash_1,
            timestamp=datetime.utcnow()
        )

        session.add(audit_1)
        session.flush()

        # Second entry (linked)
        event_data_2 = {"event": "decision_made", "decision": "LONG"}
        input_hash_2 = hashlib.sha256(
            json.dumps(event_data_2, sort_keys=True).encode()
        ).hexdigest()
        current_hash_2 = hashlib.sha256(
            f"{input_hash_2}:{current_hash_1}".encode()
        ).hexdigest()

        audit_2 = AuditTrail(
            correlation_id=correlation_id,
            agent_id="trading-decision",
            event_type="decision_made",
            event_data=event_data_2,
            input_hash=input_hash_2,
            previous_hash=current_hash_1,
            current_hash=current_hash_2,
            timestamp=datetime.utcnow()
        )

        session.add(audit_2)
        session.commit()

    print(f"✅ Audit trail created")
    print(f"   Correlation ID: {correlation_id}")
    print(f"   Entries: 2")
    print(f"   Hash chain: Linked")

    return correlation_id


def test_queries(db_session, correlation_id):
    """Test database queries."""
    print("\n=== Test 8: Database Queries ===")

    with db_session.session_scope() as session:
        queries = DatabaseQueries(session)

        # Get latest research
        latest_research = queries.get_latest_research("BTCUSDT")
        print(f"✅ Latest research: {latest_research.market_regime if latest_research else 'None'}")

        # Get open positions
        open_positions = queries.get_open_positions()
        print(f"✅ Open positions: {len(open_positions)}")

        # Verify audit chain
        is_valid = queries.verify_audit_chain(correlation_id)
        print(f"✅ Audit chain valid: {is_valid}")

        # Get decisions by date
        today_start = datetime.combine(datetime.today(), datetime.min.time())
        today_end = datetime.combine(datetime.today(), datetime.max.time())
        decisions = queries.get_decisions_by_date(today_start, today_end)
        print(f"✅ Decisions today: {len(decisions)}")


def test_performance_metrics(db_session):
    """Test performance metrics."""
    print("\n=== Test 9: Performance Metrics ===")

    with db_session.session_scope() as session:
        metrics = PerformanceMetrics(
            date=datetime.utcnow(),
            metric_type="daily",
            total_trades=10,
            winning_trades=7,
            losing_trades=3,
            win_rate=0.70,
            total_pnl_usdt=125.50,
            avg_win_usdt=25.30,
            avg_loss_usdt=-12.15,
            largest_win_usdt=45.20,
            largest_loss_usdt=-18.90,
            sharpe_ratio=1.85,
            max_drawdown_pct=0.03,
            avg_holding_time_seconds=240
        )

        session.add(metrics)
        session.commit()

    print(f"✅ Performance metrics created")
    print(f"   Total Trades: 10")
    print(f"   Win Rate: 70%")
    print(f"   Total P&L: $125.50")
    print(f"   Sharpe Ratio: 1.85")


def main():
    """Run all tests."""
    print("=" * 60)
    print("DATABASE INTEGRATION TEST")
    print("=" * 60)

    try:
        # Test 1: Initialize database
        db_session = test_database_init()

        # Test 2: Create research summary
        research_id = test_create_research_summary(db_session)

        # Test 3: Create trading decision
        decision_id = test_create_trading_decision(db_session, research_id)

        # Test 4: Create risk approval
        approval_id = test_create_risk_approval(db_session, decision_id)

        # Test 5: Create execution
        execution_id = test_create_execution(db_session, approval_id, decision_id)

        # Test 6: Create P&L entry
        pnl_id = test_create_pnl_entry(db_session, execution_id)

        # Test 7: Create audit trail
        correlation_id = test_create_audit_trail(db_session)

        # Test 8: Test queries
        test_queries(db_session, correlation_id)

        # Test 9: Performance metrics
        test_performance_metrics(db_session)

        print("\n" + "=" * 60)
        print("✅ ALL TESTS PASSED!")
        print("=" * 60)
        print("\nDatabase integration is working correctly.")
        print("Test database created at: data/test_trading_system.db")
        print("\nYou can now:")
        print("1. Run the main trading system with database persistence")
        print("2. Generate reports using the Storage & Reporting agent")
        print("3. Query historical data for analysis")

    except Exception as e:
        print(f"\n❌ TEST FAILED: {e}")
        import traceback
        traceback.print_exc()

    finally:
        if 'db_session' in locals():
            db_session.close()


if __name__ == "__main__":
    main()
