"""
Database Session Management

Handles database connections and session lifecycle.
"""

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, scoped_session
from contextlib import contextmanager
import structlog
from pathlib import Path

from .models import Base

logger = structlog.get_logger()


class DatabaseSession:
    """
    Database session manager with connection pooling.
    """

    def __init__(self, database_url: str = None, echo: bool = False):
        """
        Initialize database session manager.

        Args:
            database_url: Database connection string (default: SQLite)
            echo: Enable SQL echo for debugging
        """
        if database_url is None:
            # Default to SQLite in data directory
            data_dir = Path("data")
            data_dir.mkdir(exist_ok=True)
            database_url = f"sqlite:///{data_dir}/trading_system.db"

        self.engine = create_engine(
            database_url,
            echo=echo,
            pool_pre_ping=True,  # Verify connections before using
            pool_recycle=3600   # Recycle connections every hour
        )

        self.session_factory = sessionmaker(bind=self.engine)
        self.Session = scoped_session(self.session_factory)

        logger.info("Database session initialized", database_url=database_url)

    def create_tables(self):
        """Create all tables in the database."""
        Base.metadata.create_all(self.engine)
        logger.info("Database tables created")

    def drop_tables(self):
        """Drop all tables (WARNING: destructive operation)."""
        Base.metadata.drop_all(self.engine)
        logger.warning("All database tables dropped")

    def get_session(self):
        """
        Get a new database session.

        Returns:
            SQLAlchemy session
        """
        return self.Session()

    @contextmanager
    def session_scope(self):
        """
        Provide a transactional scope for database operations.

        Usage:
            with db.session_scope() as session:
                session.add(obj)
                session.commit()
        """
        session = self.Session()
        try:
            yield session
            session.commit()
        except Exception as e:
            session.rollback()
            logger.error("Database transaction failed", error=str(e))
            raise
        finally:
            session.close()

    def close(self):
        """Close all sessions and dispose engine."""
        self.Session.remove()
        self.engine.dispose()
        logger.info("Database connections closed")


# Global database session instance
_db_session: DatabaseSession = None


def init_database(config: dict = None) -> DatabaseSession:
    """
    Initialize the global database session.

    Args:
        config: Database configuration dict

    Returns:
        DatabaseSession instance
    """
    global _db_session

    if _db_session is not None:
        logger.warning("Database already initialized")
        return _db_session

    # Get database config
    if config is None:
        config = {}

    db_config = config.get("data", {}).get("database", {})
    db_type = db_config.get("type", "sqlite")

    # Build connection string
    if db_type == "sqlite":
        db_path = db_config.get("path", "data/trading_system.db")
        database_url = f"sqlite:///{db_path}"
    elif db_type == "postgresql":
        host = db_config.get("host", "localhost")
        port = db_config.get("port", 5432)
        database = db_config.get("database", "trading_system")
        user = db_config.get("user", "trading_user")
        password = db_config.get("password", "")
        database_url = f"postgresql://{user}:{password}@{host}:{port}/{database}"
    else:
        raise ValueError(f"Unsupported database type: {db_type}")

    _db_session = DatabaseSession(database_url, echo=db_config.get("echo", False))

    # Create tables if they don't exist
    _db_session.create_tables()

    logger.info("Global database initialized", type=db_type)

    return _db_session


def get_db() -> DatabaseSession:
    """
    Get the global database session instance.

    Returns:
        DatabaseSession instance

    Raises:
        RuntimeError: If database not initialized
    """
    if _db_session is None:
        raise RuntimeError("Database not initialized. Call init_database() first.")
    return _db_session
