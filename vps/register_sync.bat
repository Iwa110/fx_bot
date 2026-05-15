@echo off
REM register_sync.bat
REM Register nightly data sync + analysis tasks in Task Scheduler.
REM Run as Administrator.
REM
REM Tasks registered:
REM   FX_Sync_History  22:00 JST - sync history.csv from MT5 and git push
REM   FX_Daily_Analysis 22:05 JST - analyze today's trades and post to Discord
REM
REM WHY /ru Administrator /it /rl HIGHEST:
REM   MT5 Python IPC requires the same user session AND elevated privileges.
REM   (same reason as register_brokers.bat)

set BAT_DIR=C:\Users\Administrator\fx_bot\vps
set VBS=%BAT_DIR%\run_hidden.vbs

echo ==============================================
echo  Nightly Sync + Analysis Task Registration
echo ==============================================
echo.

REM ----------------------------------------------
REM (1) FX_Sync_History - daily 22:00 JST
REM ----------------------------------------------
set TASK_NAME=FX_Sync_History
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
) else (
    echo [ERROR] %TASK_NAME% registration failed. Run as Administrator.
    exit /b 1
)
echo.

REM ----------------------------------------------
REM (2) FX_Daily_Analysis - daily 22:05 JST
REM     5 min after FX_Sync_History to ensure history.csv is ready
REM ----------------------------------------------
set TASK_NAME2=FX_Daily_Analysis
set BAT2=%BAT_DIR%\daily_analysis_all.bat

echo [INFO] Registering: %TASK_NAME2%
schtasks /delete /tn "%TASK_NAME2%" /f 2>nul

schtasks /create ^
  /tn "%TASK_NAME2%" ^
  /tr "wscript.exe //nologo \"%VBS%\" \"%BAT2%\"" ^
  /sc DAILY ^
  /st 22:05 ^
  /ru Administrator ^
  /it ^
  /rl HIGHEST ^
  /f

if %ERRORLEVEL% == 0 (
    echo [OK] %TASK_NAME2% registered: daily 22:05 JST
) else (
    echo [ERROR] %TASK_NAME2% registration failed. Run as Administrator.
    exit /b 1
)
echo.

echo Check: schtasks /Query /TN "%TASK_NAME%" /FO LIST /V
echo Check: schtasks /Query /TN "%TASK_NAME2%" /FO LIST /V
echo      Sync log    : C:\Users\Administrator\fx_bot\logs\sync_history.log
echo      Analysis log: C:\Users\Administrator\fx_bot\logs\daily_analysis.log

pause
