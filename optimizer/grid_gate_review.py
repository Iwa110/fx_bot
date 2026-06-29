"""
grid_gate_review.py - 確定Grid 4本の「未エントリー診断 + 次機会予測」

実口座(OANDA) go-live 後、Gridは大半の日がCIゲート未達でアイドルになる
(`[[project_grid_audcad_idle_observation_20260621]]`)。本スクリプトは各ペアが
「なぜ今日建たないのか」「次にレンジ(=エントリー可能)になるのはいつ頃か」を
日次評価ルーティンに提示する。

データソース(ネットワーク不要・正確さと軽さを両立):
  1) optimizer/grid_gate_log.csv … VPS grid_monitor が 1日1行/ペア で出力する
     実ゲート時系列(botが実際に見たMT5値=最も正確)。go-live後から蓄積。
  2) data/{PAIR}_1h_dukas.csv     … gate_log が薄い初期の bootstrap。長期CI履歴から
     しきい値クロスの base-rate(年あたり発火回数/レンジ窓・無発火窓の中央値)を算出。

ゲート定義は vps/grid_monitor.py PAIR_CONFIG の確定4本と一致させること。

Usage:
    python optimizer/grid_gate_review.py            # gate_log優先・無ければdukas
    python optimizer/grid_gate_review.py --bootstrap # dukasで履歴/base-rateを再構築
    python optimizer/grid_gate_review.py --json      # 機械可読(ルーティン通知用)
"""

import argparse
import json
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd

JST = timezone(timedelta(hours=9))
OPT_DIR  = Path(__file__).resolve().parent
DATA_DIR = OPT_DIR.parent / 'data'
GATE_LOG = OPT_DIR / 'grid_gate_log.csv'

CI_PERIOD = 14

# 確定4本のゲート設定（vps/grid_monitor.py PAIR_CONFIG と一致）
PAIRS = {
    'AUDCAD': {'ci_threshold': 65.0, 'dir_mode': 'regime_short',
               'sma_period': 1200, 'mom_thr': 2.0,  'mom120_thr': None},
    'CADCHF': {'ci_threshold': 65.0, 'dir_mode': 'regime_short',
               'sma_period': 1200, 'mom_thr': None, 'mom120_thr': None},
    'AUDNZD': {'ci_threshold': 65.0, 'dir_mode': 'regime_short',
               'sma_period': 1200, 'mom_thr': 2.0,  'mom120_thr': None},
    'EURGBP': {'ci_threshold': 65.0, 'dir_mode': 'both',
               'sma_period': None, 'mom_thr': 2.0,  'mom120_thr': 4.0},
}
MOM_WINDOW    = 24
MOM120_WINDOW = 120


# ══════════════════════════════════════════
# Indicators (vps/grid_monitor.py と同一ロジックを移植; MT5非依存)
# ══════════════════════════════════════════
def _true_range(h, l, c):
    return pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()],
                     axis=1).max(axis=1)


def ci_series(df_d1: pd.DataFrame, period: int = CI_PERIOD) -> pd.Series:
    """各完了D1バーについて trailing `period` 本の Choppiness Index を返す。
    CI = 100 * log10(SUM_TR / (max_high - min_low)) / log10(period)。
    grid_monitor.calc_ci は最新の completed `period` 本を使う = 本系列の末尾値に相当。"""
    h, l, c = df_d1['high'], df_d1['low'], df_d1['close']
    tr = _true_range(h, l, c)
    tr_sum = tr.rolling(period).sum()
    hh = h.rolling(period).max()
    ll = l.rolling(period).min()
    rng = (hh - ll).replace(0, np.nan)
    return 100.0 * np.log10(tr_sum / rng) / math.log10(period)


def calc_atr(df: pd.DataFrame, period: int = 14):
    if len(df) < period + 2:
        return None
    tr = _true_range(df['high'], df['low'], df['close'])
    v = tr.rolling(period).mean().iloc[-1]
    return float(v) if not pd.isna(v) else None


