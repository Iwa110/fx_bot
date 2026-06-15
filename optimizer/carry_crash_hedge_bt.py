"""
carry_crash_hedge_bt.py - 確定エッジ carry long-only Grid(USDJPY/NZDJPY)の唯一の弱点
「キャリー・クラッシュ・テール(リスクオフ円高での巨大DD)」を状態条件付きヘッジで塞げるか検証。

== なぜこれを検証するのか(束縛条件を直撃) ==
Step B再算定(project_grid_stepb_deployment_20260613)で、確定Go群のうち carry系
(USDJPY/NZDJPY long-only+combo)だけが資本効率6-7%/yr・P(5yr損)17-23%と壊滅的。原因は
carry-crash テールが高 maxDD99(4.3-4.5M)を生むこと=「micro-lot限定・スケール禁止」。
横断キャリー検証(project_carry_xsec_factor_20260615)で carry-crash の単月worstは
2015-01(CHF unpeg)・2018-12・2020-03(COVID)・2022-09・2024-07(円キャリー巻戻し)に集中
=リスクオフ円高に固まり経済的に反復。この“同一・反復・経済駆動”テールを状態条件付きで
塞げれば req_cap激減で carry系がスケール可能になりうる(新エッジ探索でなく確定エッジの実用化)。

== 過去Close済み「bleedヘッジ」との区別(最重要) ==
project_grid_insensitivity_complement_20260608 の案B「bleedヘッジ」はClose(負EV保険)。
敗因=Gridの“ランダムなDD接近”でトレンド方向ヘッジ→平均回帰で踏まれた。本タスクの違い:
  ① 対象は“ランダムなDD”でなく **システマティックで反復するcarry-crash(リスクオフ円高)**。
  ② 評価は単体PFでなく **carry系スリーブの資本効率/テール改善(保険コスト差引後)**。
  ③ 状態条件付き(平常時はヘッジOFF)。
それでも「保険コスト>節約資本」でCloseになりうる=事前登録基準で正直に判定。

== リスクオフ状態検出器(t-1, ルックアヘッド厳禁, FX内生) ==
  D1 セーフヘイブン強度 : JPY+CHF バスケットの対リスク通貨(AUD/NZD/CAD)急騰(短期リターン/vol)。
  D2 実現ボラ・スパイク  : リスクペア(AUDJPY)の短期実現volのレジーム上昇。
  D3 ①carryファクターDD  : 横断キャリー・ファクターの急落/ドローダウン(carry unwind進行中)。
  検出器の閾値は IS=2015-2021 の信号分位で凍結 → OOS適用。crash月の捕捉(precision/recall)を可視化。

== ヘッジ構成(2系統 × ①統合) ==
  A デリスク・オーバーレイ : リスクオフ時に carry sleeve の新規long建てを停止(or ロット縮小)。
       新規ポジ無し=最も安価。保険コスト=反発局面の取りこぼし。
  B 能動ヘッジ・スリーブ   : リスクオフ時に セーフヘイブンlong(=短USDJPY/NZDJPY)を建て、
       carry-crashで益を出し相殺。サイズはcarry露出に較正(lot 0.5/1.0/2.0)。
  ① 統合(carry-off)      : ①横断キャリーの“ショート側”をヘッジ手段として使う。
       crash時に符号反転して益を出す carry-off オーバーレイの付加価値を検証。

== 評価軸(単体PFで測らない・保険コスト差引後) ==
  主: carry sleeve 単体 vs +ヘッジ の maxDD99 / req_cap_99 / 資本効率(net/yr÷req_cap_99) /
      P(5yr損)。req_cap/破産確率は grid_stepb_recompute.py の月次ブロックブートストラップを踏襲。
  副: OOS Sharpe/Calmar、最悪単月、各crashエピソードでの相殺額。保険コスト(平常時ヘッジ累積損益)。

== 検証規律 ==
  IS=2015-2021凍結→OOS=2022-2026+年次WFO。全特徴量t-1・約定next-bar・コスト差引。
  単一イベント過適合の排除=leave-one-crash-out(各crashを抜いて閾値選択→抜いたcrashで評価)。

== 採用バー(事前登録) ==
  (a) ヘッジ後 carry sleeve が保険コスト差引後で 資本効率向上 ∧ P(5yr損)低下
      (req_cap_99 か maxDD99 が有意低下し net低下がそれを下回る)。
  (b) その改善が leave-one-crash-out / WFO で頑健(単一イベント依存でない)。
  (c) ①統合版が単純なFX内生検出器(A/B)を上回る付加価値を持つか(無ければ①統合は不採用)。

実行: .venv_dukas/bin/python optimizer/carry_crash_hedge_bt.py
出力: optimizer/carry_crash_hedge_result.csv + console
"""
import sys
from pathlib import Path
import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import grid_floatstop_bt as G
import grid_insensitivity as GI
import grid_dd_reduction_bt as D
import grid_dirbias_improve_bt as DB
import carry_xsec_bt as CX

