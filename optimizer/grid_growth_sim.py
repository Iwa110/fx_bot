"""
grid_growth_sim.py - 確定Grid 4本の「複利成長」モンテカルロ。

目的: 初期投資50万円から確定Grid 4本(AUDCAD/CADCHF/AUDNZD/EURGBP)を
等req_cap分散バスケットで運用し、毎月の実現益で自己資本が増えるたびにlotを
複利再計算(幾何成長)したときの到達分布を求める。新規エッジ探索でなく、
grid_joint_stepb.py(暦月基盤・相関保持MC)で確定したバスケットの上に
「複利のlotサイジング則」を被せた成長シミュレーション。

★本シミュレーションの肝(実機観察を反映, 2026-06-21):
  Gridは大半の時間アイドル(高CIレンジ窓でのみ建つ)。AUDCAD demo は2026-06に
  3週間エントリーゼロを実機確認。暦月基盤の月次系列は休眠月を 0 で埋めているため、
  暦月ブロックブートストラップは「発火頻度・アイドル月」を自動的に honest に内包する
  (grid_episode_stats の gate_share/発火頻度と整合)。アクティブ月だけで複利を
  回さない=成長カーブに idle のフラット区間がそのまま現れる。

手法(grid_joint_stepb.py / grid_stepb_recompute.py を厳密踏襲):
  - 月次PnL系列を DB.run_bt(collect=True) で各ペア取得(lot=1.0, 円換算 quote_jpy)。
  - 全ペア共通の暦月レンジに reindex し 0 埋め → 行列 M (n_months × 4)。
  - 等req_cap配分の相対lot比 w=[1, 0.305, 0.552, 0.303] でバスケット月次 b = M @ w。
  - ★静的整合assert: per-pair net11yr と 等req_cap配分の (net_yr, req99, capEff, P5) が
    grid_joint_stepb_improved.csv と一致することを確認(=M とブートストラップが既存と同一)。
  - 複利MC: 自己資本 E0=500k から月次ブロックブートストラップ(block=3, 20000パス,
    60ヶ月)。各月 scale_t = risk_frac * E_t / unit_reqcap を再計算(=複利でlot漸増/
    DD時は自動デレバ)、その月のバスケットPnL = scale_t * b_sampled。E を更新。
  - 出力: 成長カーブ分位 / 月利30万ランレート到達月 / P(破産=自己資本がreq_cap割れ) /
    マイルストーン到達確率・時期 / 各ペアの初回約定待ち・年間アイドル月数。
  - ★感度比較: 「AUDCAD単独開始」vs「4本小バスケット開始」で到達月数・P(ruin)・
    資本稼働率(=資本が建玉に使われている月の割合)を並べ、小バスケット優位を定量提示。

実行: ../.venv_dukas/bin/python optimizer/grid_growth_sim.py
出力: grid_growth_sim_result.csv (+ _curve.csv / _activity.csv) + console
"""
import numpy as np, pandas as pd
from pathlib import Path
import grid_joint_stepb as J

HERE = Path(__file__).resolve().parent
SEED = 42
N_MC = 20000
BLOCK = 3
HORIZON = 60            # ヶ月(=5年)
E0 = 500_000.0          # 初期投資
TARGET_NET_YR = 3_600_000.0  # 月利30万円 = 360万/yr
RISK_FRAC = 1.0         # lotサイジング: scale = RISK_FRAC * E / unit_reqcap (plan既定=1.0)
MILESTONES = [1_000_000.0, 2_000_000.0, 2_800_000.0]  # 自己資本マイルストーン


# ---------------------------------------------------------------------------
# 1. 暦月行列 M の構築(grid_joint_stepb.run_basket と同一の reindex/0埋め)
# ---------------------------------------------------------------------------
def build_matrix(defs):
    series = {}; net11 = {}
    for pair, cfg, label, kwfn in defs:
        res, s = J.monthly_series(pair, cfg, kwfn)
        series[pair] = s; net11[pair] = float(s.sum())
    pairs = [d[0] for d in defs]
    all_idx = pd.PeriodIndex(sorted(set().union(*[s.index for s in series.values()])), freq='M')
    cal = pd.period_range(all_idx.min(), all_idx.max(), freq='M').strftime('%Y-%m')
    M = pd.DataFrame({p: series[p].reindex(cal).fillna(0.0) for p in pairs})
    return M, pairs, net11


