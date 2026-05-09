@echo off
REM trail_monitor_all.bat
REM Launch trail_monitor.py for all enabled brokers in parallel (for Task Scheduler).
REM
REM HOW TO DISABLE A BROKER:
REM   Set enabled=False in broker_config.py, then REM-out the corresponding start /B line below.
REM
REM oanda_demo NOTE:
REM   attach=True - OANDA MT5 terminal must already be running and logged in.
REM
REM oanda (live) NOTE:
REM   Remove the REM prefix from the oanda line when ready.

set PYTHON=C:\Users\Administrator\AppData\Local\Programs\Python\Python312\python.exe
set SCRIPT=C:\Users\Administrator\fx_bot\vps\trail_monitor.py

REM axiory (enabled=True, demo)
start /B "" "%PYTHON%" "%SCRIPT%" --broker axiory

REM exness (enabled=True, demo)
start /B "" "%PYTHON%" "%SCRIPT%" --broker exness

REM oanda_demo (enabled=True, attach=True - OANDA terminal must be running)
start /B "" "%PYTHON%" "%SCRIPT%" --broker oanda_demo

REM oanda (enabled=True, live account - uncomment when ready)
REM start /B "" "%PYTHON%" "%SCRIPT%" --broker oanda
