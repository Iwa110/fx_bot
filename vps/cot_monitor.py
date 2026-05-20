"""
cot_monitor.py - COT Extreme x Daily Trend strategy v1
COT Index (CFTC TFF Leveraged Funds, 156-week rolling) extreme signals
  >90 -> fade longs (short) / <10 -> fade shorts (long)
  + D1 EMA50 direction filter

magic: 20260020
v1 2026-05-20: initial implementation
  BT: cot_extreme_bt.py, 2023-07-14~2026-02-27
    EURUSD n=16 WR=75% PF=1.940
    GBPUSD n=17 WR=94% PF=9.739
    USDJPY n=17 WR=71% PF=1.958
    Overall PF=1.968, WR=80% (n=50)
  Note: LONG direction PF=5.888, SHORT direction PF=0.983
    -> SHORT performance weakened in 2023-2026 trend regime; monitor closely

COT data: CFTC Socrata API (publicreporting.cftc.gov, dataset gpe5-46if)
Execution: run daily or hourly, COT updated weekly (Fri ~20:30 UTC)
"""

import sys, os, time, argparse, json
from datetime import datetime, timezone, timedelta
from pathlib import Path

import MetaTrader5 as mt5
import pandas as pd
import numpy as np
import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import risk_manager as rm
from broker_utils import connect_mt5, disconnect_mt5, build_symbol_map, is_live_broker

# ══════════════════════════════════════════
# Constants
# ══════════════════════════════════════════
MAGIC         = 20260020
STRATEGY_TAG  = 'COT'
BROKER_KEY    = 'axiory'
LOOP_INTERVAL = 3600   # 1h (weekly signal, no need for tight loop)

# COT extreme thresholds (BT sensitivity: >90/<10 gives PF=6.344 n=21)
COT_HIGH = 90   # COT Index above this -> fade longs
COT_LOW  = 10   # COT Index below this -> fade shorts

# COT Index lookback (weeks)
COT_LOOKBACK = 156  # 3 years rolling

ATR_PERIOD   = 14
SL_ATR_MULT  = 1.5
TP_ATR_MULT  = 3.0   # TP2 from BT
MAX_HOLD_DAYS = 14
MAX_TOTAL_POS = 3

# CFTC contract codes for Leveraged Funds (TFF FutOnly)
# sign: +1 if pair moves same direction as futures net, -1 if inverse
PAIRS_CFG = {
    'EURUSD': {'cftc_code': '099741', 'sign': 1,  'enabled': True},
    'GBPUSD': {'cftc_code': '096742', 'sign': 1,  'enabled': True},
    'USDJPY': {'cftc_code': '097741', 'sign': -1, 'enabled': True},
}

SOCRATA_URL = 'https://publicreporting.cftc.gov/resource/gpe5-46if.json'
COT_CACHE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                               'cot_cache.json')

_SYMBOL_MAP: dict[str, str] = {}

def _rsym(base: str) -> str:
    return _SYMBOL_MAP.get(base, base)

# ══════════════════════════════════════════
# Logging / env
# ══════════════════════════════════════════
DEBUG    = False
LOG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         'cot_monitor_log.txt')
ENV_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), '.env')


