@echo off
REM daily_report_all.bat
REM Run daily_report.py for all enabled brokers sequentially (for Task Scheduler).
REM Sequential (no start /B) to avoid MT5 IPC conflicts between brokers.
REM
REM HOW TO DISABLE A BROKER:
REM   Set enabled=False in broker_config.py, then REM-out the corresponding line below.
REM
REM oanda_demo NOTE:
REM   attach=True - OANDA MT5 terminal must already be running and logged in.
REM
REM oanda (live) NOTE:
REM   Remove the REM prefix from the oanda line when ready.

set PYTHON=C:\Users\Administrator\AppData\Local\Programs\Python\Python312\pythonw.exe
set SCRIPT=C:\Users\Administrator\fx_bot\vps\daily_report.py

REM axiory (enabled=True, demo)
"%PYTHON%" "%SCRIPT%" --broker axiory

REM exness (enabled=True, demo)
"%PYTHON%" "%SCRIPT%" --broker exness

REM oanda (enabled=True, demo)
"%PYTHON%" "%SCRIPT%" --broker oanda

REM oanda_demo (enabled=False)
REM "%PYTHON%" "%SCRIPT%" --broker oanda_demo
