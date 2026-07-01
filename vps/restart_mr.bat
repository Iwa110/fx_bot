@echo off
REM restart_mr.bat - double-click launcher for the correlation-cross(4h) MR monitors.
REM Pulls nothing; just (re)starts AUDCAD + CADCHF mr_monitor.py on demo brokers via restart_mr.ps1.
REM Run AFTER `git pull origin main`.
chcp 65001 > nul
powershell -ExecutionPolicy Bypass -File "%~dp0restart_mr.ps1"
pause
