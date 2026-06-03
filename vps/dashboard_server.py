"""
dashboard_server.py - FX Dashboard Flask server (port=5000)

Usage: python vps/dashboard_server.py
  GET /?broker=axiory&days=7
"""

import sys
import os
import logging
import threading
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    from flask import Flask, request, Response, jsonify, send_file
except ImportError:
    print('[ERROR] Flask not found: pip install flask')
    sys.exit(1)

try:
    import MetaTrader5 as mt5
except ImportError:
    print('[ERROR] MetaTrader5 not found: pip install MetaTrader5')
    sys.exit(1)

from broker_utils import connect_mt5, disconnect_mt5
from dashboard import fetch_deals_range, generate_html, STRATEGY_COLORS
from daily_report import fetch_open_positions

BASE_DIR = r'C:\Users\Administrator\fx_bot'
LOG_DIR  = os.path.join(BASE_DIR, 'logs')
JST      = timezone(timedelta(hours=9))

VALID_BROKERS = {'axiory', 'oanda', 'exness'}
VALID_DAYS    = {7, 30, 0}

os.makedirs(LOG_DIR, exist_ok=True)
_log_handlers = [logging.StreamHandler(sys.stdout)]
try:
    _log_handlers.insert(0, logging.FileHandler(
        os.path.join(LOG_DIR, 'dashboard_server.log'), encoding='utf-8'))
except PermissionError:
    try:
        _log_handlers.insert(0, logging.FileHandler(
            os.path.join(LOG_DIR, 'dashboard_server2.log'), encoding='utf-8'))
    except PermissionError:
        pass  # stdout only

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(message)s',
    handlers=_log_handlers,
)
log = logging.getLogger(__name__)

app = Flask(__name__)

# ─────────────────────────────────────────────────────────────────
# 「決済確認中」ポジション追跡
#   positions_get() から消えた（=決済済）が、history_deals_get() に
#   まだ反映されていないポジションをインメモリで保持する。
#   broker × ticket をキーにし、3時間で自動削除。
# ─────────────────────────────────────────────────────────────────
_state_lock    = threading.Lock()
_pending_closed: dict = {}  # (broker, ticket) -> {pos_info + pending_since: float}
_last_open_pos: dict  = {}  # (broker, ticket) -> pos_info


def _update_pending(broker: str, open_positions: list, closed_deals: list) -> list:
    """
    オープンポジションの前後を比較し、「消えたが履歴にない」ポジションを
    pending_closed に登録/解除する。
    戻り値: 現在の pending_closed エントリ（このbroker分のみ）
    """
    global _pending_closed, _last_open_pos
    now_ts     = datetime.now(tz=JST).timestamp()
    closed_tix = {t['ticket'] for t in closed_deals}
    cur_tix    = {p['ticket'] for p in open_positions}

    with _state_lock:
        # 前回オープンだったポジションのうち、今回オープンにも履歴にもないもの → pending
        for (brk, tix), info in list(_last_open_pos.items()):
            if brk != broker:
                continue
            key = (broker, tix)
            if tix not in cur_tix and tix not in closed_tix and key not in _pending_closed:
                _pending_closed[key] = {**info, 'pending_since': now_ts}
                log.info('pending_closed add: broker=%s ticket=%d symbol=%s',
                         broker, tix, info.get('symbol', '?'))

        # 履歴に現れたら pending から削除
        for key in list(_pending_closed.keys()):
            brk, tix = key
            if brk == broker and tix in closed_tix:
                log.info('pending_closed resolved: broker=%s ticket=%d', broker, tix)
                del _pending_closed[key]

        # 3時間を超えた古いエントリを削除
        cutoff = now_ts - 3 * 3600
        _pending_closed = {k: v for k, v in _pending_closed.items()
                           if v['pending_since'] > cutoff}

        # 今回のオープンポジションで last_open_pos を更新（このbrokerのみ差替え）
        for key in [k for k in list(_last_open_pos.keys()) if k[0] == broker]:
            del _last_open_pos[key]
        for p in open_positions:
            _last_open_pos[(broker, p['ticket'])] = p

        return [v for (brk, _), v in _pending_closed.items() if brk == broker]


@app.route('/strategy')
def strategy():
    resp = send_file(os.path.join(BASE_DIR, 'strategy_spec.html'))
    resp.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
    resp.headers['Pragma'] = 'no-cache'
    return resp


