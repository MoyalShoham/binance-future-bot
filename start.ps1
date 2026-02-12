# Binance Future Bot - Startup Script
# Usage: Right-click > Run with PowerShell, or: powershell -ExecutionPolicy Bypass -File start.ps1

$ProjectDir = "C:\Users\User\Desktop\binance-future-bot"
$Python = "$ProjectDir\.conda\python.exe"

# --- Start Ollama in background ---
Write-Host "[1/3] Starting Ollama..." -ForegroundColor Cyan
$ollamaProcess = Start-Process -FilePath "ollama" -ArgumentList "serve" -PassThru -WindowStyle Minimized -ErrorAction SilentlyContinue

if (-not $ollamaProcess) {
    Write-Host "  WARNING: Could not start Ollama. Is it installed?" -ForegroundColor Yellow
    Write-Host "  Bot will fall back to rule-based mode." -ForegroundColor Yellow
} else {
    Write-Host "  Ollama started (PID: $($ollamaProcess.Id))" -ForegroundColor Green
}

# --- Wait for Ollama to be ready ---
Write-Host "[2/3] Waiting for Ollama to be ready..." -ForegroundColor Cyan
$ready = $false
for ($i = 0; $i -lt 15; $i++) {
    try {
        $response = Invoke-WebRequest -Uri "http://localhost:11434/api/tags" -TimeoutSec 2 -ErrorAction Stop
        if ($response.StatusCode -eq 200) {
            $ready = $true
            break
        }
    } catch {
        Start-Sleep -Seconds 1
    }
}

if ($ready) {
    Write-Host "  Ollama is ready!" -ForegroundColor Green
} else {
    Write-Host "  Ollama not responding after 15s - continuing anyway (rule-based fallback)" -ForegroundColor Yellow
}

# --- Run the trading bot ---
Write-Host "[3/3] Starting trading bot..." -ForegroundColor Cyan
Write-Host "  Python: $Python" -ForegroundColor Gray
Write-Host ""

Set-Location $ProjectDir
& $Python main.py --mode live --continuous --interval 30
