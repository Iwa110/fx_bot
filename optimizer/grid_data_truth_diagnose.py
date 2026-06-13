"""
grid_data_truth_diagnose.py - Step 0: yfinance vs Dukascopy のH1バー真値診断。

目的:
  「v7 PFはyfinanceでは高い/Dukascopyでは崩落」の真因 = yfinance時間足が高安(ヒゲ)を
  過小報告し TP が刺さりやすく float-stop が過少発火 → PF過大、という仮説をバー単位で確定。

手法:
  1) 同一2年窓(yfが持つ範囲)で yf と duk を同一タイムスタンプにinner-join。
  2) H1レンジ(high-low)の分布を比較: 中央値比 duk/yf, 平均比, レンジ拡大バー率。
  3) ヒゲ(上ヒゲ=high-max(open,close) / 下ヒゲ=min(open,close)-low)の中央値比。
  4) Grid執行影響の代理指標:
       - 「TPが当バーで刺さるか」= 各バーで close±gw (gw=ATR*atr_mult) に high/low が到達する
         回数を yf/duk で数える(同一 close・同一 ATR(duk基準) で判定 → ヒゲの差だけが効く)。
       - 「float-stop方向の逆行extreme」= low(long)/high(short) の到達度。
  5) (裏取り) optimizer/history.csv の実約定価格が overlap期間で Dukascopy バーの high-low 内に
     収まるか = Dukascopy が実機ブローカー価格水準に整合するかの sanity check。

出力: grid_data_truth_diagnose_result.csv + console
実行: python3 optimizer/grid_data_truth_diagnose.py
"""

import numpy as np
import pandas as pd
from pathlib import Path

import grid_floatstop_bt as G
import grid_insensitivity as GI

DATA = Path(__file__).resolve().parent.parent / 'data'
OUT = Path(__file__).resolve().parent / 'grid_data_truth_diagnose_result.csv'
HIST = Path(__file__).resolve().parent / 'history.csv'

PAIRS = ['GBPJPY', 'CHFJPY', 'NZDJPY', 'AUDCAD', 'USDJPY']


def load_duk(pair):
    df = pd.read_csv(DATA / f'{pair}_1h_dukas.csv')
    df['datetime'] = pd.to_datetime(df['datetime'], utc=True)
    return df.set_index('datetime')[['open', 'high', 'low', 'close']].sort_index().dropna()


def load_yf(pair):
    df = pd.read_csv(DATA / f'{pair}_1h.csv', index_col=0)
    df.index = pd.to_datetime(df.index, utc=True)
    return df[['open', 'high', 'low', 'close']].sort_index().dropna()


