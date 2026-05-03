"""
200MA Pullback Pin Bar Strategy - Live Trading
Symbol : USDJPY
Pattern: Pin bar reversal (BT pattern=2)
RR     : 1.5
Magic  : 20260003
Entry  : Limit order (High+1pip for long, Low-1pip for short)
Lot    : 0.02 fixed (demo, ~2% risk target)
TF     : 1H

Pin bar definition (from BT):
  lower_wick >= 60% of candle range AND body <= 30% -> Bullish
  upper_wick >= 60% of candle range AND body <= 30% -> Bearish

MA: average of SMA200 and EMA200
Slope filter: ma_slope >= 0.0001 (long), <= -0.0001 (short)
SL: swing low/high over last 20 bars + spread
TP: entry +/- SL_dist * RR
"""

import MetaTrader5 as mt5
import pandas as pd
import numpy as np
from datetime import datetime, timezone
import time
import logging

# ── Config ─────────────────────────────────────────────────
SYMBOL       = 'USDJPY'
MAGIC        = 20260003
LOT          = 0.02
RR           = 1.5
MA_PERIOD    = 200
TOUCH_BAND   = 0.0003      # relative band around MA
SPREAD_PIPS  = 0.3
PIP          = 0.01
SLOPE_THRESH = 0.0001
SL_MIN_PIPS  = 10
LOOKBACK     = 20          # swing high/low lookback bars
COOLDOWN_MIN = 60          # minutes between entries (same direction)
COMMENT      = 'ma200_pinbar'
LOG_FILE     = r'C:\Users\Administrator\fx_bot\vps\ma200_pinbar.log'

# ── Logging ────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(message)s',
    handlers=[
        logging.FileHandler(LOG_FILE, encoding='utf-8'),
        logging.StreamHandler(),
    ]
)
log = logging.getLogger(__name__)

# ── Cooldown tracker ───────────────────────────────────────
last_entry_time = {'long': None, 'short': None}


# ── Indicators ─────────────────────────────────────────────
def calc_sma(s: pd.Series, n: int) -> pd.Series:
    return s.rolling(n).mean()


def calc_ema(s: pd.Series, n: int) -> pd.Series:
    return s.ewm(span=n, adjust=False).mean()


def get_ma(close: pd.Series) -> pd.Series:
    return (calc_sma(close, MA_PERIOD) + calc_ema(close, MA_PERIOD)) / 2


# ── Pin bar check (identical to BT) ───────────────────────
def is_pin_bar(row: pd.Series, direction: str) -> bool:
    body         = abs(row['close'] - row['open'])
    candle_range = row['high'] - row['low']
    if candle_range == 0:
        return False
    upper_wick = row['high'] - max(row['close'], row['open'])
    lower_wick = min(row['close'], row['open']) - row['low']
    if direction == 'long':
        return lower_wick >= 0.6 * candle_range and body <= 0.3 * candle_range
    else:
        return upper_wick >= 0.6 * candle_range and body <= 0.3 * candle_range


# ── Swing high/low (identical to BT) ──────────────────────
def swing_low(df: pd.DataFrame, idx: int) -> float:
    start = max(0, idx - LOOKBACK)
    return df['low'].iloc[start:idx].min()


def swing_high(df: pd.DataFrame, idx: int) -> float:
    start = max(0, idx - LOOKBACK)
    return df['high'].iloc[start:idx].max()


# ── MT5 helpers ────────────────────────────────────────────
def get_bars(n: int = MA_PERIOD + 10) -> pd.DataFrame | None:
    rates = mt5.copy_rates_from_pos(SYMBOL, mt5.TIMEFRAME_H1, 0, n)
    if rates is None or len(rates) < MA_PERIOD + 2:
        log.error('copy_rates_from_pos failed')
        return None
    df = pd.DataFrame(rates)
    df['time'] = pd.to_datetime(df['time'], unit='s', utc=True)
    return df


def count_positions() -> int:
    pos = mt5.positions_get(symbol=SYMBOL, magic=MAGIC)
    return len(pos) if pos else 0


def cooldown_ok(direction: str) -> bool:
    t = last_entry_time[direction]
    if t is None:
        return True
    elapsed = (datetime.now(timezone.utc) - t).total_seconds() / 60
    return elapsed >= COOLDOWN_MIN


def cancel_pending() -> None:
    """Cancel existing pending limit orders for this magic."""
    orders = mt5.orders_get(symbol=SYMBOL)
    if not orders:
        return
    for o in orders:
        if o.magic == MAGIC:
            req = {
                'action':   mt5.TRADE_ACTION_REMOVE,
                'order':    o.ticket,
            }
            res = mt5.order_send(req)
            if res.retcode == mt5.TRADE_RETCODE_DONE:
                log.info(f'Cancelled pending order ticket={o.ticket}')
            else:
                log.warning(f'Cancel failed ticket={o.ticket} retcode={res.retcode}')


