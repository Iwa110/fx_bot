"""
intervention_event_study.py - USDJPY 為替介入イベントの実測・較正

目的:
    案B(反応型ショート)/ 案D(介入後の押し目買い=キャリー順方向)の
    トリガー閾値とターゲットを、過去の MOF 円買い介入から実測して較正する。
    介入はサンプルが極小(歴史的に十数イベント)のため、本スクリプトは
    "統計的エッジ証明" ではなく "プレイブック較正" を目的とする。

データ:
    data/USDJPY_5m_10y.csv(.gz)  columns: datetime,open,high,low,close,volume
    UTC naive / BID側OHLC / 2015-12 .. 2026-06。
    2022(9/22,10/21,10/24)・2024(4/29,5/1,7/11,7/12)・2026(4-5月,11.7兆円)を内包。

ATR整合(実機 risk_manager 準拠):
    1h足ATR14(Wilder ewm, span=14, adjust=False)を5m足にマップ(直近確定1h足を参照)。
    介入の "速度" 判定は ATR で正規化する(2015年120円台と2026年160円台の
    絶対pip差・ボラレジーム差を吸収)。

2つの分析:
  (1) KNOWN_EVENTS  : 文書化された介入日近傍で最鋭の下落レッグを実測。
  (2) SPIKE DETECTOR: 全期間を走査し "介入シグネチャ"(短時間の大幅JPY高=USDJPY急落)
                      を検出。2026の正確な日時を私が知らなくても捕捉できる。
  各スパイクで:
    案B指標 : 検出点から更にどれだけ下落フォローするか(1h/3h/6h)・最大逆行(ストップ較正)。
    案D指標 : 谷からの反転(50%/100%リトレースまでの営業日数・回復率)。

実行:
    python optimizer/intervention_event_study.py
    出力: コンソールサマリ + optimizer/intervention_event_study_result.csv
"""

import argparse
from pathlib import Path

import numpy as np
import pandas as pd

DATA_DIR = Path(__file__).resolve().parent.parent / 'data'

BAR_MIN   = 5                      # 5m足
PER_HOUR  = 60 // BAR_MIN          # 12本/h
ATR_N     = 14

# 文書化された MOF 円買い介入(UTC 日付)。日内の正確時刻は相場から検出する。
KNOWN_EVENTS = [
    ('2022-09-22', '2022 1st (since1998) ~145.9->140'),
    ('2022-10-21', '2022 stealth late-NY ~152->144'),
    ('2022-10-24', '2022 Tokyo-am follow'),
    ('2024-04-29', '2024 holiday thin ~160->154'),
    ('2024-05-01', '2024 post-FOMC late-NY ~158->153'),
    ('2024-07-11', '2024 US-CPI ~162->157'),
    ('2024-07-12', '2024 follow-up'),
]


# ─────────────────────────────────────────────────────────────
def load_5m(sym: str = 'USDJPY', suffix: str = '_5m_10y') -> pd.DataFrame:
    path = DATA_DIR / f'{sym}{suffix}.csv'
    if not path.exists():
        gz = DATA_DIR / f'{sym}{suffix}.csv.gz'
        if not gz.exists():
            raise FileNotFoundError(f'{path} / {gz} not found')
        path = gz
    df = pd.read_csv(path, parse_dates=['datetime'])
    return df.sort_values('datetime').reset_index(drop=True)


