@echo off
REM register_update.bat
REM FX data update task registration
REM Run as Administrator

SET TASK_NAME=FX_Data_Update
SET PYTHON=C:\Users\Administrator\AppData\Local\Programs\Python\Python312\python.exe
SET SCRIPT=C:\Users\Administrator\fx_bot\update_data.py
SET LOG=C:\Users\Administrator\fx_bot\data\scheduler_log.txt

schtasks /Delete /TN "%TASK_NAME%" /F >nul 2>&1

schtasks /Create ^
    /TN "%TASK_NAME%" ^
    /TR ""%PYTHON%" "%SCRIPT%" >> "%LOG%" 2>&1" ^
    /SC DAILY ^
    /ST 00:00 ^
    /RU SYSTEM ^
    /RL HIGHEST ^
    /F

IF %ERRORLEVEL% EQU 0 (
    echo [OK] Task "%TASK_NAME%" registered.
    echo      Schedule: Daily 00:00
    echo      Script  : %SCRIPT%
    echo      Log     : %LOG%
    echo.
    echo Check: schtasks /Query /TN "%TASK_NAME%" /FO LIST /V
) ELSE (
    echo [ERROR] Registration failed. Run as Administrator.
    exit /b 1
)

pause