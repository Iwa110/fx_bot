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

★BT結論(optimizer/intervention_playbook_bt.py で撤退ポリシーを比較した正直な結論):
  撤退の設計が全てを決める(KNOWN介入 n=9, demo lot):
    * abort(-3円撤退) : net-NEGATIVE(-185k)。abort が一時含み損を損失確定させる張本人。
    * hold_ph(塩漬け) : ★net +1,257k・9/9 全介入が元水準へ回復。無レバなら回復まで保有できる。
    * hold_be(建値)   : price P&L~0 + carry のみ(資本回転の安全策)。
  => 無レバ塩漬け(hold_ph)が正解。ただし塩漬けコスト = 最大含み損 ~21円(2022-10)/~19円
     (2024-07, データ内未回復=構造転換テール)・回復に最長 ~1-2年。回復DDが最大21円なので
     "21円未満の賢い中間abort"は存在せず(勝ちを切る)。テール防御は価格でなくファンダ判断
     (BOJ持続利上げ/米景気後退で金利差消滅=マクロ前提崩壊時のみ手動撤退)。
  なお全発火の黒字の相当部分は非介入ディップ(=汎用キャリー押し目買い, 既存carry-gridと同型)。
  => demo forward-test でBT乖離・塩漬けDD・スリッページを観測。LIVE は LIVE_LOT_SCALE>0 を
     明示設定するまで拒否(既定0)。実lotは maxDD~21円を無レバで耐える資本から逆算。

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
D_ARM_TIMEOUT_H = 12        # この時間内に安定(反発)しなければエピソード放棄(grind下落=マクロ転換回避)

# --- 案D 撤退ポリシー(intervention_playbook_bt.py で比較) ---
# 'hold_ph' = ★推奨/既定: abort無し・pre_high(元水準)まで塩漬け保有(無レバ前提)。
#             BTで KNOWN介入 9/9 回復・+126万円。ただし最大含み損~21円/回復最長~1.5-2年に耐える
#             資本サイズが前提(下記 CAVEAT)。価格abortは"賢い中間値"が存在しない(回復DDが最大21円=
#             21円未満のabortは勝ちトレードを切る)ため無し。テールはファンダ判断で手動撤退。
# 'abort'   = 凍結trough - D_ABORT_YEN で機械撤退(損失確定・資本回転重視)。BTでは net-negative。
# 'hold_be' = 建値(avg_entry)復帰で決済(塩漬け容認しつつ資本回転)。price P&L~0 + carryのみ。
D_EXIT_POLICY   = 'hold_ph'
D_ABORT_YEN     = 3.0       # abort ポリシー時のみ使用
D_TP_MODE       = 'full'    # 'full' = 目標で全決済 / 'half' = 50%地点で半分利確(pre_high系のみ)
D_MAX_DAYS      = 30        # abort ポリシー時の陳腐化決済(営業日)
D_HOLD_MAX_DAYS = None      # hold系の安全上限(営業日, None=無期限塩漬け)。数値化すれば強制手仕舞い

# --- ★BOJ連続利上げ撤退トリガ(金利差消滅=塩漬け前提の崩壊で手仕舞い) ---
# 米日政策金利差(USD Fed上限 - JPY BoJ, build_policy_rates.py同値+2026補完)。
# 政策決定のたびに手で追記すること(発表日, 水準%)。CURRENT_RATE_DIFF_OVERRIDE を
# 数値にすると当日のテーブル計算を上書き(速報対応)。
RATE_USD_TABLE = [
    ('2020-03-16', 0.25), ('2022-03-17', 0.50), ('2022-06-16', 1.75),
    ('2022-09-22', 3.25), ('2022-12-15', 4.50), ('2023-07-27', 5.50),
    ('2024-09-19', 5.00), ('2024-12-19', 4.50), ('2025-10-30', 4.00),
    ('2026-03-18', 3.75),
]
RATE_JPY_TABLE = [
    ('2016-02-16', -0.10), ('2024-03-19', 0.10), ('2024-07-31', 0.25),
    ('2025-01-24', 0.50), ('2025-07-31', 0.75), ('2026-06-17', 1.00),
]
RATE_EXIT_TH   = 1.0        # 金利差(%)がこれ以下に縮小したら塩漬けを手仕舞い(BOJ利上げ回路遮断)
CURRENT_RATE_DIFF_OVERRIDE = None   # 速報で手動上書き(例 0.75)。None=テーブルから当日算出。
# ★塩漬け CAVEAT: 実測 maxDD ~21円(2022-10)/~19円(2024-07, データ内未回復=構造転換テール)。
#   demo lot(0.10/tier x3=0.30lot)で ~640k JPY の評価損。実lotに線形。無レバでこれを耐える資本必須。
#   "永久に戻らない"構造転換(日本財政危機/恒常円高)は塩漬けを永久損に変える真のテール。

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

