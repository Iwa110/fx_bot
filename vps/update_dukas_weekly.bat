@echo off
REM update_dukas_weekly.bat
REM Weekly refresh of Dukascopy 1h data for the Grid AI-loop (loop/run_weekly.py SKIPs when
REM any pair's last bar is older than loop/state.json max_data_age_days=120).
REM Flow: git pull -> fetch 4 pairs (.venv_dukas) -> freshness check (last bar <= 7d) -> commit/push.
REM Freshness NG -> no commit/push (exit 1). Called by Task Scheduler: FX_Update_Dukas_Weekly (Sat).
REM .venv_dukas is created on first run if missing (dukascopy-python + pandas).

set REPO=C:\Users\Administrator\fx_bot
set BASE_PY=C:\Users\Administrator\AppData\Local\Programs\Python\Python312\python.exe
set VENV_PY=%REPO%\.venv_dukas\Scripts\python.exe
set PAIRS=AUDCAD CADCHF AUDNZD EURGBP
set LOG=%REPO%\logs\update_dukas_weekly.log

if not exist "%REPO%\logs" mkdir "%REPO%\logs"
cd /d "%REPO%"
echo === %date% %time% === >> "%LOG%"

git pull origin main >> "%LOG%" 2>&1
if errorlevel 1 (
    echo [ERROR] git pull failed >> "%LOG%"
    exit /b 1
)

if not exist "%VENV_PY%" (
    echo [INFO] creating .venv_dukas >> "%LOG%"
    "%BASE_PY%" -m venv "%REPO%\.venv_dukas" >> "%LOG%" 2>&1
    "%VENV_PY%" -m pip install --upgrade pip dukascopy-python pandas >> "%LOG%" 2>&1
)

"%VENV_PY%" optimizer\fetch_dukascopy_ohlc.py --tf 1h --years 11 --pairs %PAIRS% >> "%LOG%" 2>&1
if errorlevel 1 (
    echo [ERROR] fetch_dukascopy_ohlc.py failed - no push >> "%LOG%"
    exit /b 1
)

"%VENV_PY%" vps\check_dukas_fresh.py --days 7 --pairs %PAIRS% >> "%LOG%" 2>&1
if errorlevel 1 (
    echo [ERROR] freshness check NG - no push >> "%LOG%"
    git checkout -- data/AUDCAD_1h_dukas.csv data/CADCHF_1h_dukas.csv data/AUDNZD_1h_dukas.csv data/EURGBP_1h_dukas.csv >> "%LOG%" 2>&1
    exit /b 1
)

git add data/AUDCAD_1h_dukas.csv data/CADCHF_1h_dukas.csv data/AUDNZD_1h_dukas.csv data/EURGBP_1h_dukas.csv >> "%LOG%" 2>&1
git diff --cached --quiet
if not errorlevel 1 (
    echo [OK] no data change >> "%LOG%"
    exit /b 0
)
git commit -m "data: weekly dukascopy 1h refresh (%PAIRS%)" >> "%LOG%" 2>&1
git push origin main >> "%LOG%" 2>&1
if errorlevel 1 (
    echo [ERROR] git push failed >> "%LOG%"
    exit /b 1
)
echo [OK] pushed >> "%LOG%"
exit /b 0