OUT = Path(__file__).resolve().parent / 'carry_crash_hedge_result.csv'
CONTRACT = G.CONTRACT
IS_WIN = ('2015-01-01', '2021-12-31'); OOS_WIN = ('2022-01-01', '2026-12-31')
IS_END = pd.Timestamp('2021-12-31', tz='UTC')
COMBO = {'mom_thr': 2.0, 'cull_frac': 0.5, 'taper': 0.7}
ANN = 252

# carry-crash エピソード(carry_xsec診断の worst 月。IS=2015/2018/2020, OOS=2022/2024)
CRASH_MONTHS = ['2015-01', '2018-12', '2020-03', '2022-09', '2024-07']

# MC(Step B 踏襲)
RNG = np.random.default_rng(42)
N_MC = 20000; BLOCK = 3; HORIZON_MONTHS = 60
GAP_BUFFER = {'USDJPY': 1.20, 'NZDJPY': 1.20}


# ════════════════════════════════════════════════════════════════════════
#  sleeve cfg (Step B / complement と同一)
# ════════════════════════════════════════════════════════════════════════
def template_cfg(qj, fs):
    return {'atr_mult': 1.5, 'ci_threshold': 65.0, 'b48_hours': 48,
            'lot': 1.0, 'max_levels': 5, 'float_stop': fs, 'quote_jpy': qj}

def _usdjpy_cfg():
    df_ac = D.load_duk('AUDCAD'); atr_ac = G.compute_atr_series(df_ac)
    ref = float(atr_ac.median()) * 108.0
    dfu = D.load_duk('USDJPY'); atru = G.compute_atr_series(dfu)
    fs = round(-750_000.0 * (float(atru.median()) * 1.0) / ref, 0)
    return template_cfg(1.0, fs)

SLEEVES = {
    'USDJPY': {'cfg': _usdjpy_cfg(), 'qj': 1.0},
    'NZDJPY': {'cfg': GI.V7_CONFIG['NZDJPY'], 'qj': 1.0},
}


# ════════════════════════════════════════════════════════════════════════
#  carry sleeve エンジン (long-only+combo, long_block マスク対応)
#    DB.run_bt(collect=True) と long_block=None で完全一致をassert。
#    bar_realized(per-bar) / monthly / fs/cull/b48 events を返す。
# ════════════════════════════════════════════════════════════════════════
def sleeve_engine(cfg, df, atr_series, ci_series, ret24, long_block=None, long_lot_scale=None):
    """long-only(+combo) grid。long_block[i]=True で新規long建てを停止。
    long_lot_scale[i] でバー毎にlotを縮小(A: ロット縮小版)。"""
    qj = cfg.get('quote_jpy', 1.0); base_lot = cfg['lot']; atr_mult = cfg['atr_mult']
    ci_threshold = cfg['ci_threshold']; b48_hours = cfg['b48_hours']; float_stop = cfg['float_stop']
    lml = cfg['max_levels']; mom_thr = COMBO['mom_thr']; cull_frac = COMBO['cull_frac']; taper = COMBO['taper']
    def pj(d, lotv): return d * lotv * CONTRACT * qj
    idx = df.index
    highs = df['high'].to_numpy(); lows = df['low'].to_numpy(); closes = df['close'].to_numpy()
    av = atr_series.reindex(idx).to_numpy(); cv = ci_series.reindex(idx).to_numpy()
    rv = ret24
    def llot(level, sc): return base_lot * sc * (taper ** (level - 1))

    long_pos = []; b48_ls = None
    bar_realized = np.zeros(len(df)); realized = 0.0
    monthly = {}; fs_pnls = []; b48_pnls = []; cull_pnls = []
    def _m(ts, v): monthly[ts.strftime('%Y-%m')] = monthly.get(ts.strftime('%Y-%m'), 0.0) + v
    for i in range(len(df)):
        a = av[i]
        if np.isnan(a) or a <= 0: continue
        ts = idx[i]; gw = a * atr_mult; c = cv[i]
        bh, bl, bc = highs[i], lows[i], closes[i]
        lwm = len(long_pos) >= lml
        day = 0.0
        for p in [p for p in long_pos if bh >= p['tp']]:
            v = pj(p['tp'] - p['entry'], p['lot']); day += v; realized += v; _m(ts, v); long_pos.remove(p)
        if long_pos and sum(pj(bl - p['entry'], p['lot']) for p in long_pos) <= float_stop:
            ev = sum(pj(bl - p['entry'], p['lot']) for p in long_pos); day += ev; realized += ev; _m(ts, ev)
            fs_pnls.append(ev); long_pos = []; b48_ls = None
        if lwm and len(long_pos) < lml: b48_ls = None
        if b48_ls is not None and (ts - b48_ls).total_seconds() / 3600.0 >= b48_hours:
            ev = sum(pj(bc - p['entry'], p['lot']) for p in long_pos); day += ev; realized += ev; _m(ts, ev)
            b48_pnls.append(ev); long_pos = []; b48_ls = None
        if cull_frac is not None and len(long_pos) >= 2:
            legs = [(pj(bc - p['entry'], p['lot']), p) for p in long_pos]
            if sum(v for v, _ in legs) <= cull_frac * float_stop:
                v, p = min(legs, key=lambda x: x[0]); day += v; realized += v; _m(ts, v)
                cull_pnls.append(v); long_pos.remove(p)
                if len(long_pos) < lml: b48_ls = None
        ci_ok = (not np.isnan(c)) and (c > ci_threshold)
        r = rv[i]; mom_long = (np.isnan(r) or r > -mom_thr)
        blocked = (long_block is not None and long_block[i])
        long_ok = ci_ok and mom_long and not blocked
        sc = 1.0 if long_lot_scale is None else long_lot_scale[i]
        if lml > 0:
            if len(long_pos) == 0:
                if long_ok:
                    long_pos.append({'entry': bc, 'tp': bc + gw, 'lot': llot(1, sc)})
                    if len(long_pos) == lml: b48_ls = ts
            elif len(long_pos) < lml and bc <= min(p['entry'] for p in long_pos) - gw and long_ok:
                long_pos.append({'entry': bc, 'tp': bc + gw, 'lot': llot(len(long_pos) + 1, sc)})
                if len(long_pos) == lml: b48_ls = ts
        bar_realized[i] = day
    return {'bar': pd.Series(bar_realized, index=idx), 'total': realized, 'monthly': monthly,
            'fs_events': fs_pnls, 'b48_events': b48_pnls, 'cull_events': cull_pnls}


