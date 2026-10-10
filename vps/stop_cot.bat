@echo off
REM stop_cot.bat - Permanently stop cot_monitor.py (strategy stopped: CLAUDE.md
REM 未決事項 2026-10-10 - axiory n5 PF0.41 net-2.9万・IS/OOS未検証 -> 停止推奨)
REM
REM NOTE on "task" removal: cot_monitor is NOT registered in Windows Task
REM Scheduler (unlike FX_Sync_History / FX_Account_Snapshot etc., which have
REM their own register_*.bat). It only ever runs as a plain background
REM pythonw.exe process started by double-clicking cot_monitor.bat / restart_cot.bat.
REM So there is no `schtasks /delete` step here - confirmed via:
REM   schtasks /Query /FO LIST | findstr /I cot
REM (should return nothing). If that ever changes, add the matching
REM `schtasks /delete /tn "..." /f` line here too.
REM
REM Stop procedure:
REM   1) (optional) Drain open positions first without opening new ones:
REM        pythonw.exe cot_monitor.py --broker axiory --close-only
REM        pythonw.exe cot_monitor.py --broker exness --close-only
REM      Leave running until cot_monitor_log_*.txt shows no more open COT
REM      positions (check MT5 terminal: magic=20260020), then proceed to step 2.
REM   2) Kill the daemons for good (this script). Do NOT run cot_monitor.bat /
REM      restart_cot.bat afterwards - that would relaunch them.
REM
REM Usage (on VPS):
REM   C:\Users\Administrator\fx_bot\vps\stop_cot.bat

echo Killing cot_monitor.py processes (axiory + exness)...
powershell -NoProfile -Command ^
  "Get-WmiObject Win32_Process | Where-Object {$_.CommandLine -like '*cot_monitor.py*'} | ForEach-Object { Stop-Process -Id $_.ProcessId -Force; Write-Host ('Killed PID ' + $_.ProcessId) }"

echo.
echo cot_monitor stopped. Do not run cot_monitor.bat / restart_cot.bat again
echo unless the strategy is explicitly re-approved (see CLAUDE.md).
pause
