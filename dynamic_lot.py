"""
dynamic_lot.py - 動的ロットサイジング
ケリー基準(フラクショナル) x 相関調整で戦略・ペア別ロットを算出する

VPS保存先: C:/Users/Administrator/fx_bot/dynamic_lot.py
"""

import logging
from pathlib import Path

# ---- 設定 ----
KELLY_FRACTION  = 0.4
EWMA_SPAN       = 30
EWMA_WEIGHT     = 0.6    # WR_ewma の重み（全件WRは 1-EWMA_WEIGHT=0.4）
MIN_LOT         = 0.01
MAX_LOT_CAP     = 0.5    # 暫定上限（現行MAX_JPY_LOT=0.4の1.25倍）
CORR_MIN_DAYS   = 30     # 相関行列を使う最低稼働日数（日次リスト長で代替）
CORR_MIN_N      = 20     # 相関行列を使う最低トレード数/戦略

_LOG_PATH = Path(__file__).parent / 'logs' / 'dynamic_lot.log'
_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    filename=str(_LOG_PATH),
    level=logging.DEBUG,
    format='%(asctime)s %(levelname)s %(message)s',
)
_logger = logging.getLogger(__name__)


# ------------------------------------------------------------------ #
#  内部ユーティリティ
# ------------------------------------------------------------------ #
def _pip_value(pair):
    """1lot・1pip の円換算近似: JPYペア=100、その他=10"""
    return 100.0 if str(pair).upper().endswith('JPY') else 10.0


def _ewma_wr(outcomes):
    """
    outcomes: list of 1.0/0.0 → EWMA勝率 (span=EWMA_SPAN)
    """
    if not outcomes:
        return 0.0
    alpha = 2.0 / (EWMA_SPAN + 1)
    val = float(outcomes[0])
    for v in outcomes[1:]:
        val = alpha * float(v) + (1.0 - alpha) * val
    return val


def _n_effective(all_strategy_returns):
    """
    相関行列から実効戦略数 n_effective を算出。
    CORR_MIN_N 未満のデータしかない場合はアクティブ戦略数で単純除算（フォールバック）。

    Returns (n_eff: float, method: str)
    """
    eligible = {
        s: rets for s, rets in all_strategy_returns.items()
        if len(rets) >= CORR_MIN_N
    }
    n_strat = len(eligible)

    if n_strat == 0:
        return 1.0, 'solo'
    if n_strat == 1:
        return 1.0, 'solo'

    try:
        import numpy as np
        min_len = min(len(r) for r in eligible.values())
        mat = [eligible[s][-min_len:] for s in eligible]
        corr = np.corrcoef(mat)
        avg_corr = (corr.sum() - n_strat) / (n_strat * (n_strat - 1))
        avg_corr = max(0.0, min(1.0, float(avg_corr)))
        n_eff = n_strat / (1.0 + (n_strat - 1) * avg_corr)
        return float(n_eff), 'corr'
    except Exception as exc:
        _logger.warning('相関行列計算エラー、フォールバック: %s', exc)
        return float(n_strat), 'fallback'


