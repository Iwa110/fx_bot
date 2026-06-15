"""
grid_complement_drift_bt.py - Grid非発火窓に対する「方向ドリフト補完スリーブ」の検証。

== 経済的仮説(なぜGrid非発火窓に構造的に効くのか) ==
Grid(平均回帰)は CI(Choppiness)が高いレンジ局面でのみ建てる。CIが低い局面=
トレンド/方向性局面では Grid は休眠(idle)するか、開いたラダーがトレンドに轢かれて
出血(float-stop/B48/cull)する。診断済(project_grid_insensitivity_complement_20260608):
Gridは大半の日が非発火で、非発火日のGrid損益はむしろ負。

今回のGrid展開で新たに確定した「方向バイアス=各ペアに構造的な long ドリフト
(JPYクロスのキャリー / 資源・欧州クロスの上方ドリフト)」を起点にする。
Grid非発火窓(CI低=トレンド局面)は、まさにこの構造的ドリフトが出る局面のはず。
→ 「Gridがレンジを刈り、補完がトレンドを刈る」分業が成立するかを検証する。

== 既に失敗した案との違い(繰り返さない) ==
案A(CI逆ゲート・汎用Donchian breakout, マルチペア)は Close。汎用trend-followingは
"方向を持たない"のでエッジ無し(PF0.54-0.79)。本案の新規性=**ペア固有の確定ドリフト
方向(long)に限定**し、**Grid非発火窓(CI<=閾値)に限定**して建てる点。
低相関は採用理由にしない(教訓4例)。採用は単体OOS頑健 ∧ ブレンド改善の両立のみ。

== 検証する3スリーブ ==
  S1 ドリフト・ロング: CI<=ci_th(Grid休眠)かつ上昇トレンド(close>SMA_N, t-1)で long。
       vol-target sizing(risk一定)+ chandelier ATR crash stop + trend-break/CI復帰 exit。
  S2 片側不在フェード: long_only/regime_short で恒常的に空く short 方向に、過熱時
       (close>>SMA, z-score高)に限定フェード。エッジ無ければ即Close。
  S3 常時稼働(対照): CIゲート無しで S1 と同条件 long。"いつ張るか(CIゲート)"が肝かを確認。

== 検証規律 ==
  - データ data/{PAIR}_1h_dukas.csv。特徴量t-1、約定next-bar open、スプレッド差引。
  - IS=2015-2021凍結 → OOS=2022-2026 + 年次WFO(2022-25)。
  - 採用バー(事前登録・低相関不可):
    (a) スリーブ単体 OOS PF>1.2 ∧ Sharpe>0 ∧ WFO各fold正、かつ
    (b) Grid(v8)+補完ブレンドが Grid単体比で OOS Sharpe向上 ∧ maxDD非悪化。
    FULL/IS のみの改善は不可。過適合signature(IS↔OOS逆相関/薄標本/単一イベント)を点検。

実行: .venv_dukas/bin/python optimizer/grid_complement_drift_bt.py
出力: grid_complement_drift_result.csv + console
"""
import numpy as np, pandas as pd
from pathlib import Path
import grid_floatstop_bt as G
import grid_insensitivity as GI
import grid_dd_reduction_bt as D
import grid_dirbias_improve_bt as DB

OUT = Path(__file__).resolve().parent / 'grid_complement_drift_result.csv'
CONTRACT = G.CONTRACT
IS_WIN = ('2015-01-01', '2021-12-31'); OOS_WIN = ('2022-01-01', '2026-12-31')
WFO_YEARS = [2022, 2023, 2024, 2025]
RISK_JPY = 50_000.0   # vol-target: 1トレードのstop到達損≈この額(scaleはPF/Sharpe不変)