def standalone_stats(M, pairs):
    """per-pair req99/netyr(整列窓・lot1.0)。grid_joint_stepb の P1 と同一手法。"""
    n_years = len(M) / 12.0
    out = {}
    for p in pairs:
        col = M[p].to_numpy(dtype=float)
        rng = np.random.default_rng(SEED)
        mdd, fin = J.bootstrap(col, rng)
        out[p] = {'req99': J.req_cap(mdd), 'netyr': col.sum() / n_years,
                  'std': col.std(), 'p5': float((fin < 0).mean())}
    return out


def basket_eval(M, pairs, standalone, w):
    """grid_joint_stepb.basket_eval と同一(バスケットを先に合算→ジョイントMC)。"""
    n_years = len(M) / 12.0
    w = np.asarray(w, dtype=float)
    basket = M.to_numpy(dtype=float) @ w
    rng = np.random.default_rng(SEED)
    mdd, fin = J.bootstrap(basket, rng)
    r99 = J.req_cap(mdd)
    ny = basket.sum() / n_years
    naive = sum(w[i] * standalone[pairs[i]]['req99'] for i in range(len(pairs)))
    return {'w': w, 'netyr': ny, 'req99': r99, 'naive': naive,
            'div': 1 - r99 / naive if naive else 0.0,
            'eff': ny / r99 if r99 else float('nan'),
            'p5': float((fin < 0).mean())}


# ---------------------------------------------------------------------------
# 2. アイドル/発火の活動性統計(エピソード頻度・初回約定待ち・年間アイドル月数)
# ---------------------------------------------------------------------------
def activity_stats(M, pairs):
    n = len(M)
    rows = []
    active_any = (M.to_numpy() != 0).any(axis=1)
    for p in pairs:
        act = (M[p].to_numpy() != 0)
        rate = act.mean()
        # 初回約定までの待ち(月): 幾何分布期待値 = (1-rate)/rate, 実測の初回非ゼロ位置も
        first = int(np.argmax(act)) if act.any() else n
        idle_per_yr = (1 - rate) * 12.0
        rows.append({'pair': p, 'active_months': int(act.sum()), 'total_months': n,
                     'activity_rate': round(rate, 3),
                     'idle_months_per_yr': round(idle_per_yr, 1),
                     'exp_wait_first_fill_mo': round((1 - rate) / rate, 1) if rate else float('inf'),
                     'empirical_first_fill_mo': first})
    # バスケット(いずれか1本でも活動)
    brate = active_any.mean()
    rows.append({'pair': 'BASKET(any)', 'active_months': int(active_any.sum()), 'total_months': n,
                 'activity_rate': round(brate, 3),
                 'idle_months_per_yr': round((1 - brate) * 12.0, 1),
                 'exp_wait_first_fill_mo': round((1 - brate) / brate, 1) if brate else float('inf'),
                 'empirical_first_fill_mo': int(np.argmax(active_any)) if active_any.any() else n})
    return pd.DataFrame(rows), brate


# ---------------------------------------------------------------------------
# 3. 複利成長MC
# ---------------------------------------------------------------------------
def sample_basket_paths(basket, rng):
    """basket月次系列をブロックブートストラップ → (N_MC, HORIZON) 行列。
    grid_joint_stepb.bootstrap と同一のサンプリング(同一ブロック=相関保持済の合算系列)。"""
    n = len(basket); n_blocks = int(np.ceil(HORIZON / BLOCK))
    starts = rng.integers(0, n - BLOCK + 1, size=(N_MC, n_blocks))
    seqs = np.empty((N_MC, HORIZON))
    for i in range(N_MC):
        seqs[i] = np.concatenate([basket[s:s + BLOCK] for s in starts[i]])[:HORIZON]
    return seqs


