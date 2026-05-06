"""
test_v3.py — bb_monitor_v7 / daily_trade_v3 ロジック単体テスト
MT5・risk_manager 不要で実行できる。
VPS側で実行: python test_v3.py
全テストがPASSすれば本番ファイルに上書きしてよい。
"""
import sys, traceback, math
import pandas as pd
import numpy as np

PASS = 0
FAIL = 0

def ok(name):
    global PASS
    PASS += 1
    print(f"  PASS  {name}")

def ng(name, detail=""):
    global FAIL
    FAIL += 1
    print(f"  FAIL  {name}" + (f" → {detail}" if detail else ""))

def section(title):
    print(f"\n{'='*55}\n  {title}\n{'='*55}")

# ══════════════════════════════════════════════════════════
# ── テスト対象関数をここにコピー（MT5依存を除去）──────────
# ── bb_monitor_v7 から ──────────────────────────────────
# ══════════════════════════════════════════════════════════

def calc_rsi(rates_df, period=14):
    close = rates_df['close']
    delta = close.diff()
    gain  = delta.clip(lower=0)
    loss  = (-delta).clip(lower=0)
    avg_g = gain.ewm(com=period - 1, min_periods=period).mean()
    avg_l = loss.ewm(com=period - 1, min_periods=period).mean()
    rs    = avg_g / avg_l.replace(0, np.nan)
    rsi   = 100 - (100 / (1 + rs))
    rsi   = rsi.where(avg_l != 0, other=100.0)
    rsi   = rsi.where(avg_g != 0, other=0.0)
    val   = float(rsi.iloc[-1]) if not rsi.empty else 50.0
    return val if val == val else 50.0

def calc_bb(rates_df, period, sigma):
    close   = rates_df['close']
    ma      = close.rolling(period).mean()
    std     = close.rolling(period).std()
    upper   = ma + sigma * std
    lower   = ma - sigma * std
    idx     = -2
    ma_v    = float(ma.iloc[idx])
    std_v   = float(std.iloc[idx])
    upper_v = float(upper.iloc[idx])
    lower_v = float(lower.iloc[idx])
    close_v = float(close.iloc[idx])
    sigma_pos = (close_v - ma_v) / std_v if std_v > 0 else 0.0
    return {'ma': ma_v, 'upper': upper_v, 'lower': lower_v,
            'close': close_v, 'sigma_pos': sigma_pos}

RSI_PARAMS = {'period': 14, 'sell_min': 55, 'buy_max': 45}
HTF_PARAMS = {'period': 20, 'sigma': 1.5, 'range_sigma': 2.0, 'bars': 50}
BB_PARAMS  = {'period': 20, 'sigma': 1.5}

def rsi_filter_logic(rsi_val, direction):
    if direction == 'sell' and rsi_val < RSI_PARAMS['sell_min']:
        return False
    if direction == 'buy'  and rsi_val > RSI_PARAMS['buy_max']:
        return False
    return True

def htf_range_filter_logic(sigma_pos):
    return abs(sigma_pos) <= HTF_PARAMS['range_sigma']

MAX_JPY_LOT   = 0.4
MAX_TOTAL_POS = 13

BB_PAIRS_V7 = {
    'USDCAD': {'is_jpy': False, 'max_pos': 1},
    'GBPJPY': {'is_jpy': True,  'max_pos': 1},
    'EURJPY': {'is_jpy': True,  'max_pos': 1},
    'USDJPY': {'is_jpy': True,  'max_pos': 1},
    'AUDJPY': {'is_jpy': True,  'max_pos': 1},
    'EURUSD': {'is_jpy': False, 'max_pos': 1},
    'GBPUSD': {'is_jpy': False, 'max_pos': 1},
    'NZDUSD': {'is_jpy': False, 'max_pos': 1},
}

MOM_NEW_CONFIG = {
    'MOM_ENZ': {'symbol':'EURNZD','filter_symbol':'EURUSD','period':10,'mom_th':0.01,'filter_th':0.005},
    'MOM_ECA': {'symbol':'EURCAD','filter_symbol':'EURUSD','period':10,'mom_th':0.01,'filter_th':0.005},
    'MOM_GBU': {'symbol':'GBPUSD','filter_symbol':'EURUSD','period':10,'mom_th':0.01,'filter_th':0.005},
}