def calc_sma_closed(df_h1: pd.DataFrame, period: int):
    if len(df_h1) < period + 1:
        return None
    return float(df_h1['close'].iloc[-(period + 1):-1].mean())


def calc_ret_norm(df_h1: pd.DataFrame, window: int, atr: float):
    if atr is None or atr <= 0 or len(df_h1) < window + 3:
        return None
    c = df_h1['close']
    return float((c.iloc[-2] - c.iloc[-2 - window]) / atr)


# ══════════════════════════════════════════
# Data loaders
# ══════════════════════════════════════════
def load_dukas_h1(pair: str):
    fp = DATA_DIR / f'{pair}_1h_dukas.csv'
    if not fp.exists():
        return None
    df = pd.read_csv(fp, parse_dates=['datetime'])
    return df.sort_values('datetime').reset_index(drop=True)


def h1_to_d1(df_h1: pd.DataFrame) -> pd.DataFrame:
    g = df_h1.set_index('datetime')
    agg = {'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last'}
    d = g.resample('1D').agg(agg).dropna(subset=['close'])
    return d.reset_index()


def load_gate_log() -> pd.DataFrame | None:
    if not GATE_LOG.exists() or GATE_LOG.stat().st_size == 0:
        return None
    df = pd.read_csv(GATE_LOG)
    if df.empty:
        return None
    df['date_jst'] = pd.to_datetime(df['date_jst'])
    return df


# ══════════════════════════════════════════
# Base-rate (CIしきい値クロス頻度) from a daily CI series
# ══════════════════════════════════════════
def base_rate(ci: pd.Series, dates: pd.Series, th: float) -> dict:
    """エントリー可能(CI>th)窓の頻度統計。年あたり発火回数・レンジ窓/無発火窓の中央値日数。"""
    s = ci.dropna()
    if len(s) < 30:
        return {}
    elig = (s > th).astype(int).values
    n_days = len(elig)
    span_days = (pd.to_datetime(dates).iloc[-1] - pd.to_datetime(dates).iloc[0]).days or 1
    years = span_days / 365.25
    # rising crossings = 新しいレンジ窓の開始
    crossings = int(((elig[1:] == 1) & (elig[:-1] == 0)).sum())
    # run-length of eligible / idle windows
    elig_runs, idle_runs, cur, val = [], [], 0, elig[0]
    for x in elig:
        if x == val:
            cur += 1
        else:
            (elig_runs if val == 1 else idle_runs).append(cur)
            cur, val = 1, x
    (elig_runs if val == 1 else idle_runs).append(cur)
    return {
        'years':         round(years, 1),
        'elig_share':    round(float(elig.mean()), 3),
        'cross_per_yr':  round(crossings / years, 2) if years else None,
        'elig_win_med':  int(np.median(elig_runs)) if elig_runs else 0,
        'idle_win_med':  int(np.median(idle_runs)) if idle_runs else 0,
    }


