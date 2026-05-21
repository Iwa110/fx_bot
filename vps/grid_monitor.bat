@echo off
REM grid_monitor.bat
REM Launch grid_monitor.py for all pairs (NZDUSD / GBPJPY / CHFJPY)
REM on axiory and exness in parallel (daemon processes).
REM Kills any existing grid_monitor.py processes before (re)starting.
REM
REM HOW TO DISABLE A PAIR / BROKER:
REM   REM-out the corresponding start /B line below.
REM
REM NOTE: Each pair+broker combination runs as a separate background daemon.
REM   Logs go to grid_log_{PAIR}_{broker}.txt in the vps\ directory.
REM   State files: grid_monitor_state_{PAIR}.json (per-pair)
REM
REM Magic numbers:
REM   NZDUSD=20260030  GBPJPY=20260031  CHFJPY=20260032
REM
REM WHY pythonw.exe:
REM   pythonw.exe is a Windows-subsystem app (no console window), so it
REM   survives the bat console closing and runs as a true background daemon.

set PYTHONW=C:\Users\Administrator\AppData\Local\Programs\Python\Python312\pythonw.exe
set SCRIPT=C:\Users\Administrator\fx_bot\vps\grid_monitor.py

REM Kill any existing grid_monitor.py daemon processes before restart
wmic process where "name='pythonw.exe' and commandline like '%%grid_monitor.py%%'" delete >nul 2>&1
timeout /t 2 /nobreak >nul

REM NZDUSD (magic=20260030)
start /B "" "%PYTHONW%" "%SCRIPT%" --pair NZDUSD --broker axiory
start /B "" "%PYTHONW%" "%SCRIPT%" --pair NZDUSD --broker exness

REM GBPJPY (magic=20260031)
start /B "" "%PYTHONW%" "%SCRIPT%" --pair GBPJPY --broker axiory
start /B "" "%PYTHONW%" "%SCRIPT%" --pair GBPJPY --broker exness

REM CHFJPY (magic=20260032)
start /B "" "%PYTHONW%" "%SCRIPT%" --pair CHFJPY --broker axiory
start /B "" "%PYTHONW%" "%SCRIPT%" --pair CHFJPY --broker exness

REM oanda (disabled - grid strategy runs on axiory/exness only)
REM start /B "" "%PYTHONW%" "%SCRIPT%" --pair NZDUSD --broker oanda
