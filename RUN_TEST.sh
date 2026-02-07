#!/bin/bash

# Quick Test Script for Trading System

echo "=================================="
echo "Multi-Agent Trading System - Test"
echo "=================================="
echo ""

# Check if .env exists
if [ ! -f .env ]; then
    echo "⚠️  ERROR: .env file not found!"
    echo "Please copy .env.example to .env and add your API keys:"
    echo "  cp .env.example .env"
    echo "  nano .env"
    exit 1
fi

# Check if API keys are set
if ! grep -q "BINANCE_API_KEY=your_api_key_here" .env; then
    echo "✅ .env file configured"
else
    echo "⚠️  WARNING: .env file still has placeholder values"
    echo "Please edit .env and add your real Binance API keys"
    exit 1
fi

echo "🔍 Checking dependencies..."
python -c "import binance, pandas, ta, langchain, structlog, yaml" 2>/dev/null
if [ $? -eq 0 ]; then
    echo "✅ All dependencies installed"
else
    echo "⚠️  Missing dependencies. Installing..."
    pip install -r requirements.txt
fi

echo ""
echo "🚀 Starting single trading cycle test..."
echo "   Symbol: BTCUSDT"
echo "   Mode: PAPER"
echo ""
echo "Press Ctrl+C to stop"
echo "=================================="
echo ""

# Run single test cycle
python main.py --mode paper --symbol BTCUSDT

echo ""
echo "=================================="
echo "Test completed!"
echo ""
echo "To run continuous mode:"
echo "  python main.py --mode paper --symbol BTCUSDT --continuous --interval 120"
echo ""
