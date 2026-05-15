@echo off
REM run_update_claude_md.bat - Task Schedulerから呼び出されるラッパー
REM cmd.exe 経由で実行されるため >> リダイレクトが正しく動作する

cd /d C:\Users\Administrator\fx_bot

set PYTHON_EXE=C:\Users\Administrator\AppData\Local\Programs\Python\Python311\python.exe
set SCRIPT=vps\update_claude_md.py
set LOG=logs\scheduler_update_claude_md.log

if not exist logs mkdir logs

"%PYTHON_EXE%" "%SCRIPT%" >> "%LOG%" 2>&1
