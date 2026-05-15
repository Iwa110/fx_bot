@echo off
REM daily_analysis_all.bat
REM 当日取引分析 + 改善提案をDiscordに通知する。
REM Task Scheduler: FX_Daily_Analysis (毎日 22:05 JST) から呼び出される。
REM sync_history_all.bat (22:00) の5分後に実行すること。

set PYTHON=C:\Users\Administrator\AppData\Local\Programs\Python\Python312\python.exe
set SCRIPT=C:\Users\Administrator\fx_bot\vps\daily_analysis.py
set LOG=C:\Users\Administrator\fx_bot\logs\daily_analysis.log

echo.>> "%LOG%"
echo === %date% %time% === >> "%LOG%"
"%PYTHON%" "%SCRIPT%" >> "%LOG%" 2>&1
echo [bat] exit code: %ERRORLEVEL% >> "%LOG%"
