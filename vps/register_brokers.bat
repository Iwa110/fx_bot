@echo off
REM register_brokers.bat
REM Task Scheduler に マルチブローカー並列起動バッチを登録する
REM 管理者権限で実行すること

set BAT_DIR=C:\Users\Administrator\fx_bot\vps
set LOG_DIR=C:\Users\Administrator\fx_bot\logs

REM ログディレクトリを作成（なければ）
if not exist "%LOG_DIR%" mkdir "%LOG_DIR%"

echo ==============================================
echo  マルチブローカー Task Scheduler 登録
echo ==============================================
echo.

REM ----------------------------------------------
REM (1) FX_BB_Monitor_All - bb_monitor_all.bat 毎分実行
REM ----------------------------------------------
set TASK_NAME_BB=FX_BB_Monitor_All
set BAT_BB=%BAT_DIR%\bb_monitor_all.bat
set LOG_BB=%LOG_DIR%\scheduler_bb_monitor_all.log

echo [INFO] タスク登録: %TASK_NAME_BB%
schtasks /delete /tn "%TASK_NAME_BB%" /f 2>nul

schtasks /create ^
  /tn "%TASK_NAME_BB%" ^
  /tr "cmd /c \"%BAT_BB%\" >> \"%LOG_BB%\" 2>&1" ^
  /sc MINUTE ^
  /mo 1 ^
  /ru SYSTEM ^
  /rl HIGHEST ^
  /f

if %ERRORLEVEL% == 0 (
    echo [OK] %TASK_NAME_BB% 登録完了: 毎分実行
) else (
    echo [ERROR] %TASK_NAME_BB% 登録失敗
    exit /b 1
)
echo.

REM ----------------------------------------------
REM (2) FX_Trail_Monitor_All - trail_monitor_all.bat 毎分実行
REM ----------------------------------------------
set TASK_NAME_TRAIL=FX_Trail_Monitor_All
set BAT_TRAIL=%BAT_DIR%\trail_monitor_all.bat
set LOG_TRAIL=%LOG_DIR%\scheduler_trail_monitor_all.log

echo [INFO] タスク登録: %TASK_NAME_TRAIL%
schtasks /delete /tn "%TASK_NAME_TRAIL%" /f 2>nul

schtasks /create ^
  /tn "%TASK_NAME_TRAIL%" ^
  /tr "cmd /c \"%BAT_TRAIL%\" >> \"%LOG_TRAIL%\" 2>&1" ^
  /sc MINUTE ^
  /mo 1 ^
  /ru SYSTEM ^
  /rl HIGHEST ^
  /f

if %ERRORLEVEL% == 0 (
    echo [OK] %TASK_NAME_TRAIL% 登録完了: 毎分実行
) else (
    echo [ERROR] %TASK_NAME_TRAIL% 登録失敗
    exit /b 1
)
echo.

REM ----------------------------------------------
REM (3) FX_Daily_Trade_All - daily_trade_all.bat 毎日07:00 JST
REM     JST = UTC+9  →  07:00 JST = 22:00 UTC (前日)
REM     schtasks の /st はローカル時刻(JST)で指定するため 07:00 を使用する
REM ----------------------------------------------
set TASK_NAME_DAILY=FX_Daily_Trade_All
set BAT_DAILY=%BAT_DIR%\daily_trade_all.bat
set LOG_DAILY=%LOG_DIR%\scheduler_daily_trade_all.log

echo [INFO] タスク登録: %TASK_NAME_DAILY%
schtasks /delete /tn "%TASK_NAME_DAILY%" /f 2>nul

schtasks /create ^
  /tn "%TASK_NAME_DAILY%" ^
  /tr "cmd /c \"%BAT_DAILY%\" >> \"%LOG_DAILY%\" 2>&1" ^
  /sc DAILY ^
  /st 07:00 ^
  /ru SYSTEM ^
  /rl HIGHEST ^
  /f

if %ERRORLEVEL% == 0 (
    echo [OK] %TASK_NAME_DAILY% 登録完了: 毎日 07:00 JST (UTC 22:00 前日)
) else (
    echo [ERROR] %TASK_NAME_DAILY% 登録失敗
    exit /b 1
)
echo.

echo ==============================================
echo  全タスク登録完了
echo ==============================================
schtasks /query /tn "%TASK_NAME_BB%"    /fo LIST 2>nul | findstr "タスク名\|Status\|状態\|Next Run\|次回"
schtasks /query /tn "%TASK_NAME_TRAIL%" /fo LIST 2>nul | findstr "タスク名\|Status\|状態\|Next Run\|次回"
schtasks /query /tn "%TASK_NAME_DAILY%" /fo LIST 2>nul | findstr "タスク名\|Status\|状態\|Next Run\|次回"
echo.
pause
