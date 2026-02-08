# Setup Script for Multi-Agent Trading System (PowerShell using venv)
# This script creates a Python virtual environment and installs all dependencies

Write-Host "============================================" -ForegroundColor Cyan
Write-Host "Multi-Agent Trading System - Setup" -ForegroundColor Cyan
Write-Host "============================================" -ForegroundColor Cyan
Write-Host ""

# Check if Python is installed
try {
    $pythonVersion = python --version 2>&1
    Write-Host "Python detected: $pythonVersion ✓" -ForegroundColor Green
    Write-Host ""
} catch {
    Write-Host "ERROR: Python is not installed or not in PATH" -ForegroundColor Red
    Write-Host "Please install Python 3.11 from https://www.python.org/downloads/" -ForegroundColor Yellow
    Read-Host "Press Enter to exit"
    exit 1
}

# Create virtual environment
Write-Host "[1/3] Creating virtual environment..." -ForegroundColor Yellow
python -m venv venv
if ($LASTEXITCODE -ne 0) {
    Write-Host "ERROR: Failed to create virtual environment" -ForegroundColor Red
    Read-Host "Press Enter to exit"
    exit 1
}
Write-Host "✓ Virtual environment created successfully" -ForegroundColor Green
Write-Host ""

# Activate virtual environment
Write-Host "[2/3] Activating virtual environment..." -ForegroundColor Yellow
& .\venv\Scripts\Activate.ps1
if ($LASTEXITCODE -ne 0) {
    Write-Host "ERROR: Failed to activate virtual environment" -ForegroundColor Red
    Write-Host "You may need to enable script execution:" -ForegroundColor Yellow
    Write-Host "  Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser" -ForegroundColor Cyan
    Read-Host "Press Enter to exit"
    exit 1
}
Write-Host "✓ Environment activated" -ForegroundColor Green
Write-Host ""

# Upgrade pip
Write-Host "Upgrading pip..." -ForegroundColor Yellow
python -m pip install --upgrade pip --quiet
Write-Host "✓ Pip upgraded" -ForegroundColor Green
Write-Host ""

# Install dependencies
Write-Host "[3/3] Installing dependencies from requirements.txt..." -ForegroundColor Yellow
Write-Host "This may take 3-5 minutes..." -ForegroundColor Gray
Write-Host ""
pip install -r requirements.txt
if ($LASTEXITCODE -ne 0) {
    Write-Host "ERROR: Failed to install dependencies" -ForegroundColor Red
    Read-Host "Press Enter to exit"
    exit 1
}
Write-Host ""
Write-Host "✓ All dependencies installed successfully" -ForegroundColor Green
Write-Host ""

Write-Host "============================================" -ForegroundColor Cyan
Write-Host "Setup Complete! 🎉" -ForegroundColor Green
Write-Host "============================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "Virtual environment created in: venv\" -ForegroundColor White
Write-Host ""
Write-Host "To use the trading system:" -ForegroundColor White
Write-Host "1. Activate the environment:  .\venv\Scripts\Activate.ps1" -ForegroundColor Yellow
Write-Host "2. Run the system:            python main.py --mode paper --symbol BTCUSDT" -ForegroundColor Yellow
Write-Host ""
Write-Host "Or simply double-click: run_trading_system_venv.bat" -ForegroundColor Cyan
Write-Host ""

Read-Host "Press Enter to exit"