def diagnose(pair):
    duk, yf = load_duk(pair), load_yf(pair)
    # 同一タイムスタンプで内部結合(yfの範囲に限定)
    j = yf.join(duk, lsuffix='_yf', rsuffix='_dk', how='inner').dropna()
    if len(j) < 500:
        return None
    rng_yf = (j['high_yf'] - j['low_yf'])
    rng_dk = (j['high_dk'] - j['low_dk'])
    # ヒゲ
    up_yf = j['high_yf'] - j[['open_yf', 'close_yf']].max(axis=1)
    up_dk = j['high_dk'] - j[['open_dk', 'close_dk']].max(axis=1)
    dn_yf = j[['open_yf', 'close_yf']].min(axis=1) - j['low_yf']
    dn_dk = j[['open_dk', 'close_dk']].min(axis=1) - j['low_dk']

    eps = 1e-9
    # 同一 close・同一 ATR(duk基準) で TP刺さりを比較 → ヒゲ差だけが効く
    atr_dk = G.compute_atr_series(duk).reindex(j.index)
    cfg = GI.V7_CONFIG[pair] if pair in GI.V7_CONFIG else {'atr_mult': 1.5}
    gw = atr_dk * cfg['atr_mult']
    cl = j['close_dk']
    tp_up = cl + gw
    tp_dn = cl - gw
    # 「次バー無し近似」: 当バー内で TP(=close±gw) に到達したバー率(long/short合算の代理)
    hit_up_yf = (j['high_yf'] >= tp_up).mean()
    hit_up_dk = (j['high_dk'] >= tp_up).mean()
    hit_dn_yf = (j['low_yf'] <= tp_dn).mean()
    hit_dn_dk = (j['low_dk'] <= tp_dn).mean()

    res = {
        'pair': pair, 'n_bars': len(j),
        'span': f'{j.index[0].date()}~{j.index[-1].date()}',
        'rng_med_yf': round(rng_yf.median(), 5),
        'rng_med_dk': round(rng_dk.median(), 5),
        'rng_med_ratio_dk_yf': round(rng_dk.median() / (rng_yf.median() + eps), 3),
        'rng_mean_ratio_dk_yf': round(rng_dk.mean() / (rng_yf.mean() + eps), 3),
        'dk_wider_bar_pct': round((rng_dk > rng_yf).mean() * 100, 1),
        'upwick_med_ratio_dk_yf': round((up_dk.median() + eps) / (up_yf.median() + eps), 3),
        'dnwick_med_ratio_dk_yf': round((dn_dk.median() + eps) / (dn_yf.median() + eps), 3),
        'tp_up_hit_yf_pct': round(hit_up_yf * 100, 2),
        'tp_up_hit_dk_pct': round(hit_up_dk * 100, 2),
        'tp_dn_hit_yf_pct': round(hit_dn_yf * 100, 2),
        'tp_dn_hit_dk_pct': round(hit_dn_dk * 100, 2),
    }
    # ヒゲ差がTP刺さりに与える符号: yfの方がhit率高ければ「yfでTP刺さりやすい=PF過大」を支持
    res['tp_hit_yf_over_dk'] = round(
        (hit_up_yf + hit_dn_yf) / (hit_up_dk + hit_dn_dk + eps), 3)
    return res


def hist_sanity():
    """history.csv 実約定価格が overlap期間で Dukascopy バー range 内かを確認。"""
    if not HIST.exists():
        return []
    h = pd.read_csv(HIST)
    h['open_time'] = pd.to_datetime(h['open_time'], format='%Y.%m.%d %H:%M:%S', utc=True)
    rows = []
    for pair in PAIRS:
        sub = h[h['symbol'] == pair].copy()
        if len(sub) == 0:
            continue
        duk = load_duk(pair)
        # 約定時刻を1h床に丸めて対応バーと照合
        sub['bar'] = sub['open_time'].dt.floor('1h')
        merged = sub.merge(duk, left_on='bar', right_index=True, how='inner')
        if len(merged) == 0:
            continue
        # 価格水準ズレ(pip相当): 約定価格とバーcloseの差の中央値, range内率
        pip = 0.01 if pair.endswith('JPY') else 0.0001
        within = ((merged['open_price'] >= merged['low'] - 5 * pip) &
                  (merged['open_price'] <= merged['high'] + 5 * pip)).mean()
        dev = (merged['open_price'] - merged['close']).abs().median() / pip
        rows.append({'pair': pair, 'n_fills': len(merged),
                     'overlap_span': f"{merged['bar'].min().date()}~{merged['bar'].max().date()}",
                     'fill_within_dukbar_pct': round(within * 100, 1),
                     'median_dev_from_close_pips': round(dev, 2)})
    return rows


