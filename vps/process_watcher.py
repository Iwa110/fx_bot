# process_watcher.py - Generic watchdog for the strategy daemons that have no
# built-in restart-on-crash of their own (grid_monitor / mr_monitor / news_monitor /
# sma_squeeze). Pattern copied from trail_watcher.py: runs ONCE at logon via Task
# Scheduler (ONLOGON), starts each configured instance as an independent subprocess,
# and relaunches any that exit (crash OR VPS reboot before this watcher itself is
# killed - the watcher process is what Task Scheduler supervises, so unlike wrapping
# a quick-exiting .bat around Start-Process/start-/B, there is no risk of Task
# Scheduler's Job Object killing the detached children once the launcher exits).
#
# HOW TO REGISTER (see register_strategy_watchers.bat):
#   Task: FX_Grid_Monitor_All / FX_MR_Monitor_All / FX_News_Monitor_All / FX_SMA_Squeeze_All
#   Trigger: ONLOGON
#   Program: pythonw.exe process_watcher.py <grid|mr|news|sma>
#
# NOTE: oanda_live instances still need the OANDA MT5 terminal logged in via its own
# GUI (OTP) first - see broker_config.py 'oanda_live' comment. This watcher will keep
# retrying them on the normal CHECK_INTERVAL, but a retry can't succeed until a human
# completes that GUI login after a reboot.

import os, subprocess, sys, time
from datetime import datetime

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

PYTHONW = os.path.join(os.path.dirname(sys.executable), 'pythonw.exe')
if not os.path.exists(PYTHONW):
    PYTHONW = r'C:\Users\Administrator\AppData\Local\Programs\Python\Python312\pythonw.exe'

CHECK_INTERVAL = 30  # seconds between liveness checks (same as trail_watcher.py)

STRATEGIES = {
    'grid': {
        'script': os.path.join(BASE_DIR, 'grid_monitor.py'),
        'instances': (
            [['--pair', p, '--broker', b]
             for p in ('AUDCAD', 'NZDJPY', 'EURGBP', 'AUDNZD', 'USDJPY', 'CADCHF')
             for b in ('axiory', 'exness')] +
            [['--pair', p, '--broker', 'oanda_live']
             for p in ('AUDCAD', 'CADCHF', 'AUDNZD', 'EURGBP')]
        ),
    },
    'mr': {
        'script': os.path.join(BASE_DIR, 'mr_monitor.py'),
        'instances': [['--pair', p, '--broker', b]
                       for p in ('AUDCAD', 'CADCHF')
                       for b in ('axiory', 'exness')],
    },
    'news': {
        'script': os.path.join(BASE_DIR, 'news_monitor.py'),
        'instances': [['--broker', b] for b in ('axiory', 'exness')],
    },
    'sma': {
        'script': os.path.join(BASE_DIR, 'sma_squeeze.py'),
        'instances': [['--broker', b] for b in ('axiory', 'exness')],
    },
}


def log(name, msg):
    ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    line = '[' + ts + '] ' + msg
    print(line)
    try:
        with open(os.path.join(BASE_DIR, name + '_watcher.log'), 'a', encoding='utf-8') as f:
            f.write(line + '\n')
    except Exception:
        pass


def start(script, args):
    return subprocess.Popen(
        [PYTHONW, script] + args,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def main():
    if len(sys.argv) < 2 or sys.argv[1] not in STRATEGIES:
        print('Usage: process_watcher.py <' + '|'.join(STRATEGIES) + '>')
        sys.exit(1)

    name = sys.argv[1]
    cfg = STRATEGIES[name]
    script, instances = cfg['script'], cfg['instances']

    procs = [None] * len(instances)
    log(name, 'watcher started. instances=' + str(len(instances)))

    while True:
        for i, args in enumerate(instances):
            p = procs[i]
            if p is None or p.poll() is not None:
                if p is not None:
                    log(name, 'restart ' + ' '.join(args) + '  (exited rc=' + str(p.poll()) + ')')
                procs[i] = start(script, args)
                time.sleep(0.5)   # avoid MT5 connect storm
        time.sleep(CHECK_INTERVAL)


if __name__ == '__main__':
    main()
