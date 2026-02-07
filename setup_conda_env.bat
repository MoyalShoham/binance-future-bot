@echo off
REM Setup Script for Multi-Agent Trading System
REM This script creates a conda environment and installs all dependencies

echo ============================================
echo Multi-Agent Trading System - Setup
echo ============================================
echo.

REM Create conda environment
echo [1/4] Creating conda environment 'trading-system' with Python 3.11...
call conda create -n trading-system python=3.11 -y
if %errorlevel% neq 0 (
    echo ERROR: Failed to create conda environment
    pause
    exit /b 1
)
echo ✓ Conda environment created successfully
echo.

REM Activate conda environment
echo [2/4] Activating conda environment...
call conda activate trading-system
if %errorlevel% neq 0 (
    echo ERROR: Failed to activate conda environment
    pause
    exit /b 1
)
echo ✓ Environment activated
echo.

REM Upgrade pip
echo [3/4] Upgrading pip...
python -m pip install --upgrade pip
echo ✓ Pip upgraded
echo.

REM Install dependencies
echo [4/4] Installing dependencies from requirements.txt...
echo This may take 3-5 minutes...
pip install -r requirements.txt
if %errorlevel% neq 0 (
    echo ERROR: Failed to install dependencies
    pause
    exit /b 1
)
echo ✓ All dependencies installed successfully
echo.

echo ============================================
echo Setup Complete! 🎉
echo ============================================
echo.
echo To use the trading system:
echo 1. Activate the environment:  conda activate trading-system
echo 2. Run the system:            python main.py --mode paper --symbol BTCUSDT
echo.
echo Quick test:
echo   python main.py --mode paper --symbol BTCUSDT
echo.
echo Continuous trading (every 5 min):
echo   python main.py --mode paper --symbol BTCUSDT --continuous --interval 300
echo.

pause
