@echo off
REM sma_squeeze_monitor.bat
REM Launch sma_squeeze.py for all enabled brokers in parallel (daemon process).
REM
REM HOW TO DISABLE A BROKER:
REM   Set enabled=False in broker_config.py, then REM-out the corresponding start /B line below.
REM
REM NOTE: Each broker runs as a separate background daemon (infinite loop).
REM   Logs go to sma_squeeze_log_<broker>.txt in the vps\ directory.
REM
REM WHY pythonw.exe (not python.exe):
REM   python.exe is a console app: when the bat's console closes, CTRL_CLOSE_EVENT
REM   kills all start /B python.exe children immediately. pythonw.exe is a
REM   Windows-subsystem app (no console), so it survives the console closing and
REM   runs as a true background daemon.

set PYTHONW=C:\Users\Administrator\AppData\Local\Programs\Python\Python312\pythonw.exe
set SCRIPT=C:\Users\Administrator\fx_bot\vps\sma_squeeze.py

REM axiory (enabled=True, demo)
start /B "" "%PYTHONW%" "%SCRIPT%" --broker axiory --debug

REM exness (enabled=True, demo)
start /B "" "%PYTHONW%" "%SCRIPT%" --broker exness --debug

REM oanda (enabled=True - IPC issue resolved 2026-05-11, use oanda not oanda_demo)
start /B "" "%PYTHONW%" "%SCRIPT%" --broker oanda --debug
