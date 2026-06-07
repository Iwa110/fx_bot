@echo off
chcp 65001 > nul
REM export_5m_history.bat
REM 一度きり実行: MT5から2年分の5m足(GBPJPY/EURJPY/GBPUSD)をエクスポートしgit push。
REM ダブルクリックで実行可。常駐タスク登録は不要(日次差分は update_data.py が継続)。
REM 前提: いずれかのMT5端末が起動済みであること。

set PYTHON=C:\Users\Administrator\AppData\Local\Programs\Python\Python312\python.exe
set SCRIPT=C:\Users\Administrator\fx_bot\vps\export_5m_history.py
set LOG=C:\Users\Administrator\fx_bot\logs\export_5m_history.log

echo.>> "%LOG%"
echo === %date% %time% === >> "%LOG%"
"%PYTHON%" "%SCRIPT%" %* >> "%LOG%" 2>&1
set EXIT_CODE=%ERRORLEVEL%
echo [bat] exit code: %EXIT_CODE% >> "%LOG%"
type "%LOG%" | more
echo.
echo ---- 終了コード: %EXIT_CODE% (0=成功) ----
pause
