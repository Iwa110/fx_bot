@echo off
REM daily_trade_all.bat
REM Run daily_trade.py for all enabled brokers sequentially (for Task Scheduler).
REM Kills any lingering daily_trade.py processes before starting fresh.
REM Sequential (no start /B) to avoid MT5 IPC conflicts between brokers.
REM
REM 前提: axiory / exness / oanda の MT5 ターミナルは常時起動・ログイン済みであること。
REM
REM HOW TO DISABLE A BROKER:
REM   Set enabled=False in broker_config.py, then REM-out the corresponding line below.

set PYTHON=C:\Users\Administrator\AppData\Local\Programs\Python\Python312\pythonw.exe
set SCRIPT=C:\Users\Administrator\fx_bot\vps\daily_trade.py

REM Kill any lingering daily_trade.py processes
wmic process where "name='pythonw.exe' and commandline like '%%daily_trade.py%%'" delete >nul 2>&1

REM axiory (enabled=True)
"%PYTHON%" "%SCRIPT%" --broker axiory

REM exness (enabled=True)
"%PYTHON%" "%SCRIPT%" --broker exness

REM oanda (RETIRED 2026-06-24: 既定パス端末をliveへ切替=demo oanda無効。実口座はgrid_monitor --broker oanda_live のみ)
REM "%PYTHON%" "%SCRIPT%" --broker oanda

REM oanda_demo (enabled=False - 実口座開設後に有効化)
REM "%PYTHON%" "%SCRIPT%" --broker oanda_demo
