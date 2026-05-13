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
    from flask import Flask, request, Response
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


if __name__ == '__main__':
    log.info('Starting FX Dashboard Server on port 5000')
    app.run(host='0.0.0.0', port=5000, debug=False)
