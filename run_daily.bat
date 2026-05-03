@echo off
start "" "C:\Program Files\OANDA MetaTrader 5\terminal64.exe"
timeout /t 5 /nobreak > nul
C:\Users\Administrator\AppData\Local\Programs\Python\Python312\python.exe C:\Users\Administrator\fx_bot\vps\daily_trade.py >> C:\Users\Administrator\fx_bot\daily.log 2>&1
