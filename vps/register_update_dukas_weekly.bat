@echo off
REM register_update_dukas_weekly.bat
REM Register FX_Update_Dukas_Weekly (every Saturday 10:00 VPS local time) in Task Scheduler.
REM Run as Administrator. Saturday = FX market closed, so the fetched week is complete before
REM the Sunday 09:49 JST grid-loop routine. /ru Administrator /it so git uses the
REM Administrator credentials (same reason as register_account_snapshot.bat).

set BAT_DIR=C:\Users\Administrator\fx_bot\vps
set VBS=%BAT_DIR%\run_hidden.vbs
set TASK_NAME=FX_Update_Dukas_Weekly
set BAT=%BAT_DIR%\update_dukas_weekly.bat

schtasks /delete /tn "%TASK_NAME%" /f 2>nul
schtasks /create ^
  /tn "%TASK_NAME%" ^
  /tr "wscript.exe //nologo \"%VBS%\" \"%BAT%\"" ^
  /sc WEEKLY ^
  /d SAT ^
  /st 10:00 ^
  /ru Administrator ^
  /it ^
  /rl HIGHEST ^
  /f

if %ERRORLEVEL% == 0 (
    echo [OK] %TASK_NAME% registered: every Saturday 10:00
    echo      Log: C:\Users\Administrator\fx_bot\logs\update_dukas_weekly.log
    echo Check: schtasks /Query /TN "%TASK_NAME%" /FO LIST /V
    echo Run now: schtasks /Run /TN "%TASK_NAME%"
) else (
    echo [ERROR] %TASK_NAME% registration failed. Run as Administrator.
    exit /b 1
)
pause
