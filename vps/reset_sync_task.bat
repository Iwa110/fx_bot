@echo off
REM reset_sync_task.bat
REM FX_Sync_History タスクを削除・再登録し、即時実行して動作確認する。
REM Run as Administrator.

set BAT_DIR=C:\Users\Administrator\fx_bot\vps
set VBS=%BAT_DIR%\run_hidden.vbs
set BAT=%BAT_DIR%\sync_history_all.bat
set LOG=C:\Users\Administrator\fx_bot\logs\sync_history.log
set TASK_NAME=FX_Sync_History

echo ==============================================
echo  FX_Sync_History Task Reset
echo ==============================================
echo.

REM ----------------------------------------------
REM Step 1: 既存タスク削除
REM ----------------------------------------------
echo [Step 1] 既存タスクを削除中...
schtasks /delete /tn "%TASK_NAME%" /f 2>nul
if %ERRORLEVEL% == 0 (
    echo [OK] 削除完了: %TASK_NAME%
) else (
    echo [INFO] タスクが存在しなかったか、削除済みです。
)
echo.

REM ----------------------------------------------
REM Step 2: タスク再登録 (毎日 19:50 JST)
REM ----------------------------------------------
echo [Step 2] タスクを再登録中...
schtasks /create ^
  /tn "%TASK_NAME%" ^
  /tr "wscript.exe //nologo \"%VBS%\" \"%BAT%\"" ^
  /sc DAILY ^
  /st 19:50 ^
  /ru Administrator ^
  /it ^
  /rl HIGHEST ^
  /f

if not %ERRORLEVEL% == 0 (
    echo [ERROR] タスク登録失敗。管理者として実行してください。
    pause
    exit /b 1
)
echo [OK] 登録完了: %TASK_NAME% - 毎日 19:50 JST
echo.

REM ----------------------------------------------
REM Step 3: 即時実行（動作確認）
REM ----------------------------------------------
echo [Step 3] タスクを即時実行して動作確認中...
echo         ログ: %LOG%
echo.
schtasks /run /tn "%TASK_NAME%"

if not %ERRORLEVEL% == 0 (
    echo [ERROR] タスクの即時実行に失敗しました。
    pause
    exit /b 1
)

echo [OK] タスク実行開始。約30秒後にログを確認します...
echo.

REM MT5接続・同期の完了を待つ
timeout /t 30 /nobreak

REM ----------------------------------------------
REM Step 4: ログ末尾を表示
REM ----------------------------------------------
echo [Step 4] ログ末尾 (最新30行):
echo -----------------------------------------------
powershell -Command "Get-Content '%LOG%' -Tail 30"
echo -----------------------------------------------
echo.

REM ----------------------------------------------
REM Step 5: タスク登録状態の確認
REM ----------------------------------------------
echo [Step 5] タスク登録状態:
schtasks /Query /TN "%TASK_NAME%" /FO LIST /V | findstr /i "TaskName Status Next Run Last Run Last Result"
echo.

echo ==============================================
echo  完了。ログ全文: %LOG%
echo ==============================================
pause
