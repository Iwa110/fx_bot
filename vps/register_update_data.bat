@echo off
chcp 65001 > nul

schtasks /create ^
  /tn "FX_UpdateData_Daily" ^
  /tr "\"C:\Users\Administrator\AppData\Local\Programs\Python\Python312\python.exe\" \"C:\Users\Administrator\fx_bot\vps\update_data.py\"" ^
  /sc DAILY ^
  /st 00:00 ^
  /ru SYSTEM ^
  /rl HIGHEST ^
  /f

if %ERRORLEVEL% == 0 (
    echo [OK] Task "FX_UpdateData_Daily" registered successfully.
) else (
    echo [ERROR] Failed to register task. Run as Administrator.
)
