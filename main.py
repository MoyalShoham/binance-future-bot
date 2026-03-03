"""
Main Entry Point for Multi-Agent Trading System

Initializes all agents and runs the trading coordinator.
"""

import os
import sys
import argparse
import logging
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
from infrastructure.database import init_database, TradesDB
from infrastructure.symbol_scanner import SymbolScanner
from infrastructure.websocket_manager import BinanceWebSocketManager
from infrastructure.regime_detector import RegimeDetector, RegimeState
from infrastructure.paper_dashboard import PaperDashboard

# Ensure logs and reports directories exist
os.makedirs("logs", exist_ok=True)
os.makedirs("reports", exist_ok=True)

# Set root logging level to INFO so structlog filter_by_level works correctly
# Configure both console and file output
root_logger = logging.getLogger()
root_logger.setLevel(logging.DEBUG)

# Silence noisy third-party loggers (websockets logs every frame at DEBUG,
# fills log file on Windows causing PermissionError on rotation)
logging.getLogger("websockets").setLevel(logging.WARNING)
logging.getLogger("binance").setLevel(logging.WARNING)

# Console handler
console_handler = logging.StreamHandler(sys.stderr)
console_handler.setLevel(logging.INFO)
console_handler.setFormatter(logging.Formatter("%(message)s"))
root_logger.addHandler(console_handler)

# File handler with rotation
from logging.handlers import RotatingFileHandler
file_handler = RotatingFileHandler(
    "logs/trading_system.log",
    maxBytes=100 * 1024 * 1024,  # 100 MB
    backupCount=10,
    encoding="utf-8"
)
file_handler.setLevel(logging.DEBUG)
file_handler.setFormatter(logging.Formatter("%(message)s"))
root_logger.addHandler(file_handler)

