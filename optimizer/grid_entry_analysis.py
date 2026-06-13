"""
grid_entry_analysis.py - 静的最良 AUDCAD(atr1.5/ci65/lv5/fs-750k)の負けパターンをエントリー条件で診断。

問い: どのエントリー文脈(t-1特徴量)が float-stop/B48 で焼かれる負けポジションを生むか。
各グリッド・レベル(=1ポジション)にエントリー時の文脈を記録し、決済種別(TP=勝ち / fstop・b48=負け)で
セグメント分析する。全特徴量は t-1(shift済)=ルックアヘッド無し。

エンジンは grid_floatstop_bt.run_backtest を1:1踏襲し、ポジション単位の記録を追加しただけ
(集計値は静的エンジンと一致する設計)。

実行: python3 optimizer/grid_entry_analysis.py
出力: grid_entry_analysis_positions.csv (全ポジション記録) + console セグメント表
"""
import numpy as np, pandas as pd
from pathlib import Path
import grid_floatstop_bt as G, grid_insensitivity as GI

DATA = Path(__file__).resolve().parent.parent / 'data'
OUT = Path(__file__).resolve().parent
PAIR = 'AUDCAD'
CFG = {**GI.V7_CONFIG[PAIR], 'atr_mult': 1.5}   # 静的最良
CONTRACT = G.CONTRACT
IS_END = pd.Timestamp('2021-12-31', tz='UTC')


def load_duk(pair):
    d = pd.read_csv(DATA / f'{pair}_1h_dukas.csv')
    d['datetime'] = pd.to_datetime(d['datetime'], utc=True)
    return d.set_index('datetime')[['open', 'high', 'low', 'close']].sort_index().dropna()


def build_features(df, atr):
    """t-1 安全な特徴量配列群。"""
    close = df['close']
    atr_s = atr.reindex(df.index)
    # realized vol proxy (price-norm ATR), t-1
    voln = (atr_s / close).shift(1)
    # 24h / 72h リターン(ATR正規化), t-1 : トレンド/モメンタム強度
    ret24 = ((close - close.shift(24)) / atr_s).shift(1)
    ret72 = ((close - close.shift(72)) / atr_s).shift(1)
    # SMA100(1h)からの乖離(ATR正規化), t-1 : 中期トレンド位置
    sma100 = close.rolling(100).mean()
    dist_sma = ((close - sma100) / atr_s).shift(1)
    # ADX風: |方向移動|の強さ。ここでは |ret24| を代理に使う(=トレンド強度)
    return {
        'voln': voln.to_numpy(),
        'ret24': ret24.to_numpy(),
        'ret72': ret72.to_numpy(),
        'dist_sma': dist_sma.to_numpy(),
    }


