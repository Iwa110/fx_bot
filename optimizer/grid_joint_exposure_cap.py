"""
grid_joint_exposure_cap.py - A1: クロスペア合算エクスポージャー上限。

動機: バスケット req_cap(grid_joint_stepb.py)を拘束するのは「4本が稀に同時にDDを踏む月」。
各ペアのCIゲートは個別に休眠/建てを判断し、合算リスクは無制御。全ペア合算の含み損(JPY)に
上限を設け、超過時は新規建てを抑止すれば、同時DDのテールを刈って req_cap を下げられる(はず)。
新エッジでなく**サイジング/露出制御**=net をほぼ保ったまま req_cap↓ を狙う。

設計:
  - 確定Grid 4本(grid_joint_stepb.build_defs と同一構成)を **統一タイムライン(union)上で
    同時シミュレート**する joint エンジン。各ペアは自分のバーでのみ動作(=standalone と同手順)。
  - 各 union timestamp で「決済/FS/B48/cull/DD更新」を全ペア処理 → その時点の **合算含み損**
    (= Σ 各ペアの open ラダー mark-to-market, JPY, lot1.0) を算出 → cap 超過なら新規建て抑止。
  - 抑止対象 = block_set: 'all'(全ペア) / 'thin1'(最薄エッジ=EURGBP) / 'thin2'(EURGBP+AUDNZD)。
    エッジ順(資本効率): AUDCAD > CADCHF > AUDNZD > EURGBP。
  - gate_scope: 'all'(level0+追加) / 'level0'(新規ラダーのみ・既存への押し目追加は許可)。

ガードレール(規律):
  - cap=None で各ペアの月次PnLが DB.run_bt(collect=True) と **完全一致(assert)** =静的同型保証。
  - req_cap は grid_joint_stepb と同一の暦月ブロックブートストラップ(block3/60mo/20000/seed42)。
  - 判定 = net/yr 非有意低下(>~5%減はNG) ∧ basket req_cap_99 有意低下 ∧ capEff 向上。

実行: .venv_dukas/bin/python optimizer/grid_joint_exposure_cap.py
出力: grid_joint_exposure_cap_result.csv + console
"""
import numpy as np, pandas as pd
from pathlib import Path
import grid_floatstop_bt as G
import grid_dd_reduction_bt as D
import grid_dirbias_improve_bt as DB
from grid_corrcross_screen import QUOTE_JPY

OUT = Path(__file__).resolve().parent / 'grid_joint_exposure_cap_result.csv'
SEED = 42; N_MC = 20000; BLOCK = 3; HORIZON = 60
COMBO = {'mom_thr': 2.0, 'cull_frac': 0.5, 'taper': 0.7}
CONTRACT = G.CONTRACT
TARGET_NET_YR = 3_600_000.0
# 資本効率順(高→低)。薄いエッジ = 末尾。
EDGE_ORDER = ['AUDCAD', 'CADCHF', 'AUDNZD', 'EURGBP']


def template_cfg(qj, fs):
    return {'atr_mult': 1.5, 'ci_threshold': 65.0, 'b48_hours': 48,
            'lot': 1.0, 'max_levels': 5, 'float_stop': fs, 'quote_jpy': qj}


def cadchf_cfg():
    df_ac = D.load_duk('AUDCAD'); atr_ac = G.compute_atr_series(df_ac)
    ref = float(atr_ac.median()) * 108.0
    df = D.load_duk('CADCHF'); atr = G.compute_atr_series(df)
    qj = QUOTE_JPY['CADCHF']
    return template_cfg(qj, round(-750_000.0 * (float(atr.median()) * qj) / ref, 0))


def build_defs():
    return [
        ('AUDCAD', D.AUDCAD, lambda df, atr: {'short_block_up': DB.sma_regime(df, 1200), **COMBO}),
        ('CADCHF', cadchf_cfg(), lambda df, atr: {'short_block_up': DB.sma_regime(df, 1200)}),
        ('AUDNZD', template_cfg(90.0, round(-750_000.0 * 90.0 / 108.0, 0)),
         lambda df, atr: {'short_block_up': DB.sma_regime(df, 1200), **COMBO}),
        ('EURGBP', D.EURGBP, lambda df, atr: {'short_lot_mult': 0.5, **COMBO}),
    ]


