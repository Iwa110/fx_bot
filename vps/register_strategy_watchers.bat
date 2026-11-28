@echo off
REM register_strategy_watchers.bat
REM Register ONLOGON watchdog tasks for grid_monitor / mr_monitor / news_monitor /
REM sma_squeeze (none of these had any Task Scheduler registration before - they were
REM only ever started by manually running restart_grid.ps1 / restart_mr.ps1 /
REM restart_news.bat / restart_sma.bat, so a VPS reboot or a crash left them down
REM until someone noticed and reran those scripts by hand).
REM Run as Administrator.
REM
REM Each task runs process_watcher.py <family> directly as pythonw.exe (no console,
REM no wrapping .bat that exits) - same pattern as FX_Trail_Monitor_All /
REM trail_watcher.py, so Task Scheduler's Job Object has no quick-exiting parent
REM process to clean up after. The watcher restarts any instance that crashes, and
REM also serves as the ONLOGON recovery for a VPS reboot.
REM
REM WHY /ru Administrator /it /rl HIGHEST: MT5 Python IPC requires the same
REM interactive user session AND elevated privileges as the MT5 terminals (same
REM reason as register_brokers.bat).
REM
REM NOTE: oanda_live (LIVE grid pairs) still needs a human to log the OANDA MT5
REM terminal into the account via its GUI (OTP) after every reboot - see
REM broker_config.py. The watcher will keep retrying on its own, but cannot get
REM past that OTP prompt unattended.

set PYTHONW=C:\Users\Administrator\AppData\Local\Programs\Python\Python312\pythonw.exe
set WATCHER=C:\Users\Administrator\fx_bot\vps\process_watcher.py

echo ==============================================
echo  Strategy Watchdog Task Registration
echo ==============================================
echo.

echo [INFO] Registering: FX_Grid_Monitor_All
schtasks /delete /tn "FX_Grid_Monitor_All" /f 2>nul
schtasks /create ^
  /tn "FX_Grid_Monitor_All" ^
  /tr "\"%PYTHONW%\" \"%WATCHER%\" grid" ^
  /sc ONLOGON ^
  /ru Administrator ^
  /it ^
  /rl HIGHEST ^
  /f

echo [INFO] Registering: FX_MR_Monitor_All
schtasks /delete /tn "FX_MR_Monitor_All" /f 2>nul
schtasks /create ^
  /tn "FX_MR_Monitor_All" ^
  /tr "\"%PYTHONW%\" \"%WATCHER%\" mr" ^
  /sc ONLOGON ^
  /ru Administrator ^
  /it ^
  /rl HIGHEST ^
  /f

echo [INFO] Registering: FX_News_Monitor_All
schtasks /delete /tn "FX_News_Monitor_All" /f 2>nul
schtasks /create ^
  /tn "FX_News_Monitor_All" ^
  /tr "\"%PYTHONW%\" \"%WATCHER%\" news" ^
  /sc ONLOGON ^
  /ru Administrator ^
  /it ^
  /rl HIGHEST ^
  /f

echo [INFO] Registering: FX_SMA_Squeeze_All
schtasks /delete /tn "FX_SMA_Squeeze_All" /f 2>nul
schtasks /create ^
  /tn "FX_SMA_Squeeze_All" ^
  /tr "\"%PYTHONW%\" \"%WATCHER%\" sma" ^
  /sc ONLOGON ^
  /ru Administrator ^
  /it ^
  /rl HIGHEST ^
  /f

if %ERRORLEVEL% == 0 (
    echo.
    echo [OK] FX_Grid_Monitor_All / FX_MR_Monitor_All / FX_News_Monitor_All / FX_SMA_Squeeze_All registered
    echo      Logs: grid_watcher.log / mr_watcher.log / news_watcher.log / sma_watcher.log
    schtasks /query /tn "FX_Grid_Monitor_All" /fo LIST 2>nul | findstr "Task Name\|Status\|Logon"
) else (
    echo [ERROR] Registration failed. Run as Administrator.
    exit /b 1
)

pause
