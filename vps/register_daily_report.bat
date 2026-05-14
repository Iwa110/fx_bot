@echo off
REM register_daily_report.bat
REM Register daily_report.py in Task Scheduler (daily 07:00 JST).
REM Run as Administrator.

set TASK_NAME=FX_DailyReport
set PYTHON_EXE=C:\Users\Administrator\AppData\Local\Programs\Python\Python311\python.exe
set SCRIPT_PATH=C:\Users\Administrator\fx_bot\vps\daily_report.py
set LOG_PATH=C:\Users\Administrator\fx_bot\logs\scheduler_daily_report.log

echo [INFO] Registering: %TASK_NAME%

REM Delete existing task if present
schtasks /delete /tn "%TASK_NAME%" /f 2>nul

REM Register task to run daily at 07:00 JST
schtasks /create ^
  /tn "%TASK_NAME%" ^
  /tr "\"%PYTHON_EXE%\" \"%SCRIPT_PATH%\" >> \"%LOG_PATH%\" 2>&1" ^
  /sc DAILY ^
  /st 07:00 ^
  /ru SYSTEM ^
  /rl HIGHEST ^
  /f

if %ERRORLEVEL% == 0 (
    echo [OK] %TASK_NAME% registered: daily 07:00 JST
    schtasks /query /tn "%TASK_NAME%" /fo LIST
) else (
    echo [ERROR] Registration failed. Run as Administrator.
    exit /b 1
)

pause
