"""
crypto_trend_bt.py - 候補C: crypto トレンドフォロー (税引後で繰延 buy&hold を追い抜けるか)。

構造ストーリー (C が唯一検証に値する理由):
    FX メジャー/クロスでトレンドフォローが全滅した原因 = 中央銀行/裁定で価格が「繋留」され
    持続トレンドが立たないため (順張り10年BT/日足・週足トレンド探索が全て墓場)。crypto は
    繋留が無く数年スケールの持続トレンドが実在する → FX で効かなかった原因が構造的に反転する
    可能性がある。これが候補A(MR)/B(carry)と別に一本だけ検証する根拠。

大前提 (Stage0 crypto_tax_gate.py の結論):
    国内 crypto は雑所得55%・繰越/通算なし。能動戦略は繰延 buy&hold の「税引後」を追い抜かねば
    意味がない (土日稼働は動機でエッジでない)。税ハードルは強気窓で非現実的(必要CAGR 89-96%)、
    弱気/レンジ窓で低い → headline OOS 勝ちは「1クラッシュ回避運」の疑いが濃厚。よって本BTは
    全て税引後で評価し、pre-registered bar ④(強気/弱気 両サブ期間で超える)で回避運を殺す。

設計 (税ドラッグ最小 = 低回転を最優先):
    - long-only (国内現物 = 空売り不可)。下降時はキャッシュ。
    - 主戦略 = 200D SMA レジーム: 確定足 close>SMA200 で全額ロング、else キャッシュ。
      年数回のフリップのみ = realize 回数最小。
    - 変種: SMA 期間 (100/200) / Donchian ブレイク / BTC<->ETH ローテーション(強い方を保有)。
    - 約定 = 次足始値。コスト = 往復手数料+スリッページを約定ごとにフルコスト計上。
    - 指標は確定足(t-1 shift)= lookahead 排除。

評価 (全て税引後):
    - IS=2017-21 凍結 / OOS=2022-26 / 年次WFO。
    - buy&hold は繰延(最終清算のみ55%) / 戦略は年次 realize(勝ち年55%・負け救済なし)。

採用バー (pre-registered, 1つでも欠けたら Close):
    ① 税引後ターミナル資産 > 繰延 buy&hold 税引後               <- 第一級ゲート
    ② ① が OOS で成立 ∧ 全WFO fold で buy&hold 税引後を上回る ∧ IS-selectable
    ③ DD調整で優位 (maxDD or Calmar が buy&hold 比で悪化しない = 単なるβ増し取りでない)
    ④ 強気偏重期 と 弱気/レンジ期 の両サブ期間で超える (1クラッシュ回避運でない)

実行:
    .venv_crypto/bin/python optimizer/crypto_trend_bt.py
    .venv_crypto/bin/python optimizer/crypto_trend_bt.py --cost-frac 0.006 --tf 1d
"""
import argparse
import os

import numpy as np
import pandas as pd

import crypto_tax_gate as TG                      # after_tax_active / after_tax_bh / TAX

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(os.path.dirname(HERE), 'data')

IS_YEARS = list(range(2017, 2022))               # 2017-2021 凍結
OOS_YEARS = list(range(2022, 2027))              # 2022-2026
ASSETS = ['BTCUSDT', 'ETHUSDT']


def load(sym, tf):
    p = os.path.join(DATA, f'{sym}_{tf}.csv')
    df = pd.read_csv(p, parse_dates=['datetime']).set_index('datetime')
    return df[['open', 'high', 'low', 'close']].astype(float).sort_index()


# ---------------------------------------------------------------------------
# シグナル生成 (全て確定足 = t-1 shift で lookahead 排除)
# ---------------------------------------------------------------------------
def sig_sma_regime(df, span):
    """close > SMA(span) で 1(ロング), else 0(キャッシュ)。確定足で判定。"""
    sma = df['close'].rolling(span).mean()
    raw = (df['close'] > sma).astype(float)
    return raw.shift(1).fillna(0.0)              # t-1 確定 → 当足で保有


def sig_donchian(df, span):
    """close が直近 span 本の最高値を更新でロング入り、最安値割れでキャッシュ。確定足。"""
    hi = df['high'].rolling(span).max().shift(1)
    lo = df['low'].rolling(span).min().shift(1)
    c = df['close']
    pos = np.zeros(len(df))
    cur = 0.0
    hv, lv, cv = hi.to_numpy(), lo.to_numpy(), c.to_numpy()
    for i in range(len(df)):
        if np.isnan(hv[i]):
            pos[i] = 0.0
            continue
        if cur == 0.0 and cv[i] > hv[i]:
            cur = 1.0
        elif cur == 1.0 and cv[i] < lv[i]:
            cur = 0.0
        pos[i] = cur
    return pd.Series(pos, index=df.index).shift(1).fillna(0.0)


