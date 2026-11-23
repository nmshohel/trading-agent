<#
.SYNOPSIS
  Verify Exness MetaTrader 5 desktop terminal is running and run a one-shot
  read-only health/account/symbol verification check.

.NOTES
  Credential policy:
  - Keep MT5 account credentials stored in Windows Credential Manager or MT5's
    encrypted local profile on the private Windows host.
  - Ensure the MT5 toolbar "Algo Trading" button is enabled before running live
    or demo order execution.
  - Disable disruptive Windows Update automatic restarts during trading hours.
#>

param(
  [string]$ProjectRoot = "D:\AI-Project\trading-agent",
  [string]$Mt5Exe = "C:\Program Files\MetaTrader 5\terminal64.exe"
)

$ErrorActionPreference = "Stop"

$proc = Get-Process -Name "terminal64" -ErrorAction SilentlyContinue
if (-not $proc) {
  if (-not (Test-Path $Mt5Exe)) {
    throw "MT5 terminal not found at $Mt5Exe"
  }
  Write-Host "Starting Exness MT5 terminal: $Mt5Exe"
  Start-Process -FilePath $Mt5Exe
  Start-Sleep -Seconds 10
} else {
  Write-Host "MT5 terminal already running (PID: $($proc.Id -join ', '))"
}

$WorkerDir = Join-Path $ProjectRoot "apps\worker"
$PythonExe = Join-Path $WorkerDir ".venv\Scripts\python.exe"

Push-Location $WorkerDir
try {
  Write-Host "Running one-shot read-only MT5 & API verification..."
  & $PythonExe -m app.main --once
  if ($LASTEXITCODE -ne 0) {
    throw "Worker read-only check failed with exit code $LASTEXITCODE"
  }
  Write-Host "MT5 & Worker verification succeeded."
} finally {
  Pop-Location
}