def load_env() -> dict:
    env: dict = {}
    try:
        with open(ENV_FILE, encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if '=' in line and not line.startswith('#'):
                    k, v = line.split('=', 1)
                    env[k.strip()] = v.strip()
    except Exception:
        pass
    return env


def log_print(msg: str, debug: bool = False) -> None:
    if debug and not DEBUG:
        return
    ts  = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    out = '[' + ts + '] ' + msg
    print(out, flush=True)
    try:
        with open(LOG_FILE, 'a', encoding='utf-8') as f:
            f.write(out + '\n')
    except Exception:
        pass


def send_discord(msg: str, webhook: str) -> None:
    if not webhook:
        return
    try:
        requests.post(webhook, json={'content': msg}, timeout=5)
    except Exception:
        pass

# ══════════════════════════════════════════
# COT data fetching & index calculation
# ══════════════════════════════════════════
def _fetch_cot_socrata(codes: list[str], start_date: str = '2018-01-01') -> list[dict]:
    codes_str = ','.join(["'" + c + "'" for c in codes])
    params = {
        '$where': (f"cftc_contract_market_code in({codes_str})"
                   f" AND report_date_as_yyyy_mm_dd >= '{start_date}'"
                   f" AND futonly_or_combined = 'FutOnly'"),
        '$select': ('report_date_as_yyyy_mm_dd,cftc_contract_market_code,'
                    'lev_money_positions_long,lev_money_positions_short'),
        '$limit': 5000,
        '$order': 'report_date_as_yyyy_mm_dd ASC',
    }
    try:
        resp = requests.get(SOCRATA_URL, params=params, timeout=30)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        log_print('COT API error: ' + str(e))
        return []


def _build_cot_index(records: list[dict], cftc_code: str,
                     lookback: int = COT_LOOKBACK) -> pd.Series:
    """Return weekly COT Index series (0-100) for given code."""
    df = pd.DataFrame(records)
    df['date'] = pd.to_datetime(df['report_date_as_yyyy_mm_dd']).dt.tz_localize(None)
    df['code'] = df['cftc_contract_market_code'].str.strip()
    sub = df[df['code'] == cftc_code].copy()
    sub['net'] = (pd.to_numeric(sub['lev_money_positions_long'], errors='coerce')
                  - pd.to_numeric(sub['lev_money_positions_short'], errors='coerce'))
    s = sub.set_index('date')['net'].dropna().sort_index()
    s = s[~s.index.duplicated(keep='last')]
    roll_min = s.rolling(lookback, min_periods=max(26, lookback // 4)).min()
    roll_max = s.rolling(lookback, min_periods=max(26, lookback // 4)).max()
    idx = (s - roll_min) / (roll_max - roll_min + 1e-8) * 100
    return idx


def load_cot_signals(force_refresh: bool = False) -> dict[str, float]:
    """
    Return {pair: cot_index_latest} from cache or fresh download.
    Cache refreshed on Fridays after 20:30 UTC (CFTC publishes ~20:30 UTC Fri).
    """
    now_utc = datetime.now(timezone.utc)
    is_friday_night = (now_utc.weekday() == 4 and now_utc.hour >= 20)
    cache_stale = True

    if os.path.exists(COT_CACHE_FILE) and not force_refresh:
        try:
            with open(COT_CACHE_FILE, 'r') as f:
                cache = json.load(f)
            cache_dt = datetime.fromisoformat(cache.get('updated', '2000-01-01'))
            # Cache valid for 7 days
            if (now_utc.replace(tzinfo=None) - cache_dt).days < 7:
                cache_stale = False
                if not is_friday_night:
                    log_print('COT cache hit  updated=' + cache_dt.strftime('%Y-%m-%d'), debug=True)
                    return cache.get('signals', {})
        except Exception:
            pass

    if cache_stale or is_friday_night or force_refresh:
        log_print('COT refresh  downloading from CFTC Socrata...')
        codes = [cfg['cftc_code'] for cfg in PAIRS_CFG.values() if cfg.get('enabled')]
        records = _fetch_cot_socrata(codes)
        if not records:
            log_print('COT download failed; using cached values if available')
            if os.path.exists(COT_CACHE_FILE):
                with open(COT_CACHE_FILE, 'r') as f:
                    return json.load(f).get('signals', {})
            return {}

        signals = {}
        for pair, cfg in PAIRS_CFG.items():
            if not cfg.get('enabled'):
                continue
            idx_series = _build_cot_index(records, cfg['cftc_code'])
            if idx_series.empty:
                continue
            latest_val = float(idx_series.iloc[-1])
            signals[pair] = round(latest_val, 1)
            log_print(f'COT {pair}: index={latest_val:.1f}  '
                      f'(date={idx_series.index[-1].date()})')

        cache = {
            'updated': now_utc.replace(tzinfo=None).isoformat(),
            'signals': signals,
        }
        with open(COT_CACHE_FILE, 'w') as f:
            json.dump(cache, f)
        return signals

    return {}

# ══════════════════════════════════════════
# Price / indicator helpers
# ══════════════════════════════════════════
def get_d1_bars(symbol: str, n: int = 250) -> pd.DataFrame | None:
    bars = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_D1, 0, n)
    if bars is None or len(bars) == 0:
        return None
    df = pd.DataFrame(bars)
    df['time'] = pd.to_datetime(df['time'], unit='s')
    df = df.rename(columns={'open': 'open', 'high': 'high',
                             'low': 'low', 'close': 'close'})
    return df


def calc_atr(df: pd.DataFrame, period: int = ATR_PERIOD) -> float:
    hl  = df['high'] - df['low']
    hc  = (df['high'] - df['close'].shift(1)).abs()
    lc  = (df['low']  - df['close'].shift(1)).abs()
    tr  = pd.concat([hl, hc, lc], axis=1).max(axis=1)
    atr = tr.ewm(span=period).mean()
    return float(atr.iloc[-1])


def calc_ema(df: pd.DataFrame, period: int, col: str = 'close') -> float:
    return float(df[col].ewm(span=period).mean().iloc[-1])

# ══════════════════════════════════════════
# Position helpers
# ══════════════════════════════════════════
def count_strategy_positions() -> int:
    pos = mt5.positions_get()
    if pos is None:
        return 0
    return sum(1 for p in pos if p.magic == MAGIC)


def has_open_position(symbol: str) -> bool:
    pos = mt5.positions_get(symbol=symbol)
    if pos is None:
        return False
    return any(p.magic == MAGIC for p in pos)


def check_max_hold(webhook: str) -> None:
    """Close positions held longer than MAX_HOLD_DAYS."""
    pos = mt5.positions_get()
    if not pos:
        return
    now_ts = datetime.now(timezone.utc).timestamp()
    for p in pos:
        if p.magic != MAGIC:
            continue
        hold_days = (now_ts - p.time) / 86400
        if hold_days >= MAX_HOLD_DAYS:
            _close_position(p, comment='max_hold')
            msg = (STRATEGY_TAG + ' CLOSE max_hold: ' + p.symbol
                   + ' hold=' + str(round(hold_days, 1)) + 'd'
                   + ' pnl=' + str(p.profit))
            log_print(msg)
            send_discord(msg, webhook)


def _close_position(p, comment: str = '') -> bool:
    is_long    = (p.type == mt5.ORDER_TYPE_BUY)
    close_type = mt5.ORDER_TYPE_SELL if is_long else mt5.ORDER_TYPE_BUY
    tick       = mt5.symbol_info_tick(p.symbol)
    if tick is None:
        return False
    price = tick.bid if is_long else tick.ask
    req = {
        'action':       mt5.TRADE_ACTION_DEAL,
        'symbol':       p.symbol,
        'volume':       p.volume,
        'type':         close_type,
        'position':     p.ticket,
        'price':        price,
        'deviation':    10,
        'magic':        MAGIC,
        'comment':      STRATEGY_TAG + '_close_' + comment,
        'type_time':    mt5.ORDER_TIME_GTC,
        'type_filling': mt5.ORDER_FILLING_IOC,
    }
    result = mt5.order_send(req)
    return result is not None and result.retcode == mt5.TRADE_RETCODE_DONE

# ══════════════════════════════════════════
# Order placement
# ══════════════════════════════════════════
def place_order(symbol: str, base_sym: str, direction: str,
                sl_dist: float, tp_dist: float, webhook: str) -> bool:
    info = mt5.symbol_info(symbol)
    if info is None:
        log_print('symbol_info failed: ' + symbol)
        return False
    tick = mt5.symbol_info_tick(symbol)
    if tick is None:
        log_print('symbol_info_tick failed: ' + symbol)
        return False

    if direction == 'long':
        order_type = mt5.ORDER_TYPE_BUY
        entry = tick.ask
        sl    = round(entry - sl_dist, info.digits)
        tp    = round(entry + tp_dist, info.digits)
    else:
        order_type = mt5.ORDER_TYPE_SELL
        entry = tick.bid
        sl    = round(entry + sl_dist, info.digits)
        tp    = round(entry - tp_dist, info.digits)

    balance = rm.get_balance()
    lot     = rm.calc_lot(balance, sl_dist, symbol)

    if is_live_broker(BROKER_KEY):
        log_print('*** LIVE ORDER *** ' + symbol + ' ' + direction.upper()
                  + ' lot=' + str(lot) + ' broker=' + BROKER_KEY)

    req = {
        'action':       mt5.TRADE_ACTION_DEAL,
        'symbol':       symbol,
        'volume':       lot,
        'type':         order_type,
        'price':        entry,
        'sl':           sl,
        'tp':           tp,
        'deviation':    10,
        'magic':        MAGIC,
        'comment':      STRATEGY_TAG + '_' + base_sym,
        'type_time':    mt5.ORDER_TIME_GTC,
        'type_filling': mt5.ORDER_FILLING_IOC,
    }
    result = mt5.order_send(req)
    if result is None or result.retcode != mt5.TRADE_RETCODE_DONE:
        code = result.retcode if result else 'None'
        log_print('order failed: ' + symbol + ' ' + direction.upper()
                  + ' code=' + str(code))
        return False

    msg = (STRATEGY_TAG + ' entry: ' + symbol + ' ' + direction.upper()
           + ' lot=' + str(lot)
           + ' entry=' + str(round(entry, info.digits))
           + ' sl=' + str(sl) + ' tp=' + str(tp))
    log_print(msg)
    send_discord(msg, webhook)
    return True

# ══════════════════════════════════════════
# Signal check
# ══════════════════════════════════════════
def check_entry(base_sym: str, cfg: dict, cot_val: float) -> str | None:
    """
    Returns 'long', 'short', or None.
    Logic:
      COT Index >COT_HIGH -> fade longs -> direction = SHORT * sign -> if sign=+1: short price
      COT Index <COT_LOW  -> fade shorts -> direction = LONG  * sign -> if sign=+1: long price
      sign=-1 (JPY) inverts: COT high JPY net long -> USDJPY tends down -> but we LONG USDJPY
    """
    sign = cfg['sign']

    if cot_val > COT_HIGH:
        raw_dir = 'short'  # fade longs
    elif cot_val < COT_LOW:
        raw_dir = 'long'   # fade shorts
    else:
        log_print(f'{base_sym}: COT={cot_val:.1f}  no extreme  skip', debug=True)
        return None

    # Sign flip for inverse-quoted futures (JPY)
    if sign == -1:
        direction = 'long' if raw_dir == 'short' else 'short'
    else:
        direction = raw_dir

    symbol = _rsym(base_sym)
    df = get_d1_bars(symbol, n=60)
    if df is None or len(df) < 55:
        log_print(f'{base_sym}: D1 bars fetch failed', debug=True)
        return None

    atr   = calc_atr(df)
    ema50 = calc_ema(df, 50)
    close = float(df['close'].iloc[-1])

    # HTF filter: EMA50 direction must align with trade direction
    if direction == 'long' and close <= ema50:
        log_print(f'{base_sym}: COT={cot_val:.1f} -> {direction}  blocked: close({close:.5f}) <= EMA50({ema50:.5f})')
        return None
    if direction == 'short' and close >= ema50:
        log_print(f'{base_sym}: COT={cot_val:.1f} -> {direction}  blocked: close({close:.5f}) >= EMA50({ema50:.5f})')
        return None

    log_print(f'{base_sym}: COT={cot_val:.1f} -> {direction.upper()}  '
              f'close={close:.5f} EMA50={ema50:.5f} ATR={atr:.5f}')
    return direction

# ══════════════════════════════════════════
# Main loop
# ══════════════════════════════════════════
def main_loop(webhook: str) -> None:
    log_print('cot_monitor started  broker=' + BROKER_KEY
              + '  interval=' + str(LOOP_INTERVAL) + 's')

    while True:
        try:
            # 1. Max hold check
            check_max_hold(webhook)

            # 2. Load COT signals
            cot_signals = load_cot_signals()
            if not cot_signals:
                log_print('COT signals unavailable  skip cycle')
                time.sleep(LOOP_INTERVAL)
                continue

            total_pos = count_strategy_positions()
            log_print('cycle  COT=' + str(cot_signals)
                      + '  pos=' + str(total_pos) + '/' + str(MAX_TOTAL_POS), debug=True)

            if total_pos >= MAX_TOTAL_POS:
                log_print('MAX_TOTAL_POS reached', debug=True)
                time.sleep(LOOP_INTERVAL)
                continue

            # 3. Check entry for each pair
            for base_sym, cfg in PAIRS_CFG.items():
                if not cfg.get('enabled', True):
                    continue
                if total_pos >= MAX_TOTAL_POS:
                    break

                symbol = _rsym(base_sym)
                if has_open_position(symbol):
                    log_print(base_sym + ': position open  skip', debug=True)
                    continue

                cot_val = cot_signals.get(base_sym)
                if cot_val is None:
                    continue

                direction = check_entry(base_sym, cfg, cot_val)
                if direction is None:
                    continue

                df = get_d1_bars(symbol, n=20)
                if df is None:
                    continue
                atr     = calc_atr(df)
                sl_dist = atr * SL_ATR_MULT
                tp_dist = atr * TP_ATR_MULT

                if sl_dist <= 0 or np.isnan(sl_dist):
                    continue

                if place_order(symbol, base_sym, direction, sl_dist, tp_dist, webhook):
                    total_pos += 1

        except Exception as e:
            log_print('loop error: ' + str(e))
            import traceback
            log_print(traceback.format_exc(), debug=True)

        time.sleep(LOOP_INTERVAL)

# ══════════════════════════════════════════
# Entry point
# ══════════════════════════════════════════
def main() -> None:
    global BROKER_KEY, LOG_FILE, DEBUG

    parser = argparse.ArgumentParser(description='COT Monitor v1')
    parser.add_argument('--broker', default=BROKER_KEY,
                        choices=['oanda', 'oanda_demo', 'axiory', 'exness'],
                        help='broker key')
    parser.add_argument('--debug', action='store_true')
    parser.add_argument('--refresh-cot', action='store_true',
                        help='force COT data refresh on startup')
    args = parser.parse_args()

    BROKER_KEY = args.broker
    if args.debug:
        DEBUG = True

    LOG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                             'cot_monitor_log_' + BROKER_KEY + '.txt')

    env     = load_env()
    webhook = env.get('DISCORD_WEBHOOK', '')

    if not connect_mt5(BROKER_KEY):
        log_print('MT5 init failed  broker=' + BROKER_KEY)
        return

    try:
        account = mt5.account_info()
        if account is None:
            log_print('account_info failed')
            disconnect_mt5()
            return
        log_print('connected  broker=' + BROKER_KEY
                  + '  login=' + str(account.login))
    except Exception as e:
        log_print('MT5 error: ' + str(e))
        disconnect_mt5()
        return

    _SYMBOL_MAP.update(build_symbol_map(list(PAIRS_CFG.keys()), BROKER_KEY))

    if args.refresh_cot:
        load_cot_signals(force_refresh=True)

    try:
        main_loop(webhook)
    finally:
        disconnect_mt5()


if __name__ == '__main__':
    main()
