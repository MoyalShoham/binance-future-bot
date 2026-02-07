# Setup Script for Multi-Agent Trading System (PowerShell)
# This script creates a conda environment and installs all dependencies

Write-Host "============================================" -ForegroundColor Cyan
Write-Host "Multi-Agent Trading System - Setup" -ForegroundColor Cyan
Write-Host "============================================" -ForegroundColor Cyan
Write-Host ""

# Create conda environment
Write-Host "[1/4] Creating conda environment 'trading-system' with Python 3.11..." -ForegroundColor Yellow
conda create -n trading-system python=3.11 -y
if ($LASTEXITCODE -ne 0) {
    Write-Host "ERROR: Failed to create conda environment" -ForegroundColor Red
    Read-Host "Press Enter to exit"
    exit 1
}
Write-Host "✓ Conda environment created successfully" -ForegroundColor Green
Write-Host ""

# Activate conda environment
Write-Host "[2/4] Activating conda environment..." -ForegroundColor Yellow
conda activate trading-system
if ($LASTEXITCODE -ne 0) {
    Write-Host "ERROR: Failed to activate conda environment" -ForegroundColor Red
    Read-Host "Press Enter to exit"
    exit 1
}
Write-Host "✓ Environment activated" -ForegroundColor Green
Write-Host ""

# Upgrade pip
Write-Host "[3/4] Upgrading pip..." -ForegroundColor Yellow
python -m pip install --upgrade pip
Write-Host "✓ Pip upgraded" -ForegroundColor Green
Write-Host ""

# Install dependencies
Write-Host "[4/4] Installing dependencies from requirements.txt..." -ForegroundColor Yellow
Write-Host "This may take 3-5 minutes..." -ForegroundColor Gray
pip install -r requirements.txt
if ($LASTEXITCODE -ne 0) {
    Write-Host "ERROR: Failed to install dependencies" -ForegroundColor Red
    Read-Host "Press Enter to exit"
    exit 1
}
Write-Host "✓ All dependencies installed successfully" -ForegroundColor Green
Write-Host ""

Write-Host "============================================" -ForegroundColor Cyan
Write-Host "Setup Complete! 🎉" -ForegroundColor Green
Write-Host "============================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "To use the trading system:" -ForegroundColor White
Write-Host "1. Activate the environment:  conda activate trading-system" -ForegroundColor Yellow
Write-Host "2. Run the system:            python main.py --mode paper --symbol BTCUSDT" -ForegroundColor Yellow
Write-Host ""
Write-Host "Quick test:" -ForegroundColor White
Write-Host "  python main.py --mode paper --symbol BTCUSDT" -ForegroundColor Cyan
Write-Host ""
Write-Host "Continuous trading (every 5 min):" -ForegroundColor White
Write-Host "  python main.py --mode paper --symbol BTCUSDT --continuous --interval 300" -ForegroundColor Cyan
Write-Host ""

Read-Host "Press Enter to exit"
