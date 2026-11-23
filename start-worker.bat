@echo off
title AI Forex Trading Worker (Cloud Connected - Exness MT5)
echo ============================================================
echo   AI Forex Trading Worker (Exness MT5 -> Vercel Cloud)
echo ============================================================
echo.
tasklist /FI "IMAGENAME eq terminal64.exe" 2>NUL | find /I /N "terminal64.exe">NUL
if "%ERRORLEVEL%"=="1" (
  echo Starting Exness MetaTrader 5 visibly...
  start "" "C:\Program Files\MetaTrader 5 EXNESS\terminal64.exe"
  timeout /t 8 /nobreak >NUL
)
echo Make sure Exness MT5 is logged in and Algo Trading is ON!
echo Cloud Dashboard: https://trading-agent-web-wheat.vercel.app
echo.
cd /d "%~dp0apps\worker"
".venv\Scripts\python.exe" -m app.main --poll-interval 2.0
pause
