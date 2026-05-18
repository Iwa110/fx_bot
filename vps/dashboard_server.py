"""
dashboard_server.py - FX Dashboard Flask server (port=5000)

Usage: python vps\dashboard_server.py
  GET /?broker=axiory&days=7
"""

import sys
import os
import logging
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    from flask import Flask, request, Response, jsonify
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
VALID_DAYS    = {7, 30, 90}

os.makedirs(LOG_DIR, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(message)s',
    handlers=[
        logging.FileHandler(os.path.join(LOG_DIR, 'dashboard_server.log'), encoding='utf-8'),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger(__name__)

app = Flask(__name__)


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
            to_utc    = now_jst.astimezone(timezone.utc)
            from_utc  = (now_jst - timedelta(days=days)).astimezone(timezone.utc)
            yesterday = (now_jst - timedelta(days=1)).strftime('%Y-%m-%d')

            trades         = fetch_deals_range(from_utc, to_utc)
            open_positions = fetch_open_positions()

            log.info('closed=%d open=%d', len(trades), len(open_positions))

            generated_at = now_jst.strftime('%Y-%m-%d %H:%M:%S') + ' JST'
            html = generate_html(trades, open_positions, broker, days, generated_at, yesterday)

        finally:
            disconnect_mt5()

        return Response(html, status=200, mimetype='text/html; charset=utf-8')

    except Exception as exc:
        log.exception('unhandled error: %s', exc)
        return Response('[ERROR] {}'.format(exc), status=500, mimetype='text/plain')


TF_STR_MAP = {
    'M5':  mt5.TIMEFRAME_M5,
    'M15': mt5.TIMEFRAME_M15,
    'H1':  mt5.TIMEFRAME_H1,
    'H4':  mt5.TIMEFRAME_H4,
    'D1':  mt5.TIMEFRAME_D1,
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
    hold_sec   = max(exit_ts - entry_ts, 3600)
    buffer_sec = max(hold_sec // 2, 7200)   # 最低2hバッファ
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
