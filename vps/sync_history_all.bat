@echo off
REM sync_history_all.bat
REM MT5全ブローカーの取引履歴をhistory.csvに同期してgit pushする。
REM Task Scheduler: FX_Sync_History (毎日 22:00 JST) から呼び出される。
REM 手動実行も可能。ログは logs\sync_history.log に追記。

set PYTHON=C:\Users\Administrator\AppData\Local\Programs\Python\Python312\python.exe
set SCRIPT=C:\Users\Administrator\fx_bot\vps\sync_history.py
set LOG=C:\Users\Administrator\fx_bot\logs\sync_history.log

echo.>> "%LOG%"
echo === %date% %time% === >> "%LOG%"
"%PYTHON%" "%SCRIPT%" --days 14 >> "%LOG%" 2>&1
echo [bat] exit code: %ERRORLEVEL% >> "%LOG%"
