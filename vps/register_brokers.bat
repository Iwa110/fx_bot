@echo off
REM register_brokers.bat
REM Register multi-broker batch tasks to Task Scheduler.
REM Run as Administrator.
REM
REM WHY /ru Administrator /it:
REM   MT5 Python IPC only works within the same Windows user session as the terminal.
REM   /ru SYSTEM runs in session 0 (isolated), causing MT5 connection to hang.
REM   /it runs in the logged-on Administrator session, which is always active on VPS.

set BAT_DIR=C:\Users\Administrator\fx_bot\vps
set LOG_DIR=C:\Users\Administrator\fx_bot\logs

if not exist "%LOG_DIR%" mkdir "%LOG_DIR%"

echo ==============================================
echo  Multi-Broker Task Scheduler Registration
echo ==============================================
echo.

REM ----------------------------------------------
REM (1) FX_BB_Monitor_All - every minute
REM ----------------------------------------------
set TASK_NAME_BB=FX_BB_Monitor_All
set BAT_BB=%BAT_DIR%\bb_monitor_all.bat
set LOG_BB=%LOG_DIR%\scheduler_bb_monitor_all.log

echo [INFO] Registering: %TASK_NAME_BB%
schtasks /delete /tn "%TASK_NAME_BB%" /f 2>nul

schtasks /create ^
  /tn "%TASK_NAME_BB%" ^
  /tr "cmd /c \"%BAT_BB%\" >> \"%LOG_BB%\" 2>&1" ^
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
set LOG_TRAIL=%LOG_DIR%\scheduler_trail_monitor_all.log

echo [INFO] Registering: %TASK_NAME_TRAIL%
schtasks /delete /tn "%TASK_NAME_TRAIL%" /f 2>nul

schtasks /create ^
  /tn "%TASK_NAME_TRAIL%" ^
  /tr "cmd /c \"%BAT_TRAIL%\" >> \"%LOG_TRAIL%\" 2>&1" ^
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
set LOG_DAILY=%LOG_DIR%\scheduler_daily_trade_all.log

echo [INFO] Registering: %TASK_NAME_DAILY%
schtasks /delete /tn "%TASK_NAME_DAILY%" /f 2>nul

schtasks /create ^
  /tn "%TASK_NAME_DAILY%" ^
  /tr "cmd /c \"%BAT_DAILY%\" >> \"%LOG_DAILY%\" 2>&1" ^
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

echo ==============================================
echo  All tasks registered successfully.
echo ==============================================
schtasks /query /tn "%TASK_NAME_BB%"    /fo LIST 2>nul | findstr "Task Name\|Status\|Next Run\|Run As"
schtasks /query /tn "%TASK_NAME_TRAIL%" /fo LIST 2>nul | findstr "Task Name\|Status\|Next Run\|Run As"
schtasks /query /tn "%TASK_NAME_DAILY%" /fo LIST 2>nul | findstr "Task Name\|Status\|Next Run\|Run As"
echo.
pause
