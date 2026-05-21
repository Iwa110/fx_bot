@echo off
REM sma_squeeze_monitor.bat
REM Launch sma_squeeze.py for all enabled brokers in parallel (daemon process).
REM Kills any existing sma_squeeze.py processes before (re)starting.
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

REM Kill any existing sma_squeeze.py daemon processes before restart
wmic process where "name='pythonw.exe' and commandline like '%%sma_squeeze.py%%'" delete >nul 2>&1
timeout /t 2 /nobreak >nul

REM axiory (enabled=True)
start /B "" "%PYTHONW%" "%SCRIPT%" --broker axiory

REM exness (enabled=True)
start /B "" "%PYTHONW%" "%SCRIPT%" --broker exness

REM oanda (stopped - enable when ready)
REM start /B "" "%PYTHONW%" "%SCRIPT%" --broker oanda