class PairState:
    """DB.run_bt の per-bar ロジックを忠実に保持し、決済フェーズと建てフェーズを分離。
    block=True で建てフェーズの新規建てを抑止(level0/追加は gate_scope で制御)。"""

    def __init__(self, pair, cfg, kw):
        self.pair = pair; self.cfg = cfg
        self.qj = cfg.get('quote_jpy', 1.0); self.base_lot = cfg['lot']
        self.atr_mult = cfg['atr_mult']; self.ci_threshold = cfg['ci_threshold']
        self.b48_hours = cfg['b48_hours']; self.float_stop = cfg['float_stop']
        self.mom_thr = kw.get('mom_thr'); self.cull_frac = kw.get('cull_frac')
        self.taper = kw.get('taper')
        self.long_lot_mult = kw.get('long_lot_mult', 1.0)
        self.short_lot_mult = kw.get('short_lot_mult', 1.0)
        self.lml = kw.get('long_ml', cfg['max_levels'])
        self.sml = kw.get('short_ml', cfg['max_levels'])
        self.short_block_up = kw.get('short_block_up')
        self.long_pos = []; self.short_pos = []
        self.b48_ls = self.b48_ss = None
        self.tp = []; self.b48p = []; self.b48pp = []; self.fsp = []; self.fspp = []; self.cull = []
        self.realized = self.peak = self.max_dd = self.worst = 0.0
        self.monthly = {}
        self.last_close = np.nan

    def _pj(self, d, lotv): return d * lotv * CONTRACT * self.qj
    def _m(self, ts, v):
        k = ts.strftime('%Y-%m'); self.monthly[k] = self.monthly.get(k, 0.0) + v
    def _llot(self, lv): return self.base_lot * self.long_lot_mult * (self.taper ** (lv - 1) if self.taper else 1.0)
    def _slot(self, lv): return self.base_lot * self.short_lot_mult * (self.taper ** (lv - 1) if self.taper else 1.0)

    def open_unreal(self):
        """現時点(last_close)での open ラダー含み損益(JPY)。"""
        if np.isnan(self.last_close):
            return 0.0
        bc = self.last_close
        v = sum(self._pj(bc - p['entry'], p['lot']) for p in self.long_pos)
        v += sum(self._pj(p['entry'] - bc, p['lot']) for p in self.short_pos)
        return v

    def close_phase(self, ts, bh, bl, bc, atr):
        """TP/FS/B48/cull/DD。建ては行わない。"""
        self.last_close = bc
        pj = self._pj; fs = self.float_stop
        lwm = len(self.long_pos) >= self.lml; swm = len(self.short_pos) >= self.sml
        for p in [p for p in self.long_pos if bh >= p['tp']]:
            v = pj(p['tp'] - p['entry'], p['lot']); self.tp.append(v); self.realized += v; self._m(ts, v); self.long_pos.remove(p)
        for p in [p for p in self.short_pos if bl <= p['tp']]:
            v = pj(p['entry'] - p['tp'], p['lot']); self.tp.append(v); self.realized += v; self._m(ts, v); self.short_pos.remove(p)

        if self.long_pos and sum(pj(bl - p['entry'], p['lot']) for p in self.long_pos) <= fs:
            pp = [pj(bl - p['entry'], p['lot']) for p in self.long_pos]; ev = sum(pp)
            self.fspp.extend(pp); self.fsp.append(ev); self.realized += ev; self._m(ts, ev); self.worst = min(self.worst, ev)
            self.long_pos = []; self.b48_ls = None
        if self.short_pos and sum(pj(p['entry'] - bh, p['lot']) for p in self.short_pos) <= fs:
            pp = [pj(p['entry'] - bh, p['lot']) for p in self.short_pos]; ev = sum(pp)
            self.fspp.extend(pp); self.fsp.append(ev); self.realized += ev; self._m(ts, ev); self.worst = min(self.worst, ev)
            self.short_pos = []; self.b48_ss = None

        if lwm and len(self.long_pos) < self.lml: self.b48_ls = None
        if swm and len(self.short_pos) < self.sml: self.b48_ss = None
        if self.b48_ls is not None and (ts - self.b48_ls).total_seconds() / 3600.0 >= self.b48_hours:
            pp = [pj(bc - p['entry'], p['lot']) for p in self.long_pos]; ev = sum(pp)
            self.b48pp.extend(pp); self.b48p.append(ev); self.realized += ev; self._m(ts, ev); self.worst = min(self.worst, ev)
            self.long_pos = []; self.b48_ls = None
        if self.b48_ss is not None and (ts - self.b48_ss).total_seconds() / 3600.0 >= self.b48_hours:
            pp = [pj(p['entry'] - bc, p['lot']) for p in self.short_pos]; ev = sum(pp)
            self.b48pp.extend(pp); self.b48p.append(ev); self.realized += ev; self._m(ts, ev); self.worst = min(self.worst, ev)
            self.short_pos = []; self.b48_ss = None

        if self.cull_frac is not None:
            if len(self.long_pos) >= 2:
                legs = [(pj(bc - p['entry'], p['lot']), p) for p in self.long_pos]
                if sum(v for v, _ in legs) <= self.cull_frac * fs:
                    v, p = min(legs, key=lambda x: x[0]); self.cull.append(v); self.realized += v; self._m(ts, v)
                    self.worst = min(self.worst, v); self.long_pos.remove(p)
                    if len(self.long_pos) < self.lml: self.b48_ls = None
            if len(self.short_pos) >= 2:
                legs = [(pj(p['entry'] - bc, p['lot']), p) for p in self.short_pos]
                if sum(v for v, _ in legs) <= self.cull_frac * fs:
                    v, p = min(legs, key=lambda x: x[0]); self.cull.append(v); self.realized += v; self._m(ts, v)
                    self.worst = min(self.worst, v); self.short_pos.remove(p)
                    if len(self.short_pos) < self.sml: self.b48_ss = None

        self.peak = max(self.peak, self.realized); self.max_dd = max(self.max_dd, self.peak - self.realized)

    def entry_phase(self, ts, bc, atr, ci, r24, block, gate_scope):
        gw = atr * self.atr_mult
        ci_ok = (not np.isnan(ci)) and (ci > self.ci_threshold)
        mom_long = (self.mom_thr is None or np.isnan(r24) or r24 > -self.mom_thr)
        mom_short = (self.mom_thr is None or np.isnan(r24) or r24 < self.mom_thr)
        reg_short = True
        if self.short_block_up is not None:
            reg_short = not (getattr(self, 'short_block_up_val', False) is True)
        long_ok = ci_ok and mom_long
        short_ok = ci_ok and mom_short and reg_short
        # 露出ゲート: block=True で新規ラダー(level0)抑止。追加は scope='all' のみ抑止。
        block_new = block
        block_add = block and (gate_scope == 'all')

        if self.lml > 0:
            if len(self.long_pos) == 0:
                if long_ok and not block_new:
                    self.long_pos.append({'entry': bc, 'tp': bc + gw, 'lot': self._llot(1)})
                    if len(self.long_pos) == self.lml: self.b48_ls = ts
            elif len(self.long_pos) < self.lml:
                if bc <= min(p['entry'] for p in self.long_pos) - gw and long_ok and not block_add:
                    self.long_pos.append({'entry': bc, 'tp': bc + gw, 'lot': self._llot(len(self.long_pos) + 1)})
                    if len(self.long_pos) == self.lml: self.b48_ls = ts
        if self.sml > 0:
            if len(self.short_pos) == 0:
                if short_ok and not block_new:
                    self.short_pos.append({'entry': bc, 'tp': bc - gw, 'lot': self._slot(1)})
                    if len(self.short_pos) == self.sml: self.b48_ss = ts
            elif len(self.short_pos) < self.sml:
                if bc >= max(p['entry'] for p in self.short_pos) + gw and short_ok and not block_add:
                    self.short_pos.append({'entry': bc, 'tp': bc - gw, 'lot': self._slot(len(self.short_pos) + 1)})
                    if len(self.short_pos) == self.sml: self.b48_ss = ts


