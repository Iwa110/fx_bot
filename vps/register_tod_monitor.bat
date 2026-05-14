@echo off
REM register_tod_monitor.bat
REM Register tod_monitor.py in Task Scheduler (hourly at :00).
REM Run as Administrator.

set TASK_NAME=FX_TOD_Monitor
set PYTHON_EXE=C:\Users\Administrator\AppData\Local\Programs\Python\Python311\python.exe
set SCRIPT_PATH=C:\Users\Administrator\fx_bot\vps\tod_monitor.py
set LOG_PATH=C:\Users\Administrator\fx_bot\vps\scheduler_tod_monitor.log

echo [INFO] Registering: %TASK_NAME%
echo        Script: %SCRIPT_PATH%
echo        Log   : %LOG_PATH%
echo.

REM Delete existing task if present
schtasks /delete /tn "%TASK_NAME%" /f 2>nul

REM Register task to run every hour at :00
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
    echo [OK] %TASK_NAME% registered: every hour at :00
    echo      (00:00 / 01:00 / 02:00 ...)
    echo.
    schtasks /query /tn "%TASK_NAME%" /fo LIST
) else (
    echo [ERROR] Registration failed. Run as Administrator.
    exit /b 1
)

pause