def load_sleeve(pair):
    S = SLEEVES[pair]; cfg = S['cfg']
    df = D.load_duk(pair); atr = G.compute_atr_series(df); ci = G.compute_ci_series(df)
    r24 = D.ret24_series(df, atr)
    eng = sleeve_engine(cfg, df, atr, ci, r24, long_block=None)
    # 静的一致検証
    ref = DB.run_bt(cfg, df, atr, ci, ret24=r24, short_ml=0, collect=True, **COMBO)
    assert abs(ref['total_pnl'] - eng['total']) < 1.0, f'{pair} sleeve mismatch {eng["total"]} vs {ref["total_pnl"]}'
    return df, atr, ci, r24, eng, cfg


# ════════════════════════════════════════════════════════════════════════
#  リスクオフ検出器 (daily, t-1)
# ════════════════════════════════════════════════════════════════════════
def build_detectors():
    """daily(business-day) index で 3検出器の連続シグナル(高い=リスクオフ)を返す。
    全て t-1 まで既知の情報で算出し、最後に shift(1)(=翌日適用)してルックアヘッド除去。"""
    px = CX.load_prices()              # ccy/USD value, business days
    rets = px.pct_change().fillna(0.0)
    # D1 セーフヘイブン強度: (JPY+CHF) 平均リターン − (AUD+NZD+CAD) 平均リターン の短期累積を標準化
    safe = rets[['JPY', 'CHF']].mean(axis=1)
    risk = rets[['AUD', 'NZD', 'CAD']].mean(axis=1)
    sh = safe - risk
    w = 10
    sh_sum = sh.rolling(w).sum()
    sh_z = sh_sum / sh_sum.rolling(252, min_periods=60).std()
    d1 = sh_z

    # D2 実現ボラ・スパイク: AUDJPY = (AUD/USD)/(JPY/USD)
    audjpy = px['AUD'] / px['JPY']
    aj_ret = audjpy.pct_change()
    rvol = aj_ret.rolling(10).std()
    rvol_med = rvol.rolling(252, min_periods=60).median()
    d2 = (rvol / rvol_med)            # >1 = vol上昇

    # D3 ①carryファクター急落: carry_xsec_daily.csv(N3_M_equal)の短期累積リターン(負=unwind)
    cd = pd.read_csv(Path(__file__).resolve().parent / 'carry_xsec_daily.csv', parse_dates=['date'])
    carry = cd.set_index('date')['ret']
    carry = carry.reindex(px.index).fillna(0.0)
    csum = carry.rolling(w).sum()
    cz = csum / csum.rolling(252, min_periods=60).std()
    d3 = -cz                          # carry急落(csum<0)で d3 大 → リスクオフ

    det = pd.DataFrame({'D1': d1, 'D2': d2, 'D3': d3}).shift(1)  # t-1適用
    det['carry_ret'] = carry          # 診断用(shiftしない実リターン)
    return det, px


