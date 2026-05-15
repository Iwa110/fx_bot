@echo off
chcp 65001 > nul

set BASE=C:\Users\Administrator\fx_bot
set LOG=%BASE%\logs\scheduler_update_claude_md.log
set SCRIPT=%BASE%\vps\update_claude_md.py

if not exist "%BASE%\logs" mkdir "%BASE%\logs"

echo ======================================== >> "%LOG%"
echo [%DATE% %TIME%] START >> "%LOG%"

cd /d "%BASE%"
if %ERRORLEVEL% NEQ 0 (
    echo [ERROR] cd failed: %BASE% >> "%LOG%"
    exit /b 1
)

set PYTHONIOENCODING=utf-8
py "%SCRIPT%" >> "%LOG%" 2>&1
set EXIT_CODE=%ERRORLEVEL%

REM Kill any lingering git child processes after script completes
taskkill /F /T /IM git.exe                      > nul 2>&1
taskkill /F /T /IM git-credential-manager.exe   > nul 2>&1
taskkill /F /T /IM git-credential-manager-core.exe > nul 2>&1

echo [%DATE% %TIME%] END ExitCode=%EXIT_CODE% >> "%LOG%"
exit /b %EXIT_CODE%