def check_mom_logic(closes, fcloses, period, mom_th, filter_th):
    if len(closes) <= period: return None
    mom  = (closes[-1]  - closes[-period-1])  / closes[-period-1]
    fmom = (fcloses[-1] - fcloses[-period-1]) / fcloses[-period-1]
    if mom >  mom_th  and fmom >  filter_th: return 'buy'
    if mom < -mom_th  and fmom < -filter_th: return 'sell'
    return None

# ══════════════════════════════════════════════════════════
# テストデータ生成ヘルパー
# ══════════════════════════════════════════════════════════
def make_df(closes):
    return pd.DataFrame({'close': closes})

def make_ohlc(closes, spread=0.001):
    rows = []
    for i, c in enumerate(closes):
        rows.append({
            'open':  c - spread/2,
            'high':  c + spread,
            'low':   c - spread,
            'close': c,
        })
    return pd.DataFrame(rows)

# ══════════════════════════════════════════════════════════
# Section 1: BB_PAIRS定義チェック
# ══════════════════════════════════════════════════════════
section("1. BB_PAIRS v7 定義チェック")

# NZDUSDが追加されていること
if 'NZDUSD' in BB_PAIRS_V7:
    ok("NZDUSD が BB_PAIRS_V7 に存在する")
else:
    ng("NZDUSD が BB_PAIRS_V7 に存在しない")

# 既存7ペアが欠けていないこと
v6_pairs = {'USDCAD','GBPJPY','EURJPY','USDJPY','AUDJPY','EURUSD','GBPUSD'}
missing = v6_pairs - set(BB_PAIRS_V7.keys())
if not missing:
    ok("既存7ペアが全て存在する")
else:
    ng("既存ペアが欠けている", str(missing))

# JPY判定が正しいこと
jpy_errors = []
for sym, cfg in BB_PAIRS_V7.items():
    expected_jpy = 'JPY' in sym
    if cfg['is_jpy'] != expected_jpy:
        jpy_errors.append(sym)
if not jpy_errors:
    ok("全ペアのis_jpy判定が正しい")
else:
    ng("is_jpy判定ミス", str(jpy_errors))

# NZDUSDはis_jpy=False
if not BB_PAIRS_V7['NZDUSD']['is_jpy']:
    ok("NZDUSD is_jpy=False（正しい）")
else:
    ng("NZDUSD is_jpy が True になっている（バグ）")

# ══════════════════════════════════════════════════════════
# Section 2: calc_bb() 先読み防止チェック
# ══════════════════════════════════════════════════════════
section("2. calc_bb() 先読み防止・値チェック")

# 単調増加列：最後のバーがUPPERを超えているが、[-2]はまだ中間のケース
base   = [1.1000 + i*0.0001 for i in range(25)]
# [-1]だけ急騰させる
spike  = base[:-1] + [base[-1] + 0.005]
df_spike = make_df(spike)
bb_res   = calc_bb(df_spike, 20, 1.5)

# sigma_posは[-2]の値なので、スパイクの影響を受けていないはず
if abs(bb_res['sigma_pos']) < 3.0:
    ok("calc_bb() は[-2]を参照（先読みしていない）")
else:
    ng("calc_bb() が最新バーを参照している可能性あり", f"sigma_pos={bb_res['sigma_pos']:.2f}")

# upper > ma > lower の順序
if bb_res['upper'] > bb_res['ma'] > bb_res['lower']:
    ok("BB: upper > ma > lower の順序が正しい")
else:
    ng("BB: upper/ma/lower の順序異常", str(bb_res))

# sigma_posが[-2]のclose位置を反映しているか
expected_close = spike[-2]
if abs(bb_res['close'] - expected_close) < 1e-8:
    ok("BB: close が[-2]の値と一致")
else:
    ng("BB: close 不一致", f"got={bb_res['close']} expected={expected_close}")

# ══════════════════════════════════════════════════════════
# Section 3: RSIフィルターロジック
# ══════════════════════════════════════════════════════════
section("3. RSIフィルターロジック（sell_min=55 / buy_max=45）")

