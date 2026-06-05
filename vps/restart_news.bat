@echo off
REM restart_news.bat - Kill and restart the news_monitor daemons (persist fix)
REM
REM news_monitor.py is a daemon (60s while-loop). news_monitor_all.bat already
REM kills any running news_monitor.py processes and relaunches axiory + exness,
REM so a restart is just re-running that canonical launcher. This wrapper keeps
REM the restart_* naming consistent with restart_bb.bat / restart_grid.ps1.
REM
REM Usage (on VPS after `git pull origin main`):
REM   C:\Users\Administrator\fx_bot\vps\restart_news.bat
REM
REM Verify: news_monitor_log_*.txt startup shows "processed_ids loaded: N entries".
REM         Processed event IDs now survive restarts via news_processed_ids.json
REM         (gitignored runtime state), preventing re-trading the same event.

call "%~dp0news_monitor_all.bat"
