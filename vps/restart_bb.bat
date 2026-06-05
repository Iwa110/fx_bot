@echo off
REM restart_bb.bat - Kill and restart the BB strategy after a git pull (v29)
REM
REM bb_monitor.py is NOT a daemon: the Task Scheduler task FX_BB_Monitor_All
REM fires bb_monitor_all.bat every minute, which connects to each broker
REM sequentially and exits. To apply a code change immediately (instead of
REM waiting for the next minute tick), this script:
REM   1) ends any currently-running FX_BB_Monitor_All task instance
REM   2) kills lingering bb_monitor.py pythonw processes
REM   3) triggers the task once so the new code runs right away
REM
REM Usage (run on VPS after `git pull origin main`, as Administrator):
REM   C:\Users\Administrator\fx_bot\vps\restart_bb.bat
REM
REM The per-minute schedule is left intact - this only forces an immediate run.
REM Logs: bb_log_{broker}.txt

chcp 65001 > nul

set TASK_NAME=FX_BB_Monitor_All

echo ==============================================
echo  Restart BB strategy (apply v29)
echo ==============================================
echo.

REM 1) End any currently-running task instance
echo [1/3] Ending running task instance: %TASK_NAME%
schtasks /end /tn "%TASK_NAME%" 2>nul

REM 2) Kill lingering bb_monitor.py pythonw processes
echo [2/3] Killing lingering bb_monitor.py processes
wmic process where "name='pythonw.exe' and commandline like '%%bb_monitor.py%%'" delete >nul 2>&1

REM give MT5 IPC handles a moment to release
ping -n 3 127.0.0.1 >nul

REM 3) Trigger the task once so the updated code runs immediately
echo [3/3] Triggering task: %TASK_NAME%
schtasks /run /tn "%TASK_NAME%"

if %ERRORLEVEL% == 0 (
    echo.
    echo [OK] %TASK_NAME% triggered. New code is now running.
) else (
    echo.
    echo [ERROR] Failed to trigger %TASK_NAME%.
    echo         Check the task exists: schtasks /query /tn "%TASK_NAME%"
    echo         (register it via register_brokers.bat if missing^)
    exit /b 1
)
echo.

REM Show task status
schtasks /query /tn "%TASK_NAME%" /fo LIST 2>nul | findstr "TaskName Status Last Next"
echo.
echo Tip: tail the logs to confirm the v29 cooldown fix:
echo   bb_log_axiory.txt / bb_log_exness.txt
echo   look for: "クールダウン中 T_max後" after a BB_time_stop close
echo.
pause