def place_limit(direction: str, price: float, sl: float, tp: float) -> bool:
    tick = mt5.symbol_info_tick(SYMBOL)
    if tick is None:
        log.error('symbol_info_tick failed')
        return False

    order_type = mt5.ORDER_TYPE_BUY_LIMIT if direction == 'long' else mt5.ORDER_TYPE_SELL_LIMIT

    req = {
        'action':      mt5.TRADE_ACTION_PENDING,
        'symbol':      SYMBOL,
        'volume':      LOT,
        'type':        order_type,
        'price':       round(price, 3),
        'sl':          round(sl, 3),
        'tp':          round(tp, 3),
        'magic':       MAGIC,
        'comment':     COMMENT,
        'type_time':   mt5.ORDER_TIME_GTC,
        'type_filling': mt5.FILLING_IOC,
    }
    res = mt5.order_send(req)
    if res.retcode == mt5.TRADE_RETCODE_DONE:
        log.info(f'Limit placed dir={direction} price={price:.3f} sl={sl:.3f} tp={tp:.3f}')
        last_entry_time[direction] = datetime.now(timezone.utc)
        return True
    else:
        log.warning(f'order_send failed retcode={res.retcode} comment={res.comment}')
        return False


# ── Signal logic ───────────────────────────────────────────
def check_signal(df: pd.DataFrame) -> None:
    df = df.copy()
    df['ma']       = get_ma(df['close'])
    df['ma_slope'] = df['ma'].diff()
    df = df.dropna().reset_index(drop=True)

    # Use second-to-last confirmed bar as signal candle
    sig_idx = len(df) - 2
    if sig_idx < LOOKBACK:
        return
    sig  = df.iloc[sig_idx]
    curr = df.iloc[-1]   # current (open) bar for entry price reference

    ma          = sig['ma']
    slope       = sig['ma_slope']
    spread      = SPREAD_PIPS * PIP
    touch_band  = ma * TOUCH_BAND
    sl_min_dist = SL_MIN_PIPS * PIP

    price_above = sig['close'] > ma
    price_below = sig['close'] < ma
    long_touch  = price_above and abs(sig['low']  - ma) <= touch_band
    short_touch = price_below and abs(sig['high'] - ma) <= touch_band

    # ── Long signal ────────────────────────────────────────
    if long_touch and slope >= SLOPE_THRESH:
        if not is_pin_bar(sig, 'long'):
            return
        sl_price = swing_low(df, sig_idx) - spread
        if sl_price <= 0:
            return
        sl_dist = sig['close'] - sl_price   # use close as ref for dist
        if sl_dist < sl_min_dist:
            log.debug(f'Long: SL dist {sl_dist/PIP:.1f}pips < min {SL_MIN_PIPS}pips')
            return
        # Limit entry: sig high + 1pip (breakout confirmation)
        entry = sig['high'] + PIP + spread
        tp    = entry + sl_dist * RR
        sl    = sl_price

        if count_positions() > 0:
            log.info('Long signal skipped: position exists')
            return
        if not cooldown_ok('long'):
            log.info('Long signal skipped: cooldown')
            return
        cancel_pending()
        log.info(f'LONG signal | MA={ma:.3f} slope={slope:.6f} | '
                 f'entry={entry:.3f} sl={sl:.3f} tp={tp:.3f} '
                 f'SLdist={sl_dist/PIP:.1f}pips')
        place_limit('long', entry, sl, tp)

    # ── Short signal ───────────────────────────────────────
    elif short_touch and slope <= -SLOPE_THRESH:
        if not is_pin_bar(sig, 'short'):
            return
        sl_price = swing_high(df, sig_idx) + spread
        sl_dist  = sl_price - sig['close']
        if sl_dist < sl_min_dist:
            log.debug(f'Short: SL dist {sl_dist/PIP:.1f}pips < min {SL_MIN_PIPS}pips')
            return
        # Limit entry: sig low - 1pip
        entry = sig['low'] - PIP - spread
        tp    = entry - sl_dist * RR
        sl    = sl_price

        if count_positions() > 0:
            log.info('Short signal skipped: position exists')
            return
        if not cooldown_ok('short'):
            log.info('Short signal skipped: cooldown')
            return
        cancel_pending()
        log.info(f'SHORT signal | MA={ma:.3f} slope={slope:.6f} | '
                 f'entry={entry:.3f} sl={sl:.3f} tp={tp:.3f} '
                 f'SLdist={sl_dist/PIP:.1f}pips')
        place_limit('short', entry, sl, tp)


# ── Main loop ──────────────────────────────────────────────
def main() -> None:
    if not mt5.initialize():
        log.error(f'MT5 initialize failed: {mt5.last_error()}')
        return
    log.info(f'ma200_pinbar started | symbol={SYMBOL} lot={LOT} RR={RR} magic={MAGIC}')

    info = mt5.symbol_info(SYMBOL)
    if info is None:
        log.error(f'Symbol {SYMBOL} not found')
        mt5.shutdown()
        return
    if not info.visible:
        mt5.symbol_select(SYMBOL, True)

    last_bar_time = None

    try:
        while True:
            df = get_bars(MA_PERIOD + 30)
            if df is None:
                time.sleep(30)
                continue

            # Fire once per new closed bar
            current_bar_time = df.iloc[-2]['time']
            if current_bar_time != last_bar_time:
                last_bar_time = current_bar_time
                log.debug(f'New bar: {current_bar_time}')
                check_signal(df)

            time.sleep(20)

    except KeyboardInterrupt:
        log.info('Stopped by user')
    finally:
        mt5.shutdown()
        log.info('MT5 shutdown')


if __name__ == '__main__':
    main()