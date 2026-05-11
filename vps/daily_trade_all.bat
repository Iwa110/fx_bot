@echo off
REM daily_trade_all.bat
REM Run daily_trade.py for all enabled brokers sequentially (for Task Scheduler).
REM Sequential (no start /B) to avoid MT5 IPC conflicts between brokers.
REM
REM 実行順序の設計: bb_monitor_all.bat と同様。
REM   axiory/exness を先に実行→ターミナル終了→ oanda を最後に実行(attach=True)
REM
REM HOW TO DISABLE A BROKER:
REM   Set enabled=False in broker_config.py, then REM-out the corresponding line below.

set PYTHON=C:\Users\Administrator\AppData\Local\Programs\Python\Python312\pythonw.exe
set SCRIPT=C:\Users\Administrator\fx_bot\vps\daily_trade.py
set AXIORY_EXE=C:\Program Files\Axiory MetaTrader 5\terminal64.exe
set EXNESS_EXE=C:\Program Files\MetaTrader 5 EXNESS\terminal64.exe

REM axiory (enabled=True, demo)
"%PYTHON%" "%SCRIPT%" --broker axiory
wmic process where "ExecutablePath='%AXIORY_EXE:\=\\%'" delete >nul 2>&1

REM exness (enabled=True, demo)
"%PYTHON%" "%SCRIPT%" --broker exness
wmic process where "ExecutablePath='%EXNESS_EXE:\=\\%'" delete >nul 2>&1

REM oanda (enabled=True, attach=True - OANDAターミナルが起動・ログイン済みであること)
"%PYTHON%" "%SCRIPT%" --broker oanda

REM oanda_demo (enabled=False - 実口座開設後に有効化)
REM "%PYTHON%" "%SCRIPT%" --broker oanda_demo
