@echo off
REM register_update_claude_md.bat
REM Register update_claude_md.py in Task Scheduler (daily 07:10 JST).
REM Run as Administrator.

set TASK_NAME=FX_UpdateClaudeMd
set WRAPPER=C:\Users\Administrator\fx_bot\vps\run_update_claude_md.bat

echo [INFO] Registering: %TASK_NAME%

REM ログディレクトリを作成（存在しない場合）
if not exist "C:\Users\Administrator\fx_bot\logs" mkdir "C:\Users\Administrator\fx_bot\logs"

REM Delete existing task if present
schtasks /delete /tn "%TASK_NAME%" /f 2>nul

REM Register task to run daily at 07:10 JST (10 minutes after daily_report.py)
REM cmd.exe 経由でラッパーbatを実行 → リダイレクトが正しく動作する
schtasks /create ^
  /tn "%TASK_NAME%" ^
  /tr "cmd /c \"%WRAPPER%\"" ^
  /sc DAILY ^
  /st 07:10 ^
  /ru SYSTEM ^
  /rl HIGHEST ^
  /f

if %ERRORLEVEL% == 0 (
    echo [OK] %TASK_NAME% registered: daily 07:10 JST
    schtasks /query /tn "%TASK_NAME%" /fo LIST
) else (
    echo [ERROR] Registration failed. Run as Administrator.
    exit /b 1
)

pause
