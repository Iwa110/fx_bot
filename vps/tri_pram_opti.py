# ══════════════════════════════════════════
# TRI パラメータ最適化（RR >= 1.5目標）
# ══════════════════════════════════════════
import itertools
import pandas as pd

# --- ATR14分析結果（EURGBPの実測値） ---
ATR_MEAN   = 0.00419
ATR_MEDIAN = 0.00392
ATR_P90    = 0.00560

def optimize_tri_params(
    entry_th_list = [0.0015, 0.0018, 0.0020, 0.0022, 0.0025, 0.0030],
    sl_th_list    = [0.0035, 0.0040, 0.0045, 0.0050, 0.0055, 0.0060],
    exit_th_list  = [0.0003, 0.0005, 0.0007, 0.0010],
    min_rr        = 1.5,
):
    """
    RR = (entry_th - exit_th) / (sl_th - entry_th)
    ※ TRI戦略の利益幅 = 乖離がentry_thからexit_thまで縮小
       損失幅 = 乖離がentry_thからsl_thまで拡大
    """
    results = []

    for entry_th, sl_th, exit_th in itertools.product(
        entry_th_list, sl_th_list, exit_th_list
    ):
        # 基本バリデーション
        if exit_th >= entry_th:
            continue
        if sl_th <= entry_th:
            continue

        tp_dist = entry_th - exit_th   # 利益方向の乖離縮小幅
        sl_dist = sl_th - entry_th     # 損失方向の乖離拡大幅

        if sl_dist <= 0:
            continue

        rr = tp_dist / sl_dist

        # ATRベースの参考指標
        sl_vs_atr_mean   = sl_th / ATR_MEAN
        sl_vs_atr_median = sl_th / ATR_MEDIAN
        entry_vs_atr     = entry_th / ATR_MEAN

        results.append({
            'entry_th':        entry_th,
            'exit_th':         exit_th,
            'sl_th':           sl_th,
            'tp_dist':         round(tp_dist, 5),
            'sl_dist':         round(sl_dist, 5),
            'RR':              round(rr, 3),
            'sl/ATR_mean':     round(sl_vs_atr_mean, 3),
            'sl/ATR_median':   round(sl_vs_atr_median, 3),
            'entry/ATR_mean':  round(entry_vs_atr, 3),
        })

    df = pd.DataFrame(results)

    # RR >= min_rr でフィルタ
    df = df[df['RR'] >= min_rr].copy()

    # 優先スコア：RRが高く、entry_thが現実的（ATR_MEANの30〜60%）
    df['score'] = (
        df['RR'] * 0.5
        + (1 - abs(df['entry/ATR_mean'] - 0.45) * 2) * 0.3   # entry_thの適正さ
        + (1 - df['sl/ATR_mean']) * 0.2                        # SLは小さいほど良い
    )

    df = df.sort_values('score', ascending=False).reset_index(drop=True)
    return df


if __name__ == '__main__':
    df = optimize_tri_params()

    print(f'RR >= 1.5 候補数: {len(df)}')
    print()
    print('=== Top 10 候補 ===')
    print(df[[
        'entry_th', 'exit_th', 'sl_th',
        'RR', 'sl/ATR_mean', 'entry/ATR_mean', 'score'
    ]].head(10).to_string(index=True))

    print()
    print('=== 現行パラメータとの比較 ===')
    current = {
        'entry_th': 0.0022,
        'exit_th':  0.0007,
        'sl_th':    0.0055,
    }
    cur_tp = current['entry_th'] - current['exit_th']
    cur_sl = current['sl_th'] - current['entry_th']
    print(f'  現行RR: {cur_tp/cur_sl:.3f}  (tp_dist={cur_tp:.4f} / sl_dist={cur_sl:.4f})')

    # CSV出力
    df.to_csv('tri_param_candidates.csv', index=False)
    print()
    print('tri_param_candidates.csv に全候補を保存しました')