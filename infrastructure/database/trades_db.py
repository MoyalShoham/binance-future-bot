"""
Trades Database - Flat, queryable trades table.

Separate from the main trading_system.db to provide easy SQL access
to trade history without JSON blobs or complex joins.

Usage:
    sqlite3 data/trades.db "SELECT symbol, side, pnl_usdt FROM trades WHERE status='CLOSED'"
"""

from datetime import datetime
from typing import Optional
from contextlib import contextmanager

from sqlalchemy import create_engine, Column, Integer, String, Float, DateTime
from sqlalchemy.orm import declarative_base, sessionmaker
import structlog

logger = structlog.get_logger()

TradesBase = declarative_base()


class Trade(TradesBase):
    """Flat trade record - no JSON blobs, easy to query."""

    __tablename__ = "trades"

    id = Column(Integer, primary_key=True, autoincrement=True)
    trade_id = Column(String, unique=True, nullable=False)
    timestamp = Column(DateTime, default=datetime.utcnow)
    symbol = Column(String, nullable=False, index=True)
    side = Column(String, nullable=False)  # LONG / SHORT
    strategy = Column(String)
    confidence = Column(Float)
    entry_price = Column(Float)
    exit_price = Column(Float)
    quantity = Column(Float)
    leverage = Column(Integer)
    pnl_usdt = Column(Float)
    fees_usdt = Column(Float)
    holding_time_seconds = Column(Integer)
    status = Column(String, nullable=False, index=True)  # OPEN / CLOSED / REJECTED
    close_reason = Column(String)  # TRAIL_STOP / HARD_STOP / TIME_EXIT / BREAKEVEN_STOP
    correlation_id = Column(String, index=True)
    decision_id = Column(String)
    execution_id = Column(String)


class TradesDB:
    """
    Manages the flat trades database.

    Provides record_open(), record_rejected(), record_close() methods
    for the storage reporter and trailing stop monitor to call.
    """

    def __init__(self, db_path: str = "data/trades.db"):
        self.engine = create_engine(f"sqlite:///{db_path}", echo=False)
        TradesBase.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)
        logger.debug("TradesDB initialized", path=db_path)

    @contextmanager
    def _session_scope(self):
        session = self.Session()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def record_open(
        self,
        trade_id: str,
        symbol: str,
        side: str,
        entry_price: float,
        quantity: float,
        leverage: int,
        strategy: Optional[str] = None,
        confidence: Optional[float] = None,
        fees_usdt: float = 0.0,
        correlation_id: Optional[str] = None,
        decision_id: Optional[str] = None,
        execution_id: Optional[str] = None,
    ):
        """Record a newly opened trade."""
        with self._session_scope() as session:
            trade = Trade(
                trade_id=trade_id,
                timestamp=datetime.utcnow(),
                symbol=symbol,
                side=side,
                strategy=strategy,
                confidence=confidence,
                entry_price=entry_price,
                quantity=quantity,
                leverage=leverage,
                fees_usdt=fees_usdt,
                status="OPEN",
                correlation_id=correlation_id,
                decision_id=decision_id,
                execution_id=execution_id,
            )
            session.add(trade)
        logger.info("Trade opened", trade_id=trade_id, symbol=symbol, side=side)

    def record_rejected(
        self,
        trade_id: str,
        symbol: str,
        side: str,
        strategy: Optional[str] = None,
        confidence: Optional[float] = None,
        correlation_id: Optional[str] = None,
        decision_id: Optional[str] = None,
    ):
        """Record a rejected trade (for audit trail)."""
        with self._session_scope() as session:
            trade = Trade(
                trade_id=trade_id,
                timestamp=datetime.utcnow(),
                symbol=symbol,
                side=side,
                strategy=strategy,
                confidence=confidence,
                status="REJECTED",
                correlation_id=correlation_id,
                decision_id=decision_id,
            )
            session.add(trade)
        logger.debug("Trade rejected recorded", trade_id=trade_id, symbol=symbol)

    def record_close(
        self,
        execution_id: str,
        exit_price: float,
        pnl_usdt: float,
        fees_usdt: float,
        holding_time_seconds: int,
        close_reason: str,
    ):
        """
        Close an open trade by execution_id.

        Updates the matching OPEN trade with exit data.
        """
        with self._session_scope() as session:
            trade = (
                session.query(Trade)
                .filter(Trade.execution_id == execution_id, Trade.status == "OPEN")
                .first()
            )
            if not trade:
                logger.debug(
                    "No open trade found for close",
                    execution_id=execution_id,
                )
                return

            trade.exit_price = exit_price
            trade.pnl_usdt = pnl_usdt
            trade.fees_usdt = fees_usdt
            trade.holding_time_seconds = holding_time_seconds
            trade.close_reason = close_reason
            trade.status = "CLOSED"

        logger.info(
            "Trade closed",
            trade_id=trade.trade_id,
            symbol=trade.symbol,
            pnl_usdt=pnl_usdt,
            reason=close_reason,
        )

    def close(self):
        """Dispose of engine connections."""
        self.engine.dispose()
        logger.debug("TradesDB closed")