# ---------------------------------------------------------------------------
# バックテスト: target 保有比率系列 (0/1) を次足始値で執行、切替時に往復コスト。
# ---------------------------------------------------------------------------
def run_equity(df, target, cost_frac):
    """target(0/1, 当足で保有すべき比率) を次足始値で執行。
       返り値: 日次 equity 系列 (倍率, 初期1.0)。コストは保有比率変化 |dpos| に cost_frac/2 * |dpos|
       (往復 cost_frac を片道 cost_frac/2 として建て/落ちの各側に適用)。"""
    o = df['open'].to_numpy()
    c = df['close'].to_numpy()
    tgt = target.to_numpy()
    n = len(df)
    equity = np.ones(n)
    pos = 0.0                                     # 現在の保有比率
    eq = 1.0
    # bar-return: close-to-close をポジションで取る。切替は次足始値で行うため、
    # pos は「当足の始値で成立し当足内保有」= target(当足)。前足からの変化にコスト。
    prev_c = c[0]
    for i in range(n):
        # ポジション更新 (次足始値執行 = 当足始値でリバランス済とみなす: target は既に shift 済)
        new_pos = tgt[i]
        dpos = abs(new_pos - pos)
        if dpos > 0:
            eq *= (1.0 - (cost_frac / 2.0) * dpos)   # 建て/落ちコスト
            pos = new_pos
        if i > 0:
            ret = c[i] / prev_c - 1.0
            eq *= (1.0 + pos * ret)
        prev_c = c[i]
        equity[i] = eq
    return pd.Series(equity, index=df.index)


def annual_returns(equity):
    """equity(倍率) から暦年リターン系列 dict{year: ret}。"""
    yr = equity.resample('YE').last()
    out = {}
    prev = 1.0
    for ts, val in yr.items():
        out[ts.year] = val / prev - 1.0
        prev = val
    return out


def max_dd(equity):
    peak = equity.cummax()
    return float((equity / peak - 1.0).min())


def bh_equity(df, cost_frac):
    """buy&hold の equity (初日始値で建て往復コスト片道のみ, 保有中コスト無)。"""
    c = df['close'].to_numpy()
    eq = (1.0 - cost_frac / 2.0) * (c / c[0])
    return pd.Series(eq, index=df.index)


# ---------------------------------------------------------------------------
# 窓評価 (税引後)
# ---------------------------------------------------------------------------
def eval_window(df, target, cost_frac, years, tax=TG.TAX):
    seg = df[df.index.year.isin(years)]
    if len(seg) < 50:
        return None
    tgt = target.reindex(seg.index).fillna(0.0)
    eq = run_equity(seg, tgt, cost_frac)
    bh = bh_equity(seg, cost_frac)
    # 年次リターン (戦略/buy&hold)
    sret = annual_returns(eq)
    # 税引後ターミナル
    at_active = TG.after_tax_active(list(sret.values()), tax)
    bh_gross = float(bh.iloc[-1])                              # 初期1基準の terminal 倍率(片道コスト込)
    at_bh = TG.after_tax_bh(bh_gross, tax)
    return dict(
        pre_terminal=float(eq.iloc[-1]), bh_terminal=bh_gross,
        at_active=at_active, at_bh=at_bh,
        beats=at_active > at_bh,
        maxdd=max_dd(eq), bh_maxdd=max_dd(bh),
        sret=sret, n_flips=int((tgt.diff().abs() > 0).sum()),
    )


def wfo_after_tax(df, target, cost_frac, folds=OOS_YEARS, tax=TG.TAX):
    """各 OOS 暦年で 戦略 vs buy&hold の税引後リターン。fold>bh = 上回り。"""
    rows = []
    for y in folds:
        seg = df[df.index.year == y]
        if len(seg) < 50:
            continue
        tgt = target.reindex(seg.index).fillna(0.0)
        eq = run_equity(seg, tgt, cost_frac)
        bh = bh_equity(seg, cost_frac)
        s_pre = float(eq.iloc[-1] / eq.iloc[0] - 1.0)
        b_pre = float(bh.iloc[-1] / bh.iloc[0] - 1.0)
        s_at = 1.0 + (s_pre * (1 - tax) if s_pre > 0 else s_pre)
        b_at = 1.0 + (b_pre * (1 - tax) if b_pre > 0 else b_pre)
        rows.append(dict(year=y, s_pre=s_pre, b_pre=b_pre, s_at=s_at, b_at=b_at,
                         beats=s_at > b_at))
    return rows


