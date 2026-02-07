# Multi-Agent AI Trading System for Binance Futures

## Overview

Production-grade, fully autonomous multi-agent AI trading system for Binance Futures with paper trading, live trading, and hybrid execution modes.

## Architecture

- **Specialized Agent Model**: Role-separated agents with clear authority boundaries
- **LangChain Orchestration**: Supervisor/coordinator pattern with LangGraph state management
- **Cheap-Model-First Routing**: GPT Nano → Gemini Flash → Claude Haiku → Claude Sonnet
- **JSON-Only Communication**: Deterministic schemas with validation
- **Hook-Driven Automation**: Event triggers for safety checks and audit logging

## Core Agents

1. **Research Coordinator**: Orchestrate market research sub-agents
2. **Trading Decision**: Evaluate indicators and apply scalping strategies
3. **Risk Manager**: Global authority over all trades (approve/reject/modify)
4. **Execution Agent**: Interface with Binance Futures API
5. **Storage & Reporting**: Persist data and generate analytics
6. **Emergency Controller**: Monitor system health and trigger kill switches

## Execution Modes

- **Paper Trading**: Simulate orders with realistic slippage
- **Live Trading**: Real orders with shadow paper execution for comparison
- **Hybrid**: Both modes simultaneously with divergence alerts

## Safety Features

- Kill switches (global, symbol, strategy, volatility circuit breaker)
- Pre-trade validation hooks
- Post-execution audit logging
- Dynamic risk controls with override authority
- Immutable audit trail with hash chains

## Implementation Status

See `IMPLEMENTATION_SUMMARY.md` for detailed progress tracking.

## Getting Started

```bash
# Install dependencies
pip install -r requirements.txt

# Configure trading parameters
cp config/trading_config.yaml.example config/trading_config.yaml
# Edit config/trading_config.yaml with your settings

# Set up Binance API credentials
cp .env.example .env
# Add your API keys to .env

# Run in paper trading mode
python main.py --mode paper

# Check status
python cli.py /trade-status
```

## Documentation

- `docs/architecture.md`: System architecture overview
- `docs/agent_communication.md`: JSON schema specifications
- `docs/risk_management.md`: Risk control mechanisms
- `docs/deployment.md`: Production deployment guide

## Development

Branches are used for each implementation phase:
- `feature/phase1-core-infrastructure`: Core schemas, orchestration, risk manager
- `feature/phase2-agents`: Agent implementations
- `feature/phase3-strategies`: Trading strategies and skills
- `feature/phase4-testing`: Configuration and testing

## License

Proprietary - All Rights Reserved
