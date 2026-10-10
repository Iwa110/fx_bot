# Candidate Card: H0041 (CADCHF / grid_partial_takeprofit)

- base_config: `CADCHF`
- params (delta): `{'ptp_mult': 0.5, 'ptp_frac': 0.35}`
- created_at: 2026-10-10T15:13:22.074029+00:00
- data source: dukas_11yr (2015-07-22T12:00:00+00:00 ~ 2026-07-17T20:00:00+00:00)

## 構造的理由 (structural reason)
グリッドの各レッグは平均回帰が部分的に進行した時点(グリッド幅の一部=ptp_mult*gw)で既に往復の一部を捕捉している。その時点でレッグlotの一部(ptp_frac)を確定させることで、再度の逆行(価格が元の往復を割り込み未実現益を消す、またはfloat_stopへ近づく)に対する当該レッグの残存エクスポージャーを下げつつ、逆行せず回帰が継続した場合は残りlotが通常TPまでの捕捉を維持する。これはAUDCAD 3段階Z-score平均回帰エンジンで判明した「回帰速度(T_reg)と決済スタイル(一括 vs 部分利確)の対応関係」[[project_mr_exit_depth_scaleout_20260716]]と機構的に同根だが、あちらは別エンジン(z-scoreの絶対水準、ラダーでない)での結果であり、同じ結論(速い回帰には部分利確が有利/遅い回帰には一括が有利)がGridのラダー深さという別の構造軸でも成立するかは未検証で、むしろ逆(部分利確はAUDCADでnet-worseだった)の可能性も示されている。それを確認することが本仮説の目的であり、結論を先取りしない。

## IS / OOS metrics
| window | PF | net | n_trades | n_years |
|---|---:|---:|---:|---:|
| IS  | 1.3978 | 4097185.0 | 1216 | 6.442 |
| OOS | 1.5032 | 5207573.0 | 1647 | 4.534 |
- decay = 1 - PF_OOS/PF_IS = **-0.0754**

## WFO (annual folds)
- folds: [1.7296, 1.4662, 2.024, 1.3626, 1.7081]
- wfo_min_pf: **1.3626** (threshold 1.0)

## Plateau (neighbor +-1 step, IS window)
| variant | pf |
|---|---:|
| 0.3 | 1.2943 |
| 0.4 | 1.342 |
| 0.5 <- selected | 1.3978 |
| 0.6 | 1.4342 |
| 0.7 | 1.4661 |
- max_variation_pct: 0.0399

## Gate verdicts
| gate | verdict | detail |
|---|---|---|
| gate1_is_oos | PASS | {'pf_is': 1.3978, 'pf_oos': 1.5032, 'decay': -0.0754, 'sign_ok': True, 'decay_ok': True, 'thresholds': {'is_pf_min': 1.0, 'oos_pf_min': 1.2, 'decay_max': 0.5}} |
| gate2_sample_size | PASS | {'n_per_yr_is': 188.76, 'n_per_yr_oos': 363.26, 'threshold': 15} |
| gate3_plateau | PASS | {'max_variation_pct': 0.0399, 'sign_flip': False, 'threshold': 0.3} |
| gate4_family_budget | PASS | {'used_this_month': 1, 'cap': 4, 'month': '2026-10'} |
| gate5_graveyard | PASS | {} |
| gate6_wfo | PASS | {'wfo_min_pf': 1.3626, 'wfo_folds': [1.7296, 1.4662, 2.024, 1.3626, 1.7081], 'threshold': 1.0} |
- **overall: PASS**

## 墓場照合 (graveyard check)
- {'pass': True}

## Required capital (req_cap) change
- baseline req_cap_99: 3905111.0
- candidate req_cap_99: 3702703.0
- p_loss_5yr: 0.044

## 推奨demo設定 (recommended demo config)
`{'atr_mult': 1.5, 'ci_threshold': 65.0, 'b48_hours': 48, 'lot': 1.0, 'max_levels': 5, 'float_stop': -943000.0, 'quote_jpy': 170.0, 'ptp_mult': 0.5, 'ptp_frac': 0.35}`

---
Review: approve (PR merge = demo投入) / reject (理由1行) / hold. See design doc sec 7.3.