def compound_mc(seqs, unit_reqcap, unit_netyr, runrate_equity, risk_frac=RISK_FRAC):
    """複利でlotを毎月再計算する成長MC。

    seqs[i,t] = lot=1単位バスケットの月次PnL(円)。
    各月: scale = risk_frac * E / unit_reqcap, pnl = scale * seqs[:,t], E += pnl。
    返り値: 成長カーブ(各月のequity全パス), 各種到達/破産統計。
    """
    N = seqs.shape[0]
    E = np.full(N, E0)
    peak = np.full(N, E0)
    max_dd = np.zeros(N)               # 自己資本ピークからの最大下落(円)
    ruined = np.zeros(N, dtype=bool)   # equity<=0(全損)
    halved = np.zeros(N, dtype=bool)   # equity が初期の50%未満に一度でも到達
    runrate_month = np.full(N, -1)     # 月利30万ランレート(scale>=target)初到達月
    ms_month = {m: np.full(N, -1) for m in MILESTONES}
    curve = np.empty((N, HORIZON + 1)); curve[:, 0] = E

    runrate_scale = TARGET_NET_YR / unit_netyr
    for t in range(HORIZON):
        scale = np.maximum(risk_frac * E / unit_reqcap, 0.0)
        scale = np.where(ruined, 0.0, scale)
        pnl = scale * seqs[:, t]
        E = E + pnl
        newly_ruined = (~ruined) & (E <= 0)
        ruined |= newly_ruined
        E = np.where(ruined, 0.0, E)
        peak = np.maximum(peak, E)
        max_dd = np.maximum(max_dd, peak - E)
        halved |= (E < 0.5 * E0)
        # ランレート(scale基準=equity基準)初到達
        hit_rr = (runrate_month < 0) & (~ruined) & (scale >= runrate_scale)
        runrate_month[hit_rr] = t + 1
        for m in MILESTONES:
            hit = (ms_month[m] < 0) & (E >= m)
            ms_month[m][hit] = t + 1
        curve[:, t + 1] = E

    def pct(a, q):
        return float(np.percentile(a, q))

    stats = {
        'unit_reqcap': unit_reqcap, 'unit_netyr': unit_netyr,
        'runrate_equity': runrate_equity, 'runrate_scale': runrate_scale,
        'E_final_p5': pct(E, 5), 'E_final_med': pct(E, 50), 'E_final_p95': pct(E, 95),
        'p_ruin': float(ruined.mean()), 'p_halved': float(halved.mean()),
        'p_final_loss': float((E < E0).mean()),
        'max_dd_med': pct(max_dd, 50), 'max_dd_p99': pct(max_dd, 99),
        'p_reach_runrate': float((runrate_month > 0).mean()),
        'runrate_med_month': (float(np.median(runrate_month[runrate_month > 0]))
                              if (runrate_month > 0).any() else float('nan')),
    }
    for m in MILESTONES:
        col = ms_month[m]
        stats[f'p_reach_{int(m/1e6*10)/10}M'] = float((col > 0).mean())
        stats[f'med_month_{int(m/1e6*10)/10}M'] = (float(np.median(col[col > 0]))
                                                   if (col > 0).any() else float('nan'))
    return stats, curve


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------
def main():
    print('=== Grid 複利成長MC: 確定4本(DD圧縮後) / 暦月基盤 / 50万→複利 ===')
    print(f'  MC {N_MC} / horizon {HORIZON}mo / block {BLOCK} / seed {SEED} / '
          f'E0 {E0:,.0f} / risk_frac {RISK_FRAC}')

    defs = J.build_defs(improved=True)
    M, pairs, net11 = build_matrix(defs)
    print(f'\n暦月数(0埋め): {len(M)} ({M.index[0]}〜{M.index[-1]})')
    print('  活動月: ' + ' / '.join(f'{p}:{(M[p]!=0).sum()}' for p in pairs))

    # --- 静的整合assert(per-pair net11yr が published と一致) ---
    EXP_NET11 = {'AUDCAD': 5596736, 'CADCHF': 10051736, 'AUDNZD': 1530201}
    for p, exp in EXP_NET11.items():
        got = round(net11[p])
        assert abs(got - exp) <= 2, f'{p} net11yr {got} != {exp} (engine drift!)'
    print('\n[assert] per-pair net11yr OK (AUDCAD/CADCHF/AUDNZD published一致)')

    standalone = standalone_stats(M, pairs)

    # --- 等req_cap配分(improved)= 成長バスケットの基準 ---
    inv_req = np.array([1.0 / standalone[p]['req99'] for p in pairs])
    w_basket = (inv_req / inv_req[0]).round(3).tolist()
    eb = basket_eval(M, pairs, standalone, w_basket)
    print(f'\n等req_cap配分 weights={["%g"%x for x in w_basket]}  '
          f'net_yr={eb["netyr"]:,.0f}  req99={eb["req99"]:,.0f}  '
          f'capEff={eb["eff"]:.3f}  P5={eb["p5"]:.4f}')

    # grid_joint_stepb_improved.csv の 等req_cap配分行と一致を確認
    imp = pd.read_csv(HERE / 'grid_joint_stepb_improved.csv')
    row = imp[imp['alloc'] == '等req_cap配分'].iloc[0]
    assert abs(eb['netyr'] - row['net_yr']) <= 2, f"netyr {eb['netyr']} != {row['net_yr']}"
    assert abs(eb['req99'] - row['req99_joint']) <= 2, f"req99 {eb['req99']} != {row['req99_joint']}"
    print('[assert] 等req_cap配分 (net_yr, req99) が grid_joint_stepb_improved.csv と一致 OK')

    # --- 活動性統計 ---
    act_df, basket_rate = activity_stats(M, pairs)
    print('\n--- 活動性(アイドル/発火) ---')
    print(act_df.to_string(index=False))
    act_df.to_csv(HERE / 'grid_growth_sim_activity.csv', index=False)

    # --- 複利成長MC: バスケット vs AUDCAD単独 ---
    Mnp = M.to_numpy(dtype=float)
    # バスケット
    b_basket = Mnp @ np.asarray(w_basket)
    rng = np.random.default_rng(SEED)
    seqs_b = sample_basket_paths(b_basket, rng)
    runrate_eq_b = eb['req99'] * (TARGET_NET_YR / eb['netyr'])
    st_b, curve_b = compound_mc(seqs_b, eb['req99'], eb['netyr'], runrate_eq_b)
    # AUDCAD単独
    b_solo = Mnp @ np.array([1.0, 0, 0, 0])
    rng = np.random.default_rng(SEED)
    seqs_s = sample_basket_paths(b_solo, rng)
    runrate_eq_s = standalone['AUDCAD']['req99'] * (TARGET_NET_YR / standalone['AUDCAD']['netyr'])
    st_s, curve_s = compound_mc(seqs_s, standalone['AUDCAD']['req99'],
                                standalone['AUDCAD']['netyr'], runrate_eq_s)

    # 資本稼働率: 資本が建玉に使われている月の割合(=活動月割合)
    util_basket = basket_rate
    util_solo = (M['AUDCAD'].to_numpy() != 0).mean()

    # --- risk_frac スイープ(レバレッジ/安全バッファ + 加速の感度) ---
    # scale = risk_frac * E / unit_reqcap。
    #   risk_frac<1 = equity>req_cap の安全バッファ。
    #   risk_frac>1 = equity<req_cap = レバを効かせ加速(速さを破産/半減確率で買う)。
    # 決め手の列:
    #   worst_month/eq = 歴史上の単月最悪(1単位basket)が現equity比何% → 100%超で単月破産=絶壁。
    #   cap_for_30man  = その risk_frac で月利30万に必要な自己資本(= runrate_eq / risk_frac)。
    worst_month_unit = float(b_basket.min())  # 1単位basketの単月最悪(負)
    print('\n' + '=' * 96)
    print('risk_frac スイープ(4本バスケット): 安全バッファ(<1) と レバ加速(>1) の感度')
    print('  ※ worst_month/eq が ~100% に近づくほど「想定外の1ヶ月で即死」= 絶壁。1.5超は非推奨')
    print('=' * 96)
    print(f'{"risk_frac":>9s} {"eq=req×":>8s} {"30万必要資本":>12s} {"P(30万|60mo)":>12s} '
          f'{"到達月(中)":>10s} {"P(半減)":>8s} {"P(破産)":>8s} {"worst月/eq":>10s} {"E_med60mo":>12s}')
    sweep_rows = []
    for rf in [0.33, 0.5, 1.0, 1.25, 1.5, 1.75, 2.0, 2.5]:
        st, _ = compound_mc(seqs_b, eb['req99'], eb['netyr'], runrate_eq_b, risk_frac=rf)
        eqx = 1.0 / rf
        cap30 = runrate_eq_b / rf
        worst_pct = abs(worst_month_unit) / (eb['req99'] / rf)  # 単月最悪/(現equity≈req99/rf)
        med = st['runrate_med_month']
        meds = f'{med:.0f}mo' if med == med else '—'
        print(f'{rf:9.2f} {eqx:7.2f}x {cap30:12,.0f} {st["p_reach_runrate"]*100:11.1f}% '
              f'{meds:>10s} {st["p_halved"]*100:7.1f}% {st["p_ruin"]*100:7.1f}% '
              f'{worst_pct*100:9.0f}% {st["E_final_med"]:12,.0f}')
        sweep_rows.append({'risk_frac': rf, 'equity_mult_of_reqcap': round(eqx, 2),
                           'cap_for_30man': round(cap30, 0),
                           'p_reach_runrate': round(st['p_reach_runrate'], 4),
                           'runrate_med_month': round(st['runrate_med_month'], 1),
                           'p_halved': round(st['p_halved'], 4),
                           'p_ruin': round(st['p_ruin'], 4),
                           'worst_month_pct_of_equity': round(worst_pct, 4),
                           'E_final_med': round(st['E_final_med'], 0),
                           'E_final_p5': round(st['E_final_p5'], 0),
                           'E_final_p95': round(st['E_final_p95'], 0)})
    pd.DataFrame(sweep_rows).to_csv(HERE / 'grid_growth_sim_riskfrac.csv', index=False)

    print('\n' + '=' * 78)
    print('複利成長MC: 「AUDCAD単独開始」 vs 「4本小バスケット開始」(感度比較)')
    print('=' * 78)
    hdr = f'{"指標":28s} {"AUDCAD単独":>16s} {"4本バスケット":>16s}'
    print(hdr)
    def line(label, a, b, fmt):
        print(f'{label:28s} {fmt(a):>16s} {fmt(b):>16s}')
    money = lambda x: f'{x:,.0f}'
    pct = lambda x: f'{x*100:.1f}%'
    mo = lambda x: ('—' if (isinstance(x, float) and np.isnan(x)) else f'{x:.0f}ヶ月')
    line('unit req_cap(円/1単位)', st_s['unit_reqcap'], st_b['unit_reqcap'], money)
    line('ランレート到達自己資本', st_s['runrate_equity'], st_b['runrate_equity'], money)
    line('資本稼働率(活動月割合)', util_solo, util_basket, pct)
    line('P(月利30万到達|60mo)', st_s['p_reach_runrate'], st_b['p_reach_runrate'], pct)
    line('到達月数(中央)', st_s['runrate_med_month'], st_b['runrate_med_month'], mo)
    line('P(破産=全損)', st_s['p_ruin'], st_b['p_ruin'], pct)
    line('P(自己資本半減経験)', st_s['p_halved'], st_b['p_halved'], pct)
    line('P(5年後 元本割れ)', st_s['p_final_loss'], st_b['p_final_loss'], pct)
    line('自己資本60mo後(中央)', st_s['E_final_med'], st_b['E_final_med'], money)
    line('自己資本60mo後(P5)', st_s['E_final_p5'], st_b['E_final_p5'], money)
    line('自己資本60mo後(P95)', st_s['E_final_p95'], st_b['E_final_p95'], money)
    print('\nマイルストーン到達確率/中央到達月:')
    for m in MILESTONES:
        k = int(m/1e6*10)/10
        print(f'  {m/1e6:.1f}M : 単独 P={st_s[f"p_reach_{k}M"]*100:4.1f}% '
              f'med={st_s[f"med_month_{k}M"]:.0f}mo  |  '
              f'バスケット P={st_b[f"p_reach_{k}M"]*100:4.1f}% '
              f'med={st_b[f"med_month_{k}M"]:.0f}mo')

    # --- 成長カーブ分位(バスケット) ---
    qs = {'p5': 5, 'p50': 50, 'p95': 95}
    curve_rows = []
    for t in range(HORIZON + 1):
        row = {'month': t}
        for name, q in qs.items():
            row[f'basket_{name}'] = round(float(np.percentile(curve_b[:, t], q)), 0)
            row[f'solo_{name}'] = round(float(np.percentile(curve_s[:, t], q)), 0)
        curve_rows.append(row)
    pd.DataFrame(curve_rows).to_csv(HERE / 'grid_growth_sim_curve.csv', index=False)

    # --- result.csv ---
    def flat(prefix, st, util):
        d = {'mode': prefix, 'capital_utilization': round(util, 3)}
        for k, v in st.items():
            if isinstance(v, float):
                d[k] = round(v, 4) if abs(v) < 10 else round(v, 0)
            else:
                d[k] = v
        return d
    res = pd.DataFrame([flat('AUDCAD_solo', st_s, util_solo),
                        flat('basket_4', st_b, util_basket)])
    res.to_csv(HERE / 'grid_growth_sim_result.csv', index=False)
    print(f'\nsaved grid_growth_sim_result.csv / _curve.csv / _activity.csv / _riskfrac.csv')


if __name__ == '__main__':
    main()