# ══════════════════════════════════════════
# Per-pair diagnosis
# ══════════════════════════════════════════
def diagnose(pair: str, cfg: dict, gate_log: pd.DataFrame | None,
             prefer_bootstrap: bool) -> dict:
    th = cfg['ci_threshold']
    out = {'pair': pair, 'ci_threshold': th}

    # ── 長期CI系列(base-rate + bootstrap trend) は dukas から ──
    df_h1 = load_dukas_h1(pair)
    duk_ci = duk_dates = None
    if df_h1 is not None:
        d1 = h1_to_d1(df_h1)
        duk_ci = ci_series(d1)
        duk_dates = d1['datetime']
        out['base_rate'] = base_rate(duk_ci, duk_dates, th)
        out['dukas_last'] = str(d1['datetime'].iloc[-1].date())

    # ── 現在状態: gate_log(VPS実値)優先。無ければ dukas 末尾で近似 ──
    src = None
    glog = None
    if gate_log is not None and not prefer_bootstrap:
        g = gate_log[gate_log['pair'] == pair].sort_values('date_jst')
        if not g.empty:
            glog = g
            last = g.iloc[-1]
            out['source'] = 'gate_log'
            out['as_of'] = str(last['date_jst'].date())
            out['ci'] = float(last['ci']) if pd.notna(last['ci']) else None
            out['allow_long']  = bool(int(last.get('allow_long', 1)))
            out['allow_short'] = bool(int(last.get('allow_short', 1)))
            out['n_long']  = int(last.get('n_long', 0))
            out['n_short'] = int(last.get('n_short', 0))
            src = 'gate_log'

    if src is None and df_h1 is not None:
        # dukas末尾で近似(stale注意)
        out['source'] = 'dukas(bootstrap)'
        out['as_of'] = out.get('dukas_last')
        out['ci'] = float(duk_ci.iloc[-1]) if pd.notna(duk_ci.iloc[-1]) else None
        atr = calc_atr(df_h1, 14)
        if cfg['dir_mode'] == 'regime_short' and cfg['sma_period']:
            sma = calc_sma_closed(df_h1, cfg['sma_period'])
            cprev = float(df_h1['close'].iloc[-2])
            out['allow_short'] = not (sma is not None and cprev > sma)
        else:
            out['allow_short'] = (cfg['dir_mode'] != 'long_only')
        out['allow_long'] = (cfg['dir_mode'] != 'short_only')
        ml = ms = True
        if cfg['mom_thr']:
            r = calc_ret_norm(df_h1, MOM_WINDOW, atr)
            if r is not None:
                ml, ms = r > -cfg['mom_thr'], r < cfg['mom_thr']
        if cfg['mom120_thr']:
            r2 = calc_ret_norm(df_h1, MOM120_WINDOW, atr)
            if r2 is not None:
                ml, ms = ml and r2 > -cfg['mom120_thr'], ms and r2 < cfg['mom120_thr']
        out['mom_long_ok'], out['mom_short_ok'] = ml, ms
        out['n_long'] = out['n_short'] = 0

    # ── reason code ──
    ci = out.get('ci')
    out['ci_gap'] = round(ci - th, 2) if ci is not None else None
    reasons = []
    if ci is None:
        reasons.append('no_data')
    elif ci <= th:
        reasons.append('CI_below')
    else:
        # CIは通過。方向/モメンタムゲートで建たない場合
        if not out.get('allow_short', True) and not out.get('allow_long', True):
            reasons.append('regime_block')
        elif cfg['dir_mode'] == 'regime_short' and not out.get('allow_short', True):
            reasons.append('regime_block(short_off)')
        if out.get('mom_long_ok', True) is False and out.get('mom_short_ok', True) is False:
            reasons.append('mom_block')
        if out.get('n_long', 0) > 0 or out.get('n_short', 0) > 0:
            reasons.append('positions_open')
        if not reasons:
            reasons.append('eligible')
    out['reasons'] = reasons

    # ── CIトレンド(直近N日) + 次機会ETA ──
    trend_ci = None
    if glog is not None and len(glog) >= 4:
        trend_ci = pd.to_numeric(glog['ci'], errors='coerce').dropna().tail(14).values
    elif duk_ci is not None:
        trend_ci = duk_ci.dropna().tail(14).values
    if trend_ci is not None and len(trend_ci) >= 4:
        x = np.arange(len(trend_ci))
        slope = float(np.polyfit(x, trend_ci, 1)[0])  # CI pts/day
        out['ci_slope_per_day'] = round(slope, 3)
        if ci is not None and ci <= th:
            if slope > 0.05:
                eta = (th - ci) / slope
                out['eta_days'] = int(round(eta)) if eta < 365 else None
            else:
                out['eta_days'] = None  # 上昇傾向なし=予測不能
    return out


# ══════════════════════════════════════════
# Rendering
# ══════════════════════════════════════════
def _reason_jp(reasons: list) -> str:
    m = {'CI_below': 'CIしきい値未達(レンジ不成立)',
         'regime_block': '上昇レジームでshort停止',
         'regime_block(short_off)': '上昇レジームでshort停止(longは可)',
         'mom_block': 'モメンタム過大で建て見送り',
         'positions_open': '建玉あり(ラダー進行中)',
         'eligible': 'エントリー可能(レンジ成立)',
         'no_data': 'データ無し'}
    return ' / '.join(m.get(r, r) for r in reasons)


