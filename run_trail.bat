@echo off
start "" "C:\Program Files\OANDA MetaTrader 5\terminal64.exe"
timeout /t 15 /nobreak > nul
C:\Users\Administrator\AppData\Local\Programs\Python\Python312\python.exe C:\Users\Administrator\fx_bot\vps\trail_monitor.py >> C:\Users\Administrator\fx_bot\trail.log 2>&1
