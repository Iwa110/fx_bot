@echo off
REM !!! STOPPED (2026-10-10, CLAUDE.md 未決事項): COT戦略は停止推奨。
REM     このbatは再稼働を意味するので使わないこと。停止は stop_cot.bat を参照。
REM
REM restart_cot.bat - Kill and restart the cot_monitor daemons (apply v2)
REM
REM cot_monitor.py is a daemon (hourly while-loop). cot_monitor.bat already
REM kills any running cot_monitor.py processes and relaunches axiory + exness,
REM so a restart is just re-running that canonical launcher. This wrapper keeps
REM the restart_* naming consistent with restart_bb.bat / restart_grid.ps1.
REM
REM Usage (on VPS after `git pull origin main`):
REM   C:\Users\Administrator\fx_bot\vps\restart_cot.bat
REM
REM Verify: cot_log_*.txt should show "cooldown X/168h since last close"
REM         after a close, and no immediate re-entry on a still-extreme COT.

call "%~dp0cot_monitor.bat"
