@echo off
REM register_sync.bat
REM Register FX_Sync_History task in Task Scheduler.
REM Run as Administrator.
REM
REM Schedule: daily 22:00 JST
REM   Syncs history.csv and git push before nightly work session.
REM
REM WHY /ru Administrator /it /rl HIGHEST:
REM   MT5 Python IPC requires the same user session AND elevated privileges.
REM   (same reason as register_brokers.bat)

set TASK_NAME=FX_Sync_History
set BAT_DIR=C:\Users\Administrator\fx_bot\vps
set VBS=%BAT_DIR%\run_hidden.vbs
set BAT=%BAT_DIR%\sync_history_all.bat

echo [INFO] Registering: %TASK_NAME%
schtasks /delete /tn "%TASK_NAME%" /f 2>nul

schtasks /create ^
  /tn "%TASK_NAME%" ^
  /tr "wscript.exe //nologo \"%VBS%\" \"%BAT%\"" ^
  /sc DAILY ^
  /st 22:00 ^
  /ru Administrator ^
  /it ^
  /rl HIGHEST ^
  /f

if %ERRORLEVEL% == 0 (
    echo [OK] %TASK_NAME% registered: daily 22:00 JST
    echo      Script: %BAT%
    echo      Log   : C:\Users\Administrator\fx_bot\logs\sync_history.log
    echo.
    echo Check: schtasks /Query /TN "%TASK_NAME%" /FO LIST /V
) else (
    echo [ERROR] Registration failed. Run as Administrator.
    exit /b 1
)

pause
