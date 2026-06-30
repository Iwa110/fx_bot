"""
intervention_monitor.py - USDJPY 為替介入プレイ v1 (案B 反応型ショート / 案D 介入後の押し目買い)

背景・較正(optimizer/intervention_event_study.py, USDJPY 5m 10y, 2022/2024/2026 の
全 MOF 円買い介入を実測):
  介入シグネチャ = 短時間(~20-30分)の大幅 USDJPY 急落(JPY高)。1h ATR の ~4倍 / 絶対 >=1.5円。
  実測の含意(正直な結論):
    * 案B(反応型ショート) = データ上 "弱い"。検出時点で大半の下落は終わっており、
      検出後の追随(median 0.3-0.9円) < 直後の戻り(median 1.1-1.5円)。
      => 単独の利益源にはならない。高速トリガで小ロットのスキャルプか、OFF が妥当。
      本monitorでは B_ENABLED=False(既定OFF)。検出ロジックは主に案Dのトリガとして使う。
    * 案D(介入後の押し目買い=キャリー順方向) = 構造的に有利。9イベント中 8 が
      介入前水準へ完全回復(median ~20営業日) + 正スワップ。
      唯一の死因 = 介入がマクロ転換点と重なる場合(2022-10 Fed打ち止め: 谷を更に7.7円割れ /
      2024-07 8月キャリー巻き戻し: 15.7円割れ・未回復)。
      較正された防御 = "凍結trough から D_ABORT_YEN(既定3.0円)下抜けたら全撤退"。
      通常の介入(lean)は谷を 0-2.7円 しか割らない => 大惨事のみ -3円に限定。

★BT CAVEAT(optimizer/intervention_playbook_bt.py で本ロジックを再生した正直な結論):
  文書化された5介入だけ では案Dは net-NEGATIVE。2/5(2022-10 Fed打ち止め, 2024-07 8月
  キャリー巻き戻し)が真のマクロ転換で、abort で限定しても勝3例の利益を上回る。
  全発火(介入+非介入ディップ)ではnet-POSITIVEだが、その黒字は "USDJPY上昇+キャリーの
  汎用的押し目買い"(=既存 long-only carry-grid と同型) から来ており、介入固有のエッジでは無い。
  => 本monitorは「検証済みエッジ」ではない。demo forward-test / データ収集 / テール警告 用途。
     LIVE は LIVE_LOT_SCALE>0 を明示設定するまで拒否(既定0)。実マネー投入は forward 蓄積後に再判定。

戦略(状態機械, 1エピソード = 1介入):
  DETECT : flat 時、直近 SPIKE_WIN_MIN 分の高値から現値までの下落が
           max(ATR1h*SPIKE_ATR_MULT, SPIKE_MIN_YEN) 以上 => エピソード開始。
           pre_high(直近2h高値) と trough(エピソード中の最安値) を記録。
  案B    : (B_ENABLED時のみ) 検出直後に小ロット SELL。target=entry-B_TARGET_YEN /
           stop=pre_high+B_STOP_BUF / 時間切れ B_MAX_MIN 分。短期スキャルプ。
  案D    : エピソード中、trough から BOUNCE_YEN 反発で安定確認 => BUY ラダー(最大3段)。
           tier 間隔 >= D_ADD_GAP_YEN。trough 近傍の押し目で買い下がる。
           撤退(abort)  : 現値が trough - D_ABORT_YEN 下抜け => 全 D 決済(マクロ転換)。
           利確(TP)     : 現値が pre_high 到達(完全リトレース) => 全 D 決済。任意で 50% 部分利確。
           時間切れ     : D_MAX_DAYS 営業日 超過 => 全 D 決済(陳腐化)。
           正スワップは保有中ブローカー側で自然に付与(long USDJPY)。
  エピソードは D が無くなり(撤退/利確/時間切れ) かつ B も無くなった時点で終了 -> 再検出待ち。

Magic/tag: 案B=20260061/INTV_B, 案D=20260062/INTV_D (他戦略 20260001-050 と非重複)。
Brokers : demo axiory/exness(forward-test 先行)。live は LIVE_LOT_SCALE>0 まで拒否。
State   : intervention_state_{broker}.json   Log: intervention_log_{broker}.txt

実行(1分poll。介入は速いが案Dは数日トレードなので1分で十分応答的):
  python intervention_monitor.py --broker axiory
  python intervention_monitor.py --broker axiory --enable-B   # 案Bスキャルプも有効化(弱い)
  python intervention_monitor.py --broker axiory --close-only  # 既存ポジの管理のみ
"""