# ── 5ペアの v8 確定 Grid 構成 + ドリフト方向 + pip/スプレッド ──
# dir_mode: 'regime' = AUDCAD型(R-SMA1200 short_block_up + combo) / 'long_only' = carry型(+combo)
# pip_size: JPYクオート=0.01 それ以外=0.0001。spread_pip = 往復コスト(保守)。
def template_cfg(qj, fs):
    return {'atr_mult': 1.5, 'ci_threshold': 65.0, 'b48_hours': 48,
            'lot': 1.0, 'max_levels': 5, 'float_stop': fs, 'quote_jpy': qj}

# USDJPY fs は allpairs と同じく AUDCAD ATR中央値×qj 基準でスケール
def _usdjpy_cfg():
    df_ac = D.load_duk('AUDCAD'); atr_ac = G.compute_atr_series(df_ac)
    ref = float(atr_ac.median()) * 108.0
    dfu = D.load_duk('USDJPY'); atru = G.compute_atr_series(dfu)
    fs = round(-750_000.0 * (float(atru.median()) * 1.0) / ref, 0)
    return template_cfg(1.0, fs)

PAIRS = {
    'AUDCAD': {'cfg': D.AUDCAD, 'dir': 'regime', 'pip': 0.0001, 'spread_pip': 2.0},
    'EURGBP': {'cfg': D.EURGBP, 'dir': 'regime', 'pip': 0.0001, 'spread_pip': 1.5},
    'AUDNZD': {'cfg': template_cfg(90.0, round(-750_000.0 * 90.0 / 108.0, 0)),
               'dir': 'regime', 'pip': 0.0001, 'spread_pip': 2.5},
    'USDJPY': {'cfg': _usdjpy_cfg(), 'dir': 'long_only', 'pip': 0.01, 'spread_pip': 1.5},
    'NZDJPY': {'cfg': GI.V7_CONFIG['NZDJPY'], 'dir': 'long_only', 'pip': 0.01, 'spread_pip': 2.0},
}


