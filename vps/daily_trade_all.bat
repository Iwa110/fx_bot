@echo off
REM daily_trade_all.bat
REM 日次戦略を enabled=True の全ブローカーで順次実行する（Task Scheduler用）
REM
REM 【なぜ順次（start /B なし）か】
REM   daily_trade.py は1回実行完了型（デーモンなし）のため並列化可能だが、
REM   broker_utils.py の mt5.initialize() は 1プロセス1接続のため問題ない。
REM   ただし WINDOWS の MT5 IPC は同一exe複数プロセスで不安定になる場合があるため、
REM   安全のため順次実行を採用する。
REM   各ブローカーの実行完了後に次のブローカーが開始される。
REM
REM 【無効化手順】
REM   broker_config.py で enabled=False にしたブローカーは、
REM   対応する python 実行行を REM でコメントアウトすること。
REM
REM 【oanda_demo 注意】
REM   attach=True のため、OANDA MT5 ターミナルが起動・ログイン済みであること。
REM
REM 【oanda ライブ注意】
REM   準備ができたら下部の rem 行のコメントを外すこと。

set PYTHON=C:\Users\Administrator\AppData\Local\Programs\Python\Python312\python.exe
set SCRIPT=C:\Users\Administrator\fx_bot\vps\daily_trade.py

REM axiory (enabled=True, デモ)
"%PYTHON%" "%SCRIPT%" --broker axiory

REM exness (enabled=True, デモ)
"%PYTHON%" "%SCRIPT%" --broker exness

REM oanda_demo (enabled=True, attach=True - OANDAターミナル起動済み必須)
"%PYTHON%" "%SCRIPT%" --broker oanda_demo

REM oanda (enabled=True, ライブ口座 - 準備完了後に以下のコメントを解除)
REM "%PYTHON%" "%SCRIPT%" --broker oanda
