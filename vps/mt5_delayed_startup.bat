@echo off
REM mt5_delayed_startup.bat
REM WHY: OANDA MT5 IPC dispatcher fails when Axiory/Exness start simultaneously.
REM      IPC port and history file locks (ERROR_SHARING_VIOLATION) are won by
REM      whichever terminal starts first. OANDA must claim IPC before others.
REM      This bat starts AFTER a delay so OANDA gets a head start.
REM
REM Called by: FX_MT5_Delayed_Startup (ONLOGON, 60s delay via ping trick)

set AXIORY_EXE=C:\Program Files\Axiory MetaTrader 5\terminal64.exe
set EXNESS_EXE=C:\Program Files\MetaTrader 5 EXNESS\terminal64.exe

REM Wait 60 seconds to let OANDA MT5 fully initialize IPC first
ping -n 61 127.0.0.1 > nul

start "" "%AXIORY_EXE%"
ping -n 6 127.0.0.1 > nul
start "" "%EXNESS_EXE%"