def matched_engine_test():
    """同一エンジン・同一timestamp集合(inner join)で yf-bars vs duk-bars の PF を直接比較。
    → PF差を『データソースのみ』に帰属させる最もクリーンな証拠。"""
    rows = []
    for pair in ['GBPJPY', 'CHFJPY', 'NZDJPY', 'AUDCAD']:
        cfg = GI.V7_CONFIG[pair]
        dk, yf = load_duk(pair), load_yf(pair)
        idx = yf.index.intersection(dk.index)
        yfj, dkj = yf.loc[idx], dk.loc[idx]
        ry = G.run_backtest(pair, cfg, yfj, G.compute_atr_series(yfj), G.compute_ci_series(yfj))
        rd = G.run_backtest(pair, cfg, dkj, G.compute_atr_series(dkj), G.compute_ci_series(dkj))
        rows.append({'pair': pair, 'n_matched': len(idx),
                     'pf_yf': ry['pf'], 'pf_dk': rd['pf'],
                     'net_yf': ry['total_pnl'], 'net_dk': rd['total_pnl'],
                     'nFS_yf': ry['n_fstop'], 'nFS_dk': rd['n_fstop'],
                     'nTP_yf': ry['n_tp'], 'nTP_dk': rd['n_tp']})
    return rows


def main():
    print('=== Step 0: yfinance vs Dukascopy H1バー真値診断 (同一窓・同一timestamp) ===\n')
    print(f'{"pair":7s} {"n":>6s} {"rngMedRatio":>11s} {"dkWider%":>9s} '
          f'{"upWick":>7s} {"dnWick":>7s} {"TPhit_yf%":>9s} {"TPhit_dk%":>9s} {"yf/dk":>6s}')
    rows = []
    for p in PAIRS:
        r = diagnose(p)
        if r is None:
            print(f'{p:7s}  [データ不足]'); continue
        rows.append(r)
        tp_yf = r['tp_up_hit_yf_pct'] + r['tp_dn_hit_yf_pct']
        tp_dk = r['tp_up_hit_dk_pct'] + r['tp_dn_hit_dk_pct']
        print(f'{p:7s} {r["n_bars"]:6d} {r["rng_med_ratio_dk_yf"]:11.3f} '
              f'{r["dk_wider_bar_pct"]:9.1f} {r["upwick_med_ratio_dk_yf"]:7.2f} '
              f'{r["dnwick_med_ratio_dk_yf"]:7.2f} {tp_yf:9.2f} {tp_dk:9.2f} '
              f'{r["tp_hit_yf_over_dk"]:6.3f}')

    print('\n  rngMedRatio>1 = Dukascopyのレンジが広い(=yfがヒゲ過小報告)')
    print('  TPhit_yf% > TPhit_dk% = 同一close/ATRでyfの方がTP刺さりやすい → yfがPF過大化')

    print('\n=== history.csv 実約定 ↔ Dukascopyバー 整合性 (Dukascopy≈実機の裏取り) ===')
    print(f'{"pair":7s} {"nFills":>6s} {"within%":>8s} {"medDev(pip)":>11s}  span')
    hrows = hist_sanity()
    for r in hrows:
        print(f'{r["pair"]:7s} {r["n_fills"]:6d} {r["fill_within_dukbar_pct"]:8.1f} '
              f'{r["median_dev_from_close_pips"]:11.2f}  {r["overlap_span"]}')
    if not hrows:
        print('  [history.csv に対象ペアの約定なし / overlap外]')

    print('\n=== 同一エンジン・同一timestamp集合で yf-bars vs duk-bars 直接比較 ===')
    print('  (PF差をデータソースのみに帰属。窓・本数を完全一致させた最クリーン証拠)')
    print(f'{"pair":7s} {"nMatch":>6s} {"PF_yf":>6s} {"PF_dk":>6s} {"net_yf":>12s} {"net_dk":>12s} {"nFS_yf":>6s} {"nFS_dk":>6s}')
    me = matched_engine_test()
    for r in me:
        print(f'{r["pair"]:7s} {r["n_matched"]:6d} {r["pf_yf"]:6.2f} {r["pf_dk"]:6.2f} '
              f'{r["net_yf"]:12,.0f} {r["net_dk"]:12,.0f} {r["nFS_yf"]:6d} {r["nFS_dk"]:6d}')

    if rows:
        pd.DataFrame(rows).to_csv(OUT, index=False)
        pd.DataFrame(me).to_csv(str(OUT).replace('.csv', '_matched.csv'), index=False)
        print(f'\nsaved {OUT}')


if __name__ == '__main__':
    main()
