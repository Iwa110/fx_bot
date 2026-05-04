@echo off
REM register_daily_report.bat
REM Task Schedulerに daily_report.py を登録する（毎朝7:00 JST実行）
REM 管理者権限で実行すること

set TASK_NAME=FX_DailyReport
set PYTHON_EXE=C:\Users\Administrator\AppData\Local\Programs\Python\Python311\python.exe
set SCRIPT_PATH=C:\Users\Administrator\fx_bot\vps\daily_report.py
set LOG_PATH=C:\Users\Administrator\fx_bot\logs\scheduler_daily_report.log

echo [INFO] タスク登録: %TASK_NAME%

REM 既存タスクがあれば削除
schtasks /delete /tn "%TASK_NAME%" /f 2>nul

REM 毎日7:00（JST）に実行するタスクを登録
schtasks /create ^
  /tn "%TASK_NAME%" ^
  /tr "\"%PYTHON_EXE%\" \"%SCRIPT_PATH%\" >> \"%LOG_PATH%\" 2>&1" ^
  /sc DAILY ^
  /st 07:00 ^
  /ru SYSTEM ^
  /rl HIGHEST ^
  /f

if %ERRORLEVEL% == 0 (
    echo [OK] タスク登録完了: %TASK_NAME%  実行時刻: 毎日 07:00 JST
    schtasks /query /tn "%TASK_NAME%" /fo LIST
) else (
    echo [ERROR] タスク登録失敗。管理者権限で実行しているか確認してください。
    exit /b 1
)

pause
