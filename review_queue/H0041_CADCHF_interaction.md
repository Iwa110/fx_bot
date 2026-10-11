# H0041 相互作用BT: CADCHF 現行デモ構成(regime_short+cull0.6) × 部分利確(ptp)

- 実施: 2026-10-10 / スクリプト `optimizer/grid_h0041_interaction_bt.py` / 結果 `optimizer/grid_h0041_interaction_result.csv`
- エンジン: `optimizer/grid_dirbias_improve_bt.run_bt` に ptp_frac/ptp_mult を移植(grid_floatstop_bt L131-191 と同一ロジック)。
  回帰ガード `optimizer/test_dirbias_ptp_port.py` 9/9 PASS(ptp=None で移植前 commit 2d0aacd と完全一致 / ptp単体で grid_floatstop_bt と完全一致)。
- データ: CADCHF Dukascopy 1h 2015-07〜2026-07 / IS 2015-21 / OOS 2022-26 / WFO=OOS内暦年fold(60日未満除外, loopと同一→2026は1-7月の部分年)
- 共通: atr1.5/lv5/ci65/fs-943k/b48=48h/lot1.0/quote_jpy170。req_cap_99 = 暦月basis block3/60mo/20000/seed42(DD99。全構成で最悪単発×gap_buffer より大きく、loop MC 値と一致)

## 4構成比較

| 構成 | IS PF | OOS PF | decay | WFO最小fold | WFO folds (2022/23/24/25/26) | n IS/OOS | req_cap_99 | net/yr | cull発火 | FS発火 |
|---|---:|---:|---:|---:|---|---:|---:|---:|---:|---:|
| A 現行 regime+cull0.6 | 1.551 | **1.462** | +0.058 | 0.741 | 1.46/1.84/3.32/1.43/0.74 | 485/687 | **2.33M** | 884k | 64 | 1 |
| B A+ptp(0.35/0.5) | 1.607 | 1.331 | +0.171 | 0.896 | 1.28/1.37/2.89/1.40/0.90 | 941/1374 | 2.59M (+11.2%) | 705k | 48 | 1 |
| C regimeのみ+ptp | 1.575 | 1.321 | +0.162 | 1.004 | 1.23/1.52/2.89/1.28/1.00 | 887/1348 | 3.53M | 667k | 0 | 12 |
| D H0041単体(再現) | 1.398 | 1.503 | -0.075 | 1.363 | 1.73/1.47/2.02/1.36/1.71 | 1216/1647 | 3.70M | 866k | 0 | 14 |
| (参考) core baseline | 1.429 | 1.428 | +0.001 | 0.897 | 1.87/1.40/0.90/1.48/1.39 | 597/813 | 3.91M | 998k | 0 | 18 |

- D は H0041 カード(IS1.3978/OOS1.5032/decay-0.075/wfoMin1.3626/req_cap 3,702,703)と完全一致 → エンジン移植の妥当性を確認。

## B plateau (ptp_mult=0.5 固定, ptp_frac 近傍)

| ptp_frac | IS PF | OOS PF | WFO最小fold | req_cap_99 | net/yr | cull | FS |
|---:|---:|---:|---:|---:|---:|---:|---:|
| 0.25 | 1.543 | 1.347 | 0.887 | 2.57M | 729k | 51 | 1 |
| 0.35 | 1.607 | 1.331 | 0.896 | 2.59M | 705k | 48 | 1 |
| 0.45 | 1.627 | 1.394 | 0.907 | 2.26M | 703k | 39 | 0 |

- 近傍全点で OOS PF < A(1.462)、net/yr は A比 -18〜-20%。plateau は平坦(=偶然の谷ではない)で、劣化は構造的。

## 判定: **反映不可**(vps/ 変更なし)

| 条件 (B vs A) | 結果 |
|---|---|
| OOS PF 非劣化 | ✗ 1.331 < 1.462 |
| WFO最小fold > 1.0 | ✗ 0.896 (2026部分年。2022-25のみでも B 1.28 < A 1.43) |
| req_cap_99 非増加 | ✗ 2.59M > 2.33M (+11%) |

- 解釈: ptp と cull0.6 はどちらも「逆行時の残存エクスポージャー削減」で効果が重複する。cull 下では ptp が利益側(TP到達レッグの利幅)を削るだけになり、IS は僅かに上がるが OOS・net・req_cap が悪化。H0041 の単体改善(core baseline 比)は既に cull0.6 が回収済みの領域。
- 注記: A 自身も 2026部分年 fold が 0.74(loop の gate6 定義では不合格)。これは ptp と独立の現行構成の観察で、2026-07 までの部分年・n小。
  方針(2026-10-11決定): 監視のみ。再評価トリガー = 2026年fold完結(年末) or CADCHFデモ実現PF<1.0。確認内容は 2026 fold の n と PF ブートストラップ信頼区間のみ(OOS非消費)。
- 推奨: H0041 は「core baseline 上でのみ有効、現行 v8 構成とは非加法的」として close。CADCHF demo は現行 A を維持。