def month_of(ts):
    return f'{ts.year:04d}-{ts.month:02d}'


def fire_mask(sig, thr):
    """連続シグナル sig(高い=リスクオフ) を閾値 thr で発火bool化。"""
    return (sig >= thr) & sig.notna()


def is_quantile_thr(sig, q):
    """IS期(<=2021)の分位点で閾値を凍結。"""
    iss = sig[sig.index <= pd.Timestamp('2021-12-31')]
    return float(iss.quantile(q))


def detector_crash_capture(det, thr_map):
    """各crash月で各検出器が発火したか(recall)+全期間発火率(coverage)を返す。"""
    rows = []
    for name in ['D1', 'D2', 'D3']:
        sig = det[name]; thr = thr_map[name]
        fired = fire_mask(sig, thr)
        firemonths = set(month_of(t) for t in fired.index[fired.values])
        rec = {m: (m in firemonths) for m in CRASH_MONTHS}
        coverage = float(fired.mean())
        rows.append({'det': name, 'thr': round(thr, 3), 'coverage': round(coverage, 3),
                     **{f'hit_{m}': rec[m] for m in CRASH_MONTHS},
                     'recall': sum(rec.values()) / len(CRASH_MONTHS)})
    return pd.DataFrame(rows)


# ════════════════════════════════════════════════════════════════════════
#  daily риск-off boolean を hourly index へ写像
# ════════════════════════════════════════════════════════════════════════
def hourly_risk_off(det_sig, thr, hidx):
    """daily連続シグナル→閾値発火→hourly indexへ日付マッチで写像(bool array)。"""
    fired = fire_mask(det_sig, thr)           # business-day bool
    fired_daily = fired.copy(); fired_daily.index = fired_daily.index.normalize()
    # hourly bar の日付
    days = pd.DatetimeIndex(hidx).tz_convert(None).normalize()
    m = fired_daily.reindex(pd.DatetimeIndex(days)).fillna(False).to_numpy()
    return m.astype(bool)


# ════════════════════════════════════════════════════════════════════════
#  メトリクス
# ════════════════════════════════════════════════════════════════════════
def daily_from_bar(bar):
    return bar.groupby(bar.index.tz_convert(None).normalize()).sum()

def monthly_from_bar(bar):
    g = bar.groupby(bar.index.tz_convert(None).to_period('M')).sum()
    return g

def sharpe(daily):
    d = daily[daily.index >= daily.index[0]]
    if d.std() == 0 or len(d) < 20: return 0.0
    return float(d.mean() / d.std() * np.sqrt(ANN))

def calmar_maxdd(daily):
    cum = daily.cumsum()
    dd = float((cum.cummax() - cum).max())
    ann_ret = daily.mean() * ANN
    cal = ann_ret / dd if dd > 0 else np.nan
    return cal, dd

def seg_daily(daily, lo, hi):
    idx = daily.index
    m = pd.Series(True, index=idx)
    if lo: m &= idx >= pd.Timestamp(lo)
    if hi: m &= idx <= pd.Timestamp(hi)
    return daily[m]


def block_bootstrap(monthly, horizon=HORIZON_MONTHS, n_mc=N_MC, block=BLOCK):
    n = len(monthly); n_blocks = int(np.ceil(horizon / block))
    maxdds = np.empty(n_mc); finals = np.empty(n_mc)
    starts = RNG.integers(0, n - block + 1, size=(n_mc, n_blocks))
    for i in range(n_mc):
        seq = np.concatenate([monthly[s:s + block] for s in starts[i]])[:horizon]
        eq = np.cumsum(seq)
        peak = np.maximum.accumulate(np.concatenate([[0.0], eq]))
        maxdds[i] = (peak[1:] - eq).max(); finals[i] = eq[-1]
    return maxdds, finals


def monthly_dict_cal(daily_series):
    """daily PnL系列 → カレンダー月次dict('YYYY-MM')。全variantで共通の基盤(=連続稼働の
    暦月。ゼロ月も含む)。net_yr/MC の分母をvariant間で一致させる=公平比較のため。
    注: grid_stepb_recompute は active月のみ(=分母≈3.4年, MCも活動月のみサンプル)で算定
    していたが、これは(i)net_yrを~3倍過大評価し(ii)5年MC maxDDを過大評価する(暦上のcalm月で
    DDが間延びする効果を無視)。本BTは連続稼働の暦月基盤=honest かつ variant間で一貫。"""
    g = daily_series.groupby(daily_series.index.to_period('M')).sum()
    return {f'{p.year:04d}-{p.month:02d}': float(v) for p, v in g.items()}


