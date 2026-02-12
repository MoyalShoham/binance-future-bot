"""
SQLAlchemy Models for Trading System

Complete database schema for all trading data.
"""

from sqlalchemy import (
    Column, Integer, String, Float, Boolean, DateTime, JSON, Text,
    ForeignKey, Index, create_engine
)
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import relationship
from datetime import datetime
import uuid

Base = declarative_base()


class ResearchSummary(Base):
    """Research summaries from Research Coordinator."""
    __tablename__ = "research_summaries"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    symbol = Column(String(20), nullable=False, index=True)
    timeframe = Column(String(10), nullable=False)
    analysis_timestamp = Column(DateTime, nullable=False, index=True)

    # JSON columns for complex data
    market_data = Column(JSON, nullable=False)
    technical_indicators = Column(JSON, nullable=False)
    sentiment = Column(JSON, nullable=False)

    # Additional fields
    market_regime = Column(String(50))
    news_events = Column(JSON)
    warnings = Column(JSON)
    time_decay_factor = Column(Float)

    created_at = Column(DateTime, default=datetime.utcnow)

    # Relationships
    trading_decisions = relationship("TradingDecision", back_populates="research_summary")

    __table_args__ = (
        Index('idx_research_symbol_time', 'symbol', 'analysis_timestamp'),
    )


class TradingDecision(Base):
    """Trading decisions from Trading Decision Agent."""
    __tablename__ = "trading_decisions"

    id = Column(String(36), primary_key=True)  # decision_id from agent
    research_summary_id = Column(String(36), ForeignKey('research_summaries.id'))

    symbol = Column(String(20), nullable=False, index=True)
    decision = Column(String(10), nullable=False, index=True)  # LONG, SHORT, NO_TRADE
    confidence = Column(Float, nullable=False)
    strategy_id = Column(String(50))
    model_used = Column(String(50))
    reasoning_summary = Column(Text)

    # Price levels
    entry_price = Column(Float)
    stop_loss = Column(Float)
    take_profit_levels = Column(JSON)

    # Position parameters
    position_size_usdt = Column(Float)
    leverage = Column(Integer)

    # Analysis
    technical_signals = Column(JSON)
    risk_metrics = Column(JSON)

    timestamp = Column(DateTime, nullable=False, index=True)
    research_summary_hash = Column(String(64))

    created_at = Column(DateTime, default=datetime.utcnow)

    # Relationships
    research_summary = relationship("ResearchSummary", back_populates="trading_decisions")
    risk_approval = relationship("RiskApproval", back_populates="trading_decision", uselist=False)

    __table_args__ = (
        Index('idx_decisions_symbol_time', 'symbol', 'timestamp'),
    )


class RiskApproval(Base):
    """Risk approvals from Risk Manager Agent."""
    __tablename__ = "risk_approvals"

    id = Column(String(36), primary_key=True)  # approval_id from agent
    decision_id = Column(String(36), ForeignKey('trading_decisions.id'), nullable=False)

    approval_status = Column(String(20), nullable=False, index=True)  # APPROVED, REJECTED, MODIFIED
    rejection_reason = Column(Text)

    # Modified parameters (if MODIFIED)
    modified_parameters = Column(JSON)

    # Risk check results
    risk_checks = Column(JSON, nullable=False)
    position_sizing = Column(JSON)
    account_status = Column(JSON, nullable=False)
    kill_switches = Column(JSON)

    timestamp = Column(DateTime, nullable=False)
    processing_time_ms = Column(Integer)

    created_at = Column(DateTime, default=datetime.utcnow)

    # Relationships
    trading_decision = relationship("TradingDecision", back_populates="risk_approval")
    execution = relationship("Execution", back_populates="risk_approval", uselist=False)

    __table_args__ = (
        Index('idx_approvals_status', 'approval_status'),
    )