def add_atr_1h(df: pd.DataFrame) -> pd.DataFrame:
    """1h足ATR14(Wilder ewm)を算出し、各5m足に直近確定1h足のATRをマップ。"""
    h1 = (df.set_index('datetime')
            .resample('1h')
            .agg(open=('open', 'first'), high=('high', 'max'),
                 low=('low', 'min'), close=('close', 'last'))
            .dropna())
    pc = h1['close'].shift(1)
    tr = pd.concat([(h1['high'] - h1['low']),
                    (h1['high'] - pc).abs(),
                    (h1['low'] - pc).abs()], axis=1).max(axis=1)
    h1['atr1h'] = tr.ewm(alpha=1.0 / ATR_N, adjust=False).mean()
    # 直近 "確定" 1h足を参照(lookahead排除): その1h足の終了時刻+でmerge_asof
    atr = h1[['atr1h']].copy()
    atr.index = atr.index + pd.Timedelta(hours=1)   # 確定時刻 = バー終端
    atr = atr.reset_index().rename(columns={'datetime': 'avail_at'})
    out = pd.merge_asof(df, atr, left_on='datetime', right_on='avail_at',
                        direction='backward')
    out['atr1h'] = out['atr1h'].bfill()
    return out


def trading_days_between(t0: pd.Timestamp, t1: pd.Timestamp) -> float:
    """概算営業日数(暦日 * 5/7)。リトレース速度の目安。"""
    return (t1 - t0).total_seconds() / 86400.0 * 5.0 / 7.0


# ─────────────────────────────────────────────────────────────
def measure_leg(df: pd.DataFrame, i_detect: int) -> dict:
    """検出インデックス i_detect を起点に、案B/案D指標を計測。

    - pre_high   : 検出前2hの高値(介入前の水準。ショートの基準/Dの回復目標)
    - trough     : 検出後6hの安値(介入レッグの底)
    - total_drop : pre_high - trough (介入レッグ全体の振れ幅, yen)
    - drop_to_det: pre_high - close[detect] (検出時点までに既に落ちた分)
    - postB_*    : 検出 close から更に下落した分(=案Bで乗った後の取り分), yen
    - maxadv_*   : 検出後Wの最大逆行(検出closeより上にどれだけ戻したか, ストップ較正), yen
    - revD_*     : trough から pre_high 方向への回復(案D)。50%/100%到達の営業日数。
    """
    close = df['close'].values
    high  = df['high'].values
    low   = df['low'].values
    t     = df['datetime'].values
    n     = len(df)

    w2h  = 2 * PER_HOUR
    w6h  = 6 * PER_HOUR
    pre_lo = max(0, i_detect - w2h)
    pre_high = float(high[pre_lo:i_detect + 1].max())
    c_det = float(close[i_detect])

    post_hi = min(n, i_detect + w6h)
    seg_low = low[i_detect:post_hi]
    trough = float(seg_low.min())
    i_trough = i_detect + int(seg_low.argmin())

    out = {
        'pre_high': round(pre_high, 3),
        'c_detect': round(c_det, 3),
        'trough': round(trough, 3),
        'total_drop': round(pre_high - trough, 3),
        'drop_to_detect': round(pre_high - c_det, 3),
        't_trough': pd.Timestamp(t[i_trough]),
    }

    # 案B: 検出後の追随下落(取り分) と 最大逆行(ストップ)
    for label, wh in [('1h', PER_HOUR), ('3h', 3 * PER_HOUR), ('6h', 6 * PER_HOUR)]:
        j = min(n, i_detect + wh)
        seg_lo = low[i_detect:j]
        seg_hi = high[i_detect:j]
        out[f'postB_drop_{label}'] = round(c_det - float(seg_lo.min()), 3)  # 更に下げた最大幅
        out[f'maxadv_{label}']     = round(float(seg_hi.max()) - c_det, 3)  # 検出後の最大上振れ

    # 案D: trough から pre_high への回復。tgt50 = trough + 0.5*total_drop, tgt100 = pre_high
    tot = pre_high - trough
    if tot <= 0:
        out['revD_50_days'] = np.nan
        out['revD_100_days'] = np.nan
        out['revD_max_recover'] = 0.0
        out['revD_horizon_days'] = np.nan
        return out
    fwd_hi = high[i_trough:]
    fwd_t  = t[i_trough:]
    tgt50  = trough + 0.5 * tot
    tgt100 = pre_high
    d50 = d100 = np.nan
    run_max = trough
    for k in range(len(fwd_hi)):
        if fwd_hi[k] > run_max:
            run_max = fwd_hi[k]
        if np.isnan(d50) and fwd_hi[k] >= tgt50:
            d50 = trading_days_between(pd.Timestamp(fwd_t[0]), pd.Timestamp(fwd_t[k]))
        if np.isnan(d100) and fwd_hi[k] >= tgt100:
            d100 = trading_days_between(pd.Timestamp(fwd_t[0]), pd.Timestamp(fwd_t[k]))
            break
    out['revD_50_days']    = round(d50, 1) if not np.isnan(d50) else np.nan
    out['revD_100_days']   = round(d100, 1) if not np.isnan(d100) else np.nan
    out['revD_max_recover'] = round((run_max - trough) / tot, 2)  # 観測窓内の最大回復率(0-1+)
    out['revD_horizon_days'] = round(
        trading_days_between(pd.Timestamp(fwd_t[0]), pd.Timestamp(fwd_t[-1])), 0)

    # 案Dテール: 検出後 N営業日以内に trough を更にどれだけ割り込むか(押し目買いの最大含み損)。
    # "良い"介入(lean)はtroughで底打ち→ほぼ割れない。"悪い"介入(マクロ転換)はtroughを割って延々下落。
    fwd_lo = low[i_detect:]
    fwd_t2 = t[i_detect:]
    for nd in (10, 20):
        horizon = pd.Timestamp(fwd_t2[0]) + pd.Timedelta(days=int(nd * 7 / 5))
        mask = fwd_t2 <= np.datetime64(horizon)
        seg = fwd_lo[mask]
        if len(seg) == 0:
            out[f'revD_below_trough_{nd}d'] = np.nan
        else:
            # trough(=6h底) を更に割り込んだ最大幅(yen, 正 = 余計に沈んだ)
            out[f'revD_below_trough_{nd}d'] = round(max(0.0, trough - float(seg.min())), 3)
    return out