# ---------------------------------------------------------------------------
def build_targets(dfs, tf):
    """検証する (name, {sym: target系列}) のリストを返す。"""
    variants = {}
    for sym, df in dfs.items():
        variants[f'{sym}:SMA200'] = {sym: sig_sma_regime(df, 200 if tf == '1d' else 1200)}
        variants[f'{sym}:SMA100'] = {sym: sig_sma_regime(df, 100 if tf == '1d' else 600)}
        variants[f'{sym}:DON50'] = {sym: sig_donchian(df, 50 if tf == '1d' else 300)}
    return variants


def rotation_target(dfs, tf):
    """BTC<->ETH ローテーション: 両者 SMA200 上なら 直近90日モメンタム強い方を100%保有。
       片方だけ上ならそれ、両方下ならキャッシュ。単一 equity として評価するため、
       ロング対象を毎バー選ぶ「合成資産」リターンを作る。"""
    span = 200 if tf == '1d' else 1200
    mom_n = 90 if tf == '1d' else 540
    btc, eth = dfs['BTCUSDT'], dfs['ETHUSDT']
    idx = btc.index.intersection(eth.index)
    btc, eth = btc.loc[idx], eth.loc[idx]
    reg_b = (btc['close'] > btc['close'].rolling(span).mean())
    reg_e = (eth['close'] > eth['close'].rolling(span).mean())
    mom_b = btc['close'] / btc['close'].shift(mom_n) - 1.0
    mom_e = eth['close'] / eth['close'].shift(mom_n) - 1.0
    # choose: 0=cash,1=btc,2=eth  (確定足 → shift)
    choose = np.zeros(len(idx))
    rb, re = reg_b.to_numpy(), reg_e.to_numpy()
    mb, me = mom_b.to_numpy(), mom_e.to_numpy()
    for i in range(len(idx)):
        if rb[i] and re[i]:
            choose[i] = 1 if (mb[i] >= me[i]) else 2
        elif rb[i]:
            choose[i] = 1
        elif re[i]:
            choose[i] = 2
        else:
            choose[i] = 0
    choose = pd.Series(choose, index=idx).shift(1).fillna(0.0).to_numpy()
    # 合成 equity: 保有資産の close-to-close を取り、資産切替時に往復コスト
    return idx, choose, btc, eth


def run_rotation(dfs, tf, cost_frac, years, tax=TG.TAX):
    idx, choose, btc, eth = rotation_target(dfs, tf)
    mask = np.isin(np.array([t.year for t in idx]), years)
    idx2 = idx[mask]
    if len(idx2) < 50:
        return None
    ch = pd.Series(choose, index=idx).reindex(idx2).to_numpy()
    cb = btc['close'].reindex(idx2).to_numpy()
    ce = eth['close'].reindex(idx2).to_numpy()
    eq = 1.0
    cur = 0.0
    equity = np.ones(len(idx2))
    for i in range(len(idx2)):
        if ch[i] != cur:
            # 落ち(cur側) + 建て(ch側) の往復。cash 絡みは片側のみ。
            legs = (1 if cur != 0 else 0) + (1 if ch[i] != 0 else 0)
            eq *= (1.0 - (cost_frac / 2.0) * legs)
            cur = ch[i]
        if i > 0:
            if cur == 1:
                ret = cb[i] / cb[i - 1] - 1.0
            elif cur == 2:
                ret = ce[i] / ce[i - 1] - 1.0
            else:
                ret = 0.0
            eq *= (1.0 + ret)
        equity[i] = eq
    eqs = pd.Series(equity, index=idx2)
    sret = annual_returns(eqs)
    at_active = TG.after_tax_active(list(sret.values()), tax)
    # ローテーションの buy&hold ベンチ = 等加重 BTC/ETH hold
    bh_b = cb / cb[0]
    bh_e = ce / ce[0]
    bh = pd.Series(0.5 * bh_b + 0.5 * bh_e, index=idx2) * (1 - cost_frac / 2.0)
    at_bh = TG.after_tax_bh(float(bh.iloc[-1]), tax)
    return dict(pre_terminal=float(eqs.iloc[-1]), bh_terminal=float(bh.iloc[-1]),
                at_active=at_active, at_bh=at_bh, beats=at_active > at_bh,
                maxdd=max_dd(eqs), bh_maxdd=max_dd(bh), sret=sret,
                n_flips=int((pd.Series(ch, index=idx2).diff().abs() > 0).sum()))


