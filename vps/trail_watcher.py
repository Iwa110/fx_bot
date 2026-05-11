# trail_watcher.py
# Watchdog: launches and monitors trail_monitor.py for each broker.
#
# WHY THIS EXISTS:
#   trail_monitor.py is a long-running daemon (while True loop).
#   Task Scheduler "every minute" + Job Object management caused the monitor
#   to be killed each minute (Job Object termination on bat exit).
#   This watcher runs ONCE at logon via Task Scheduler, starts the monitors
#   as independent subprocesses, and restarts any that crash.
#
# HOW TO REGISTER (see register_brokers.bat):
#   Task: FX_Trail_Monitor_All
#   Trigger: ONLOGON
#   Program: pythonw.exe trail_watcher.py
#   (pythonw.exe has no console window - no popup ever appears)

import os, subprocess, sys, time
from datetime import datetime

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_PATH = os.path.join(BASE_DIR, 'trail_watcher.log')

PYTHONW = os.path.join(os.path.dirname(sys.executable), 'pythonw.exe')
if not os.path.exists(PYTHONW):
    PYTHONW = r'C:\Users\Administrator\AppData\Local\Programs\Python\Python312\pythonw.exe'

SCRIPT  = os.path.join(BASE_DIR, 'trail_monitor.py')
BROKERS = ['axiory', 'exness', 'oanda']

CHECK_INTERVAL = 30  # seconds between liveness checks

_procs = {}


def log(msg):
    ts   = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    line = '[' + ts + '] ' + msg
    print(line)
    try:
        with open(LOG_PATH, 'a', encoding='utf-8') as f:
            f.write(line + '\n')
    except Exception:
        pass


def start_broker(broker):
    proc = subprocess.Popen(
        [PYTHONW, SCRIPT, '--broker', broker],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    log('Started trail_monitor --broker ' + broker + ' (PID=' + str(proc.pid) + ')')
    return proc


log('trail_watcher started. brokers=' + ', '.join(BROKERS))

while True:
    for broker in BROKERS:
        proc = _procs.get(broker)
        if proc is None or proc.poll() is not None:
            if proc is not None:
                log('trail_monitor --broker ' + broker +
                    ' exited (code=' + str(proc.returncode) + '), restarting...')
            _procs[broker] = start_broker(broker)
    time.sleep(CHECK_INTERVAL)
