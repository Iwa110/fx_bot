@echo off
REM cot_monitor.bat
REM COT Extreme x Daily Trend strategy v1 (magic=20260020)
REM Weekly COT signal, hourly loop. Single broker (oanda) sufficient.
REM Run once; daemon loops internally every 3600s.
REM
REM NOTE: Uses pythonw.exe to survive console close (same pattern as sma_squeeze_monitor.bat)

set PYTHONW=C:\Users\Administrator\AppData\Local\Programs\Python\Python312\pythonw.exe
set SCRIPT=C:\Users\Administrator\fx_bot\vps\cot_monitor.py

REM ── Kill existing cot_monitor.py processes ──────────────────────────────────
echo Killing existing cot_monitor.py processes...
powershell -NoProfile -Command ^
  "Get-WmiObject Win32_Process | Where-Object {$_.CommandLine -like '*cot_monitor.py*'} | ForEach-Object { Stop-Process -Id $_.ProcessId -Force; Write-Host ('Killed PID ' + $_.ProcessId) }"
timeout /T 2 /NOBREAK >nul

REM ── Launch daemons (axiory + exness) ────────────────────────────────────────
start /B "" "%PYTHONW%" "%SCRIPT%" --broker axiory --debug

start /B "" "%PYTHONW%" "%SCRIPT%" --broker exness --debug

echo cot_monitor started (axiory, exness).
