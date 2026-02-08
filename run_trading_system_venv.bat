@echo off
REM Quick run script for Multi-Agent Trading System (using venv)
REM Activates virtual environment and runs the system

echo Activating virtual environment...
call venv\Scripts\activate.bat

if %errorlevel% neq 0 (
    echo ERROR: Virtual environment not found!
    echo Please run setup_venv.bat first to create the environment.
    pause
    exit /b 1
)

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
echo Database saved to: data\trading_system.db
echo.

pause
