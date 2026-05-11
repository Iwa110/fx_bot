@echo off
REM daily_trade_all.bat
REM Run daily_trade.py for all enabled brokers sequentially (for Task Scheduler).
REM Sequential (no start /B) to avoid MT5 IPC conflicts between brokers.
REM
REM 前提: axiory / exness / oanda の MT5 ターミナルは常時起動・ログイン済みであること。
REM
REM HOW TO DISABLE A BROKER:
REM   Set enabled=False in broker_config.py, then REM-out the corresponding line below.

set PYTHON=C:\Users\Administrator\AppData\Local\Programs\Python\Python312\pythonw.exe
set SCRIPT=C:\Users\Administrator\fx_bot\vps\daily_trade.py

REM axiory (enabled=True, demo)
"%PYTHON%" "%SCRIPT%" --broker axiory

REM exness (enabled=True, demo)
"%PYTHON%" "%SCRIPT%" --broker exness

REM oanda (enabled=False - terminal.trade_allowed=False 問題未解決のため停止中)
REM "%PYTHON%" "%SCRIPT%" --broker oanda

REM oanda_demo (enabled=False - 実口座開設後に有効化)
REM "%PYTHON%" "%SCRIPT%" --broker oanda_demo
