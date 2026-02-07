"""
Database Initialization Script

Creates all database tables and optionally seeds test data.
"""

import sys
import argparse
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

import yaml
import structlog
from infrastructure.database import init_database, DatabaseSession

logger = structlog.get_logger()


def load_config(config_path: str = "config/trading_config.yaml") -> dict:
    """Load trading configuration."""
    with open(config_path, 'r') as f:
        return yaml.safe_load(f)


def initialize_database(config_path: str = "config/trading_config.yaml", drop_existing: bool = False):
    """
    Initialize the database with all tables.

    Args:
        config_path: Path to configuration file
        drop_existing: If True, drop all existing tables first (WARNING: destructive)
    """
    logger.info("Starting database initialization...")

    # Load config
    config = load_config(config_path)

    # Initialize database
    db_session = init_database(config)

    if drop_existing:
        logger.warning("Dropping all existing tables...")
        db_session.drop_tables()
        db_session.create_tables()
        logger.info("Database recreated successfully")
    else:
        logger.info("Database tables created (existing tables preserved)")

    # Verify tables
    logger.info("Verifying database tables...")
    with db_session.session_scope() as session:
        from infrastructure.database.models import Base

        table_names = [
            "research_summaries",
            "trading_decisions",
            "risk_approvals",
            "executions",
            "pnl_ledger",
            "audit_trail",
            "performance_metrics"
        ]

        logger.info(f"Expected tables: {', '.join(table_names)}")

    logger.info("✅ Database initialization complete!")

    # Print database info
    db_config = config.get("data", {}).get("database", {})
    db_type = db_config.get("type", "sqlite")

    if db_type == "sqlite":
        db_path = db_config.get("path", "data/trading_system.db")
        logger.info(f"Database location: {db_path}")
    else:
        logger.info(f"Database type: {db_type}")

    return db_session


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(description="Initialize trading system database")
    parser.add_argument(
        "--config",
        type=str,
        default="config/trading_config.yaml",
        help="Path to configuration file"
    )
    parser.add_argument(
        "--drop",
        action="store_true",
        help="Drop existing tables before creating (WARNING: destructive)"
    )

    args = parser.parse_args()

    # Configure logging
    structlog.configure(
        processors=[
            structlog.stdlib.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.dev.ConsoleRenderer()
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
    )

    try:
        initialize_database(args.config, args.drop)
        print("\n✅ Database ready for use!")

    except Exception as e:
        logger.error(f"Database initialization failed: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