def run_instrumented(pair, cfg, df, atr_series, ci_series, feats):
    qj = cfg.get('quote_jpy', 1.0)
    lot = cfg['lot']; atr_mult = cfg['atr_mult']; ci_threshold = cfg['ci_threshold']
    b48_hours = cfg['b48_hours']; max_levels = cfg['max_levels']; float_stop = cfg['float_stop']

    def pjpy(d): return d * lot * CONTRACT * qj

    idx = df.index
    highs = df['high'].to_numpy(); lows = df['low'].to_numpy(); closes = df['close'].to_numpy()
    atr_vals = atr_series.reindex(idx).to_numpy(); ci_vals = ci_series.reindex(idx).to_numpy()

    long_pos, short_pos = [], []
    b48_long_start = b48_short_start = None
    records = []   # per-position closed records

    def feat_at(i, side):
        f = {k: feats[k][i] for k in feats}
        # 「逆行モメンタム」= ラダーに不利な方向の24hリターン強度
        # long は価格下落(ret24<0)が不利, short は上昇(ret24>0)が不利
        f['adv_mom'] = (-f['ret24']) if side == 'long' else (f['ret24'])
        f['side'] = side
        f['hour'] = idx[i].hour
        f['ci'] = ci_vals[i]
        return f

    def close_pos(p, exit_price, kind, ts):
        if p['side'] == 'long':
            pnl = pjpy(exit_price - p['entry'])
        else:
            pnl = pjpy(p['entry'] - exit_price)
        rec = dict(p['feat']); rec.update({
            'entry_ts': p['ts'], 'exit_ts': ts, 'level': p['level'], 'kind': kind,
            'pnl': pnl, 'win': int(pnl >= 0), 'hold_h': (ts - p['ts']).total_seconds() / 3600.0,
            'is_oos': 'IS' if p['ts'] <= IS_END else 'OOS'})
        records.append(rec)

    for i in range(len(df)):
        atr = atr_vals[i]
        if np.isnan(atr) or atr <= 0:
            continue
        ts = idx[i]; gw = atr * atr_mult
        bar_h, bar_l, bar_cl = highs[i], lows[i], closes[i]
        ci = ci_vals[i]

        long_was_max = len(long_pos) >= max_levels
        short_was_max = len(short_pos) >= max_levels

        for p in [p for p in long_pos if bar_h >= p['tp']]:
            close_pos(p, p['tp'], 'tp', ts); long_pos.remove(p)
        for p in [p for p in short_pos if bar_l <= p['tp']]:
            close_pos(p, p['tp'], 'tp', ts); short_pos.remove(p)

        if long_pos:
            unreal = sum(pjpy(bar_l - p['entry']) for p in long_pos)
            if unreal <= float_stop:
                for p in list(long_pos): close_pos(p, bar_l, 'fstop', ts)
                long_pos = []; b48_long_start = None
        if short_pos:
            unreal = sum(pjpy(p['entry'] - bar_h) for p in short_pos)
            if unreal <= float_stop:
                for p in list(short_pos): close_pos(p, bar_h, 'fstop', ts)
                short_pos = []; b48_short_start = None

        if long_was_max and len(long_pos) < max_levels: b48_long_start = None
        if short_was_max and len(short_pos) < max_levels: b48_short_start = None

        if b48_long_start is not None and (ts - b48_long_start).total_seconds()/3600.0 >= b48_hours:
            for p in list(long_pos): close_pos(p, bar_cl, 'b48', ts)
            long_pos = []; b48_long_start = None
        if b48_short_start is not None and (ts - b48_short_start).total_seconds()/3600.0 >= b48_hours:
            for p in list(short_pos): close_pos(p, bar_cl, 'b48', ts)
            short_pos = []; b48_short_start = None

        ci_ok = (not np.isnan(ci)) and (ci > ci_threshold)
        if len(long_pos) == 0:
            if ci_ok:
                long_pos.append({'entry': bar_cl, 'tp': bar_cl+gw, 'side': 'long', 'ts': ts,
                                 'level': 1, 'feat': feat_at(i, 'long')})
                if len(long_pos) == max_levels: b48_long_start = ts
        elif len(long_pos) < max_levels:
            if bar_cl <= min(p['entry'] for p in long_pos) - gw and ci_ok:
                long_pos.append({'entry': bar_cl, 'tp': bar_cl+gw, 'side': 'long', 'ts': ts,
                                 'level': len(long_pos)+1, 'feat': feat_at(i, 'long')})
                if len(long_pos) == max_levels: b48_long_start = ts

        if len(short_pos) == 0:
            if ci_ok:
                short_pos.append({'entry': bar_cl, 'tp': bar_cl-gw, 'side': 'short', 'ts': ts,
                                  'level': 1, 'feat': feat_at(i, 'short')})
                if len(short_pos) == max_levels: b48_short_start = ts
        elif len(short_pos) < max_levels:
            if bar_cl >= max(p['entry'] for p in short_pos) + gw and ci_ok:
                short_pos.append({'entry': bar_cl, 'tp': bar_cl-gw, 'side': 'short', 'ts': ts,
                                  'level': len(short_pos)+1, 'feat': feat_at(i, 'short')})
                if len(short_pos) == max_levels: b48_short_start = ts

    return pd.DataFrame(records)


