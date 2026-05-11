@echo off
REM trail_monitor_all.bat
REM Launch trail_monitor.py for all enabled brokers in parallel (for Task Scheduler).
REM
REM HOW TO DISABLE A BROKER:
REM   Set enabled=False in broker_config.py, then REM-out the corresponding start /B line below.
REM
REM NOTE: trail_watcher.py (ONLOGON) is the primary way to run trail_monitor.
REM   This bat is kept as a manual fallback only.

REM WHY pythonw.exe (not python.exe):
REM   python.exe is a console app: when the bat's console closes, CTRL_CLOSE_EVENT
REM   kills all start /B python.exe children immediately. pythonw.exe is a
REM   Windows-subsystem app (no console), so it survives the console closing and
REM   runs as a true background daemon. Logs go to trail_log_<broker>.txt.
set PYTHONW=C:\Users\Administrator\AppData\Local\Programs\Python\Python312\pythonw.exe
set SCRIPT=C:\Users\Administrator\fx_bot\vps\trail_monitor.py

REM axiory (enabled=True, demo)
start /B "" "%PYTHONW%" "%SCRIPT%" --broker axiory

REM exness (enabled=True, demo)
start /B "" "%PYTHONW%" "%SCRIPT%" --broker exness

REM oanda (enabled=True, demo)
start /B "" "%PYTHONW%" "%SCRIPT%" --broker oanda

REM oanda_demo (enabled=False)
REM start /B "" "%PYTHONW%" "%SCRIPT%" --broker oanda_demo