# ════════════════════════════════════════════════════════════════════════
#  Grid v8 の per-bar realized PnL 系列 (ブレンド用)
# ════════════════════════════════════════════════════════════════════════
def grid_bar_pnl(pair, P):
    """v8確定構成(dir_mode別)で per-bar realized PnL系列(JPY)を返す。
    DB.run_bt と同じロジックを per-bar 記録付きで再実装し、full netが一致することをassert。"""
    cfg = P['cfg']; df = D.load_duk(pair); atr = G.compute_atr_series(df); ci = G.compute_ci_series(df)
    r24 = D.ret24_series(df, atr)
    kw = dict(ret24=r24, mom_thr=2.0, cull_frac=0.5, taper=0.7)
    if P['dir'] == 'long_only':
        kw['short_ml'] = 0
    else:
        reg = DB.sma_regime(df, 1200); kw['short_block_up'] = reg

    qj = cfg.get('quote_jpy', 1.0); base_lot = cfg['lot']; atr_mult = cfg['atr_mult']
    ci_th = cfg['ci_threshold']; b48_hours = cfg['b48_hours']; float_stop = cfg['float_stop']
    lml = 0 if kw.get('short_ml') == 0 else cfg['max_levels']  # placeholder; set below
    lml = cfg['max_levels']; sml = 0 if kw.get('short_ml') == 0 else cfg['max_levels']
    mom_thr = kw['mom_thr']; cull_frac = kw['cull_frac']; taper = kw['taper']
    sbu = kw.get('short_block_up')

    def pj(d, lotv): return d * lotv * CONTRACT * qj
    def llot(level): return base_lot * (taper ** (level - 1))
    def slot(level): return base_lot * (taper ** (level - 1))
    idx = df.index
    highs = df['high'].to_numpy(); lows = df['low'].to_numpy(); closes = df['close'].to_numpy()
    av = atr.reindex(idx).to_numpy(); cv = ci.reindex(idx).to_numpy(); rv = r24

    long_pos, short_pos = [], []; b48_ls = b48_ss = None
    bar_realized = np.zeros(len(df)); total = 0.0
    for i in range(len(df)):
        a = av[i]
        if np.isnan(a) or a <= 0: continue
        ts = idx[i]; gw = a * atr_mult; c = cv[i]
        bh, bl, bc = highs[i], lows[i], closes[i]
        lwm = len(long_pos) >= lml; swm = len(short_pos) >= sml
        day = 0.0
        for p in [p for p in long_pos if bh >= p['tp']]:
            v = pj(p['tp'] - p['entry'], p['lot']); day += v; long_pos.remove(p)
        for p in [p for p in short_pos if bl <= p['tp']]:
            v = pj(p['entry'] - p['tp'], p['lot']); day += v; short_pos.remove(p)
        if long_pos and sum(pj(bl - p['entry'], p['lot']) for p in long_pos) <= float_stop:
            day += sum(pj(bl - p['entry'], p['lot']) for p in long_pos); long_pos = []; b48_ls = None
        if short_pos and sum(pj(p['entry'] - bh, p['lot']) for p in short_pos) <= float_stop:
            day += sum(pj(p['entry'] - bh, p['lot']) for p in short_pos); short_pos = []; b48_ss = None
        if lwm and len(long_pos) < lml: b48_ls = None
        if swm and len(short_pos) < sml: b48_ss = None
        if b48_ls is not None and (ts - b48_ls).total_seconds() / 3600.0 >= b48_hours:
            day += sum(pj(bc - p['entry'], p['lot']) for p in long_pos); long_pos = []; b48_ls = None
        if b48_ss is not None and (ts - b48_ss).total_seconds() / 3600.0 >= b48_hours:
            day += sum(pj(p['entry'] - bc, p['lot']) for p in short_pos); short_pos = []; b48_ss = None
        if cull_frac is not None:
            if len(long_pos) >= 2:
                legs = [(pj(bc - p['entry'], p['lot']), p) for p in long_pos]
                if sum(v for v, _ in legs) <= cull_frac * float_stop:
                    v, p = min(legs, key=lambda x: x[0]); day += v; long_pos.remove(p)
                    if len(long_pos) < lml: b48_ls = None
            if len(short_pos) >= 2:
                legs = [(pj(p['entry'] - bc, p['lot']), p) for p in short_pos]
                if sum(v for v, _ in legs) <= cull_frac * float_stop:
                    v, p = min(legs, key=lambda x: x[0]); day += v; short_pos.remove(p)
                    if len(short_pos) < sml: b48_ss = None
        ci_ok = (not np.isnan(c)) and (c > ci_th)
        r = rv[i]
        mom_long = (np.isnan(r) or r > -mom_thr); mom_short = (np.isnan(r) or r < mom_thr)
        reg_short = True
        if sbu is not None and sbu[i] == True: reg_short = False
        long_ok = ci_ok and mom_long; short_ok = ci_ok and mom_short and reg_short
        if lml > 0:
            if len(long_pos) == 0:
                if long_ok:
                    long_pos.append({'entry': bc, 'tp': bc + gw, 'lot': llot(1)})
                    if len(long_pos) == lml: b48_ls = ts
            elif len(long_pos) < lml and bc <= min(p['entry'] for p in long_pos) - gw and long_ok:
                long_pos.append({'entry': bc, 'tp': bc + gw, 'lot': llot(len(long_pos) + 1)})
                if len(long_pos) == lml: b48_ls = ts
        if sml > 0:
            if len(short_pos) == 0:
                if short_ok:
                    short_pos.append({'entry': bc, 'tp': bc - gw, 'lot': slot(1)})
                    if len(short_pos) == sml: b48_ss = ts
            elif len(short_pos) < sml and bc >= max(p['entry'] for p in short_pos) + gw and short_ok:
                short_pos.append({'entry': bc, 'tp': bc - gw, 'lot': slot(len(short_pos) + 1)})
                if len(short_pos) == sml: b48_ss = ts
        bar_realized[i] = day; total += day

    # 静的一致検証(DB.run_bt full net と一致)
    ref = DB.run_bt(cfg, df, atr, ci, **kw)
    assert abs(ref['total_pnl'] - total) < 1.0, f'{pair} grid_bar_pnl mismatch {total} vs {ref["total_pnl"]}'
    return pd.Series(bar_realized, index=idx), df, atr, ci


