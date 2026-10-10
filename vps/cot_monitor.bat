@echo off
REM !!! STOPPED (2026-10-10, CLAUDE.md 未決事項): axiory n5 PF0.41 net-2.9万・
REM     IS/OOS未検証のため停止推奨。このbatを実行すると停止中の戦略が再起動する。
REM     停止手順は stop_cot.bat を参照。再稼働は明示的な再承認後のみ。
REM
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