def detect_spikes(df: pd.DataFrame, win_min: int, atr_mult: float,
                  min_yen: float, cluster_h: int) -> list:
    """介入シグネチャ検出: win_min分での下落が ATR1h*atr_mult かつ min_yen 以上。
    cluster_h 時間以内の連続検出は1イベントに統合(最鋭点を代表に)。"""
    w = win_min // BAR_MIN
    close = df['close'].values
    atr   = df['atr1h'].values
    n = len(df)
    drop_w = np.full(n, 0.0)
    drop_w[w:] = close[:-w] - close[w:]           # 正 = 下落幅(yen)
    thr = np.maximum(atr * atr_mult, min_yen)
    is_spike = drop_w >= thr
    idxs = np.where(is_spike)[0]
    if len(idxs) == 0:
        return []
    # クラスタリング
    clu_gap = cluster_h * PER_HOUR
    clusters = []
    cur = [idxs[0]]
    for k in idxs[1:]:
        if k - cur[-1] <= clu_gap:
            cur.append(k)
        else:
            clusters.append(cur)
            cur = [k]
    clusters.append(cur)
    reps = []
    for c in clusters:
        # 代表 = 窓内下落が最大の点
        rep = c[int(np.argmax(drop_w[c]))]
        reps.append(rep)
    return reps


def nearest_known(ts: pd.Timestamp) -> str:
    for d, label in KNOWN_EVENTS:
        ev = pd.Timestamp(d)
        if abs((ts.normalize() - ev).days) <= 1:
            return label
    if ts >= pd.Timestamp('2026-04-20') and ts <= pd.Timestamp('2026-05-31'):
        return '2026 Apr-May campaign (11.7T)'
    return ''


