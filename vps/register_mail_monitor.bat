@echo off
REM register_mail_monitor.bat
REM Register "FX Mail Monitor" in Task Scheduler (every 5 minutes).
REM Run as Administrator.
REM
REM BACKGROUND (2026-11-28):
REM   The task existed only as a hand-created registration (never checked into
REM   this repo), with LogonType=Password and a legacy "run once on 2026-04-15 +
REM   indefinite 5-minute repetition" trigger. At some point its stored password
REM   credential went stale (likely the same event that broke grid/mr/news/sma/
REM   stat_arb_monitor around 2026-10-22) and Task Scheduler silently stopped
REM   launching it: "next run time" kept advancing every 5 minutes, but no
REM   process ever started and heartbeat.json's mail_monitor key never updated.
REM   Recreated here with LogonType=InteractiveToken (/it, no stored password -
REM   same mechanism as every other FX_* task) and a native /sc minute /mo 5
REM   trigger instead of the once+indefinite-repeat trick.
REM
REM WHY run_hidden.vbs: avoids a console window flashing on the Administrator's
REM   RDP desktop every 5 minutes (mail_monitor.py uses python.exe, not pythonw.exe,
REM   since it needs to print diagnostics when run manually).
REM
REM NOTE: like every other MT5-dependent task here, FX RESET/STOP/STATUS mail
REM   commands can only reach MT5 while the Administrator's interactive session
REM   is active - see CLAUDE.md. The plain inbox check (no command matched) does
REM   not need MT5 and works regardless.

set TASK_NAME=FX Mail Monitor
set VBS=C:\Users\Administrator\fx_bot\vps\run_hidden.vbs
set BAT=C:\Users\Administrator\fx_bot\run_mail.bat

echo [INFO] Registering: %TASK_NAME%
schtasks /delete /tn "%TASK_NAME%" /f 2>nul

schtasks /create ^
  /tn "%TASK_NAME%" ^
  /tr "wscript.exe //nologo \"%VBS%\" \"%BAT%\"" ^
  /sc minute ^
  /mo 5 ^
  /ru Administrator ^
  /it ^
  /rl HIGHEST ^
  /f

if %ERRORLEVEL% == 0 (
    echo [OK] %TASK_NAME% registered: every 5 minutes
    schtasks /query /tn "%TASK_NAME%" /fo LIST /v | findstr "Task Name\|Status\|Logon Mode\|Next Run"
) else (
    echo [ERROR] Registration failed. Run as Administrator.
    exit /b 1
)

pause
