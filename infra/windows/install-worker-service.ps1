<#
.SYNOPSIS
  Install and supervise the Python MT5 Trading Worker on Windows using NSSM
  (or Windows Task Scheduler fallback if NSSM is not installed).

.NOTES
  Run as Administrator on the private Windows VPS / MT5 host.
  Never store plaintext MT5 passwords in .env — keep the Exness MT5 desktop
  terminal logged in under a dedicated Windows user session or Windows
  Credential Manager.
#>

param(
  [string]$ProjectRoot = "D:\AI-Project\trading-agent",
  [string]$ServiceName = "TradingAgentWorker",
  [string]$NssmPath = "C:\tools\nssm\nssm.exe"
)

$ErrorActionPreference = "Stop"

$WorkerDir = Join-Path $ProjectRoot "apps\worker"
$PythonExe = Join-Path $WorkerDir ".venv\Scripts\python.exe"
$LogDir = Join-Path $ProjectRoot "logs"

if (-not (Test-Path $PythonExe)) {
  throw "Worker virtualenv not found at $PythonExe. Run Python 3.12 venv setup first."
}

New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

if (Test-Path $NssmPath) {
  Write-Host "Installing Windows Service '$ServiceName' via NSSM..."
  & $NssmPath install $ServiceName $PythonExe "-m app.main --poll-interval 2.0"
  & $NssmPath set $ServiceName AppDirectory $WorkerDir
  & $NssmPath set $ServiceName AppStdout (Join-Path $LogDir "worker-stdout.log")
  & $NssmPath set $ServiceName AppStderr (Join-Path $LogDir "worker-stderr.log")
  & $NssmPath set $ServiceName AppRotateFiles 1
  & $NssmPath set $ServiceName AppRotateBytes 10485760
  & $NssmPath set $ServiceName AppExit Default Restart
  & $NssmPath set $ServiceName AppRestartDelay 5000
  & $NssmPath set $ServiceName Start SERVICE_AUTO_START
  Write-Host "Service installed. Start with: nssm start $ServiceName"
} else {
  Write-Host "NSSM not found at $NssmPath; registering Task Scheduler auto-start task..."
  $Action = New-ScheduledTaskAction `
    -Execute $PythonExe `
    -Argument "-m app.main --poll-interval 2.0" `
    -WorkingDirectory $WorkerDir
  $Trigger = New-ScheduledTaskTrigger -AtLogOn
  $Settings = New-ScheduledTaskSettingsSet `
    -RestartCount 999 `
    -RestartInterval (New-TimeSpan -Minutes 1) `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit ([TimeSpan]::Zero)

  Register-ScheduledTask `
    -TaskName $ServiceName `
    -Action $Action `
    -Trigger $Trigger `
    -Settings $Settings `
    -Description "Supervised Exness MT5 Python Trading Worker (DEMO-first)" `
    -Force | Out-Null

  Write-Host "Scheduled task '$ServiceName' registered. Start via Task Scheduler or Start-ScheduledTask -TaskName $ServiceName"
}