# ─────────────────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--win-min', type=int, default=60,
                    help='速度判定の窓(分). default 60')
    ap.add_argument('--atr-mult', type=float, default=2.5,
                    help='1h ATR の何倍の下落をスパイクとするか. default 2.5')
    ap.add_argument('--min-yen', type=float, default=0.8,
                    help='スパイク最小絶対下落幅(yen). default 0.8')
    ap.add_argument('--cluster-h', type=int, default=12,
                    help='同一イベント統合時間(h). default 12')
    args = ap.parse_args()

    df = load_5m()
    df = add_atr_1h(df)
    print('data %s .. %s rows=%d' %
          (df['datetime'].iloc[0], df['datetime'].iloc[-1], len(df)))

    reps = detect_spikes(df, args.win_min, args.atr_mult, args.min_yen, args.cluster_h)
    print('detected spikes: %d (win=%dm atr_mult=%.1f min_yen=%.2f cluster=%dh)' %
          (len(reps), args.win_min, args.atr_mult, args.min_yen, args.cluster_h))

    rows = []
    for i in reps:
        ts = df['datetime'].iloc[i]
        m = measure_leg(df, i)
        m['datetime'] = ts
        m['known'] = nearest_known(ts)
        m['atr1h'] = round(float(df['atr1h'].iloc[i]), 3)
        rows.append(m)
    res = pd.DataFrame(rows).sort_values('datetime').reset_index(drop=True)

    cols = ['datetime', 'known', 'atr1h', 'pre_high', 'c_detect', 'trough',
            'total_drop', 'drop_to_detect',
            'postB_drop_1h', 'postB_drop_3h', 'postB_drop_6h',
            'maxadv_1h', 'maxadv_3h',
            'revD_50_days', 'revD_100_days', 'revD_max_recover',
            'revD_below_trough_10d', 'revD_below_trough_20d', 'revD_horizon_days']
    res_out = res[cols]
    out_csv = Path(__file__).resolve().parent / 'intervention_event_study_result.csv'
    res_out.to_csv(out_csv, index=False)
    print('wrote %s' % out_csv)

    pd.set_option('display.width', 220)
    pd.set_option('display.max_columns', 40)

    # ── 全検出スパイク ──
    print('\n=== ALL DETECTED SPIKES ===')
    print(res_out.to_string(index=False))

    # ── 既知介入のみ(較正の主対象) ──
    known = res[res['known'] != ''].copy()
    print('\n=== KNOWN-INTERVENTION SPIKES (n=%d) ===' % len(known))
    if len(known):
        def stat(s):
            s = pd.to_numeric(s, errors='coerce').dropna()
            return (round(s.median(), 2), round(s.mean(), 2),
                    round(s.min(), 2), round(s.max(), 2))
        print('metric                 median   mean    min    max')
        for c in ['total_drop', 'drop_to_detect',
                  'postB_drop_1h', 'postB_drop_3h', 'postB_drop_6h',
                  'maxadv_1h', 'maxadv_3h',
                  'revD_50_days', 'revD_100_days', 'revD_max_recover',
                  'revD_below_trough_10d', 'revD_below_trough_20d']:
            md, mn, lo, hi = stat(known[c])
            print('%-20s %7s %6s %6s %6s' % (c, md, mn, lo, hi))
        full_rec = pd.to_numeric(known['revD_max_recover'], errors='coerce')
        print('\n案D: 谷からpre_highへ完全回復(>=1.0)した割合: %d/%d'
              % (int((full_rec >= 1.0).sum()), len(known)))
        print('案D: 100%%リトレースの観測営業日数(median): %s'
              % round(pd.to_numeric(known['revD_100_days'], errors='coerce').median(), 1))

    print('\n較正の読み方:')
    print('  案B: drop_to_detect = 検出までに失う分 / postB_drop_3h = 検出後に乗れる追随幅。')
    print('       maxadv_* がストップ幅(検出後の戻り)の目安。')
    print('  案D: revD_100_days = 介入の谷から介入前水準へ戻るまでの営業日数。')
    print('       revD_max_recover>=1.0 が多いほど "介入は続かず戻る" = 押し目買い有利。')


if __name__ == '__main__':
    main()
