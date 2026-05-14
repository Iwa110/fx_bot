@echo off
REM daily_trade_all.bat
REM Run daily_trade.py for all enabled brokers sequentially (for Task Scheduler).
REM Sequential (no start /B) to avoid MT5 IPC conflicts between brokers.
REM
REM Prereq: axiory / exness / oanda MT5 terminals must be running and logged in.
REM
REM HOW TO DISABLE A BROKER:
REM   Set enabled=False in broker_config.py, then REM-out the corresponding line below.

set PYTHON=C:\Users\Administrator\AppData\Local\Programs\Python\Python312\pythonw.exe
set SCRIPT=C:\Users\Administrator\fx_bot\vps\daily_trade.py

REM axiory (enabled=True)
"%PYTHON%" "%SCRIPT%" --broker axiory

REM exness (enabled=True)
"%PYTHON%" "%SCRIPT%" --broker exness

REM oanda (enabled=True)
"%PYTHON%" "%SCRIPT%" --broker oanda

REM oanda_demo (enabled=False)
REM "%PYTHON%" "%SCRIPT%" --broker oanda_demo