class Execution(Base):
    """Order executions from Execution Agent."""
    __tablename__ = "executions"

    id = Column(String(36), primary_key=True)  # execution_id from agent
    approval_id = Column(String(36), ForeignKey('risk_approvals.id'), nullable=False)
    decision_id = Column(String(36), ForeignKey('trading_decisions.id'), nullable=False)

    execution_mode = Column(String(10), nullable=False, index=True)  # PAPER, LIVE, HYBRID
    execution_status = Column(String(20), nullable=False, index=True)

    symbol = Column(String(20), nullable=False, index=True)
    side = Column(String(10), nullable=False)  # LONG, SHORT

    # Order details
    order_details = Column(JSON, nullable=False)
    stop_loss_order = Column(JSON)
    take_profit_orders = Column(JSON)

    # Shadow execution (for LIVE and HYBRID modes)
    shadow_paper_execution = Column(JSON)
    paper_trading_simulation = Column(JSON)

    # Timeline and errors
    execution_timeline = Column(JSON)
    errors = Column(JSON)

    timestamp = Column(DateTime, nullable=False, index=True)
    processing_time_ms = Column(Integer)
    idempotency_check = Column(JSON)

    created_at = Column(DateTime, default=datetime.utcnow)

    # Relationships
    risk_approval = relationship("RiskApproval", back_populates="execution")
    pnl_entry = relationship("PnLLedger", back_populates="execution", uselist=False)

    __table_args__ = (
        Index('idx_executions_symbol_time', 'symbol', 'timestamp'),
    )


class PnLLedger(Base):
    """P&L tracking ledger."""
    __tablename__ = "pnl_ledger"

    id = Column(Integer, primary_key=True, autoincrement=True)
    execution_id = Column(String(36), ForeignKey('executions.id'))

    symbol = Column(String(20), nullable=False, index=True)
    side = Column(String(10), nullable=False)

    # Entry details
    entry_price = Column(Float, nullable=False)
    quantity = Column(Float, nullable=False)
    leverage = Column(Integer, nullable=False)
    entry_time = Column(DateTime, nullable=False, index=True)

    # Exit details
    exit_price = Column(Float)
    exit_time = Column(DateTime)
    holding_time_seconds = Column(Integer)

    # P&L
    fees_usdt = Column(Float)
    realized_pnl_usdt = Column(Float)
    unrealized_pnl_usdt = Column(Float)

    is_closed = Column(Boolean, default=False, index=True)

    # Binance-side protective order IDs
    sl_order_id = Column(String(50))
    tp_order_id = Column(String(50))
    close_reason = Column(String(30))

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    execution = relationship("Execution", back_populates="pnl_entry")

    __table_args__ = (
        Index('idx_pnl_symbol_entry', 'symbol', 'entry_time'),
    )


class AuditTrail(Base):
    """Immutable audit trail with hash chain."""
    __tablename__ = "audit_trail"

    id = Column(Integer, primary_key=True, autoincrement=True)
    correlation_id = Column(String(36), nullable=False, index=True)
    agent_id = Column(String(50), nullable=False, index=True)

    event_type = Column(String(50), nullable=False)
    event_data = Column(JSON, nullable=False)

    # Hash chain for tamper detection
    input_hash = Column(String(64))
    previous_hash = Column(String(64))
    current_hash = Column(String(64), nullable=False)

    timestamp = Column(DateTime, nullable=False, index=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        Index('idx_audit_correlation', 'correlation_id'),
        Index('idx_audit_time', 'timestamp'),
    )


class PerformanceMetrics(Base):
    """Aggregated performance metrics."""
    __tablename__ = "performance_metrics"

    id = Column(Integer, primary_key=True, autoincrement=True)
    date = Column(DateTime, nullable=False, index=True)
    metric_type = Column(String(50), nullable=False, index=True)  # daily, weekly, monthly

    # Trade statistics
    total_trades = Column(Integer)
    winning_trades = Column(Integer)
    losing_trades = Column(Integer)
    win_rate = Column(Float)

    # P&L metrics
    total_pnl_usdt = Column(Float)
    avg_win_usdt = Column(Float)
    avg_loss_usdt = Column(Float)
    largest_win_usdt = Column(Float)
    largest_loss_usdt = Column(Float)

    # Risk metrics
    sharpe_ratio = Column(Float)
    max_drawdown_pct = Column(Float)
    avg_holding_time_seconds = Column(Integer)

    # Breakdowns
    strategy_breakdown = Column(JSON)
    model_accuracy = Column(JSON)

    created_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        Index('idx_metrics_date', 'date'),
    )
