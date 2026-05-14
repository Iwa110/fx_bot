@echo off
REM sync_history_all.bat
REM Sync trade history from all MT5 brokers to history.csv and git push.
REM Called by Task Scheduler: FX_Sync_History (daily 22:00 JST).
REM Can also be run manually. Log is appended to logs\sync_history.log.

set PYTHON=C:\Users\Administrator\AppData\Local\Programs\Python\Python312\python.exe
set SCRIPT=C:\Users\Administrator\fx_bot\vps\sync_history.py
set LOG=C:\Users\Administrator\fx_bot\logs\sync_history.log

echo.>> "%LOG%"
echo === %date% %time% === >> "%LOG%"
"%PYTHON%" "%SCRIPT%" --days 14 >> "%LOG%" 2>&1
echo [bat] exit code: %ERRORLEVEL% >> "%LOG%"