# ════════════════════════════════════════════════════════════════════════
#  補完スリーブ・エンジン
# ════════════════════════════════════════════════════════════════════════
def sleeve_bar_pnl(P, df, atr, ci, sma_n=480, stop_mult=3.0, side='long',
                   gate='ci_idle', z_n=120, z_thr=2.0, tmax_h=None):
    """方向ドリフト補完スリーブ。per-bar realized PnL系列(JPY)とトレード一覧を返す。
    side='long' : ドリフト方向ロング(S1/S3)。side='short_fade': 片側不在フェード(S2)。
    gate='ci_idle' : CI(t-1)<=ci_th(=Grid休眠窓)でのみ建てる。'always': ゲート無し(対照)。
    特徴量は全て t-1 shift、約定は次バー open。vol-target sizing、chandelier ATR crash stop。"""
    cfg = P['cfg']; qj = cfg['quote_jpy']; ci_th = cfg['ci_threshold']
    pip = P['pip']; cost_price = P['spread_pip'] * pip   # 往復コスト(price)
    idx = df.index
    opens = df['open'].to_numpy(); highs = df['high'].to_numpy()
    lows = df['low'].to_numpy(); closes = df['close'].to_numpy()
    av = atr.reindex(idx).to_numpy(); cv = ci.reindex(idx).to_numpy()
    sma = df['close'].rolling(sma_n, min_periods=sma_n).mean().to_numpy()
    # z-score(過熱) = (close - sma_z)/std_z  (short_fade用)
    sma_z = df['close'].rolling(z_n, min_periods=z_n).mean()
    std_z = df['close'].rolling(z_n, min_periods=z_n).std()
    zsc = ((df['close'] - sma_z) / std_z).to_numpy()

    bar_pnl = np.zeros(len(df)); trades = []
    pos = None  # dict: entry, lot, dir(+1/-1), peak/trough, entry_i, stop
    for i in range(1, len(df)):
        a1 = av[i - 1]  # t-1 ATR(sizing/stop基準)
        # ── 既存ポジションの更新・exit判定(このバー内) ──
        if pos is not None:
            d = pos['dir']
            # chandelier trail 更新(t-1までの極値で)
            if d > 0:
                pos['peak'] = max(pos['peak'], highs[i - 1])
                pos['stop'] = pos['peak'] - stop_mult * pos['atr']
            else:
                pos['trough'] = min(pos['trough'], lows[i - 1])
                pos['stop'] = pos['trough'] + stop_mult * pos['atr']
            exit_px = None; reason = None
            # crash stop(intrabar、約定はstop価格=保守的に貫通せず)
            if d > 0 and lows[i] <= pos['stop']:
                exit_px = min(opens[i], pos['stop']); reason = 'stop'
            elif d < 0 and highs[i] >= pos['stop']:
                exit_px = max(opens[i], pos['stop']); reason = 'stop'
            else:
                # trend-break / CI復帰(レンジ回帰=Gridへ返す) / time-stop は t-1 判定→次バーopen決済
                sma1 = sma[i - 1]; ci1 = cv[i - 1]
                trend_break = (d > 0 and closes[i - 1] < sma1) or (d < 0 and closes[i - 1] > sma1)
                ci_back = (not np.isnan(ci1)) and (ci1 > ci_th)  # レンジ回帰
                timeout = tmax_h is not None and (i - pos['entry_i']) >= tmax_h
                if (not np.isnan(sma1) and trend_break) or ci_back or timeout:
                    exit_px = opens[i]; reason = 'trend' if trend_break else ('ci' if ci_back else 'time')
            if exit_px is not None:
                gross = (exit_px - pos['entry']) * d * pos['lot'] * CONTRACT * qj
                net = gross - cost_price * pos['lot'] * CONTRACT * qj
                bar_pnl[i] += net
                trades.append({'entry_ts': idx[pos['entry_i']], 'exit_ts': idx[i],
                               'dir': d, 'net': net, 'reason': reason, 'bars': i - pos['entry_i']})
                pos = None

        if pos is not None:
            continue
        # ── 新規entry判定(t-1特徴量 → 次バーopen=このバーopen で約定) ──
        if np.isnan(a1) or a1 <= 0:
            continue
        sma1 = sma[i - 1]; ci1 = cv[i - 1]
        if np.isnan(sma1):
            continue
        ci_idle = np.isnan(ci1) or (ci1 <= ci_th)   # Grid休眠窓
        gate_ok = (gate == 'always') or (gate == 'ci_idle' and ci_idle)
        if not gate_ok:
            continue
        if side == 'long':
            uptrend = closes[i - 1] > sma1
            if uptrend:
                lot = RISK_JPY / (stop_mult * a1 * CONTRACT * qj)
                pos = {'entry': opens[i], 'lot': lot, 'dir': 1, 'atr': a1,
                       'peak': highs[i - 1], 'trough': lows[i - 1], 'entry_i': i,
                       'stop': highs[i - 1] - stop_mult * a1}
        elif side == 'short_fade':
            # 片側不在(short)を過熱時にフェード: 上昇トレンド過熱(close>>sma, z高)で short
            z1 = zsc[i - 1]
            overheat = (not np.isnan(z1)) and (z1 >= z_thr) and (closes[i - 1] > sma1)
            if overheat:
                lot = RISK_JPY / (stop_mult * a1 * CONTRACT * qj)
                pos = {'entry': opens[i], 'lot': lot, 'dir': -1, 'atr': a1,
                       'peak': highs[i - 1], 'trough': lows[i - 1], 'entry_i': i,
                       'stop': lows[i - 1] + stop_mult * a1}
    return pd.Series(bar_pnl, index=idx), trades