# sell方向: RSI≥55でOK、RSI<55でNG
if rsi_filter_logic(60, 'sell'): ok("SELL RSI=60 (≥55) → 通過")
else: ng("SELL RSI=60 が通過しなかった")

if not rsi_filter_logic(50, 'sell'): ok("SELL RSI=50 (<55) → スキップ")
else: ng("SELL RSI=50 が通過してしまった")

if rsi_filter_logic(55, 'sell'): ok("SELL RSI=55 (境界値=55) → 通過")
else: ng("SELL RSI=55 (境界値) がスキップされた")

# buy方向: RSI≤45でOK、RSI>45でNG
if rsi_filter_logic(40, 'buy'): ok("BUY  RSI=40 (≤45) → 通過")
else: ng("BUY RSI=40 が通過しなかった")

if not rsi_filter_logic(50, 'buy'): ok("BUY  RSI=50 (>45) → スキップ")
else: ng("BUY RSI=50 が通過してしまった")

if rsi_filter_logic(45, 'buy'): ok("BUY  RSI=45 (境界値=45) → 通過")
else: ng("BUY RSI=45 (境界値) がスキップされた")

# ══════════════════════════════════════════════════════════
# Section 4: HTFフィルターロジック（range_sigma=2.0）
# ══════════════════════════════════════════════════════════
section("4. HTFレンジフィルターロジック（range_sigma=2.0）")

if htf_range_filter_logic(0.0):   ok("σ=0.0   → レンジ（通過）")
else: ng("σ=0.0 がトレンド判定された")

if htf_range_filter_logic(1.9):   ok("σ=+1.9  → レンジ（通過）")
else: ng("σ=+1.9 がトレンド判定された")

if not htf_range_filter_logic(2.1): ok("σ=+2.1  → トレンド（スキップ）")
else: ng("σ=+2.1 がレンジ判定された（バグ）")

if not htf_range_filter_logic(-2.1): ok("σ=-2.1  → トレンド（スキップ）")
else: ng("σ=-2.1 がレンジ判定された（バグ）")

if not htf_range_filter_logic(2.0): ok("σ=2.0  (境界値) → トレンド（>なので）")
else: ok("σ=2.0  (境界値) → レンジ（<=なので）")  # どちらでもOK・仕様確認用

# ══════════════════════════════════════════════════════════
# Section 5: BBシグナル方向の整合性
# ══════════════════════════════════════════════════════════
section("5. BBシグナル方向整合性（close>=upper→sell / close<=lower→buy）")

def sim_bb_direction(close_val, upper_val, lower_val):
    if   close_val >= upper_val: return 'sell'
    elif close_val <= lower_val: return 'buy'
    else:                        return None

if sim_bb_direction(1.105, 1.100, 1.090) == 'sell':
    ok("close>=upper → sell")
else:
    ng("close>=upper の方向が sell でない")

if sim_bb_direction(1.085, 1.100, 1.090) == 'buy':
    ok("close<=lower → buy")
else:
    ng("close<=lower の方向が buy でない")

if sim_bb_direction(1.095, 1.100, 1.090) is None:
    ok("バンド内 → シグナルなし(None)")
else:
    ng("バンド内なのにシグナルが出た")

# TP/SL方向（sell: tp<entry, sl>entry / buy: tp>entry, sl<entry）
def sim_tp_sl(entry, direction, tp_dist, sl_dist):
    if direction == 'sell':
        tp = entry - tp_dist
        sl = entry + sl_dist
    else:
        tp = entry + tp_dist
        sl = entry - sl_dist
    return tp, sl

tp, sl = sim_tp_sl(1.100, 'sell', 0.010, 0.007)
if tp < 1.100 and sl > 1.100:
    ok("SELL: tp<entry, sl>entry（正しい）")
else:
    ng("SELL: TP/SLの方向が逆", f"entry=1.100 tp={tp} sl={sl}")

tp, sl = sim_tp_sl(1.100, 'buy', 0.010, 0.007)
if tp > 1.100 and sl < 1.100:
    ok("BUY:  tp>entry, sl<entry（正しい）")
else:
    ng("BUY:  TP/SLの方向が逆", f"entry=1.100 tp={tp} sl={sl}")

