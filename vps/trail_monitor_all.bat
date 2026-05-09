@echo off
REM trail_monitor_all.bat
REM トレーリングストップ監視を enabled=True の全ブローカーで並列起動する（Task Scheduler用）
REM
REM 【無効化手順】
REM   broker_config.py で enabled=False にしたブローカーは、
REM   対応する start /B 行を REM でコメントアウトすること。
REM
REM 【oanda_demo 注意】
REM   attach=True のため、OANDA MT5 ターミナルが起動・ログイン済みであること。
REM
REM 【oanda ライブ注意】
REM   準備ができたら下部の rem 行のコメントを外すこと。

set PYTHON=C:\Users\Administrator\AppData\Local\Programs\Python\Python312\python.exe
set SCRIPT=C:\Users\Administrator\fx_bot\vps\trail_monitor.py

REM axiory (enabled=True, デモ)
start /B "" "%PYTHON%" "%SCRIPT%" --broker axiory

REM exness (enabled=True, デモ)
start /B "" "%PYTHON%" "%SCRIPT%" --broker exness

REM oanda_demo (enabled=True, attach=True - OANDAターミナル起動済み必須)
start /B "" "%PYTHON%" "%SCRIPT%" --broker oanda_demo

REM oanda (enabled=True, ライブ口座 - 準備完了後に以下のコメントを解除)
REM start /B "" "%PYTHON%" "%SCRIPT%" --broker oanda
