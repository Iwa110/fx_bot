@echo off
REM run_update_claude_md.bat - Task Schedulerから呼び出されるラッパー

set BASE=C:\Users\Administrator\fx_bot
set LOG=%BASE%\logs\scheduler_update_claude_md.log
set SCRIPT=%BASE%\vps\update_claude_md.py

if not exist "%BASE%\logs" mkdir "%BASE%\logs"

echo ========================================  >> "%LOG%"
echo [%DATE% %TIME%] 開始                      >> "%LOG%"

REM 環境確認
echo [診断] USERNAME=%USERNAME%                >> "%LOG%"
echo [診断] USERPROFILE=%USERPROFILE%          >> "%LOG%"
where python                                   >> "%LOG%" 2>&1
where py                                       >> "%LOG%" 2>&1

REM cd してから実行
cd /d "%BASE%"
if %ERRORLEVEL% NEQ 0 (
    echo [ERROR] cd失敗: %BASE%               >> "%LOG%"
    exit /b 1
)

REM py ランチャーを優先、なければ python コマンドで実行
where py >nul 2>&1
if %ERRORLEVEL% EQU 0 (
    echo [INFO] py ランチャーで実行            >> "%LOG%"
    py "%SCRIPT%"                              >> "%LOG%" 2>&1
) else (
    echo [INFO] python コマンドで実行          >> "%LOG%"
    python "%SCRIPT%"                          >> "%LOG%" 2>&1
)

echo [%DATE% %TIME%] 終了 ExitCode=%ERRORLEVEL% >> "%LOG%"
