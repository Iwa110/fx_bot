@echo off
REM account_snapshot.bat
REM Append balance/equity/margin_level of all enabled brokers to optimizer\account_snapshot.csv.
REM Called by Task Scheduler: FX_Account_Snapshot (hourly). Pushed by FX_Sync_History.

set PYTHON=C:\Users\Administrator\AppData\Local\Programs\Python\Python312\python.exe
set SCRIPT=C:\Users\Administrator\fx_bot\vps\account_snapshot.py
set LOG=C:\Users\Administrator\fx_bot\logs\account_snapshot.log

echo === %date% %time% === >> "%LOG%"
"%PYTHON%" "%SCRIPT%" >> "%LOG%" 2>&1
exit /b %ERRORLEVEL%