def run_joint(defs, cap=None, block_set='all', gate_scope='level0'):
    """4本を union タイムライン上で同時シミュレート。cap=None で standalone と一致。
    block_set: 'all'/'thin1'/'thin2'。gate_scope: 'all'/'level0'。"""
    pairs = {}
    for pair, cfg, kwfn in defs:
        df = D.load_duk(pair); atr = G.compute_atr_series(df); ci = G.compute_ci_series(df)
        r24 = D.ret24_series(df, atr); kw = kwfn(df, atr)
        st = PairState(pair, cfg, kw)
        sb = kw.get('short_block_up')
        pairs[pair] = {'st': st, 'df': df,
                       'av': atr.reindex(df.index).to_numpy(), 'cv': ci.reindex(df.index).to_numpy(),
                       'r24': r24, 'sb': sb,
                       'highs': df['high'].to_numpy(), 'lows': df['low'].to_numpy(),
                       'closes': df['close'].to_numpy(), 'idx': df.index,
                       'ptr': 0, 'n': len(df)}
    # block 対象集合(エッジ薄い順)
    if block_set == 'all':
        blocked_pairs = set(EDGE_ORDER)
    elif block_set == 'thin1':
        blocked_pairs = {EDGE_ORDER[-1]}
    elif block_set == 'thin2':
        blocked_pairs = set(EDGE_ORDER[-2:])
    else:
        blocked_pairs = set()

    union = pd.DatetimeIndex(sorted(set().union(*[p['df'].index for p in pairs.values()])))
    for ts in union:
        active = []
        for name, P in pairs.items():
            j = P['ptr']
            if j < P['n'] and P['idx'][j] == ts:
                atr = P['av'][j]
                if not (np.isnan(atr) or atr <= 0):
                    st = P['st']
                    st.close_phase(ts, P['highs'][j], P['lows'][j], P['closes'][j], atr)
                    active.append((name, P, j, atr))
                P['ptr'] = j + 1
        if not active:
            continue
        agg = sum(P['st'].open_unreal() for P in pairs.values())
        breached = (cap is not None) and (agg <= cap)
        for name, P, j, atr in active:
            st = P['st']
            sbv = P['sb'][j] if P['sb'] is not None else None
            st.short_block_up_val = (sbv is True) if P['sb'] is not None else False
            blk = breached and (name in blocked_pairs)
            st.entry_phase(ts, P['closes'][j], atr, P['cv'][j], P['r24'][j], blk, gate_scope)
    return {name: P['st'] for name, P in pairs.items()}


