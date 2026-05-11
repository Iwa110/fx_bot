@echo off
REM bb_monitor_all.bat
REM Launch bb_monitor.py for all enabled brokers sequentially (for Task Scheduler).
REM Sequential execution avoids MT5 IPC conflicts between brokers.
REM
REM 実行順序の設計:
REM   1. axiory / exness を先に実行し、終了後にターミナルをwmicで終了させる
REM   2. oanda を最後に実行 (attach=True = mt5.initialize()引数なし)
REM      oanda実行時はOANDAターミナルのみ起動中の状態にする必要がある。
REM      path指定でmt5.initialize()するとterminal.trade_allowed=Falseになるため。
REM
REM HOW TO DISABLE A BROKER:
REM   Set enabled=False in broker_config.py, then REM-out the line below.

set PYTHON=C:\Users\Administrator\AppData\Local\Programs\Python\Python312\pythonw.exe
set SCRIPT=C:\Users\Administrator\fx_bot\vps\bb_monitor.py
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
