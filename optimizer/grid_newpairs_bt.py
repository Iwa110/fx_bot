"""
grid_newpairs_bt.py - Grid戦略の特性(=相関通貨ペアの平均回帰)から選んだ新ペアで、
グリッド+モメンタム・ゲートが有効かを検証。

選定理由(戦略特性): グリッドは平均回帰エンジン=レンジで稼ぎトレンドで焼ける。
AUDCAD(AUD/CAD=共に資源通貨→独立トレンド乏しい)が唯一のGo。同じ論理で「2通貨が同じ
ドライバで動く相関クロス」が有望:
  AUDNZD(共にアンティポデアン資源通貨, AUDCADより高相関) / EURGBP(共に欧州, 低vol典型レンジ) /
  EURCHF(共に欧州安全資産, ただしSNB介入テールあり)。
No-Goは全てJPYクロス(キャリー/リスクでトレンド)=グリッドに不利、と整合。

検証: AUDCADの静的最良テンプレ(atr1.5/ci65/lv5)をそのまま適用(=構成は再最適化しない、転移性テスト)。
float_stop は quote_jpy 比でスケールし price-distance-to-stop を AUDCAD と一致させる(PFはqjに不感)。
モメンタム・ゲート thr=2.0(AUDCADで決めた値, 再チューニングせず)=真の構造的out-of-sample。
参考に atr_mult を IS で軽くスイープ(1.0/1.5/2.0)し自然なbackboneも確認。

真値=Dukascopy 11.5yr 1h。t-1。エンジン=grid_entry_filter_bt(static一致確認済+モメンタムゲート)。
実行: python3 optimizer/grid_newpairs_bt.py  出力: grid_newpairs_bt_result.csv
"""
import numpy as np, pandas as pd
from pathlib import Path
import grid_floatstop_bt as G
import grid_entry_filter_bt as EF

DATA = Path(__file__).resolve().parent.parent / 'data'
OUT = Path(__file__).resolve().parent / 'grid_newpairs_bt_result.csv'
IS_WIN = ('2015-01-01', '2021-12-31'); OOS_WIN = ('2022-01-01', '2026-12-31')
WFO_YEARS = [2022, 2023, 2024, 2025]

# quote_jpy 概算(クロスのquote通貨/JPY)。float_stop= -750k*(qj/108) で AUDCAD と price距離一致。
NEWPAIRS = {
    'AUDNZD': {'quote_jpy': 90.0},    # quote=NZD
    'EURGBP': {'quote_jpy': 190.0},   # quote=GBP
    'EURCHF': {'quote_jpy': 170.0},   # quote=CHF
}
FS_AUDCAD = -750_000.0; QJ_AUDCAD = 108.0


def load_duk(pair):
    d = pd.read_csv(DATA / f'{pair}_1h_dukas.csv')
    d['datetime'] = pd.to_datetime(d['datetime'], utc=True)
    return d.set_index('datetime')[['open', 'high', 'low', 'close']].sort_index().dropna()


def base_cfg(pair, atr_mult=1.5):
    qj = NEWPAIRS[pair]['quote_jpy']
    return {'atr_mult': atr_mult, 'ci_threshold': 65.0, 'b48_hours': 48, 'lot': 1.0,
            'max_levels': 5, 'float_stop': round(FS_AUDCAD * qj / QJ_AUDCAD, 0), 'quote_jpy': qj}