# ════════════════════════════════════════════════════════════════════════
#  メトリクス
# ════════════════════════════════════════════════════════════════════════
def _daily(s):
    return s.groupby(s.index.tz_convert(None).normalize()).sum()

def _seg_mask(idx, lo, hi):
    m = pd.Series(True, index=idx)
    if lo: m &= idx >= pd.Timestamp(lo, tz='UTC')
    if hi: m &= idx <= pd.Timestamp(hi, tz='UTC') + pd.Timedelta(days=1)
    return m

def pf_of(trades):
    g = sum(t['net'] for t in trades if t['net'] > 0)
    l = abs(sum(t['net'] for t in trades if t['net'] < 0))
    return (g / l) if l > 0 else float('inf')

def sharpe_of(bar_pnl, lo=None, hi=None):
    s = bar_pnl[_seg_mask(bar_pnl.index, lo, hi)]
    d = _daily(s)
    d = d[d.index >= d.index[0]]  # keep all days incl zeros for honest Sharpe
    if d.std() == 0 or len(d) < 10: return 0.0
    return float(d.mean() / d.std() * np.sqrt(252))

def maxdd_of(bar_pnl, lo=None, hi=None):
    s = bar_pnl[_seg_mask(bar_pnl.index, lo, hi)]
    cum = s.cumsum()
    return float((cum.cummax() - cum).max())

def trades_in(trades, lo, hi):
    lo_t = pd.Timestamp(lo, tz='UTC') if lo else None
    hi_t = (pd.Timestamp(hi, tz='UTC') + pd.Timedelta(days=1)) if hi else None
    out = []
    for t in trades:
        if lo_t and t['exit_ts'] < lo_t: continue
        if hi_t and t['exit_ts'] > hi_t: continue
        out.append(t)
    return out