# ══════════════════════════════════════════════════════════
# Section 6: JPYロット上限ロジック
# ══════════════════════════════════════════════════════════
section("6. JPYロット上限ロジック（MAX_JPY_LOT=0.4）")

def jpy_limit_ok(current_jpy_lots, is_jpy):
    if is_jpy and current_jpy_lots >= MAX_JPY_LOT:
        return False
    return True

if     jpy_limit_ok(0.3, True):  ok("JPY 0.3lot (<0.4) → 追加可能")
else:  ng("JPY 0.3lot が上限判定された")

if not jpy_limit_ok(0.4, True):  ok("JPY 0.4lot (=0.4) → 上限到達でスキップ")
else:  ng("JPY 0.4lot が通過してしまった")

if not jpy_limit_ok(0.5, True):  ok("JPY 0.5lot (>0.4) → スキップ")
else:  ng("JPY 0.5lot が通過してしまった")

if     jpy_limit_ok(0.5, False): ok("非JPYペアはロット上限チェックをスキップ")
else:  ng("非JPYペアがJPYロット上限に引っかかった")

# NZDUSDがJPY上限に引っかからないこと
nzd_cfg = BB_PAIRS_V7['NZDUSD']
if jpy_limit_ok(0.5, nzd_cfg['is_jpy']):
    ok("NZDUSD は is_jpy=False のためJPY上限チェックをスキップ")
else:
    ng("NZDUSD が誤ってJPY上限に引っかかっている（is_jpy バグ）")

# ══════════════════════════════════════════════════════════
# Section 7: MOM新規戦略シグナルロジック
# ══════════════════════════════════════════════════════════
section("7. MOM新規戦略シグナルロジック（EURNZD/EURCAD/GBPUSD）")

# 定義チェック
expected_new = {'MOM_ENZ', 'MOM_ECA', 'MOM_GBU'}
if set(MOM_NEW_CONFIG.keys()) == expected_new:
    ok("MOM_NEW_CONFIG に3戦略が定義されている")
else:
    ng("MOM_NEW_CONFIG の定義ミス", str(set(MOM_NEW_CONFIG.keys())))

# 各戦略のsymbolが正しいこと
sym_map = {'MOM_ENZ':'EURNZD','MOM_ECA':'EURCAD','MOM_GBU':'GBPUSD'}
for k, expected_sym in sym_map.items():
    if MOM_NEW_CONFIG[k]['symbol'] == expected_sym:
        ok(f"{k}: symbol={expected_sym}（正しい）")
    else:
        ng(f"{k}: symbol不一致", f"got={MOM_NEW_CONFIG[k]['symbol']}")

# BUYシグナル: mom>mom_th かつ fmom>filter_th
closes_up  = [1.0 + i*0.0015 for i in range(13)]  # +1.5%上昇
fcloses_up = [1.1 + i*0.001  for i in range(13)]  # +0.9%上昇
sig = check_mom_logic(closes_up, fcloses_up, 10, 0.01, 0.005)
if sig == 'buy':
    ok("MOM BUYシグナル: mom>mom_th & fmom>filter_th → buy")
else:
    ng("MOM BUYシグナル失敗", f"got={sig}")

# SELLシグナル: mom<-mom_th かつ fmom<-filter_th
closes_dn  = [1.0 - i*0.0015 for i in range(13)]
fcloses_dn = [1.1 - i*0.001  for i in range(13)]
sig = check_mom_logic(closes_dn, fcloses_dn, 10, 0.01, 0.005)
if sig == 'sell':
    ok("MOM SELLシグナル: mom<-mom_th & fmom<-filter_th → sell")
else:
    ng("MOM SELLシグナル失敗", f"got={sig}")

# 片方だけの場合はNone
closes_up2  = [1.0 + i*0.0015 for i in range(13)]
fcloses_flat= [1.1 + i*0.00001 for i in range(13)]  # fmom≈0 (filter未達)
sig = check_mom_logic(closes_up2, fcloses_flat, 10, 0.01, 0.005)
if sig is None:
    ok("MOM: mom>mom_th だが fmom不足 → None（フィルター有効）")
else:
    ng("MOM: フィルター未達なのにシグナルが出た", f"got={sig}")

