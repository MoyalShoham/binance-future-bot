@echo off
REM Quick run script for Multi-Agent Trading System
REM Activates conda environment and runs the system

echo Activating trading-system environment...
call conda activate trading-system

echo.
echo ============================================
echo Multi-Agent Trading System
echo ============================================
echo.
echo Running in PAPER mode (safe - no real money)
echo Symbol: BTCUSDT
echo Mode: Single cycle test
echo.

REM Run the trading system
python main.py --mode paper --symbol BTCUSDT

echo.
echo ============================================
echo Trading cycle complete!
echo ============================================
echo.
echo Check the logs above for results.
echo Database saved to: data/trading_system.db
echo.

pause