import sys
import os
import time
import argparse
import json
from datetime import datetime, timezone, timedelta

import MetaTrader5 as mt5
import pandas as pd
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from broker_utils import connect_mt5, disconnect_mt5, build_symbol_map, is_live_broker

# ══════════════════════════════════════════
# 戦略定数(event study 較正値)
# ══════════════════════════════════════════
SYMBOL          = 'USDJPY'
ATR_N           = 14

# --- 介入スパイク検出 ---
SPIKE_WIN_MIN   = 30        # 速度判定の窓(分)。較正: 30分でも全介入を捕捉
SPIKE_ATR_MULT  = 3.5       # 1h ATR の何倍の急落でスパイクとするか(較正 ~4.0 を少し緩め早期検出)
SPIKE_MIN_YEN   = 1.5       # スパイク最小絶対下落(円)。ノイズ床(通常リスクオフ~1円)を上回る
PRE_HIGH_MIN    = 120       # pre_high を取る遡及窓(分) = 2h

# --- 案D 押し目買い(主戦) ---
D_ENABLED       = True
D_FLUSH_WIN_H   = 6.0       # 検出後この時間 flush 安値を追跡してから arm(早すぎる凍結=偽abort回避)
D_BOUNCE_YEN    = 0.4       # trough からこの幅反発で "安定" と見なし tier1 を入れる
D_TIERS         = 3         # 最大ラダー段数
D_TIER_LOT      = 0.10      # 1段あたり基準ロット(* LOT_SCALE)
D_ADD_GAP_YEN   = 0.5       # 追加 tier は前回約定からこの幅以上 下で
D_ABORT_YEN     = 3.0       # ★較正: 凍結trough から下抜けで全撤退(マクロ転換の死因を限定)
D_TP_MODE       = 'full'    # 'full' = pre_high で全決済 / 'half' = 50%地点で半分利確
D_MAX_DAYS      = 30        # 営業日。超過で陳腐化決済(回復 median ~20営業日)
D_ARM_TIMEOUT_H = 12        # この時間内に安定(反発)しなければエピソード放棄(grind下落=マクロ転換回避)

# --- 案B 反応型ショート(既定OFF, 弱い) ---
B_ENABLED       = False
B_LOT           = 0.10
B_TARGET_YEN    = 0.8       # 検出後の追随 median(15分トリガ時) ~0.9円 を控えめに
B_STOP_BUF      = 0.3       # stop = pre_high + buf
B_MAX_MIN       = 90        # 時間切れスキャルプ

LOOP_INTERVAL   = 60        # 1分poll
HB_CYCLES       = 30        # heartbeat ~30分毎
M5_BARS         = 8 * 24 * 12   # 約8日分の5m足(ATR1h算出 + 窓に十分)

MAGIC_B, TAG_B  = 20260061, 'INTV_B'
MAGIC_D, TAG_D  = 20260062, 'INTV_D'

# Lot scale. demo=1.0(=> D 1段0.10lot)。live は MC/DD からサイズし明示設定。
LOT_SCALE_DEMO  = 1.0
LIVE_LOT_SCALE  = 0.0

# ══════════════════════════════════════════
# Runtime globals
# ══════════════════════════════════════════
BROKER_KEY   = 'axiory'
LOT_SCALE    = LOT_SCALE_DEMO
CLOSE_ONLY   = False
_SYMBOL_MAP: dict = {}

def _rsym() -> str:
    return _SYMBOL_MAP.get(SYMBOL, SYMBOL)

# ══════════════════════════════════════════
# Logging / state
# ══════════════════════════════════════════
_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_FILE  = os.path.join(_BASE_DIR, 'intervention_log.txt')

def log(msg: str) -> None:
    ts   = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    line = ts + '  INTV  ' + msg
    print(line)
    try:
        with open(LOG_FILE, 'a', encoding='utf-8') as f:
            f.write(line + '\n')
    except Exception:
        pass

