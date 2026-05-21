@echo off
REM daily_report_all.bat
REM Run daily_report.py for all enabled brokers sequentially (for Task Scheduler).
REM Kills any lingering daily_report.py processes before starting fresh.
REM Sequential (no start /B) to avoid MT5 IPC conflicts between brokers.
REM
REM HOW TO DISABLE A BROKER:
REM   Set enabled=False in broker_config.py, then REM-out the corresponding line below.

set PYTHON=C:\Users\Administrator\AppData\Local\Programs\Python\Python312\pythonw.exe
set SCRIPT=C:\Users\Administrator\fx_bot\vps\daily_report.py

REM Kill any lingering daily_report.py processes
wmic process where "name='pythonw.exe' and commandline like '%%daily_report.py%%'" delete >nul 2>&1

REM axiory (enabled=True)
"%PYTHON%" "%SCRIPT%" --broker axiory

REM exness (enabled=True)
"%PYTHON%" "%SCRIPT%" --broker exness

REM oanda (enabled=True)
"%PYTHON%" "%SCRIPT%" --broker oanda

REM oanda_demo (enabled=False)
REM "%PYTHON%" "%SCRIPT%" --broker oanda_demo
