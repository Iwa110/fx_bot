@echo off
REM restart_sma.bat - Kill and restart the sma_squeeze daemons (apply v4.6)
REM
REM sma_squeeze.py is a daemon (60s while-loop). sma_squeeze_monitor.bat already
REM kills any running sma_squeeze.py processes and relaunches axiory + exness,
REM so a restart is just re-running that canonical launcher. This wrapper keeps
REM the restart_* naming consistent with restart_bb.bat / restart_grid.ps1.
REM
REM Usage (on VPS after `git pull origin main`):
REM   C:\Users\Administrator\fx_bot\vps\restart_sma.bat
REM
REM Verify: sma_squeeze_log_*.txt startup line shows "sma_squeeze v4.6 started",
REM         and after a force-close (TMAX/CLOSE/SLOPE_EXIT) a re-entry is gated
REM         by "cooldown X/180min".

call "%~dp0sma_squeeze_monitor.bat"