_STATE_FILE = os.path.join(_BASE_DIR, 'intervention_state.json')
_STATE_DEFAULTS = {
    'episode_active':  False,
    'pre_high':        0.0,
    'trough':          0.0,    # エピソード中の最安値(更新する)
    'detect_iso':      '',
    'detect_price':    0.0,
    'd_tiers_filled':  0,
    'd_last_add':      0.0,    # 最後に D tier を約定した価格
    'd_armed':         False,  # 安定確認済み(tier1投入可)
    'd_half_done':     False,  # half利確済み
    'traded':          False,  # このエピソードで実際に建てたか(終了判定用)
    'b_open':          False,
    'b_entry':         0.0,
    'b_open_iso':      '',
}

def load_state() -> dict:
    try:
        with open(_STATE_FILE, 'r', encoding='utf-8') as f:
            s = json.load(f)
        for k, v in _STATE_DEFAULTS.items():
            s.setdefault(k, v)
        return s
    except Exception:
        return dict(_STATE_DEFAULTS)

def save_state(s: dict) -> None:
    try:
        with open(_STATE_FILE, 'w', encoding='utf-8') as f:
            json.dump(s, f, indent=2, ensure_ascii=False)
    except Exception as e:
        log('state_save_error ' + str(e))

# ══════════════════════════════════════════
# Data / indicators
# ══════════════════════════════════════════
def get_m5(symbol: str, n: int):
    bars = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_M5, 0, n)
    if bars is None or len(bars) < PRE_HIGH_MIN // 5 + 30:
        return None
    df = pd.DataFrame(bars)
    df['datetime'] = pd.to_datetime(df['time'], unit='s', utc=True)
    return df.sort_values('datetime').reset_index(drop=True)

def atr1h_from_m5(df: pd.DataFrame) -> float:
    """直近確定1h足ベースの ATR14(Wilder ewm)。実機 risk_manager と整合。"""
    h1 = (df.set_index('datetime').resample('1h')
            .agg(high=('high', 'max'), low=('low', 'min'), close=('close', 'last'))
            .dropna())
    if len(h1) < ATR_N + 2:
        return float('nan')
    pc = h1['close'].shift(1)
    tr = pd.concat([(h1['high'] - h1['low']), (h1['high'] - pc).abs(),
                    (h1['low'] - pc).abs()], axis=1).max(axis=1)
    atr = tr.ewm(alpha=1.0 / ATR_N, adjust=False).mean()
    # 最後の1h足は未確定の可能性 -> 1つ前(確定足)を使う
    return float(atr.iloc[-2]) if len(atr) >= 2 else float('nan')

def window_high(df: pd.DataFrame, minutes: int) -> float:
    cutoff = df['datetime'].iloc[-1] - pd.Timedelta(minutes=minutes)
    seg = df[df['datetime'] >= cutoff]
    return float(seg['high'].max()) if len(seg) else float(df['high'].iloc[-1])

# ══════════════════════════════════════════
# Positions / orders
# ══════════════════════════════════════════
def get_positions(magic: int):
    pos = mt5.positions_get(symbol=_rsym())
    if not pos:
        return []
    return [p for p in pos if p.magic == magic]

def _round_lot(vol: float) -> float:
    info = mt5.symbol_info(_rsym())
    if info is None:
        return round(vol, 2)
    step = getattr(info, 'volume_step', 0.01) or 0.01
    vmin = getattr(info, 'volume_min', step) or step
    vmax = getattr(info, 'volume_max', vol) or vol
    v = round(round(vol / step) * step, 8)
    return max(vmin, min(vmax, v))

def market_order(side: str, lot: float, magic: int, tag: str, suffix: str) -> bool:
    sym  = _rsym()
    info = mt5.symbol_info(sym)
    tick = mt5.symbol_info_tick(sym)
    if info is None or tick is None:
        log('order_failed %s symbol_info/tick=None' % tag)
        return False
    vol = _round_lot(lot)
    if vol <= 0:
        log('order_skip %s vol<=0 (LOT_SCALE=%s)' % (tag, LOT_SCALE))
        return False
    if side == 'buy':
        otype, price = mt5.ORDER_TYPE_BUY, tick.ask
    else:
        otype, price = mt5.ORDER_TYPE_SELL, tick.bid
    req = {
        'action': mt5.TRADE_ACTION_DEAL, 'symbol': sym, 'volume': vol,
        'type': otype, 'price': price, 'tp': 0.0, 'sl': 0.0,
        'deviation': 30, 'magic': magic, 'comment': tag + suffix,
        'type_time': mt5.ORDER_TIME_GTC, 'type_filling': mt5.ORDER_FILLING_IOC,
    }
    res = mt5.order_send(req)
    if res is None or res.retcode != mt5.TRADE_RETCODE_DONE:
        log('order_failed %s %s code=%s' % (tag, side, res.retcode if res else 'None'))
        return False
    log('ENTRY %s %s lot=%s price=%s' % (tag, side, vol, round(price, info.digits)))
    return True