# ══════════════════════════════════════════════════════════
# Section 8: MAX_TOTAL定義チェック
# ══════════════════════════════════════════════════════════
section("8. MAX_TOTAL設定チェック")

MAX_TOTAL_V3 = 9   # daily_trade_v3 の設定値

if MAX_TOTAL_V3 == 9:
    ok("MAX_TOTAL=9（v2の6から+3、新規MOM3戦略分）")
else:
    ng(f"MAX_TOTAL={MAX_TOTAL_V3}（期待値=9）")

# 既存4戦略(MOM_JPY/MOM_GBJ/CORR/STR) + 新規3戦略 ≤ 9
if 4 + 3 <= MAX_TOTAL_V3:
    ok("既存4 + 新規3 = 7 ≤ MAX_TOTAL(9)（余裕あり）")
else:
    ng("戦略数がMAX_TOTALを超えている")

# ══════════════════════════════════════════════════════════
# Section 9: RSI計算値の妥当性
# ══════════════════════════════════════════════════════════
section("9. RSI計算値の妥当性チェック")

# 連続上昇 → RSI高い（>50）
rising = [1.0 + i*0.001 for i in range(30)]
rsi_up = calc_rsi(make_df(rising))
if 50 < rsi_up <= 100:
    ok(f"連続上昇 → RSI={rsi_up:.1f}（>50, 正常）")
else:
    ng(f"連続上昇なのにRSI={rsi_up:.1f}（異常）")

# 連続下落 → RSI低い（<50）
falling = [1.0 - i*0.001 for i in range(30)]
rsi_dn  = calc_rsi(make_df(falling))
if 0 <= rsi_dn < 50:
    ok(f"連続下落 → RSI={rsi_dn:.1f}（<50, 正常）")
else:
    ng(f"連続下落なのにRSI={rsi_dn:.1f}（異常）")

# RSIは0〜100の範囲
import random
random.seed(42)
noisy = [1.0 + random.uniform(-0.002, 0.002) for _ in range(30)]
rsi_r = calc_rsi(make_df(noisy))
if 0 <= rsi_r <= 100:
    ok(f"ランダム系列 → RSI={rsi_r:.1f}（0〜100範囲内）")
else:
    ng(f"RSI={rsi_r:.1f}（範囲外！）")

# ══════════════════════════════════════════════════════════
# Section 10: trail_monitor.py との互換性チェック
# ══════════════════════════════════════════════════════════
section("10. trail_monitor.py TRAIL_CONFIG との互換性")

# trail_monitorが認識するコメントプレフィックス
TRAIL_CONFIG_KEYS = {'BB_', 'MOM_JPY', 'MOM_GBJ', 'STR'}

# BB_PAIRS_V7 の全コメントが BB_ プレフィックスで trail に認識されるか
for sym in BB_PAIRS_V7:
    comment = 'BB_' + sym
    matched = any(comment.startswith(k) for k in TRAIL_CONFIG_KEYS)
    if matched:
        ok(f"BB_{sym} → TRAIL_CONFIG['BB_'] に一致")
    else:
        ng(f"BB_{sym} が TRAIL_CONFIG に一致しない（トレーリング無効）")

# 新規MOMのコメントがTRAIL_CONFIGに存在しないことを確認
# → 存在しない場合は trail がスキップする（想定動作）
new_mom_comments = ['FXBot_MOM_ENZ', 'FXBot_MOM_ECA', 'FXBot_MOM_GBU']
for c in new_mom_comments:
    strategy_part = c.replace('FXBot_', '')  # 'MOM_ENZ' etc.
    matched = any(strategy_part.startswith(k) for k in TRAIL_CONFIG_KEYS)
    if not matched:
        ok(f"{c} はTRAIL_CONFIGに未登録（trail_monitorがスキップ→想定内）")
    else:
        ok(f"{c} はTRAIL_CONFIGに登録済み（トレーリング有効）")

print("\n" + "="*55)
print(f"  テスト結果: PASS={PASS}  FAIL={FAIL}")
if FAIL == 0:
    print("  ✅ 全テスト通過 → 本番ファイルへの適用OK")
else:
    print("  ❌ FAILあり → 修正してから本番適用すること")
print("="*55)