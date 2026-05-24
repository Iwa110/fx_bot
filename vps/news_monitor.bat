@echo off
REM news_monitor.bat
REM 経済指標戦略(B+C複合) ライブ監視 v1 (magic=20260040)
REM ForexFactory JSON から高インパクト指標を取得し、サプライズ+値動きでエントリー。
REM
REM 対象ブローカー: axiory / exness (oanda は停止中)
REM 対象指標: NFP(USDJPY) / US CPI(USDJPY/EURUSD) / UK CPI(GBPUSD/GBPJPY)
REM
REM NOTE: pythonw.exe を使用してコンソールクローズ後もデーモン継続
REM       ログ: vps\news_monitor_log_{broker}.txt
REM       VPS BT完了後: news_monitor.py の PARAMS セクションを更新して再起動

set PYTHONW=C:\Users\Administrator\AppData\Local\Programs\Python\Python312\pythonw.exe
set SCRIPT=C:\Users\Administrator\fx_bot\vps\news_monitor.py

REM ── Kill existing news_monitor.py processes ───────────────────────────────
echo Killing existing news_monitor.py processes...
wmic process where "name='pythonw.exe' and commandline like '%%news_monitor.py%%'" delete >nul 2>&1
timeout /t 2 /nobreak >nul

REM ── Launch daemons ────────────────────────────────────────────────────────
REM axiory (enabled=True)
start /B "" "%PYTHONW%" "%SCRIPT%" --broker axiory

REM exness (enabled=True)
start /B "" "%PYTHONW%" "%SCRIPT%" --broker exness

REM oanda (停止中 - 必要な場合は以下を有効化)
REM start /B "" "%PYTHONW%" "%SCRIPT%" --broker oanda

echo news_monitor started (axiory, exness).
