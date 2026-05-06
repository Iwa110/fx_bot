@echo off
REM register_tod_monitor.bat
REM Task Scheduler に tod_monitor.py を登録する（毎時0分実行）
REM 管理者権限で実行すること

set TASK_NAME=FX_TOD_Monitor
set PYTHON_EXE=C:\Users\Administrator\AppData\Local\Programs\Python\Python311\python.exe
set SCRIPT_PATH=C:\Users\Administrator\fx_bot\vps\tod_monitor.py
set LOG_PATH=C:\Users\Administrator\fx_bot\vps\scheduler_tod_monitor.log

echo [INFO] タスク登録: %TASK_NAME%
echo        スクリプト: %SCRIPT_PATH%
echo        ログ      : %LOG_PATH%
echo.

REM 既存タスクがあれば削除
schtasks /delete /tn "%TASK_NAME%" /f 2>nul

REM 毎時0分に実行するタスクを登録（HOURLY / mo=1 / st=00:00）
schtasks /create ^
  /tn "%TASK_NAME%" ^
  /tr "\"%PYTHON_EXE%\" \"%SCRIPT_PATH%\" >> \"%LOG_PATH%\" 2>&1" ^
  /sc HOURLY ^
  /mo 1 ^
  /st 00:00 ^
  /ru SYSTEM ^
  /rl HIGHEST ^
  /f

if %ERRORLEVEL% == 0 (
    echo [OK] タスク登録完了
    echo      タスク名: %TASK_NAME%
    echo      実行時刻: 毎時 0分 （00:00 / 01:00 / 02:00 ...）
    echo.
    schtasks /query /tn "%TASK_NAME%" /fo LIST
) else (
    echo [ERROR] タスク登録失敗。管理者権限で実行しているか確認してください。
    exit /b 1
)

pause