def seg_table(df, col, bins, labels):
    df = df.copy()
    df['bucket'] = pd.cut(df[col], bins=bins, labels=labels)
    g = df.groupby('bucket', observed=True)
    out = g.agg(n=('pnl', 'size'), win_rate=('win', 'mean'),
               gross_win=('pnl', lambda s: s[s >= 0].sum()),
               gross_loss=('pnl', lambda s: -s[s < 0].sum()),
               net=('pnl', 'sum'))
    out['pf'] = out['gross_win'] / out['gross_loss'].replace(0, np.nan)
    return out


def main():
    df = load_duk(PAIR); atr = G.compute_atr_series(df); ci = G.compute_ci_series(df)
    feats = build_features(df, atr)
    rec = run_instrumented(PAIR, CFG, df, atr, ci, feats)
    rec.to_csv(OUT / 'grid_entry_analysis_positions.csv', index=False)

    # 整合チェック
    chk = G.run_backtest(PAIR, CFG, df, atr, ci)
    net_rec = rec['pnl'].sum()
    print(f'整合: engine net={chk["total_pnl"]:,.0f}  instrumented net={net_rec:,.0f}  '
          f'nTP={ (rec.kind=="tp").sum() } nFS_pos={ (rec.kind=="fstop").sum() } '
          f'nB48_pos={ (rec.kind=="b48").sum() }  (engine nFS={chk["n_fstop"]} nB48={chk["n_b48"]})')

    print(f'\n総ポジション={len(rec)}  勝(TP)={int(rec.win.sum())}  負={int((1-rec.win).sum())}  '
          f'全体WR={rec.win.mean():.3f}  net={net_rec:,.0f}')

    # 決済種別ごとの損益寄与
    print('\n=== 決済種別ごとの損益寄与 ===')
    g = rec.groupby('kind').agg(n=('pnl','size'), net=('pnl','sum'),
                                gloss=('pnl', lambda s:-s[s<0].sum()), gwin=('pnl', lambda s:s[s>=0].sum()))
    print(g.to_string())

    print('\n=== ラダー深さ(level)別 ===')
    print(rec.groupby('level').agg(n=('pnl','size'), win_rate=('win','mean'),
          net=('pnl','sum'), gloss=('pnl', lambda s:-s[s<0].sum())).to_string())

    print('\n=== 逆行モメンタム adv_mom(24hリターン/ATR, ラダー不利方向>0) 別 ===')
    print(seg_table(rec, 'adv_mom', [-np.inf,-2,-1,0,1,2,np.inf],
                    ['<-2','-2..-1','-1..0','0..1','1..2','>2']).to_string())

    print('\n=== SMA100乖離 dist_sma(ATR正規化) 別 (ラダー方向問わず絶対位置) ===')
    print(seg_table(rec, 'dist_sma', [-np.inf,-3,-1.5,0,1.5,3,np.inf],
                    ['<-3','-3..-1.5','-1.5..0','0..1.5','1.5..3','>3']).to_string())

    print('\n=== ボラ voln(price-norm ATR, t-1) 別 ===')
    qs = np.nanquantile(rec['voln'], [0.2,0.4,0.6,0.8])
    print(seg_table(rec, 'voln', [-np.inf,*qs,np.inf],
                    ['Q1低','Q2','Q3','Q4','Q5高']).to_string())

    print('\n=== entry時CI(>65) 別 ===')
    print(seg_table(rec, 'ci', [65,67,70,75,np.inf], ['65-67','67-70','70-75','>75']).to_string())

    # 負けポジ(fstop/b48)のみの文脈プロファイル
    loss = rec[rec.kind != 'tp']
    print(f'\n=== 負けポジ(fstop+b48) n={len(loss)} の文脈(平均) vs 勝ち(TP) ===')
    tp = rec[rec.kind == 'tp']
    for c in ['level','adv_mom','ret24','ret72','dist_sma','voln','hold_h','ci']:
        print(f'  {c:9s} loss平均={loss[c].mean():8.3f}   win平均={tp[c].mean():8.3f}')
    print(f'\nsaved positions -> {OUT/"grid_entry_analysis_positions.csv"}')


if __name__ == '__main__':
    main()