# Configure structured logging (no colors for clean file output)
structlog.configure(
    processors=[
        structlog.stdlib.filter_by_level,
        structlog.stdlib.add_log_level,
        structlog.stdlib.PositionalArgumentsFormatter(),
        structlog.processors.TimeStamper(fmt="%Y-%m-%d %H:%M:%S"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        structlog.processors.UnicodeDecoder(),
        structlog.dev.ConsoleRenderer(colors=False)
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


def initialize_agents(config: dict, binance_client: BinanceFuturesClient, db_session, model_router: ModelRouter, trades_db=None, ws_manager=None, regime_state=None, paper_dashboard=None) -> dict:
    """
    Initialize all trading agents.

    Args:
        config: Trading configuration
        binance_client: Binance API client
        db_session: Database session instance
        model_router: ModelRouter instance for LLM calls
        trades_db: Optional TradesDB for flat trade records
        paper_dashboard: Optional PaperDashboard for paper trade tracking

    Returns:
        Dict of initialized agents
    """
    agents = {
        "research_coordinator": ResearchCoordinatorAgent("research-coordinator", config, binance_client, model_router=model_router, ws_manager=ws_manager),
        "trading_decision": TradingDecisionAgent("trading-decision", config, model_router=model_router, binance_client=binance_client, db_session=db_session, regime_state=regime_state),
        "risk_manager": RiskManagerAgent("risk-manager", config, binance_client, db_session),  # NO model_router - stays rule-based
        "execution_agent": ExecutionAgent("execution-agent", config, binance_client, db_session, model_router=model_router),
        "storage_reporter": StorageReporterAgent("storage-reporter", config, db_session, model_router=model_router, trades_db=trades_db, paper_dashboard=paper_dashboard),
        "emergency_controller": EmergencyControllerAgent("emergency-controller", config, binance_client, db_session, model_router=model_router, trades_db=trades_db, ws_manager=ws_manager, paper_dashboard=paper_dashboard),
    }

    logger.info("Agents initialized", agent_count=len(agents), llm_enabled=config.get("models", {}).get("enabled", True))
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
    import time as _time
    _cycle_start = _time.monotonic()

    try:
        final_state = coordinator.run_trading_cycle(symbol=symbol, mode=mode)

        # Handle None final_state (should not happen, but add safety check)
        if final_state is None:
            logger.error("Trading cycle returned None state")
            return

        # Build concise CYCLE summary line
        trading_decision = final_state.get("trading_decision") or {}
        execution_result = final_state.get("execution_result") or {}
        decision = trading_decision.get("decision", "UNKNOWN")
        confidence = trading_decision.get("confidence", 0)
        strategy = trading_decision.get("strategy_id", "none")
        elapsed_ms = int((_time.monotonic() - _cycle_start) * 1000)
        errors = final_state.get("errors", [])

        # R:R and size info for trade decisions
        rr = trading_decision.get("risk_metrics", {}).get("risk_reward_ratio", 0)
        size = trading_decision.get("position_size_usdt", 0)
        extra = {}
        if decision in ("LONG", "SHORT"):
            extra["rr"] = f"{rr:.1f}:1"
            extra["size"] = f"${size:.0f}"

        if errors:
            logger.warning(
                "CYCLE",
                symbol=symbol,
                decision=decision,
                confidence=f"{confidence:.0%}",
                strategy=strategy,
                time=f"{elapsed_ms}ms",
                errors=len(errors),
                **extra,
            )
        else:
            logger.info(
                "CYCLE",
                symbol=symbol,
                decision=decision,
                confidence=f"{confidence:.0%}",
                strategy=strategy,
                time=f"{elapsed_ms}ms",
                **extra,
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
        default="all",
        help="Trading symbol or 'all' for all configured symbols (default: all)"
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
        default=120,
        help="Interval between cycles in seconds (default: 120s to reduce overtrading)"
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

        # Initialize flat trades database
        trades_db = TradesDB("data/trades.db")

        # Initialize paper dashboard (paper/hybrid modes)
        paper_dashboard = None
        if args.mode in ("paper", "hybrid") and config.get("paper_dashboard", {}).get("enabled", True):
            paper_dashboard = PaperDashboard(config)
            logger.info("Paper dashboard initialized")

        # Initialize Binance client
        binance_client = initialize_binance_client(config)

        # Initialize model router for LLM calls
        model_router = ModelRouter(config.get("models", {}))

        # Initialize WebSocket manager for real-time market data
        ws_manager = None
        if config.get("websocket", {}).get("enabled", False):
            ws_manager = BinanceWebSocketManager(
                api_key=os.getenv("BINANCE_API_KEY"),
                api_secret=os.getenv("BINANCE_API_SECRET"),
                config=config,
            )
            ws_manager.start()
            logger.info("WebSocket manager initialized")

        # Initialize regime detector
        regime_state = RegimeState()
        regime_detector = None
        if config.get("regime_detection", {}).get("enabled", False):
            regime_detector = RegimeDetector(
                binance_client, config, regime_state, db_session=db_session
            )

        # Give paper dashboard the binance client for SL/TP simulation
        if paper_dashboard:
            paper_dashboard.binance_client = binance_client

        # Initialize agents
        agents = initialize_agents(config, binance_client, db_session, model_router, trades_db, ws_manager=ws_manager, regime_state=regime_state, paper_dashboard=paper_dashboard)

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

        # Start paper dashboard periodic reports
        if paper_dashboard:
            paper_dashboard.start_periodic_reports()

        # Start regime detector
        if regime_detector:
            regime_detector.start()

        # Initialize hot symbol scanner
        symbol_scanner = SymbolScanner(binance_client, config)

        # Determine symbols to trade
        if args.symbol.lower() == "all":
            # Use scanner if enabled, else fall back to config list
            if symbol_scanner.enabled:
                symbols = symbol_scanner.scan()
                if not symbols:
                    symbols = config.get("trading", {}).get("symbols", ["XRPUSDT"])
            else:
                symbols = config.get("trading", {}).get("symbols", ["XRPUSDT"])
        else:
            symbols = [args.symbol]

        # Always include symbols with open positions (prevents orphaned trades)
        try:
            open_pos_symbols = {p["symbol"] for p in binance_client.get_positions()}
            if open_pos_symbols:
                for s in open_pos_symbols:
                    if s not in symbols:
                        symbols.append(s)
                        logger.info("Added open-position symbol to watch list", symbol=s)
        except Exception as e:
            logger.warning("Could not fetch open positions for symbol list", error=str(e))

        logger.info("Trading symbols", symbols=symbols, count=len(symbols))

        # Low equity warning: each trade is ~13% of account at Binance $100 minimum
        try:
            balance = binance_client.get_account_balance()
            equity = balance.get("total_equity", 0)
            if 0 < equity < 200:
                min_margin_pct = 100.0 / config.get("trading", {}).get("default_leverage", 10) / equity
                logger.warning(
                    "LOW EQUITY WARNING: each trade risks ~%.0f%% of account "
                    "(Binance $100 minimum notional, equity $%.2f, leverage %dx)",
                    min_margin_pct * 100,
                    equity,
                    config.get("trading", {}).get("default_leverage", 10),
                )
        except Exception:
            pass

        # Subscribe WebSocket to initial symbols
        if ws_manager:
            ws_manager.subscribe(symbols)

        # Run trading cycles
        if args.continuous:
            logger.info("Running in continuous mode", interval_seconds=args.interval)

            import time
            while True:
                # Get symbols with open Binance positions (skip these for new trades)
                open_pos_symbols = set()
                try:
                    open_pos_symbols = {p["symbol"] for p in binance_client.get_positions()}
                except Exception:
                    pass

                for symbol in symbols:
                    # Skip symbols with open positions — trailing stop handles them
                    if symbol in open_pos_symbols:
                        logger.debug("Skipping symbol with open position", symbol=symbol)
                        continue
                    run_single_cycle(coordinator, symbol, execution_mode)

                # Re-scan for hot symbols each round
                if args.symbol.lower() == "all" and symbol_scanner.enabled:
                    new_symbols = symbol_scanner.scan()
                    if new_symbols:
                        symbols = new_symbols
                    # Always keep symbols with open positions in the watch list
                    # (so trailing stop monitor tracks them, even if we skip new-trade cycles)
                    for s in open_pos_symbols:
                        if s not in symbols:
                            symbols.append(s)

                # Subscribe WebSocket to any new symbols
                if ws_manager:
                    ws_manager.subscribe(symbols)

                time.sleep(args.interval)
        else:
            # Single cycle across all symbols
            for symbol in symbols:
                run_single_cycle(coordinator, symbol, execution_mode)

    except KeyboardInterrupt:
        logger.info("Shutting down gracefully...")
    except Exception as e:
        logger.error("Fatal error", error=str(e), exc_info=True)
        sys.exit(1)
    finally:
        # Suppress asyncio "Task exception was never retrieved" noise during shutdown
        # (python-binance/websockets version mismatch — cosmetic, no impact)
        import warnings
        warnings.filterwarnings("ignore", category=RuntimeWarning, message=".*was never retrieved.*")

        # Cleanup
        if 'regime_detector' in locals() and regime_detector:
            regime_detector.stop()

        if 'ws_manager' in locals() and ws_manager:
            ws_manager.stop()

        if 'emergency_controller' in locals() and emergency_controller:
            emergency_controller.stop_continuous_monitoring()

        # Generate final paper trading report and cleanup
        if 'paper_dashboard' in locals() and paper_dashboard:
            try:
                report_path = paper_dashboard.generate_report()
                if report_path:
                    logger.info("Final paper trading report generated", path=report_path)
            except Exception as e:
                logger.warning("Failed to generate final paper report", error=str(e))
            paper_dashboard.close()

        # Close database connections
        if 'trades_db' in locals() and trades_db:
            trades_db.close()

        if 'db_session' in locals() and db_session:
            db_session.close()
            logger.info("Database connections closed")

        logger.info("System shutdown complete")


if __name__ == "__main__":
    main()