def metrics(cfg, df, atr, ci, ret24, mom_thr=None):
    def w(lo=None, hi=None):
        m = EF.win_mask(df, lo, hi); sub = df[m]
        if len(sub) < 300: return None
        return EF.run_bt(cfg, sub, atr, ci, ret24[m], mom_thr)
    full = w(); isr = w(*IS_WIN); oos = w(*OOS_WIN)
    wfo = [w(f'{y}-01-01', f'{y}-12-31') for y in WFO_YEARS]
    wfo = np.array([r['pf'] for r in wfo if r and r['n_tp'] >= 10])
    return {'full_pf': full['pf'], 'full_net': full['total_pnl'], 'full_dd': full['max_dd'],
            'full_nfs': full['n_fstop'], 'full_nb48': full['n_b48'], 'full_worst': full['worst_event'],
            'full_ntp': full['n_tp'], 'is_pf': isr['pf'] if isr else float('nan'),
            'oos_pf': oos['pf'], 'oos_net': oos['total_pnl'], 'oos_dd': oos['max_dd'],
            'wfo_med': float(np.median(wfo)) if len(wfo) else float('nan'),
            'wfo_min': float(wfo.min()) if len(wfo) else float('nan'),
            'wfo_gt12': float((wfo > 1.2).mean()) if len(wfo) else float('nan'),
            'wfo_each': [round(x, 2) for x in wfo]}


def show(tag, m):
    print(f'{tag:24s} fPF={m["full_pf"]:.2f} net={m["full_net"]:>12,.0f} DD={m["full_dd"]:>10,.0f} '
          f'nFS={m["full_nfs"]:2d} nB48={m["full_nb48"]:2d} worst={m["full_worst"]:>11,.0f} nTP={m["full_ntp"]:4d} | '
          f'IS={m["is_pf"]:.2f} OOS={m["oos_pf"]:.2f} OOSdd={m["oos_dd"]:>10,.0f} | '
          f'WFOmed={m["wfo_med"]:.2f} min={m["wfo_min"]:.2f} >1.2={m["wfo_gt12"]:.2f} {m["wfo_each"]}')


def main():
    rows = []
    for pair in NEWPAIRS:
        try:
            df = load_duk(pair)
        except FileNotFoundError:
            print(f'[{pair}] データ未取得, skip'); continue
        atr = G.compute_atr_series(df); ci = G.compute_ci_series(df)
        ret24 = EF.ret24_series(df, atr)
        print(f'\n{"="*120}\n{pair}  期間 {df.index[0].date()}~{df.index[-1].date()} ({len(df)}本)  '
              f'price≈{df["close"].iloc[-1]:.4f}  fs={base_cfg(pair)["float_stop"]:,.0f}(qj{NEWPAIRS[pair]["quote_jpy"]:.0f})\n{"="*120}')

        # atr backbone を IS で確認
        print('  -- atr_mult IS スイープ(参考: 自然なbackbone) --')
        for am in [1.0, 1.5, 2.0]:
            m = metrics(base_cfg(pair, am), df, atr, ci, ret24, None)
            mark = ' <-template' if am == 1.5 else ''
            print(f'    atr={am}: full PF={m["full_pf"]:.2f} IS={m["is_pf"]:.2f} OOS={m["oos_pf"]:.2f} '
                  f'WFOmed={m["wfo_med"]:.2f} nFS={m["full_nfs"]}{mark}')

        cfg = base_cfg(pair, 1.5)
        base_m = metrics(cfg, df, atr, ci, ret24, None)
        mom_m = metrics(cfg, df, atr, ci, ret24, 2.0)
        print('  -- baseline(atr1.5テンプレ) vs +モメンタムゲート(thr2.0, 再チューニングなし) --')
        show('  baseline', base_m)
        show('  +mom_gate2.0', mom_m)
        rows.append({'pair': pair, 'variant': 'baseline', **{k: v for k, v in base_m.items() if k != 'wfo_each'}})
        rows.append({'pair': pair, 'variant': 'mom2.0', **{k: v for k, v in mom_m.items() if k != 'wfo_each'}})

    pd.DataFrame(rows).to_csv(OUT, index=False)
    print(f'\nsaved {OUT}')
    print('\n判定軸: グリッド自体のエッジ= full/OOS PF>1.2 ∧ WFOmed>1.2 ∧ wfo_min>1.0。'
          'モメンタムゲート有効性= base比でPF/WFO維持〜向上 ∧ nFS/worst悪化なし。')


if __name__ == '__main__':
    main()