def close_positions(positions: list, magic: int, tag: str, suffix: str) -> tuple:
    sym = _rsym()
    closed, tot = 0, 0.0
    for p in positions:
        tick = mt5.symbol_info_tick(sym)
        if tick is None:
            continue
        is_long = (p.type == mt5.ORDER_TYPE_BUY)
        ctype = mt5.ORDER_TYPE_SELL if is_long else mt5.ORDER_TYPE_BUY
        price = tick.bid if is_long else tick.ask
        req = {
            'action': mt5.TRADE_ACTION_DEAL, 'symbol': sym, 'volume': p.volume,
            'type': ctype, 'price': price, 'deviation': 30, 'magic': magic,
            'comment': tag + suffix, 'position': p.ticket,
            'type_time': mt5.ORDER_TIME_GTC, 'type_filling': mt5.ORDER_FILLING_IOC,
        }
        res = mt5.order_send(req)
        if res and res.retcode == mt5.TRADE_RETCODE_DONE:
            tot += p.profit
            closed += 1
        else:
            log('close_failed ticket=%s code=%s' % (p.ticket, res.retcode if res else 'None'))
    return closed, tot

def bars_held_days(positions) -> float:
    if not positions:
        return 0.0
    t0 = min(p.time for p in positions)
    return (datetime.now(timezone.utc).timestamp() - t0) / 86400.0