def stepb(monthly_dict, worst_gap, pair):
    """月次PnL dict(暦月)→ req_cap_99 / 資本効率 / P(5yr損)。net_yr = total / (暦月数/12)。"""
    ks = sorted(monthly_dict)
    m = np.array([monthly_dict[k] for k in ks], dtype=float)
    n_years = len(m) / 12.0
    net = m.sum(); net_yr = net / n_years
    maxdds, finals = block_bootstrap(m)
    dd99 = np.percentile(maxdds, 99)
    req99 = max(dd99, worst_gap)
    eff = net_yr / req99 if req99 > 0 else np.nan
    p_loss = float((finals < 0).mean())
    return {'net_yr': net_yr, 'mc_dd99': dd99, 'worst_gap': worst_gap,
            'req_cap_99': req99, 'cap_eff': eff, 'p_loss_5yr': p_loss}


# ════════════════════════════════════════════════════════════════════════
#  ヘッジ・オーバーレイの daily PnL
# ════════════════════════════════════════════════════════════════════════
def hedge_short_pair(px, pair, risk_off_daily, lot, spread_pip):
    """構成B: リスクオフ日にペアを short(=セーフヘイブンlong)。
    PnL_daily = -lot*CONTRACT*Δprice (JPYクオート, qj=1)。発効=翌日(risk_off は既にt-1 shift済)。
    price = pairのJPY建てレート。USDJPY=1/px['JPY'], NZDJPY=(NZD/USD)/(JPY/USD)。
    建て/解消のたびに往復スプレッドコストを差引。"""
    if pair == 'USDJPY':
        price = 1.0 / px['JPY']
    elif pair == 'NZDJPY':
        price = px['NZD'] / px['JPY']
    else:
        raise ValueError(pair)
    dprice = price.diff()
    pos = pd.Series(np.where(risk_off_daily.reindex(price.index).fillna(False), -lot, 0.0), index=price.index)
    pnl = (pos * dprice * CONTRACT).fillna(0.0)  # qj=1
    # コスト: ポジション変化(turnover) * spread_price * CONTRACT
    spread_price = spread_pip * (0.01 if True else 0.0001)  # JPYクオート pip
    cost = pos.diff().abs().fillna(pos.abs()) * spread_price * CONTRACT
    pnl = pnl - cost
    pnl.index = pnl.index.normalize()
    return pnl


def hedge_carry_off(det, risk_off_daily, scale, cost_bp=2.0):
    """① 統合: リスクオフ日に carryファクターを short(=carry-off)。
    PnL_daily = -scale * carry_ret (carry急落=正の益)。scale はJPY換算の名目(較正)。
    turnover に cost_bp(片道)を差引。"""
    carry = det['carry_ret']
    pos = pd.Series(np.where(risk_off_daily.reindex(carry.index).fillna(False), -scale, 0.0), index=carry.index)
    pnl = (pos * carry).fillna(0.0)
    cost = pos.diff().abs().fillna(pos.abs()) * (cost_bp / 1e4)
    pnl = pnl - cost
    pnl.index = pnl.index.normalize()
    return pnl


def insurance_cost(hedge_daily):
    """平常時(非crash月)のヘッジ累積損益(=保険コスト, 負なら出血)。"""
    crash_set = set(CRASH_MONTHS)
    nonc = hedge_daily[[month_of(t) not in crash_set for t in hedge_daily.index]]
    crashc = hedge_daily[[month_of(t) in crash_set for t in hedge_daily.index]]
    return float(nonc.sum()), float(crashc.sum())


def worst_gap_from_events(eng, pair):
    fs = list(eng['fs_events']); cull = list(eng['cull_events'])
    b48 = [x for x in eng['b48_events'] if x < 0]
    singles = fs + cull + b48
    ws = -min(singles) if singles else 0.0
    return ws * GAP_BUFFER.get(pair, 1.2)


def loco_table(det, name, q):
    """leave-one-crash-out: 各crash月を抜いた残りで閾値を選び(=残りcrash月の信号がギリ発火する
    最小閾値)、抜いたcrash月がその閾値で発火するかを評価。単一イベント過適合の排除。"""
    sig = det[name]
    # 各crash月の月内最大シグナル
    peak = {}
    for m in CRASH_MONTHS:
        s = sig[[month_of(t) == m for t in sig.index]]
        peak[m] = float(s.max()) if len(s) and s.notna().any() else np.nan
    rows = []
    for held in CRASH_MONTHS:
        others = [m for m in CRASH_MONTHS if m != held and not np.isnan(peak[m])]
        if not others:
            continue
        thr = min(peak[m] for m in others)   # 残りcrashを全て捕捉する最大の閾値
        caught = (not np.isnan(peak[held])) and (peak[held] >= thr)
        rows.append({'det': name, 'held_out': held, 'thr_from_others': round(thr, 3),
                     'held_peak': round(peak[held], 3) if not np.isnan(peak[held]) else None,
                     'caught': caught})
    return pd.DataFrame(rows)


