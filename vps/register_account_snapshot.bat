@echo off
REM register_account_snapshot.bat
REM Register FX_Account_Snapshot (hourly at :05) in Task Scheduler. Run as Administrator.
REM :05 avoids FX_Sync_History (19:50) overlap. Same /ru /it /rl HIGHEST reason as register_sync.bat
REM (MT5 Python IPC requires the same user session AND elevated privileges).

set BAT_DIR=C:\Users\Administrator\fx_bot\vps
set VBS=%BAT_DIR%\run_hidden.vbs
set TASK_NAME=FX_Account_Snapshot
set BAT=%BAT_DIR%\account_snapshot.bat

schtasks /delete /tn "%TASK_NAME%" /f 2>nul
schtasks /create ^
  /tn "%TASK_NAME%" ^
  /tr "wscript.exe //nologo \"%VBS%\" \"%BAT%\"" ^
  /sc HOURLY ^
  /mo 1 ^
  /st 00:05 ^
  /ru Administrator ^
  /it ^
  /rl HIGHEST ^
  /f

if %ERRORLEVEL% == 0 (
    echo [OK] %TASK_NAME% registered: hourly at :05
    echo      Log: C:\Users\Administrator\fx_bot\logs\account_snapshot.log
    echo Check: schtasks /Query /TN "%TASK_NAME%" /FO LIST /V
) else (
    echo [ERROR] %TASK_NAME% registration failed. Run as Administrator.
    exit /b 1
)
pause
