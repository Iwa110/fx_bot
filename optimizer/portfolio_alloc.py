"""
portfolio_alloc.py - Case A: allocation layer over the 4 positive Grid sleeves.

Sleeves = GRID_{GBPJPY,CHFJPY,NZDJPY,AUDCAD} at v7 (all positive Sharpe).
BB_USDJPY excluded from the weighting optimiser: its 1h proxy is a net loser
(PF 0.84) yet has the lowest vol, so naive inverse-vol would lever the loser.
It is reported separately as an additive diversifier.

Schemes (all causal: weight for day t uses ONLY data up to t-1):
  base        : fixed live lot (weight 1.0 each) = current production.
  inv_vol     : w_i proportional 1/trailing_std_i  (= risk-parity at ~0 corr).
  risk_parity : iterative equal-risk-contribution on trailing covariance.
  vol_target  : fixed weights, whole portfolio scaled to constant target vol.

Fair comparison: every scheme rescaled to the BASELINE realised daily std, so
net & maxDD are directly comparable (Sharpe is scale-free anyway).

Adoption: OOS Sharpe +20% vs base AND OOS maxDD <= base maxDD.
"""

import numpy as np
import pandas as pd
from portfolio_meta_bt import build_sleeves, metrics, IS_END, OOS_START

GRID = ['GRID_GBPJPY', 'GRID_CHFJPY', 'GRID_NZDJPY', 'GRID_AUDCAD']


def trailing_std(daily, L):
    """std over trailing L days, shifted 1 (causal)."""
    return daily.rolling(L, min_periods=max(5, L // 3)).std().shift(1)


def inv_vol_weights(daily, L):
    sd = trailing_std(daily, L)
    w = 1.0 / sd.replace(0, np.nan)
    w = w.div(w.sum(axis=1), axis=0) * len(daily.columns)   # sum -> n_sleeves
    return w.fillna(0.0)


def risk_parity_weights(daily, L, rebal=5):
    """Iterative equal-risk-contribution on trailing cov, rebalanced every
    `rebal` days (causal). Falls back to inv-vol if cov ill-conditioned."""
    cols = list(daily.columns)
    W = pd.DataFrame(0.0, index=daily.index, columns=cols)
    last_w = np.ones(len(cols)) / len(cols)
    for i, day in enumerate(daily.index):
        if i % rebal == 0 and i > L:
            win = daily.iloc[i - L:i]              # up to t-1
            cov = win.cov().values
            w = last_w.copy()
            for _ in range(200):
                mrc = cov @ w
                rc = w * mrc
                if np.any(mrc <= 0):
                    w = 1.0 / (np.sqrt(np.diag(cov)) + 1e-9); w /= w.sum(); break
                target = rc.mean()
                w = w * (target / rc) ** 0.1
                w = np.clip(w, 1e-6, None); w /= w.sum()
            last_w = w
        W.loc[day] = last_w
    return W * len(cols)


def apply_weights(daily, W):
    return (daily * W).sum(axis=1)


def vol_target(port, L, target_std):
    """Scale a portfolio series so trailing vol ~ target (causal)."""
    tv = port.rolling(L, min_periods=max(5, L // 3)).std().shift(1)
    scale = (target_std / tv).clip(upper=3.0).fillna(1.0)   # cap leverage 3x
    return port * scale


def rescale_to(series, ref_std):
    s = series.std()
    return series * (ref_std / s) if s > 0 else series


def split(s):
    return s[s.index <= IS_END], s[s.index >= OOS_START]


def report(name, series, ref_std_full):
    s = rescale_to(series, ref_std_full)
    out = {}
    for tag, seg in [('FULL', s), ('IS', split(s)[0]), ('OOS', split(s)[1])]:
        m = metrics(seg)
        out[tag] = m
    return name, out


def main():
    daily_all = build_sleeves()
    daily = daily_all[GRID]
    base = daily.sum(axis=1)
    ref = base.std()
    base_is, base_oos = split(base)
    base_sharpe_oos = metrics(base_oos)['sharpe']

    print('=== Case A: allocation over 4 Grid sleeves (v7) ===')
    print(f'period {daily.index.min().date()}~{daily.index.max().date()}  '
          f'IS<= {IS_END.date()}  OOS>= {OOS_START.date()}')

    schemes = {'base': base}
    results = {}
    # lookback sweep for inv_vol / risk_parity, pick best IS Sharpe
    for L in [20, 40, 60, 90]:
        schemes[f'inv_vol_L{L}'] = apply_weights(daily, inv_vol_weights(daily, L))
        schemes[f'risk_parity_L{L}'] = apply_weights(daily, risk_parity_weights(daily, L))
    # vol-target on fixed weights (DD-control lever), target = base full std
    for L in [20, 40, 60]:
        schemes[f'vol_target_L{L}'] = vol_target(base, L, base.std())

    csv_rows = []
    hdr = f'{"scheme":18s} | {"PF":>5s} {"Shrp":>5s} {"maxDD":>11s} {"net":>12s} {"worst":>11s}'
    for seg_name in ['FULL', 'IS', 'OOS']:
        print(f'\n--- {seg_name} (all rescaled to base full-period std) ---')
        print(hdr)
        for nm, sr in schemes.items():
            _, out = report(nm, sr, ref)
            m = out[seg_name]
            star = ''
            if seg_name == 'OOS' and nm != 'base':
                if m['sharpe'] >= 1.2 * base_sharpe_oos and m['maxdd'] <= out_base_oos_dd:
                    star = '  <= ADOPT'
            print(f'{nm:18s} | {m["pf"]:5.2f} {m["sharpe"]:5.2f} {m["maxdd"]:11,.0f} '
                  f'{m["net"]:12,.0f} {m["worst"]:11,.0f}{star}')
            results[(nm, seg_name)] = m
            csv_rows.append({'scheme': nm, 'seg': seg_name, 'pf': round(m['pf'], 2),
                             'sharpe': round(m['sharpe'], 2), 'maxdd': round(m['maxdd']),
                             'net': round(m['net']), 'worst': round(m['worst'])})
        if seg_name == 'IS':
            pass

    pd.DataFrame(csv_rows).to_csv('portfolio_alloc_result.csv', index=False)
    print('\n(adoption needs OOS Sharpe >= 1.2x base-OOS and OOS maxDD <= base-OOS maxDD)')
    print('Saved: portfolio_alloc_result.csv')


# base OOS dd referenced inside loop; compute before main body via global
if __name__ == '__main__':
    # pre-compute base OOS maxDD for adoption flagging
    _d = build_sleeves()[GRID]
    _base = _d.sum(axis=1)
    _ref = _base.std()
    _base_oos = rescale_to(_base, _ref)[_base.index >= OOS_START]
    out_base_oos_dd = metrics(_base_oos)['maxdd']
    main()
