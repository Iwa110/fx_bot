@echo off
REM register_sync.bat
REM Register FX_Sync_History task in Task Scheduler.
REM Run as Administrator.
REM
REM Tasks registered:
REM   FX_Sync_History  19:50 JST - sync history.csv from MT5 and git push
REM     -> Claude routine on cloud analyzes at 20:00 JST and pushes to iPhone
REM
REM WHY /ru Administrator /it /rl HIGHEST:
REM   MT5 Python IPC requires the same user session AND elevated privileges.
REM   (same reason as register_brokers.bat)

set BAT_DIR=C:\Users\Administrator\fx_bot\vps
set VBS=%BAT_DIR%\run_hidden.vbs

echo ==============================================
echo  FX_Sync_History Task Registration
echo ==============================================
echo.

REM ----------------------------------------------
REM FX_Sync_History - daily 19:50 JST
REM   10 min before Claude analysis routine (20:00 JST)
REM ----------------------------------------------
set TASK_NAME=FX_Sync_History
set BAT=%BAT_DIR%\sync_history_all.bat

echo [INFO] Registering: %TASK_NAME%
schtasks /delete /tn "%TASK_NAME%" /f 2>nul

schtasks /create ^
  /tn "%TASK_NAME%" ^
  /tr "wscript.exe //nologo \"%VBS%\" \"%BAT%\"" ^
  /sc DAILY ^
  /st 19:50 ^
  /ru Administrator ^
  /it ^
  /rl HIGHEST ^
  /f

if %ERRORLEVEL% == 0 (
    echo [OK] %TASK_NAME% registered: daily 19:50 JST
    echo      Log: C:\Users\Administrator\fx_bot\logs\sync_history.log
    echo.
    echo Check: schtasks /Query /TN "%TASK_NAME%" /FO LIST /V
) else (
    echo [ERROR] %TASK_NAME% registration failed. Run as Administrator.
    exit /b 1
)

REM FX_Daily_Analysis タスクは削除済み（Claude routineに移行）
schtasks /delete /tn "FX_Daily_Analysis" /f 2>nul

pause
