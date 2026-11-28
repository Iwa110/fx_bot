@echo off
REM register_summary.bat
REM Register summary_notify.py in Task Scheduler (daily 07:00/12:00/16:00/21:00 JST).
REM Run as Administrator.
REM
REM WHY THIS EXISTS:
REM   summary_notify.py (口座状況サマリー + heartbeat_check.check_heartbeats) had no
REM   committed Task Scheduler registration. It is the only script that calls
REM   heartbeat_check on a schedule independent of the trading daemons themselves,
REM   so if it isn't reliably scheduled, a full stop of grid/mr/news/sma daemons
REM   (e.g. VPN/MT5切断) can go unnoticed until someone looks manually.
REM
REM WHY /ru Administrator /it /rl HIGHEST:
REM   summary_notify.py calls mt5.initialize(), which requires the same
REM   interactive user session as the MT5 terminals (same reason as
REM   register_brokers.bat / register_account_snapshot.bat).

set BAT_DIR=C:\Users\Administrator\fx_bot
set VBS=C:\Users\Administrator\fx_bot\vps\run_hidden.vbs
set BAT=%BAT_DIR%\run_summary.bat

echo ==============================================
echo  FX_Summary (summary_notify.py) Task Registration
echo ==============================================
echo.

REM ---------------------------------------------
REM FX_Summary_07 - daily 07:00 JST
REM ---------------------------------------------
echo [INFO] Registering: FX_Summary_07
schtasks /delete /tn "FX_Summary_07" /f 2>nul
schtasks /create ^
  /tn "FX_Summary_07" ^
  /tr "wscript.exe //nologo \"%VBS%\" \"%BAT%\"" ^
  /sc DAILY ^
  /st 07:00 ^
  /ru Administrator ^
  /it ^
  /rl HIGHEST ^
  /f

REM ---------------------------------------------
REM FX_Summary_12 - daily 12:00 JST
REM ---------------------------------------------
echo [INFO] Registering: FX_Summary_12
schtasks /delete /tn "FX_Summary_12" /f 2>nul
schtasks /create ^
  /tn "FX_Summary_12" ^
  /tr "wscript.exe //nologo \"%VBS%\" \"%BAT%\"" ^
  /sc DAILY ^
  /st 12:00 ^
  /ru Administrator ^
  /it ^
  /rl HIGHEST ^
  /f

REM ---------------------------------------------
REM FX_Summary_16 - daily 16:00 JST
REM ---------------------------------------------
echo [INFO] Registering: FX_Summary_16
schtasks /delete /tn "FX_Summary_16" /f 2>nul
schtasks /create ^
  /tn "FX_Summary_16" ^
  /tr "wscript.exe //nologo \"%VBS%\" \"%BAT%\"" ^
  /sc DAILY ^
  /st 16:00 ^
  /ru Administrator ^
  /it ^
  /rl HIGHEST ^
  /f

REM ---------------------------------------------
REM FX_Summary_21 - daily 21:00 JST
REM ---------------------------------------------
echo [INFO] Registering: FX_Summary_21
schtasks /delete /tn "FX_Summary_21" /f 2>nul
schtasks /create ^
  /tn "FX_Summary_21" ^
  /tr "wscript.exe //nologo \"%VBS%\" \"%BAT%\"" ^
  /sc DAILY ^
  /st 21:00 ^
  /ru Administrator ^
  /it ^
  /rl HIGHEST ^
  /f

if %ERRORLEVEL% == 0 (
    echo.
    echo [OK] FX_Summary_07 / FX_Summary_12 / FX_Summary_16 / FX_Summary_21 registered
    echo      Log: C:\Users\Administrator\fx_bot\summary.log
    schtasks /query /tn "FX_Summary_07" /fo LIST 2>nul | findstr "Task Name\|Status\|Next Run"
    schtasks /query /tn "FX_Summary_12" /fo LIST 2>nul | findstr "Task Name\|Status\|Next Run"
    schtasks /query /tn "FX_Summary_16" /fo LIST 2>nul | findstr "Task Name\|Status\|Next Run"
    schtasks /query /tn "FX_Summary_21" /fo LIST 2>nul | findstr "Task Name\|Status\|Next Run"
) else (
    echo [ERROR] Registration failed. Run as Administrator.
    exit /b 1
)

pause