def assert_static_match(defs):
    """cap=None の joint 月次 == DB.run_bt(collect) 月次。"""
    states = run_joint(defs, cap=None)
    for pair, cfg, kwfn in defs:
        df = D.load_duk(pair); atr = G.compute_atr_series(df); ci = G.compute_ci_series(df)
        r24 = D.ret24_series(df, atr)
        ref = DB.run_bt(cfg, df, atr, ci, ret24=r24, collect=True, **kwfn(df, atr))
        a = states[pair].monthly; b = ref['monthly']
        keys = set(a) | set(b)
        diff = max((abs(a.get(k, 0.0) - b.get(k, 0.0)) for k in keys), default=0.0)
        assert diff < 1.0, f'{pair} joint!=standalone diff={diff:,.0f}'
        print(f'  [static-match] {pair:7s} OK (月数{len(b)}, net={states[pair].realized:,.0f})')


def calendar_matrix(states, pairs):
    series = {p: pd.Series(states[p].monthly) for p in pairs}
    all_m = sorted(set().union(*[set(s.index) for s in series.values()]))
    cal = pd.period_range(min(all_m), max(all_m), freq='M').strftime('%Y-%m')
    return pd.DataFrame({p: series[p].reindex(cal).fillna(0.0) for p in pairs})


def bootstrap(monthly, rng):
    n = len(monthly); nb = int(np.ceil(HORIZON / BLOCK))
    starts = rng.integers(0, n - BLOCK + 1, size=(N_MC, nb))
    mdd = np.empty(N_MC); fin = np.empty(N_MC)
    for i in range(N_MC):
        seq = np.concatenate([monthly[s:s + BLOCK] for s in starts[i]])[:HORIZON]
        eq = np.cumsum(seq); peak = np.maximum.accumulate(np.concatenate([[0.0], eq]))
        mdd[i] = (peak[1:] - eq).max(); fin[i] = eq[-1]
    return mdd, fin