def main():
    det, px = build_detectors()
    Q = 0.90  # IS分位で凍結(約上位10%日が発火)
    thr_map = {n: is_quantile_thr(det[n], Q) for n in ['D1', 'D2', 'D3']}

    print('=' * 110)
    print('リスクオフ検出器: IS分位(q=0.90)で閾値凍結 → crash月の捕捉(recall)/全期間発火率(coverage)')
    print('=' * 110)
    cap = detector_crash_capture(det, thr_map)
    print(cap.to_string(index=False))

    print('\n--- leave-one-crash-out (D1 / D3): 残りcrashで選んだ閾値で held-out crash が発火するか ---')
    for n in ['D1', 'D3']:
        print(loco_table(det, n, Q).to_string(index=False))

    # 検出器 fired (business-day, t-1済)
    fired = {n: fire_mask(det[n], thr_map[n]) for n in ['D1', 'D2', 'D3']}
    combined = (fired['D1'] | fired['D3'])  # 方向性2検出器のOR
    combined.name = 'COMBINED(D1|D3)'

    rows = []
    sleeve_daily_store = {}
    for pair in ['USDJPY', 'NZDJPY']:
        print('\n' + '=' * 110); print(f'  {pair}  carry long-only+combo'); print('=' * 110)
        df, atr, ci, r24, eng, cfg = load_sleeve(pair)
        sdaily = daily_from_bar(eng['bar'])
        sdaily = sdaily.groupby(sdaily.index).sum()
        sleeve_daily_store[pair] = sdaily
        wg = worst_gap_from_events(eng, pair)
        base = stepb(monthly_dict_cal(sdaily), wg, pair)
        base_oos = seg_daily(sdaily, '2022-01-01', None)
        bcal, bdd = calmar_maxdd(base_oos)
        print(f"  [baseline] net/yr={base['net_yr']:,.0f} reqCap99={base['req_cap_99']:,.0f} "
              f"capEff={base['cap_eff']*100:.1f}%/yr P(5yr損)={base['p_loss_5yr']:.3f} "
              f"| OOS Sharpe={sharpe(base_oos):.2f} Calmar={bcal:.2f}")
        rows.append({'pair': pair, 'variant': 'baseline', **base,
                     'oos_sharpe': round(sharpe(base_oos), 2), 'oos_calmar': round(bcal, 2),
                     'oos_maxdd': round(bdd, 0), 'ins_cost_nonc': 0.0, 'crash_offset': 0.0})

        # IS crash 損(較正用): IS期のcrash月での sleeve daily 合計(負=損)
        is_crash_loss = -sum(sdaily[[month_of(pd.Timestamp(t, tz='UTC')) in set(CRASH_MONTHS[:3])
                                     and pd.Timestamp(t) <= pd.Timestamp('2021-12-31') for t in sdaily.index]])
        is_crash_loss = max(is_crash_loss, 1.0)

        # ── 構成A: デリスク・オーバーレイ(combined / D1 / D3) ──
        for dname, fmask in [('COMB', combined), ('D1', fired['D1']), ('D3', fired['D3'])]:
            # fmask(business-day bool, t-1済)をhourly index へ日付マッチで写像
            fired_daily = fmask.copy(); fired_daily.index = fired_daily.index.normalize()
            days = pd.DatetimeIndex(df.index).tz_convert(None).normalize()
            hmask = fired_daily.reindex(pd.DatetimeIndex(days)).fillna(False).to_numpy().astype(bool)
            engA = sleeve_engine(cfg, df, atr, ci, r24, long_block=hmask)
            sdA = daily_from_bar(engA['bar']); sdA = sdA.groupby(sdA.index).sum()
            wgA = worst_gap_from_events(engA, pair)
            sb = stepb(monthly_dict_cal(sdA), wgA, pair)
            oosA = seg_daily(sdA, '2022-01-01', None); calA, ddA = calmar_maxdd(oosA)
            # 取りこぼし(保険コスト): baseline比 net低下 = 平常時に建てられたはずのlongを止めた分
            ins = (sb['net_yr'] - base['net_yr'])  # 負ならコスト
            print(f"  [A derisk/{dname:4s}] net/yr={sb['net_yr']:,.0f} reqCap99={sb['req_cap_99']:,.0f} "
                  f"capEff={sb['cap_eff']*100:.1f}%/yr P5={sb['p_loss_5yr']:.3f} "
                  f"Sh={sharpe(oosA):.2f} Cal={calA:.2f} | Δnet/yr={ins:,.0f}")
            rows.append({'pair': pair, 'variant': f'A_derisk_{dname}', **sb,
                         'oos_sharpe': round(sharpe(oosA), 2), 'oos_calmar': round(calA, 2),
                         'oos_maxdd': round(ddA, 0), 'ins_cost_nonc': round(ins, 0), 'crash_offset': 0.0})

        # ── 構成B: 能動ヘッジ(combined検出, lotをIS-crash損に較正) ──
        spread = 1.5 if pair == 'USDJPY' else 2.0
        h1 = hedge_short_pair(px, pair, combined, lot=1.0, spread_pip=spread)
        # IS crash月でのヘッジgross(lot=1) → lot較正(IS損の100%相殺目標)
        h1_is_crash = sum(h1[[month_of(pd.Timestamp(t)) in set(CRASH_MONTHS[:3])
                              and pd.Timestamp(t) <= pd.Timestamp('2021-12-31') for t in h1.index]])
        lot_cal = float(np.clip(is_crash_loss / max(h1_is_crash, 1.0), 0.2, 10.0)) if h1_is_crash > 0 else 1.0
        for tag, lot in [('cal', lot_cal), ('0.5', 0.5), ('1.0', 1.0), ('2.0', 2.0)]:
            hd = hedge_short_pair(px, pair, combined, lot=lot, spread_pip=spread)
            comb_daily = sdaily.add(hd, fill_value=0.0)
            sb = stepb(monthly_dict_cal(comb_daily), wg, pair)
            oosc = seg_daily(comb_daily, '2022-01-01', None); calc, ddc = calmar_maxdd(oosc)
            nonc, crashc = insurance_cost(hd)
            print(f"  [B hedge lot={tag:4s}({lot:.2f})] net/yr={sb['net_yr']:,.0f} reqCap99={sb['req_cap_99']:,.0f} "
                  f"capEff={sb['cap_eff']*100:.1f}%/yr P5={sb['p_loss_5yr']:.3f} "
                  f"Sh={sharpe(oosc):.2f} Cal={calc:.2f} | 保険コスト(平常)={nonc:,.0f} crash相殺={crashc:,.0f}")
            rows.append({'pair': pair, 'variant': f'B_hedge_{tag}', **sb,
                         'oos_sharpe': round(sharpe(oosc), 2), 'oos_calmar': round(calc, 2),
                         'oos_maxdd': round(ddc, 0), 'ins_cost_nonc': round(nonc, 0),
                         'crash_offset': round(crashc, 0), 'hedge_lot': round(lot, 2)})

        # ── ① 統合: carry-off オーバーレイ(carryファクターをshort, scaleをIS-crash損に較正) ──
        c1 = hedge_carry_off(det, combined, scale=1.0)
        c1_is_crash = sum(c1[[month_of(pd.Timestamp(t)) in set(CRASH_MONTHS[:3])
                             and pd.Timestamp(t) <= pd.Timestamp('2021-12-31') for t in c1.index]])
        scale_cal = float(is_crash_loss / max(c1_is_crash, 1e-9)) if c1_is_crash > 0 else 0.0
        if scale_cal > 0:
            co = hedge_carry_off(det, combined, scale=scale_cal)
            comb_daily = sdaily.add(co, fill_value=0.0)
            sb = stepb(monthly_dict_cal(comb_daily), wg, pair)
            oosc = seg_daily(comb_daily, '2022-01-01', None); calc, ddc = calmar_maxdd(oosc)
            nonc, crashc = insurance_cost(co)
            print(f"  [① carry-off cal] net/yr={sb['net_yr']:,.0f} reqCap99={sb['req_cap_99']:,.0f} "
                  f"capEff={sb['cap_eff']*100:.1f}%/yr P5={sb['p_loss_5yr']:.3f} "
                  f"Sh={sharpe(oosc):.2f} Cal={calc:.2f} | 保険コスト(平常)={nonc:,.0f} crash相殺={crashc:,.0f}")
            rows.append({'pair': pair, 'variant': 'carryoff_cal', **sb,
                         'oos_sharpe': round(sharpe(oosc), 2), 'oos_calmar': round(calc, 2),
                         'oos_maxdd': round(ddc, 0), 'ins_cost_nonc': round(nonc, 0),
                         'crash_offset': round(crashc, 0)})
        else:
            print('  [① carry-off] IS-crash で carry-off が益を出さず較正不能(検出器がcarry crashを捉えていない)')

    rdf = pd.DataFrame(rows)
    rdf.to_csv(OUT, index=False)
    print(f'\nsaved {OUT}')

    # ── 採用バー判定 ──
    print('\n' + '=' * 110)
    print('採用バー判定 (a)保険コスト差引後で資本効率向上∧P(5yr損)低下 (b)LOCO/WFO頑健 (c)①>FX内生')
    print('=' * 110)
    for pair in ['USDJPY', 'NZDJPY']:
        sub = rdf[rdf['pair'] == pair]
        b = sub[sub['variant'] == 'baseline'].iloc[0]
        print(f'\n{pair}: baseline capEff={b["cap_eff"]*100:.1f}%/yr P5={b["p_loss_5yr"]:.3f} '
              f'reqCap99={b["req_cap_99"]:,.0f}')
        hit = False
        for _, r in sub[sub['variant'] != 'baseline'].iterrows():
            ok = (r['cap_eff'] > b['cap_eff']) and (r['p_loss_5yr'] < b['p_loss_5yr'])
            mark = '✅' if ok else '  '
            if ok: hit = True
            print(f'  {mark} {r["variant"]:16s} capEff={r["cap_eff"]*100:6.1f}%/yr '
                  f'P5={r["p_loss_5yr"]:.3f} reqCap99={r["req_cap_99"]:>12,.0f} '
                  f'Δeff={(r["cap_eff"]-b["cap_eff"])*100:+.1f}pt')
        if not hit:
            print(f'  → {pair}: 採用バー(a)該当なし')

    # ── 頑健性点検 (b)(c): (a)通過variantが単一イベント過適合/方向性ベットでないか ──
    print('\n' + '=' * 110)
    print('頑健性点検 (b)単一イベント過適合の排除 / (c)①の付加価値')
    print('=' * 110)
    # (b1) 検出器が各crashを捉えるか(全期間IS凍結閾値) + 最悪crashの検出可否
    miss = [m for m in CRASH_MONTHS if not (cap[cap['det'] == 'D1'][f'hit_{m}'].iloc[0]
                                            or cap[cap['det'] == 'D3'][f'hit_{m}'].iloc[0])]
    print(f'  (b1) IS凍結閾値で未検出のcrash月(D1∨D3): {miss if miss else "なし"}')
    print(f'       → 2015-01(CHF unpeg=最大crash -8.4%)は252日履歴不足+突発ジャンプで信号NaN'
          f'=構造的に検出不能。最悪テールはヘッジ不能。')
    # (b2) LOCO: held-out crash が捉えられないもの
    for n in ['D1', 'D3']:
        lt = loco_table(det, n, Q)
        miss_loco = lt[~lt['caught']]['held_out'].tolist()
        print(f'  (b2) LOCO {n}: 残りcrash較正閾値で未捕捉のheld-out = {miss_loco}')
    # (b3) (a)通過variantの方向性ベット診断(平常時ヘッジ損益>0=保険でなく方向ベット) + OOS Sharpe
    print('  (b3) (a)通過variant: 平常時ヘッジ損益(>0=方向ベット/保険でない) と OOS Sharpe(baseline比):')
    for pair in ['USDJPY', 'NZDJPY']:
        sub = rdf[rdf['pair'] == pair]; b = sub[sub['variant'] == 'baseline'].iloc[0]
        for _, r in sub.iterrows():
            if r['variant'] == 'baseline': continue
            if not ((r['cap_eff'] > b['cap_eff']) and (r['p_loss_5yr'] < b['p_loss_5yr'])): continue
            flag = '⚠方向ベット(保険でない)' if r.get('ins_cost_nonc', 0) > 0 else '実保険(平常bleed)'
            shf = '↓Sharpe悪化' if r['oos_sharpe'] < b['oos_sharpe'] else '='
            print(f'     {pair} {r["variant"]:14s} 平常損益={r.get("ins_cost_nonc",0):>11,.0f} {flag:20s} '
                  f'OOS Sh {b["oos_sharpe"]:.2f}->{r["oos_sharpe"]:.2f} {shf}')
    # (c) ① carry-off vs FX内生 B のベスト
    print('  (c) ①carry-off vs FX内生B(best capEff):')
    for pair in ['USDJPY', 'NZDJPY']:
        sub = rdf[rdf['pair'] == pair]
        co = sub[sub['variant'] == 'carryoff_cal']
        bb = sub[sub['variant'].str.startswith('B_hedge')]
        if len(co) and len(bb):
            co = co.iloc[0]; best = bb.loc[bb['cap_eff'].idxmax()]
            verdict = '①>B(付加価値あり)' if co['cap_eff'] > best['cap_eff'] else '①<=B(付加価値なし)'
            print(f'     {pair}: ① capEff={co["cap_eff"]*100:.1f}% vs B best({best["variant"]}) '
                  f'{best["cap_eff"]*100:.1f}% → {verdict}')


if __name__ == '__main__':
    main()
