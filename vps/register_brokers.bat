@echo off
REM register_brokers.bat
REM Register multi-broker batch tasks to Task Scheduler.
REM Run as Administrator.
REM
REM WHY /ru Administrator /it /rl HIGHEST:
REM   MT5 Python IPC requires the same user session AND elevated privileges as the terminal.
REM   /ru SYSTEM: isolated in session 0, MT5 unreachable.
REM   /it: runs in the logged-on Administrator interactive session.
REM   /rl HIGHEST: elevates the process to match MT5 terminal privilege level.
REM
REM WHY no >> log redirect:
REM   The redirect requires the log directory to exist. If missing, cmd exits with
REM   error code 1 immediately without running the bat file. Each Python script
REM   writes its own log (bb_log_axiory.txt, trail_log_axiory.txt, etc.), so the
REM   external redirect is unnecessary.
REM
REM WHY wscript.exe //nologo run_hidden.vbs:
REM   Task Scheduler with /it shows a cmd.exe window every time a .bat is launched.
REM   run_hidden.vbs calls WshShell.Run with WindowStyle=0, hiding the window
REM   while keeping the process in the same interactive session for MT5 IPC.

set BAT_DIR=C:\Users\Administrator\fx_bot\vps
set VBS=%BAT_DIR%\run_hidden.vbs

echo ==============================================
echo  Multi-Broker Task Scheduler Registration
echo ==============================================
echo.

REM ----------------------------------------------
REM (1) FX_BB_Monitor_All - every minute
REM ----------------------------------------------
set TASK_NAME_BB=FX_BB_Monitor_All
set BAT_BB=%BAT_DIR%\bb_monitor_all.bat

echo [INFO] Registering: %TASK_NAME_BB%
schtasks /delete /tn "%TASK_NAME_BB%" /f 2>nul

schtasks /create ^
  /tn "%TASK_NAME_BB%" ^
  /tr "wscript.exe //nologo \"%VBS%\" \"%BAT_BB%\"" ^
  /sc MINUTE ^
  /mo 1 ^
  /ru Administrator ^
  /it ^
  /rl HIGHEST ^
  /f

if %ERRORLEVEL% == 0 (
    echo [OK] %TASK_NAME_BB% registered: every minute
) else (
    echo [ERROR] %TASK_NAME_BB% registration failed
    exit /b 1
)
echo.

REM ----------------------------------------------
REM (2) FX_Trail_Monitor_All - every minute
REM ----------------------------------------------
set TASK_NAME_TRAIL=FX_Trail_Monitor_All
set BAT_TRAIL=%BAT_DIR%\trail_monitor_all.bat

echo [INFO] Registering: %TASK_NAME_TRAIL%
schtasks /delete /tn "%TASK_NAME_TRAIL%" /f 2>nul

schtasks /create ^
  /tn "%TASK_NAME_TRAIL%" ^
  /tr "wscript.exe //nologo \"%VBS%\" \"%BAT_TRAIL%\"" ^
  /sc MINUTE ^
  /mo 1 ^
  /ru Administrator ^
  /it ^
  /rl HIGHEST ^
  /f

if %ERRORLEVEL% == 0 (
    echo [OK] %TASK_NAME_TRAIL% registered: every minute
) else (
    echo [ERROR] %TASK_NAME_TRAIL% registration failed
    exit /b 1
)
echo.

REM ----------------------------------------------
REM (3) FX_Daily_Trade_All - daily 07:00 JST (= UTC 22:00 prev day)
REM     /st uses local time (JST), so specify 07:00 directly.
REM ----------------------------------------------
set TASK_NAME_DAILY=FX_Daily_Trade_All
set BAT_DAILY=%BAT_DIR%\daily_trade_all.bat

echo [INFO] Registering: %TASK_NAME_DAILY%
schtasks /delete /tn "%TASK_NAME_DAILY%" /f 2>nul

schtasks /create ^
  /tn "%TASK_NAME_DAILY%" ^
  /tr "wscript.exe //nologo \"%VBS%\" \"%BAT_DAILY%\"" ^
  /sc DAILY ^
  /st 07:00 ^
  /ru Administrator ^
  /it ^
  /rl HIGHEST ^
  /f

if %ERRORLEVEL% == 0 (
    echo [OK] %TASK_NAME_DAILY% registered: daily 07:00 JST
) else (
    echo [ERROR] %TASK_NAME_DAILY% registration failed
    exit /b 1
)
echo.

REM ----------------------------------------------
REM (4) FX_DailyReport_All - daily 07:05 JST (5 min after FX_Daily_Trade_All)
REM
REM NOTE: FX_DailyReport (registered via register_daily_report.bat, Python311) is
REM   kept as-is. FX_DailyReport_All replaces it for multi-broker operation.
REM   If both tasks conflict, disable FX_DailyReport via Task Scheduler GUI.
REM ----------------------------------------------
set TASK_NAME_REPORT=FX_DailyReport_All
set BAT_REPORT=%BAT_DIR%\daily_report_all.bat

echo [INFO] Registering: %TASK_NAME_REPORT%
schtasks /delete /tn "%TASK_NAME_REPORT%" /f 2>nul

schtasks /create ^
  /tn "%TASK_NAME_REPORT%" ^
  /tr "wscript.exe //nologo \"%VBS%\" \"%BAT_REPORT%\"" ^
  /sc DAILY ^
  /st 07:05 ^
  /ru Administrator ^
  /it ^
  /rl HIGHEST ^
  /f

if %ERRORLEVEL% == 0 (
    echo [OK] %TASK_NAME_REPORT% registered: daily 07:05 JST
) else (
    echo [ERROR] %TASK_NAME_REPORT% registration failed
    exit /b 1
)
echo.

echo ==============================================
echo  All tasks registered successfully.
echo ==============================================
schtasks /query /tn "%TASK_NAME_BB%"     /fo LIST 2>nul | findstr "Task Name\|Status\|Next Run\|Run As"
schtasks /query /tn "%TASK_NAME_TRAIL%"  /fo LIST 2>nul | findstr "Task Name\|Status\|Next Run\|Run As"
schtasks /query /tn "%TASK_NAME_DAILY%"  /fo LIST 2>nul | findstr "Task Name\|Status\|Next Run\|Run As"
schtasks /query /tn "%TASK_NAME_REPORT%" /fo LIST 2>nul | findstr "Task Name\|Status\|Next Run\|Run As"
echo.
pause