@app.route('/')
def index():
    broker = request.args.get('broker', 'axiory')
    try:
        days = int(request.args.get('days', '7'))
    except ValueError:
        days = 7

    if broker not in VALID_BROKERS:
        log.warning('invalid broker=%s, fallback to axiory', broker)
        broker = 'axiory'
    if days not in VALID_DAYS:
        log.warning('invalid days=%s, fallback to 7', days)
        days = 7

    log.info('request: broker=%s days=%d', broker, days)

    try:
        if not connect_mt5(broker):
            log.error('MT5 connection failed: broker=%s', broker)
            return Response('[ERROR] MT5 connection failed', status=500, mimetype='text/plain')

        try:
            now_jst   = datetime.now(tz=JST)
            # +60秒バッファ: 直近決済ポジションがMT5ヒストリーに反映されるまでのラグを吸収
            to_utc    = (now_jst + timedelta(seconds=60)).astimezone(timezone.utc)
            from_utc  = (now_jst - timedelta(days=365)).astimezone(timezone.utc)
            yesterday = (now_jst - timedelta(days=1)).strftime('%Y-%m-%d')

            trades         = fetch_deals_range(from_utc, to_utc)
            open_positions = fetch_open_positions()

        finally:
            disconnect_mt5()

        # 決済済みだが履歴にまだ出ていないポジションを追跡
        pending_closed = _update_pending(broker, open_positions, trades)

        log.info('closed=%d open=%d pending_closed=%d',
                 len(trades), len(open_positions), len(pending_closed))

        generated_at = now_jst.strftime('%Y-%m-%d %H:%M:%S') + ' JST'
        html = generate_html(trades, open_positions, broker, days, generated_at, yesterday,
                             pending_closed=pending_closed)

        resp = Response(html, status=200, mimetype='text/html; charset=utf-8')
        resp.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
        resp.headers['Pragma'] = 'no-cache'
        resp.headers['Expires'] = '0'
        return resp

    except Exception as exc:
        log.exception('unhandled error: %s', exc)
        return Response('[ERROR] {}'.format(exc), status=500, mimetype='text/plain')


TF_STR_MAP = {
    'M5':  mt5.TIMEFRAME_M5,
    'M15': mt5.TIMEFRAME_M15,
    'M30': mt5.TIMEFRAME_M30,
    'H1':  mt5.TIMEFRAME_H1,
    'H2':  mt5.TIMEFRAME_H2,
    'H4':  mt5.TIMEFRAME_H4,
    'D1':  mt5.TIMEFRAME_D1,
    'W1':  mt5.TIMEFRAME_W1,
}

TF_SECONDS = {
    'M5': 300, 'M15': 900, 'M30': 1800,
    'H1': 3600, 'H2': 7200, 'H4': 14400,
    'D1': 86400, 'W1': 604800,
}


@app.route('/api/chart')
def api_chart():
    symbol   = request.args.get('symbol', '').upper()
    tf_str   = request.args.get('tf', 'H1').upper()
    broker   = request.args.get('broker', 'axiory')
    try:
        entry_ts = int(request.args.get('entry_ts', 0))
        exit_ts  = int(request.args.get('exit_ts', 0))
    except ValueError:
        return jsonify({'error': 'invalid timestamps'}), 400

    if not symbol or entry_ts == 0 or exit_ts == 0:
        return jsonify({'error': 'missing parameters'}), 400
    if broker not in VALID_BROKERS:
        broker = 'axiory'

    tf = TF_STR_MAP.get(tf_str, mt5.TIMEFRAME_H1)
    buffer_sec = 5 * TF_SECONDS.get(tf_str, 3600)
    from_dt = datetime.fromtimestamp(entry_ts - buffer_sec, tz=timezone.utc)
    to_dt   = datetime.fromtimestamp(exit_ts  + buffer_sec, tz=timezone.utc)

    log.info('api/chart: symbol=%s tf=%s broker=%s entry=%d exit=%d', symbol, tf_str, broker, entry_ts, exit_ts)

    try:
        if not connect_mt5(broker):
            return jsonify({'error': 'MT5 connection failed'}), 500

        try:
            rates = mt5.copy_rates_range(symbol, tf, from_dt, to_dt)
        finally:
            disconnect_mt5()

        if rates is None or len(rates) == 0:
            return jsonify({'error': 'No rate data (symbol={} tf={})'.format(symbol, tf_str)}), 404

        candles = [
            {'time': int(r['time']), 'open': float(r['open']),
             'high': float(r['high']), 'low': float(r['low']), 'close': float(r['close'])}
            for r in rates
        ]
        log.info('api/chart: %d candles returned', len(candles))
        return jsonify({'candles': candles, 'entry_ts': entry_ts, 'exit_ts': exit_ts, 'tf_label': tf_str})

    except Exception as exc:
        log.exception('api/chart error: %s', exc)
        return jsonify({'error': str(exc)}), 500


if __name__ == '__main__':
    log.info('Starting FX Dashboard Server on port 5000')
    app.run(host='0.0.0.0', port=5000, debug=False)