def render_text(results: list) -> str:
    lines = [f'=== Grid 未エントリー診断 / 次機会予測  ({datetime.now(JST):%Y-%m-%d %H:%M JST}) ===']
    for r in results:
        ci = r.get('ci')
        ci_s = f'{ci:.1f}' if ci is not None else 'N/A'
        gap = r.get('ci_gap')
        gap_s = f'{gap:+.1f}' if gap is not None else '?'
        src = r.get('source', '?')
        asof = r.get('as_of', '?')
        lines.append('')
        lines.append(f"■ {r['pair']}  CI={ci_s} / th={r['ci_threshold']}  gap={gap_s}"
                     f"   [{src} @ {asof}]")
        lines.append(f"   判定: {_reason_jp(r['reasons'])}")
        # gate detail
        det = []
        if 'allow_short' in r:
            det.append('short ' + ('可' if r['allow_short'] else '停止'))
        if r.get('n_long', 0) or r.get('n_short', 0):
            det.append(f"建玉 L{r.get('n_long',0)}/S{r.get('n_short',0)}")
        if det:
            lines.append('   ゲート: ' + ' | '.join(det))
        # trend + ETA
        slope = r.get('ci_slope_per_day')
        if slope is not None:
            dirn = '上昇中' if slope > 0.05 else ('低下中' if slope < -0.05 else '横ばい')
            line = f"   CIトレンド: {slope:+.2f}/日 ({dirn})"
            if 'eta_days' in r:
                eta = r['eta_days']
                line += (f" → 次機会ETA 約{eta}日(線形外挿・不確実)"
                         if eta else " → 上昇傾向乏しく時期予測不能")
            lines.append(line)
        # base rate
        br = r.get('base_rate') or {}
        if br:
            lines.append(f"   過去base-rate({br.get('years')}yr): "
                         f"発火{br.get('cross_per_yr')}回/年, "
                         f"レンジ窓中央{br.get('elig_win_med')}日, "
                         f"無発火窓中央{br.get('idle_win_med')}日, "
                         f"レンジ滞在{br.get('elig_share')}")
    lines.append('')
    lines.append('※ 損失/大DDの発生は開始時点では予測不能(project_grid_episode_prediction)。')
    lines.append('  本予測は「レンジ(エントリー可能)局面の到来時期」のレンジ提示であり断定でない。')
    return '\n'.join(lines)


def one_liner(results: list) -> str:
    """通知用1行サマリ。"""
    parts = []
    for r in results:
        ci = r.get('ci')
        tag = r['reasons'][0] if r.get('reasons') else '?'
        short = {'CI_below': '未達', 'eligible': '可',
                 'regime_block(short_off)': 'short停止', 'mom_block': 'mom',
                 'positions_open': '建玉', 'no_data': 'NA'}.get(tag, tag)
        parts.append(f"{r['pair']}{('%.0f' % ci) if ci is not None else '?'}({short})")
    return 'Grid ' + ' '.join(parts)


def main():
    ap = argparse.ArgumentParser(description='Grid 未エントリー診断 + 次機会予測')
    ap.add_argument('--bootstrap', action='store_true',
                    help='gate_logを使わずdukasのみで再構築(初期/検証用)')
    ap.add_argument('--json', action='store_true', help='JSON出力')
    args = ap.parse_args()

    gate_log = None if args.bootstrap else load_gate_log()
    results = [diagnose(p, cfg, gate_log, args.bootstrap) for p, cfg in PAIRS.items()]

    if args.json:
        print(json.dumps({'results': results, 'summary': one_liner(results)},
                         ensure_ascii=False, indent=2, default=str))
    else:
        print(render_text(results))
        print('\n[通知1行] ' + one_liner(results))


if __name__ == '__main__':
    main()