def current_rate_diff() -> float:
    """本日の米日政策金利差(%). CURRENT_RATE_DIFF_OVERRIDE 優先, なければテーブルから算出。"""
    if CURRENT_RATE_DIFF_OVERRIDE is not None:
        return float(CURRENT_RATE_DIFF_OVERRIDE)
    today = pd.Timestamp.now(tz='UTC').tz_localize(None)
    def _rate(tbl):
        r = None
        for d, v in tbl:
            if pd.Timestamp(d) <= today:
                r = v
        return r if r is not None else 0.0
    return _rate(RATE_USD_TABLE) - _rate(RATE_JPY_TABLE)

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
    log('cal: spike win=%dm atr_mult=%.1f min_yen=%.1f | D policy=%s tiers=%d abort=%.1f' %
        (SPIKE_WIN_MIN, SPIKE_ATR_MULT, SPIKE_MIN_YEN, D_EXIT_POLICY, D_TIERS, D_ABORT_YEN))

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

            # ───────────────── 案D 管理(撤退ポリシー別) ─────────────────
            if d_pos:
                trough = state['trough']
                pre_high = state['pre_high']
                avg_entry = (sum(p.price_open * p.volume for p in d_pos)
                             / sum(p.volume for p in d_pos))
                held_days = bars_held_days(d_pos)
                # 目標(TP)と abort/time はポリシーで決まる
                if D_EXIT_POLICY == 'abort':
                    tp_lvl    = pre_high
                    hit_abort = cur_bid <= trough - D_ABORT_YEN
                    expired   = held_days >= D_MAX_DAYS * 7.0 / 5.0
                elif D_EXIT_POLICY == 'hold_be':
                    tp_lvl    = avg_entry                          # 建値復帰で決済
                    hit_abort = False
                    expired   = (D_HOLD_MAX_DAYS is not None
                                 and held_days >= D_HOLD_MAX_DAYS * 7.0 / 5.0)
                else:                                             # 'hold_ph' (既定)
                    tp_lvl    = pre_high                          # 元水準まで塩漬け
                    hit_abort = False
                    expired   = (D_HOLD_MAX_DAYS is not None
                                 and held_days >= D_HOLD_MAX_DAYS * 7.0 / 5.0)
                # half利確(pre_high系のみ)
                if (D_TP_MODE == 'half' and D_EXIT_POLICY != 'hold_be'
                        and not state['d_half_done'] and pre_high > trough):
                    half_lvl = trough + 0.5 * (pre_high - trough)
                    if cur_bid >= half_lvl:
                        half = d_pos[:max(1, len(d_pos) // 2)]
                        nclosed, pnl = close_positions(half, MAGIC_D, TAG_D, '_HALF')
                        log('exitD_HALF legs=%d pnl=%.0f' % (nclosed, pnl))
                        state['d_half_done'] = True
                        d_pos = get_positions(MAGIC_D)
                hit_tp = cur_bid >= tp_lvl
                # ★BOJ連続利上げ撤退: 金利差が RATE_EXIT_TH 以下に縮小したら塩漬け手仕舞い
                rdiff = current_rate_diff()
                hit_ratexit = (D_EXIT_POLICY in ('hold_ph', 'hold_be')
                               and rdiff <= RATE_EXIT_TH)
                reason = ('_ABORT' if hit_abort else
                          ('_RATEEXIT' if hit_ratexit else
                           ('_TP' if hit_tp else ('_TIME' if expired else None))))
                if reason and d_pos:
                    nclosed, pnl = close_positions(d_pos, MAGIC_D, TAG_D, reason)
                    log('exitD%s[%s] legs=%d held=%.1fd avg=%.3f trough=%.3f pre_high=%.3f rdiff=%.2f pnl=%.0f' %
                        (reason, D_EXIT_POLICY, nclosed, held_days, avg_entry, trough,
                         pre_high, rdiff, pnl))
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
                    'trough=%.3f pre_high=%.3f tiers=%d policy=%s rdiff=%.2f%%' %
                    (state['episode_active'], len(d_pos), state['b_open'], cur_bid,
                     ('%.3f' % atr1h) if not np.isnan(atr1h) else 'na',
                     state['trough'], state['pre_high'], state['d_tiers_filled'],
                     D_EXIT_POLICY, current_rate_diff()))

        except Exception as e:
            log('loop_error ' + str(e))
        time.sleep(LOOP_INTERVAL)

# ══════════════════════════════════════════
# Entry point
# ══════════════════════════════════════════
def main():
    global BROKER_KEY, LOT_SCALE, CLOSE_ONLY, B_ENABLED, D_ENABLED, D_EXIT_POLICY
    global LOG_FILE, _STATE_FILE

    ap = argparse.ArgumentParser(description='USDJPY intervention play (B reactive short / D dip-buy) v1')
    ap.add_argument('--broker', default=BROKER_KEY,
                    choices=['axiory', 'exness', 'oanda', 'oanda_demo', 'oanda_live'])
    ap.add_argument('--enable-B', action='store_true', help='案B反応型ショートを有効化(既定OFF, 弱い)')
    ap.add_argument('--disable-D', action='store_true', help='案D押し目買いを無効化')
    ap.add_argument('--exit-policy', default=D_EXIT_POLICY,
                    choices=['hold_ph', 'abort', 'hold_be'],
                    help='案D撤退: hold_ph=塩漬け元水準(既定/BT黒字) / abort=-3円撤退 / hold_be=建値決済')
    ap.add_argument('--live-total-lot', type=float, default=0.0,
                    help='live時の合計ロット(3tier合計)。intervention_sizing.py の safe_lot を入れる。'
                         '例 0.17。未指定(0)は live 拒否。demoは無視(常に0.30)。')
    ap.add_argument('--close-only', action='store_true', help='既存ポジ管理のみ・新規なし')
    args = ap.parse_args()

    BROKER_KEY = args.broker
    CLOSE_ONLY = args.close_only
    D_EXIT_POLICY = args.exit_policy
    if args.enable_B:
        B_ENABLED = True
    if args.disable_D:
        D_ENABLED = False

    if is_live_broker(BROKER_KEY):
        # live合計ロット -> LOT_SCALE(tier lot 0.10*3=0.30 を基準にスケール)
        base_total = D_TIER_LOT * D_TIERS
        live_scale = (args.live_total_lot / base_total) if args.live_total_lot > 0 else LIVE_LOT_SCALE
        LOT_SCALE = live_scale
        if LOT_SCALE <= 0:
            log('LIVE refuse: --live-total-lot 未指定 (intervention_sizing.py で safe_lot を算出し '
                '--live-total-lot に渡す。demo forward-test 先行を強く推奨) broker=%s' % BROKER_KEY)
            return
        log('LIVE sizing: total_lot=%.3f -> LOT_SCALE=%.3f (tier=%.3f) rate_diff=%.2f%% exit_th=%.2f%%'
            % (args.live_total_lot or base_total * LIVE_LOT_SCALE, LOT_SCALE,
               D_TIER_LOT * LOT_SCALE, current_rate_diff(), RATE_EXIT_TH))
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