def basket_stats(M, weights):
    pairs = list(M.columns); w = np.array([weights[p] for p in pairs])
    basket = M.to_numpy() @ w
    rng = np.random.default_rng(SEED)
    mdd, fin = bootstrap(basket, rng)
    n_years = len(M) / 12.0
    r99 = float(np.percentile(mdd, 99)); ny = basket.sum() / n_years
    return {'net_yr': ny, 'req99': r99, 'capEff': ny / r99 if r99 else float('nan'),
            'p5': float((fin < 0).mean())}


def main():
    defs = build_defs()
    pairs = [d[0] for d in defs]
    print('=== A1: クロスペア合算エクスポージャー上限 / 4本ジョイント / 暦月MC ===\n')
    print('静的一致検証(cap=None):')
    assert_static_match(defs)

    # baseline 月次行列(cap無し) → 等req_cap配分の重み算定
    base_states = run_joint(defs, cap=None)
    Mbase = calendar_matrix(base_states, pairs)
    # per-pair req_cap(standalone, 暦月) で等req_cap重み
    standalone_req = {}
    for p in pairs:
        rng = np.random.default_rng(SEED)
        mdd, _ = bootstrap(Mbase[p].to_numpy(), rng)
        standalone_req[p] = float(np.percentile(mdd, 99))
    w_eqreq = {p: standalone_req[pairs[0]] / standalone_req[p] for p in pairs}  # AUDCAD=1
    w_eqreq = {p: round(w_eqreq[p] / w_eqreq[pairs[0]], 3) for p in pairs}
    print(f'\n等req_cap配分(AUDCAD=1基準): {w_eqreq}')

    base_b = basket_stats(Mbase, w_eqreq)
    print(f'\n--- baseline(cap無し, 等req_cap配分) ---')
    print(f'  net/yr={base_b["net_yr"]:,.0f}  req99={base_b["req99"]:,.0f}  '
          f'capEff={base_b["capEff"]:.3f}  P5={base_b["p5"]:.3f}')

    # 合算含み損の分布(cap較正の足場)
    # cap候補: baseline の最悪合算含み損に対する比率で振る
    print('\n--- cap スイープ(合算含み損上限) ---')
    print(f'{"cap(JPY)":>12s} {"block":>6s} {"scope":>7s} {"net/yr":>11s} {"Δnet%":>7s} '
          f'{"req99":>11s} {"Δreq%":>7s} {"capEff":>7s} {"P5":>6s}')
    rows = [{'variant': 'baseline', 'cap': None, 'block_set': '-', 'gate_scope': '-',
             'net_yr': round(base_b['net_yr'], 0), 'req99': round(base_b['req99'], 0),
             'capEff': round(base_b['capEff'], 3), 'p5': round(base_b['p5'], 4)}]
    caps = [-6_000_000, -4_500_000, -3_500_000, -2_500_000, -1_800_000]
    for cap in caps:
        for block_set in ('all', 'thin2', 'thin1'):
            for gate_scope in ('level0', 'all'):
                states = run_joint(defs, cap=cap, block_set=block_set, gate_scope=gate_scope)
                M = calendar_matrix(states, pairs).reindex(columns=pairs).fillna(0.0)
                M = M.reindex(Mbase.index).fillna(0.0)
                b = basket_stats(M, w_eqreq)
                dnet = (b['net_yr'] / base_b['net_yr'] - 1) * 100 if base_b['net_yr'] else 0
                dreq = (b['req99'] / base_b['req99'] - 1) * 100 if base_b['req99'] else 0
                print(f'{cap:>12,.0f} {block_set:>6s} {gate_scope:>7s} {b["net_yr"]:>11,.0f} '
                      f'{dnet:>+6.1f}% {b["req99"]:>11,.0f} {dreq:>+6.1f}% {b["capEff"]:>7.3f} {b["p5"]:>6.3f}')
                rows.append({'variant': 'cap', 'cap': cap, 'block_set': block_set,
                             'gate_scope': gate_scope, 'net_yr': round(b['net_yr'], 0),
                             'req99': round(b['req99'], 0), 'capEff': round(b['capEff'], 3),
                             'p5': round(b['p5'], 4), 'dnet_pct': round(dnet, 2), 'dreq_pct': round(dreq, 2)})
        print()

    pd.DataFrame(rows).to_csv(OUT, index=False)
    print(f'saved {OUT}')


if __name__ == '__main__':
    main()