def sleeve_metrics(bar_pnl, trades):
    def seg(lo, hi):
        tr = trades_in(trades, lo, hi)
        return {'pf': round(pf_of(tr), 3), 'n': len(tr),
                'net': round(sum(t['net'] for t in tr), 0),
                'sharpe': round(sharpe_of(bar_pnl, lo, hi), 2),
                'dd': round(maxdd_of(bar_pnl, lo, hi), 0)}
    full = seg(None, None); isr = seg(*IS_WIN); oos = seg(*OOS_WIN)
    wfo = []
    for y in WFO_YEARS:
        tr = trades_in(trades, f'{y}-01-01', f'{y}-12-31')
        if len(tr) >= 5: wfo.append(round(pf_of(tr), 2))
    return {'full': full, 'is': isr, 'oos': oos, 'wfo': wfo,
            'wfo_min': (min(wfo) if wfo else np.nan)}


def blend_metrics(grid_pnl, sleeve_pnl):
    """Grid単体 vs Grid+補完 の OOS Sharpe / maxDD 比較。"""
    idx = grid_pnl.index.union(sleeve_pnl.index)
    g = grid_pnl.reindex(idx, fill_value=0.0); s = sleeve_pnl.reindex(idx, fill_value=0.0)
    comb = g + s
    out = {}
    for tag, lo, hi in [('full', None, None), ('oos', *OOS_WIN)]:
        out[f'{tag}_g_sh'] = round(sharpe_of(g, lo, hi), 2)
        out[f'{tag}_c_sh'] = round(sharpe_of(comb, lo, hi), 2)
        out[f'{tag}_g_dd'] = round(maxdd_of(g, lo, hi), 0)
        out[f'{tag}_c_dd'] = round(maxdd_of(comb, lo, hi), 0)
    # OOSスリーブ-グリッド日次相関
    gd = _daily(g[_seg_mask(idx, *OOS_WIN)]); sd = _daily(s[_seg_mask(idx, *OOS_WIN)])
    j = pd.concat([gd, sd], axis=1).fillna(0.0)
    out['oos_corr'] = round(float(j.iloc[:, 0].corr(j.iloc[:, 1])), 3) if len(j) > 10 else np.nan
    return out


def fmt_seg(s):
    return f"PF{s['pf']:.2f} n{s['n']:4d} net{s['net']:>11,.0f} Sh{s['sharpe']:5.2f} DD{s['dd']:>10,.0f}"


