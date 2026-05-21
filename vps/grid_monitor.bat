@echo off
REM grid_monitor.bat
REM Launch grid_monitor.py for axiory and exness in parallel (daemon process).
REM Kills any existing grid_monitor.py processes before (re)starting.
REM
REM HOW TO DISABLE A BROKER:
REM   Set enabled=False in broker_config.py, then REM-out the corresponding start /B line below.
REM
REM NOTE: Each broker runs as a separate background daemon (infinite loop).
REM   Logs go to grid_log_{broker}.txt in the vps\ directory.
REM
REM WHY pythonw.exe:
REM   pythonw.exe is a Windows-subsystem app (no console window), so it
REM   survives the bat console closing and runs as a true background daemon.

set PYTHONW=C:\Users\Administrator\AppData\Local\Programs\Python\Python312\pythonw.exe
set SCRIPT=C:\Users\Administrator\fx_bot\vps\grid_monitor.py

REM Kill any existing grid_monitor.py daemon processes before restart
wmic process where "name='pythonw.exe' and commandline like '%%grid_monitor.py%%'" delete >nul 2>&1
timeout /t 2 /nobreak >nul

REM axiory (enabled=True)
start /B "" "%PYTHONW%" "%SCRIPT%" --broker axiory

REM exness (enabled=True)
start /B "" "%PYTHONW%" "%SCRIPT%" --broker exness

REM oanda (disabled - grid strategy runs on axiory/exness only)
REM start /B "" "%PYTHONW%" "%SCRIPT%" --broker oanda