# ------------------------------------------------------------------ #
#  メイン関数
# ------------------------------------------------------------------ #
def calc_lot(pair, strategy, balance, sl_pips, trade_history, all_strategy_returns):
    """
    Parameters
    ----------
    pair                 : str   例 'GBPJPY'
    strategy             : str   例 'BB'
    balance              : float 現在残高（円）
    sl_pips              : float 今回トレードのSL幅（pips）
    trade_history        : list[dict]
                           {'result': 1/0, 'rr': float, 'timestamp': datetime}
    all_strategy_returns : dict[str, list[float]]
                           戦略名 -> 日次損益リスト

    Returns
    -------
    lot        : float
    debug_info : dict  （ログ用）
    """
    n = len(trade_history)

    # 1. WR推計
    #    N >= 50: EWMA x 0.6 + 全件WR x 0.4
    #    N <  50: 全件WRのみ（WARNINGログ）
    outcomes = [float(t['result']) for t in trade_history]
    wr_all   = sum(outcomes) / n if n > 0 else 0.0

    if n >= 50:
        wr_ewma = _ewma_wr(outcomes)
        wr_est  = EWMA_WEIGHT * wr_ewma + (1.0 - EWMA_WEIGHT) * wr_all
    else:
        wr_ewma = None
        wr_est  = wr_all
        _logger.warning(
            'WR推計: N=%d < 50、全件WRのみ使用 [pair=%s strategy=%s]',
            n, pair, strategy,
        )

    # 2. RR推計（直近30件の平均RR）
    recent  = trade_history[-30:] if n >= 30 else trade_history
    rr_vals = [float(t['rr']) for t in recent if t.get('rr', 0) > 0]
    rr      = sum(rr_vals) / len(rr_vals) if rr_vals else 1.0

    # 3. ケリーf計算
    f_kelly = wr_est - (1.0 - wr_est) / rr if rr > 0 else 0.0

    if f_kelly <= 0:
        _logger.warning(
            'f_kelly=%.4f<=0、MIN_LOTを返却 [pair=%s strategy=%s WR=%.3f RR=%.3f]',
            f_kelly, pair, strategy, wr_est, rr,
        )
        debug_info = {
            'wr_ewma':     wr_ewma,
            'wr_all':      round(wr_all, 4),
            'wr_est':      round(wr_est, 4),
            'rr':          round(rr, 3),
            'f_kelly':     round(f_kelly, 4),
            'f_adj':       None,
            'n_effective': None,
            'corr_method': None,
            'lot':         MIN_LOT,
        }
        _logger.debug(
            'calc_lot: pair=%s strategy=%s balance=%.0f sl_pips=%.1f -> %s',
            pair, strategy, balance, sl_pips, debug_info,
        )
        return MIN_LOT, debug_info

    f_frac = f_kelly * KELLY_FRACTION

    # 4. 相関調整
    #    CORR_MIN_DAYS・CORR_MIN_N を満たす場合: 相関行列で n_effective 計算
    #    満たさない場合: active_strategies 数で単純除算（フォールバック）
    if all_strategy_returns:
        use_corr = all(
            len(rets) >= CORR_MIN_DAYS
            for rets in all_strategy_returns.values()
            if rets is not None
        )
        if use_corr:
            n_eff, corr_method = _n_effective(all_strategy_returns)
        else:
            n_eff        = float(max(1, len(all_strategy_returns)))
            corr_method  = 'fallback'
    else:
        n_eff       = 1.0
        corr_method = 'solo'

    f_adj = f_frac / n_eff if n_eff > 0 else f_frac

    # 5. lot換算
    pip_val  = _pip_value(pair)
    sl_pips  = max(sl_pips, 0.1)  # ゼロ除算防止
    risk_jpy = balance * f_adj
    lot      = risk_jpy / (sl_pips * pip_val)

    # 6. キャップ適用・丸め
    lot = max(MIN_LOT, min(MAX_LOT_CAP, round(lot, 2)))

    debug_info = {
        'wr_ewma':     round(wr_ewma, 4) if wr_ewma is not None else None,
        'wr_all':      round(wr_all, 4),
        'wr_est':      round(wr_est, 4),
        'rr':          round(rr, 3),
        'f_kelly':     round(f_kelly, 4),
        'f_adj':       round(f_adj, 6),
        'n_effective': round(n_eff, 3),
        'corr_method': corr_method,
        'lot':         lot,
    }
    _logger.debug(
        'calc_lot: pair=%s strategy=%s balance=%.0f sl_pips=%.1f -> %s',
        pair, strategy, balance, sl_pips, debug_info,
    )

    return lot, debug_info


# ------------------------------------------------------------------ #
#  ロット概算プレビュー（daily_report 埋め込み用）
# ------------------------------------------------------------------ #
def lot_preview_from_metrics(pair_metrics, balance, ref_sl_pips=20.0):
    """
    計算済みWR/RRメトリクスから推奨ロット概算文字列を返す。
    daily_report.py から毎日呼び出して「今日の推奨ロット」として表示する用途。

    pair_metrics: dict  { 'BB:GBPJPY': {'win_rate':float, 'rr':float, 'n':int}, ... }
    balance:      float 現在残高（円）
    ref_sl_pips:  float 参照SL幅 (pips) — 実際の発注SLではなく概算用
    """
    header = '[推奨ロット概算 (参考SL={:.0f}pips / 残高={:,.0f}円)]'.format(
        ref_sl_pips, balance,
    )
    lines = [header]

    strats = set(k.split(':')[0] for k in pair_metrics if ':' in k)
    n_eff  = max(1.0, float(len(strats)))

    for key in sorted(pair_metrics.keys()):
        m         = pair_metrics[key]
        pair_part = key.split(':')[-1] if ':' in key else key
        wr  = float(m.get('win_rate', 0.0))
        rr  = float(m.get('rr', 0.0))
        n   = int(m.get('n', 0))

        if n == 0 or rr <= 0 or wr <= 0:
            lines.append('  {:<22}  データ不足 (n={})'.format(key, n))
            continue

        f_kelly = wr - (1.0 - wr) / rr
        if f_kelly <= 0:
            lines.append('  {:<22}  Kelly<=0 (WR={:.0f}% RR={:.2f})  -> {:.2f}lot'.format(
                key, wr * 100, rr, MIN_LOT,
            ))
            continue

        f_adj    = f_kelly * KELLY_FRACTION / n_eff
        pip_val  = _pip_value(pair_part)
        risk_jpy = balance * f_adj
        lot      = max(MIN_LOT, min(MAX_LOT_CAP, round(risk_jpy / (ref_sl_pips * pip_val), 2)))

        n_warn = ' (N<50)' if n < 50 else ''
        lines.append(
            '  {:<22} n={:>3}  WR={:.0f}%  RR={:.2f}  Kelly={:.1f}%  {:>4.2f}lot{}'.format(
                key, n, wr * 100, rr, f_kelly * 100, lot, n_warn,
            )
        )

    return '\n'.join(lines)