# ══════════════════════════════════════════
# Main loop
# ══════════════════════════════════════════
def main_loop():
    state = load_state()
    cycle = 0
    log('start symbol=%s broker=%s lot_scale=%s B_enabled=%s D_enabled=%s close_only=%s' %
        (SYMBOL, BROKER_KEY, LOT_SCALE, B_ENABLED, D_ENABLED, CLOSE_ONLY))
    log('cal: spike win=%dm atr_mult=%.1f min_yen=%.1f | D abort=%.1f tiers=%d max_days=%d' %
        (SPIKE_WIN_MIN, SPIKE_ATR_MULT, SPIKE_MIN_YEN, D_ABORT_YEN, D_TIERS, D_MAX_DAYS))

    while True:
        cycle += 1
        try:
            df = get_m5(_rsym(), M5_BARS)
            tick = mt5.symbol_info_tick(_rsym())
            if df is None or tick is None:
                log('data_fetch_failed')
                time.sleep(LOOP_INTERVAL)
                continue
            cur_bid = tick.bid
            cur_ask = tick.ask
            atr1h = atr1h_from_m5(df)
            d_pos = get_positions(MAGIC_D)
            b_pos = get_positions(MAGIC_B)

            # update trough ONLY while the flush is developing (before D is armed).
            # Once armed, the trough is FROZEN = the flush low; abort/half measure against it.
            if state['episode_active'] and not state['d_armed']:
                state['trough'] = min(state['trough'], cur_bid) if state['trough'] > 0 else cur_bid

            # ───────────────── 案B 管理(スキャルプ) ─────────────────
            if state['b_open'] and b_pos:
                entry = state['b_entry']
                hit_tp = cur_ask <= entry - B_TARGET_YEN          # short利確
                hit_stop = cur_ask >= state['pre_high'] + B_STOP_BUF
                t_open = pd.Timestamp(state['b_open_iso']) if state['b_open_iso'] else None
                expired = (t_open is not None and
                           (pd.Timestamp.now(tz='UTC') - t_open) > pd.Timedelta(minutes=B_MAX_MIN))
                reason = '_TP' if hit_tp else ('_STOP' if hit_stop else ('_TIME' if expired else None))
                if reason:
                    nclosed, pnl = close_positions(b_pos, MAGIC_B, TAG_B, reason)
                    log('exitB%s legs=%d pnl=%.0f' % (reason, nclosed, pnl))
                    state['b_open'] = False
                    b_pos = []
            elif not b_pos and state['b_open']:
                state['b_open'] = False                            # 外部決済等で消えた

            # ───────────────── 案D 管理 ─────────────────
            if d_pos:
                trough = state['trough']
                pre_high = state['pre_high']
                hit_abort = cur_bid <= trough - D_ABORT_YEN        # ★マクロ転換撤退
                hit_full  = cur_bid >= pre_high                    # 完全リトレース
                held_days = bars_held_days(d_pos)
                expired   = held_days >= D_MAX_DAYS * 7.0 / 5.0     # 営業->暦換算
                # half利確
                if (D_TP_MODE == 'half' and not state['d_half_done']
                        and pre_high > trough):
                    half_lvl = trough + 0.5 * (pre_high - trough)
                    if cur_bid >= half_lvl:
                        half = d_pos[:max(1, len(d_pos) // 2)]
                        nclosed, pnl = close_positions(half, MAGIC_D, TAG_D, '_HALF')
                        log('exitD_HALF legs=%d pnl=%.0f' % (nclosed, pnl))
                        state['d_half_done'] = True
                        d_pos = get_positions(MAGIC_D)
                reason = ('_ABORT' if hit_abort else
                          ('_TP' if hit_full else ('_TIME' if expired else None)))
                if reason and d_pos:
                    nclosed, pnl = close_positions(d_pos, MAGIC_D, TAG_D, reason)
                    log('exitD%s legs=%d held=%.1fd trough=%.3f pre_high=%.3f pnl=%.0f' %
                        (reason, nclosed, held_days, trough, pre_high, pnl))
                    d_pos = []

            # ───────────────── エピソード終了判定 ─────────────────
            # 実際に建てた(traded) エピソードが flat に戻ったら終了 -> 検出再開。
            # (検出直後・arm前の flat では終了しない。それは abandon-timeout が処理)
            if (state['episode_active'] and state['traded']
                    and not d_pos and not b_pos and not CLOSE_ONLY):
                log('episode_end -> re-arm detection')
                for k, v in _STATE_DEFAULTS.items():
                    state[k] = v

            # ───────────────── 検出 / 案D エントリ ─────────────────
            if not CLOSE_ONLY:
                if not state['episode_active']:
                    # DETECT(flat時のみ)
                    if not np.isnan(atr1h):
                        ref_high = window_high(df, SPIKE_WIN_MIN)
                        drop = ref_high - cur_bid
                        thr = max(atr1h * SPIKE_ATR_MULT, SPIKE_MIN_YEN)
                        if drop >= thr:
                            state['episode_active'] = True
                            state['pre_high'] = window_high(df, PRE_HIGH_MIN)
                            state['trough'] = cur_bid
                            state['detect_iso'] = df['datetime'].iloc[-1].isoformat()
                            state['detect_price'] = cur_bid
                            log('SPIKE detected drop=%.2f thr=%.2f(atr1h=%.3f) '
                                'pre_high=%.3f price=%.3f' %
                                (drop, thr, atr1h, state['pre_high'], cur_bid))
                            # 案B 即時スキャルプ(有効時)
                            if B_ENABLED and not state['b_open']:
                                if market_order('sell', B_LOT * LOT_SCALE, MAGIC_B, TAG_B, '_scalp'):
                                    state['b_open'] = True
                                    state['traded'] = True
                                    state['b_entry'] = cur_bid
                                    state['b_open_iso'] = pd.Timestamp.now(tz='UTC').isoformat()
                else:
                    # arm timeout: 反発せず grind 下落が続くなら(マクロ転換の疑い)エピソード放棄
                    if not state['d_armed'] and state['detect_iso']:
                        elapsed = pd.Timestamp.now(tz='UTC') - pd.Timestamp(state['detect_iso'])
                        if elapsed > pd.Timedelta(hours=D_ARM_TIMEOUT_H) and not d_pos and not b_pos:
                            log('episode_abandon (no stabilization within %dh, grind=macro-turn risk)'
                                % D_ARM_TIMEOUT_H)
                            for k, v in _STATE_DEFAULTS.items():
                                state[k] = v
                    # 案D ラダー(エピソード中)
                    if D_ENABLED and state['episode_active'] and state['d_tiers_filled'] < D_TIERS:
                        trough = state['trough']
                        bounced = cur_bid >= trough + D_BOUNCE_YEN
                        flush_done = (state['detect_iso'] and
                                      pd.Timestamp.now(tz='UTC') - pd.Timestamp(state['detect_iso'])
                                      >= pd.Timedelta(hours=D_FLUSH_WIN_H))
                        # tier1: flush窓後の安定(反発)確認で arm。tier2+: 前回約定から D_ADD_GAP 下で押し目買い
                        if not state['d_armed']:
                            if bounced and flush_done:
                                state['d_armed'] = True
                                if market_order('buy', D_TIER_LOT * LOT_SCALE,
                                                MAGIC_D, TAG_D, '_T1'):
                                    state['d_tiers_filled'] = 1
                                    state['traded'] = True
                                    state['d_last_add'] = cur_ask
                        else:
                            # 追加段: 価格が前回約定より D_ADD_GAP 以上 下 かつ abort 手前
                            if (cur_ask <= state['d_last_add'] - D_ADD_GAP_YEN
                                    and cur_bid > trough - D_ABORT_YEN):
                                tier = state['d_tiers_filled'] + 1
                                if market_order('buy', D_TIER_LOT * LOT_SCALE,
                                                MAGIC_D, TAG_D, '_T' + str(tier)):
                                    state['d_tiers_filled'] = tier
                                    state['d_last_add'] = cur_ask

            save_state(state)

            # ───────────────── Heartbeat ─────────────────
            if cycle % HB_CYCLES == 0:
                log('heartbeat alive ep=%s d_legs=%d b=%s price=%.3f atr1h=%s '
                    'trough=%.3f pre_high=%.3f tiers=%d' %
                    (state['episode_active'], len(d_pos), state['b_open'], cur_bid,
                     ('%.3f' % atr1h) if not np.isnan(atr1h) else 'na',
                     state['trough'], state['pre_high'], state['d_tiers_filled']))

        except Exception as e:
            log('loop_error ' + str(e))
        time.sleep(LOOP_INTERVAL)

# ══════════════════════════════════════════
# Entry point
# ══════════════════════════════════════════
def main():
    global BROKER_KEY, LOT_SCALE, CLOSE_ONLY, B_ENABLED, D_ENABLED
    global LOG_FILE, _STATE_FILE

    ap = argparse.ArgumentParser(description='USDJPY intervention play (B reactive short / D dip-buy) v1')
    ap.add_argument('--broker', default=BROKER_KEY,
                    choices=['axiory', 'exness', 'oanda', 'oanda_demo', 'oanda_live'])
    ap.add_argument('--enable-B', action='store_true', help='案B反応型ショートを有効化(既定OFF, 弱い)')
    ap.add_argument('--disable-D', action='store_true', help='案D押し目買いを無効化')
    ap.add_argument('--close-only', action='store_true', help='既存ポジ管理のみ・新規なし')
    args = ap.parse_args()

    BROKER_KEY = args.broker
    CLOSE_ONLY = args.close_only
    if args.enable_B:
        B_ENABLED = True
    if args.disable_D:
        D_ENABLED = False

    if is_live_broker(BROKER_KEY):
        LOT_SCALE = LIVE_LOT_SCALE
        if LOT_SCALE <= 0:
            log('LIVE refuse: LIVE_LOT_SCALE not set (clear demo forward-test first, '
                'size from event-study DD) broker=%s' % BROKER_KEY)
            return
    else:
        LOT_SCALE = LOT_SCALE_DEMO

    LOG_FILE    = os.path.join(_BASE_DIR, 'intervention_log_' + BROKER_KEY + '.txt')
    _STATE_FILE = os.path.join(_BASE_DIR, 'intervention_state_' + BROKER_KEY + '.json')

    if not connect_mt5(BROKER_KEY):
        log('MT5 init failed broker=' + BROKER_KEY)
        return
    try:
        acc = mt5.account_info()
        if acc is None:
            log('account_info failed')
            return
        log('connected broker=%s login=%s lot_scale=%s' % (BROKER_KEY, acc.login, LOT_SCALE))
        _SYMBOL_MAP.update(build_symbol_map([SYMBOL], BROKER_KEY))
        rsym = _rsym()
        if not mt5.symbol_select(rsym, True):
            log('symbol_select failed for %s (%s)' % (rsym, mt5.last_error()))
        main_loop()
    finally:
        disconnect_mt5()


if __name__ == '__main__':
    main()