# ---------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--tf', default='1d', choices=['1d', '4h'])
    ap.add_argument('--cost-frac', type=float, default=0.006,
                    help='往復手数料+スリッページ (国内現物, 既定 0.6%%)')
    ap.add_argument('--tax', type=float, default=TG.TAX)
    args = ap.parse_args()
    tf, cost, tax = args.tf, args.cost_frac, args.tax

    dfs = {s: load(s, tf) for s in ASSETS}
    variants = build_targets(dfs, tf)

    print('=' * 108)
    print(f'候補C crypto トレンドフォロー  tf={tf}  往復コスト={cost*100:.2f}%  税率={tax*100:.0f}%')
    print('  全て税引後で buy&hold(繰延) と比較。beat=税引後で追い抜き。')
    print('  採用バー: ①税引後>BH ②OOS成立∧全WFO fold>BH∧IS-selectable ③DD非悪化 ④両レジーム')
    print('=' * 108)

    rows = []
    hdr = (f'{"variant":<16} {"win":<14} {"pre_term":>9} {"BH_term":>9} '
           f'{"AT_act":>8} {"AT_BH":>8} {"beat":>5} {"maxDD":>7} {"BH_DD":>7} {"flips":>6}')
    for wname, years in [('IS(2017-21)', IS_YEARS), ('OOS(2022-26)', OOS_YEARS),
                         ('FULL', IS_YEARS + OOS_YEARS)]:
        print(f'\n--- {wname} ---')
        print(hdr)
        for vname, tmap in variants.items():
            sym = list(tmap.keys())[0]
            r = eval_window(dfs[sym], tmap[sym], cost, years, tax)
            if r is None:
                continue
            rows.append(dict(window=wname, variant=vname, **{k: v for k, v in r.items()
                                                             if k != 'sret'}))
            print(f'{vname:<16} {wname:<14} {r["pre_terminal"]:9.2f} {r["bh_terminal"]:9.2f} '
                  f'{r["at_active"]:8.2f} {r["at_bh"]:8.2f} {str(r["beats"]):>5} '
                  f'{r["maxdd"]*100:6.1f}% {r["bh_maxdd"]*100:6.1f}% {r["n_flips"]:6d}')
        # rotation
        rot = run_rotation(dfs, tf, cost, years, tax)
        if rot:
            rows.append(dict(window=wname, variant='ROT_BTC_ETH',
                             **{k: v for k, v in rot.items() if k != 'sret'}))
            print(f'{"ROT_BTC_ETH":<16} {wname:<14} {rot["pre_terminal"]:9.2f} '
                  f'{rot["bh_terminal"]:9.2f} {rot["at_active"]:8.2f} {rot["at_bh"]:8.2f} '
                  f'{str(rot["beats"]):>5} {rot["maxdd"]*100:6.1f}% {rot["bh_maxdd"]*100:6.1f}% '
                  f'{rot["n_flips"]:6d}')

    # WFO (税引後, OOS各年 vs buy&hold) — 主戦略 SMA200 のみ
    print('\n' + '=' * 108)
    print('WFO (OOS 各暦年, 税引後): s_at=戦略税引後係数 / b_at=buy&hold税引後係数 / beat=上回り')
    print('=' * 108)
    for sym in ASSETS:
        wfo = wfo_after_tax(dfs[sym], sig_sma_regime(dfs[sym], 200 if tf == '1d' else 1200),
                            cost, OOS_YEARS, tax)
        beats = sum(1 for w in wfo if w['beats'])
        print(f'\n{sym}:SMA200  fold勝ち {beats}/{len(wfo)}')
        for w in wfo:
            print(f'  {w["year"]}: s_pre={w["s_pre"]*100:+7.1f}% b_pre={w["b_pre"]*100:+7.1f}% '
                  f'| s_at={w["s_at"]:.3f} b_at={w["b_at"]:.3f} beat={w["beats"]}')

    df = pd.DataFrame(rows)
    out = os.path.join(HERE, 'crypto_trend_bt_result.csv')
    df.to_csv(out, index=False)

    # pre-registered 判定サマリ (SMA200 主戦略中心)
    print('\n' + '=' * 108)
    print('pre-registered 判定 (bar ①-④):')
    for sym in ASSETS:
        v = f'{sym}:SMA200'
        oos = df[(df.window == 'OOS(2022-26)') & (df.variant == v)]
        iss = df[(df.window == 'IS(2017-21)') & (df.variant == v)]
        if oos.empty or iss.empty:
            continue
        o, s = oos.iloc[0], iss.iloc[0]
        b1_oos = bool(o['beats'])
        b1_is = bool(s['beats'])
        b3 = o['maxdd'] >= o['bh_maxdd']            # maxDD(負値)が buy&hold 以上 = 浅い
        print(f'  {v}: ①OOS税引後>BH={b1_oos} | IS税引後>BH={b1_is} '
              f'(④両レジーム= OOS勝ち∧IS勝ち={b1_oos and b1_is}) | ③DD非悪化(OOS)={b3}')
    print(f'\n結果 CSV: {out}')
    print('=' * 108)


if __name__ == '__main__':
    main()
