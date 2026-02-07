"""
Main Entry Point for Multi-Agent Trading System

Initializes all agents and runs the trading coordinator.
"""

import os
import sys
import argparse
from datetime import datetime
from dotenv import load_dotenv
import yaml
import structlog

from orchestration import TradingCoordinator, StateManager, ModelRouter
from orchestration.state_manager import ExecutionMode
from agents.implementations import (
    ResearchCoordinatorAgent,
    TradingDecisionAgent,
    RiskManagerAgent,
    StorageReporterAgent,
    ExecutionAgent,
    EmergencyControllerAgent,
)
from infrastructure.binance_api import BinanceFuturesClient
from infrastructure.database import init_database

# Configure structured logging
structlog.configure(
    processors=[
        structlog.stdlib.filter_by_level,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.stdlib.PositionalArgumentsFormatter(),
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        structlog.processors.UnicodeDecoder(),
        structlog.dev.ConsoleRenderer()
    ],
    context_class=dict,
    logger_factory=structlog.stdlib.LoggerFactory(),
    cache_logger_on_first_use=True,
)

logger = structlog.get_logger()


def load_config(config_path: str = "config/trading_config.yaml") -> dict:
    """
    Load trading configuration from YAML file.

    Args:
        config_path: Path to config file

    Returns:
        Configuration dict
    """
    with open(config_path, 'r') as f:
        config = yaml.safe_load(f)

    logger.info("Configuration loaded", config_file=config_path)
    return config


def load_environment():
    """Load environment variables from .env file."""
    load_dotenv()

    required_vars = [
        "BINANCE_API_KEY",
        "BINANCE_API_SECRET",
        # AI model API keys are optional for testing
    ]

    missing_vars = [var for var in required_vars if not os.getenv(var)]

    if missing_vars:
        logger.error("Missing required environment variables", missing=missing_vars)
        raise ValueError(f"Missing environment variables: {missing_vars}")

    logger.info("Environment variables loaded")


def initialize_binance_client(config: dict) -> BinanceFuturesClient:
    """
    Initialize Binance Futures API client.

    Args:
        config: Trading configuration

    Returns:
        Binance client instance
    """
    api_key = os.getenv("BINANCE_API_KEY")
    api_secret = os.getenv("BINANCE_API_SECRET")
    testnet = config.get("trading", {}).get("testnet", False)

    client = BinanceFuturesClient(
        api_key=api_key,
        api_secret=api_secret,
        testnet=testnet
    )

    # Test connection
    if not client.ping():
        raise ConnectionError("Failed to connect to Binance API")

    logger.info("Binance client initialized", testnet=testnet)
    return client


def initialize_agents(config: dict, binance_client: BinanceFuturesClient, db_session) -> dict:
    """
    Initialize all trading agents.

    Args:
        config: Trading configuration
        binance_client: Binance API client
        db_session: Database session instance

    Returns:
        Dict of initialized agents
    """
    agents = {
        "research_coordinator": ResearchCoordinatorAgent("research-coordinator", config, binance_client),
        "trading_decision": TradingDecisionAgent("trading-decision", config),
        "risk_manager": RiskManagerAgent("risk-manager", config, binance_client, db_session),
        "execution_agent": ExecutionAgent("execution-agent", config, binance_client, db_session),
        "storage_reporter": StorageReporterAgent("storage-reporter", config, db_session),
        "emergency_controller": EmergencyControllerAgent("emergency-controller", config, binance_client, db_session),
    }

    logger.info("Agents initialized", agent_count=len(agents))
    return agents


def run_single_cycle(
    coordinator: TradingCoordinator,
    symbol: str,
    mode: ExecutionMode
):
    """
    Run a single trading cycle.

    Args:
        coordinator: Trading coordinator instance
        symbol: Trading symbol
        mode: Execution mode
    """
    logger.info(
        "Starting trading cycle",
        symbol=symbol,
        mode=mode
    )

    try:
        final_state = coordinator.run_trading_cycle(symbol=symbol, mode=mode)

        # Log results
        logger.info(
            "Trading cycle completed",
            correlation_id=final_state.get("correlation_id"),
            pipeline_stage=final_state.get("pipeline_stage"),
            decision=final_state.get("trading_decision", {}).get("decision"),
            execution_status=final_state.get("execution_result", {}).get("execution_status"),
            total_time_ms=final_state.get("total_processing_time_ms"),
            errors=len(final_state.get("errors", []))
        )

    except Exception as e:
        logger.error("Trading cycle failed", error=str(e), exc_info=True)


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(description="Multi-Agent AI Trading System")
    parser.add_argument(
        "--mode",
        type=str,
        default="paper",
        choices=["paper", "live", "hybrid"],
        help="Execution mode (default: paper)"
    )
    parser.add_argument(
        "--symbol",
        type=str,
        default="BTCUSDT",
        help="Trading symbol (default: BTCUSDT)"
    )
    parser.add_argument(
        "--config",
        type=str,
        default="config/trading_config.yaml",
        help="Path to config file"
    )
    parser.add_argument(
        "--continuous",
        action="store_true",
        help="Run continuously (loop)"
    )
    parser.add_argument(
        "--interval",
        type=int,
        default=60,
        help="Interval between cycles in seconds (for continuous mode)"
    )

    args = parser.parse_args()

    # Convert mode string to enum
    mode_map = {
        "paper": ExecutionMode.PAPER,
        "live": ExecutionMode.LIVE,
        "hybrid": ExecutionMode.HYBRID
    }
    execution_mode = mode_map[args.mode]

    logger.info("Starting Multi-Agent AI Trading System")

    try:
        # Load configuration and environment
        config = load_config(args.config)
        load_environment()

        # Initialize database
        logger.info("Initializing database...")
        db_session = init_database(config)
        logger.info("Database initialized successfully")

        # Initialize Binance client
        binance_client = initialize_binance_client(config)

        # Initialize agents
        agents = initialize_agents(config, binance_client, db_session)

        # Initialize coordinator
        coordinator = TradingCoordinator(config)

        # Register agents
        for agent_name, agent_instance in agents.items():
            if agent_instance:  # Skip None agents
                coordinator.register_agent(agent_name, agent_instance)

        # Start emergency controller monitoring
        emergency_controller = agents.get("emergency_controller")
        if emergency_controller:
            emergency_controller.start_continuous_monitoring()

        # Run trading cycles
        if args.continuous:
            logger.info("Running in continuous mode", interval_seconds=args.interval)

            import time
            while True:
                run_single_cycle(coordinator, args.symbol, execution_mode)
                time.sleep(args.interval)
        else:
            # Single cycle
            run_single_cycle(coordinator, args.symbol, execution_mode)

    except KeyboardInterrupt:
        logger.info("Shutting down gracefully...")
    except Exception as e:
        logger.error("Fatal error", error=str(e), exc_info=True)
        sys.exit(1)
    finally:
        # Cleanup
        if 'emergency_controller' in locals() and emergency_controller:
            emergency_controller.stop_continuous_monitoring()

        # Close database connection
        if 'db_session' in locals() and db_session:
            db_session.close()
            logger.info("Database connection closed")

        logger.info("System shutdown complete")


if __name__ == "__main__":
    main()