def main():
    rows = []
    for pair, P in PAIRS.items():
        print('=' * 132); print(f"{pair}  dir={P['dir']}  cfg fs={P['cfg']['float_stop']:,.0f} "
                                 f"ci={P['cfg']['ci_threshold']} atr={P['cfg']['atr_mult']}"); print('=' * 132)
        grid_pnl, df, atr, ci = grid_bar_pnl(pair, P)
        gm_oos_sh = sharpe_of(grid_pnl, *OOS_WIN); gm_oos_dd = maxdd_of(grid_pnl, *OOS_WIN)
        print(f"  [grid v8] OOS Sharpe={gm_oos_sh:.2f}  OOS maxDD={gm_oos_dd:,.0f}  "
              f"(engine一致OK)")

        # S1 ドリフト・ロング (CI-idle gate) — SMA/stop をスイープ
        print('  --- S1 ドリフト・ロング (CI<=th 窓限定, vol-target, chandelier stop) ---')
        for sma_n in (200, 480):
            for stop_mult in (3.0, 5.0):
                sp, tr = sleeve_bar_pnl(P, df, atr, ci, sma_n=sma_n, stop_mult=stop_mult,
                                        side='long', gate='ci_idle')
                m = sleeve_metrics(sp, tr); bl = blend_metrics(grid_pnl, sp)
                tag = f'S1_sma{sma_n}_stop{stop_mult}'
                print(f"    {tag:20s} IS[{fmt_seg(m['is'])}]")
                print(f"    {'':20s} OOS[{fmt_seg(m['oos'])}] wfo{m['wfo']} | "
                      f"blendOOS gSh{bl['oos_g_sh']}->cSh{bl['oos_c_sh']} "
                      f"gDD{bl['oos_g_dd']:,.0f}->cDD{bl['oos_c_dd']:,.0f} corr{bl['oos_corr']}")
                rows.append({'pair': pair, 'tag': tag, **flat(m, bl)})

        # S3 常時稼働(対照, gate無し) — best SMA/stopで1本
        sp, tr = sleeve_bar_pnl(P, df, atr, ci, sma_n=480, stop_mult=3.0, side='long', gate='always')
        m = sleeve_metrics(sp, tr); bl = blend_metrics(grid_pnl, sp)
        print(f"  --- S3 常時稼働(対照, ゲート無し sma480 stop3.0) ---")
        print(f"    {'S3_always':20s} OOS[{fmt_seg(m['oos'])}] wfo{m['wfo']}")
        rows.append({'pair': pair, 'tag': 'S3_always_sma480_stop3.0', **flat(m, bl)})

        # S2 片側不在フェード (short) — long_only/regime で空くshort側
        print('  --- S2 片側不在フェード (short, 過熱z>=2.0, CI<=th窓) ---')
        for z_thr in (2.0, 2.5):
            sp, tr = sleeve_bar_pnl(P, df, atr, ci, sma_n=480, stop_mult=3.0,
                                    side='short_fade', gate='ci_idle', z_thr=z_thr)
            m = sleeve_metrics(sp, tr); bl = blend_metrics(grid_pnl, sp)
            tag = f'S2_fade_z{z_thr}'
            print(f"    {tag:20s} OOS[{fmt_seg(m['oos'])}] wfo{m['wfo']}")
            rows.append({'pair': pair, 'tag': tag, **flat(m, bl)})
        print()

    pd.DataFrame(rows).to_csv(OUT, index=False)
    print(f'saved {OUT}')

    # 採用バー判定サマリー
    print('\n=== 採用バー判定 ===')
    print('(a) 単体: OOS PF>1.2 ∧ OOS Sharpe>0 ∧ WFO各fold>0')
    print('(b) ブレンド: OOS Sharpe向上 ∧ OOS maxDD非悪化')
    dfo = pd.DataFrame(rows)
    any_hit = False
    for _, r in dfo.iterrows():
        a = (r['oos_pf'] > 1.2) and (r['oos_sharpe'] > 0) and (not np.isnan(r['wfo_min'])) and (r['wfo_min'] > 0)
        b = (r['blend_oos_c_sh'] > r['blend_oos_g_sh']) and (r['blend_oos_c_dd'] <= r['blend_oos_g_dd'] * 1.001)
        if a and b:
            any_hit = True
            print(f"  ✅ {r['pair']} {r['tag']}: OOS PF{r['oos_pf']:.2f} Sh{r['oos_sharpe']:.2f} "
                  f"wfoMin{r['wfo_min']:.2f} | blend Sh{r['blend_oos_g_sh']}->{r['blend_oos_c_sh']} "
                  f"DD{r['blend_oos_g_dd']:,.0f}->{r['blend_oos_c_dd']:,.0f}")
    if not any_hit:
        print('  該当なし → 補完窓にも頑健エッジ無し(Close)')


def flat(m, bl):
    return {'full_pf': m['full']['pf'], 'full_sharpe': m['full']['sharpe'], 'full_n': m['full']['n'],
            'is_pf': m['is']['pf'], 'is_sharpe': m['is']['sharpe'],
            'oos_pf': m['oos']['pf'], 'oos_sharpe': m['oos']['sharpe'], 'oos_n': m['oos']['n'],
            'oos_net': m['oos']['net'], 'wfo_min': m['wfo_min'], 'wfo': str(m['wfo']),
            'blend_oos_g_sh': bl['oos_g_sh'], 'blend_oos_c_sh': bl['oos_c_sh'],
            'blend_oos_g_dd': bl['oos_g_dd'], 'blend_oos_c_dd': bl['oos_c_dd'], 'oos_corr': bl['oos_corr']}


if __name__ == '__main__':
    main()
