@echo off
REM bb_monitor_all.bat
REM Launch bb_monitor.py for all enabled brokers sequentially (for Task Scheduler).
REM Sequential execution avoids MT5 IPC conflicts between brokers.
REM
REM 前提: axiory / exness / oanda の MT5 ターミナルは常時起動・ログイン済みであること。
REM       スクリプトはターミナルの起動・終了を行わず、接続のみを行う。
REM       ターミナルをログオン時に自動起動する設定は register_brokers.bat で行う。
REM
REM HOW TO DISABLE A BROKER:
REM   Set enabled=False in broker_config.py, then REM-out the line below.

set PYTHON=C:\Users\Administrator\AppData\Local\Programs\Python\Python312\pythonw.exe
set SCRIPT=C:\Users\Administrator\fx_bot\vps\bb_monitor.py

REM axiory (enabled=True, demo)
"%PYTHON%" "%SCRIPT%" --broker axiory

REM exness (enabled=True, demo)
"%PYTHON%" "%SCRIPT%" --broker exness

REM oanda - FX_MT5_OANDA_Startup(ONLOGON即時) → FX_MT5_Delayed_Startup(+60s)で
REM 起動順制御後、IPC dispatcher起動確認済みなら有効化する
REM (terminal.trade_allowed=False が解消されたことをtest_trade_execution.pyで確認してから有効化)
REM "%PYTHON%" "%SCRIPT%" --broker oanda

REM oanda_demo (enabled=False - 実口座開設後に有効化)
REM "%PYTHON%" "%SCRIPT%" --broker oanda_demo
