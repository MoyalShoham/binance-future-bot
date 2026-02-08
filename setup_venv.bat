@echo off
REM Setup Script for Multi-Agent Trading System (using venv)
REM This script creates a Python virtual environment and installs all dependencies

echo ============================================
echo Multi-Agent Trading System - Setup
echo ============================================
echo.

REM Check if Python is installed
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo ERROR: Python is not installed or not in PATH
    echo Please install Python 3.11 from https://www.python.org/downloads/
    pause
    exit /b 1
)

echo Python detected ✓
echo.

REM Create virtual environment
echo [1/3] Creating virtual environment...
python -m venv venv
if %errorlevel% neq 0 (
    echo ERROR: Failed to create virtual environment
    pause
    exit /b 1
)
echo ✓ Virtual environment created successfully
echo.

REM Activate virtual environment
echo [2/3] Activating virtual environment...
call venv\Scripts\activate.bat
if %errorlevel% neq 0 (
    echo ERROR: Failed to activate virtual environment
    pause
    exit /b 1
)
echo ✓ Environment activated
echo.

REM Upgrade pip
echo Upgrading pip...
python -m pip install --upgrade pip --quiet
echo ✓ Pip upgraded
echo.

REM Install dependencies
echo [3/3] Installing dependencies from requirements.txt...
echo This may take 3-5 minutes...
echo.
pip install -r requirements.txt
if %errorlevel% neq 0 (
    echo ERROR: Failed to install dependencies
    pause
    exit /b 1
)
echo.
echo ✓ All dependencies installed successfully
echo.

echo ============================================
echo Setup Complete! 🎉
echo ============================================
echo.
echo Virtual environment created in: venv\
echo.
echo To use the trading system:
echo 1. Activate the environment:  venv\Scripts\activate
echo 2. Run the system:            python main.py --mode paper --symbol BTCUSDT
echo.
echo Or simply double-click: run_trading_system_venv.bat
echo.

pause
