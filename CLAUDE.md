# FX Bot - Claude Code Context

## プロジェクト概要
FX自動売買システム。VPS(Windows Server 2022)で複数戦略を並行稼働。
月利30万円目標。現在Phase1完了判定フェーズ→Phase2移行検討中。

## ディレクトリ構成
```
C:\Users\Administrator\fx_bot\
├── vps\          # 稼働中ボット
│   ├── bb_monitor.py      # v27 magic=20250001
│   ├── trail_monitor.py   # v15
│   ├── smc_gbpaud.py      # v4 magic=20260002
│   ├── stat_arb.py        # magic=20260001
│   ├── sma_squeeze.py     # v4.5 magic=20260010 (trailing無効化 / USDJPY・EURUSD有効)
│   └── news_monitor.py    # v1 magic=20260040 (経済指標B+C複合戦略)
├── optimizer\    # バックテスト・最適化
│   ├── loop_runner.py
│   ├── backtest.py
│   ├── evaluate.py
│   ├── phase2_ai_analysis.py
│   ├── sma_squeeze_bt.py             # エントリーパラメータ最適化BT
│   ├── sma_squeeze_exit_bt.py        # 決済パラメータ最適化BT
│   └── sma_squeeze_daily_filter_bt.py
└── data\         # 14ペア 1h/5m足
```

## 戦略別現状

### BB戦略
- PAIRS: GBPJPY/USDJPY/EURUSD/GBPUSD (USDCADは停止中)
- GBPJPY bb_sigma: v27で1.5→2.0（BT全データPF: 1.019→1.275）
- USDJPY: bb_sigma=2.0、T_max=8h+exp TP Decay(τ=8h)
- EURJPY: bb_sigma=1.5、T_max=6h
- 実RR乖離の原因確定: 実機はH1足ATR(比率≈3.7倍)、BT誤差あり
- **10年BT(2016-2026, Dukascopy 5m, v29)結論: 全3ペア頑健エッジ無し**（下記Top of mind参照）。IS/OOS両方でPF>1.2の頑健条件を満たすペアは皆無。BBは長期では均衡〜負。実マネーのUSDJPY黒字(PF1.42)は短期窓の現象であり10年では再現しない。

#### ★ EURJPY バグポジション事件（2026-06-05〜06-08）記録
- **根本原因（v29修正済）**: `is_in_cooldown` が `DEAL_REASON_SL` のみ対象だったため、T_max強制決済（`DEAL_REASON_EXPERT` / comment=`BB_time_stop`）後にクールダウンが発動せず、未反転BBシグナルへ毎分(Task Scheduler)即時再エントリー→T_max決済→再エントリーを反復。
- **発生日時**: 2026-06-05 UTC 11〜12時（NFP発表時間帯）
- **被害**: EURJPY magic=20250001 で71件・-549,300円（同日USDJPY+1,660円で実質-547,638円）
- **バグポジション決済**: 2026-06-08 JST 00:12 に magic=0（手動/外部決済）で59件・+1,331,620円として決済
  - 1件あたり22,320〜22,980円の均一利益（sellグリッドが相場下落でTP一斉ヒット）
  - この利益は実力ではなく偶発的な相場動向による回収
- **v29修正内容**: `is_in_cooldown` に `BB_time_stop` コメント判定を追加。SL/T_max どちらの決済後も `COOLDOWN_MINUTES(15分)` の再エントリー禁止を適用。2026-06-05 実装・push済み。
- **EURJPY nリセット**: バグ71件は評価対象外として除外。**正常サンプル n=9（PF=0.254, WR=66.7%）からリセット**。Phase1は n=9 から再蓄積。

### trail_monitor v15
- STR/MOM_JPYペア別設定分離済み
- SMC_GBPAUD追加(activate=1.0, distance=0.7, Sell専用)

### SMC_GBPAUD v4
- Sell専用、TF=1h/HTF=1d、Session=8-20UTC、MAX_POS=1

### stat_arb
- GBPJPY/USDJPY・EURUSD/GBPUSD、MAX_POS=2ペア

### SMA Squeeze Play v4.5（稼働中、2026-06-02更新）
- magic=20260010、STRATEGY_TAG='SMA_SQ'
- **有効ペア**: USDJPY / EURUSD
- **停止ペア**: GBPJPY（v4.5: BT PF=0.999<1.2 even w/o trail + 実稼働損失）、GBPUSD（BT PF<1.0）、EURJPY（実稼働WR=0% -9,900円）
- ブローカー: axiory/exness（oanda停止中）
- ロジック: SMA200スロープフィルタ + SMAスクイーズ解放エントリー + 日足フィルター
  - エントリー条件: ADX14>20、divergence_rate≤squeeze_th、SMAスロープ単調 + 日足SMA方向一致
  - 決済: ATR×sl_atr_mult でSL、SL×rr でTP、SMA長期ブレイク強制決済 / slope-exit=3
  - **ATR trailing無効**: atr_trail_mult=0.0（全ペア）。v4.5で無効化（intrabar trailing が RR勝ちトレードを早期カットしていた真因）
  - **T_max=24h**: USDJPY/EURUSDで最大保有24時間超過で強制成行決済
  - クールダウン: 180分/ペア、MAX_TOTAL_POS=3、MAX_JPY_LOT=0.4
- 監視: heartbeat log 30分毎（`heartbeat alive pos=X/Y`）

## GitHub運用
- Repo: https://github.com/Iwa110/fx_bot (Public)
- VPS更新フロー: commit/push → VPS側でgit pull
- Raw URL: https://raw.githubusercontent.com/Iwa110/fx_bot/main/

## コーディング規約
- ASCIIクォートのみ(' と ")、スマートクォート禁止
- Pythonファイルのmagic番号体系を維持すること

## Top of mind（2026-06-08 更新）

### ★結論: BB戦略 10年BT(2016-2026) → 全3ペア頑健エッジ無し（2026-06-08）
現行ローカルは約2年のみ。Dukascopyで **5m足10.5年(2015-12〜2026-06, 各≈78万本)** を取得(`data/{GBPJPY,USDJPY,EURJPY}_5m_10y.csv`)し、v29パラメータをIS(2016-2022 7年)/OOS(2023-2026.06 3.5年)分割で検証(`optimizer/bb_10y_bt.py`)。**ATRは1h足ATR14(ewm)＝実機(risk_manager.get_atr)整合**, next-bar fill, JPYスプレッド2.0pip往復差引。

| ペア | IS PF | IS n | OOS PF | OOS n | OOS net(pip) | OOS Sharpe | 判定 |
|------|---:|---:|---:|---:|---:|---:|---|
| GBPJPY | 1.048 | 2121 | **0.927** | 1286 | -4,904 | -0.43 | ❌OOS<1.0 |
| USDJPY | 0.914 | 8105 | **0.904** | 3953 | -6,707 | -1.12 | ❌IS/OOS共<1.0 |
| EURJPY | 0.935 | 5721 | **0.863** | 2812 | -6,244 | -1.75 | ❌IS/OOS共<1.0 |

- **判定基準(IS/OOS両方 PF>1.2 ∧ n>200)を満たすペアは皆無**。GBPJPYのみIS辛うじてPF1.048だがOOSで0.927へ崩落=エッジ非持続。USDJPY/EURJPYはISすら<1.0。
- **年次黒字率**: GBPJPY 8/12年・USDJPY 3/12年・EURJPY 1/12年。GBPJPYは「負けない年が多いが利幅が薄く累積で均衡〜負」、USDJPY/EURJPYは構造的に負。
- **戦略含意**: BB逆張り(5mバンド+1h ATR SL/TP+htf4h方向ゲート)は **10年スケールで頑健な期待値を持たない**。実マネーのBB USDJPY黒字(PF1.42/WR82.8%/n=64)は **短期窓(数ヶ月)の現象**であり長期では再現しない(10年OOS WR47.9%/PF0.90に回帰)。Grid AUDCADの「フル黒字でもWFOで崩落」と同種の過適合構図。
- **BTの限界(過小評価でない方向)**: 本BTは仕様書に従い **htf4h=方向一致のみ(RSI/BBwidthサブフィルタ省略)** の簡略v29。実機のRSI<60/BBwidth等の追加ゲートは「入る回数を減らす」方向で、母集団PFを劇的に正へ反転させる効果は薄い(既存知見と整合)。よって「フィルタ簡略のせいでPFが低く出た」可能性は低く、**長期エッジ不在の結論は頑健**。
- **次アクション**: ①Phase1のBB GBPJPY停止判断(7月)は本結果で補強=停止寄り。②USDJPYは実マネーn=100到達で再判定だが、10年BTは継続を支持しない=**過大ロット投入は禁止・現行少額蓄積に留める**。③BB戦略全体のスケールアップは非推奨、リソースはGrid AUDCADへ。
- 成果物(全保持): `optimizer/bb_10y_bt.py` / `bb_10y_bt_result.csv` / `bb_10y_monthly.csv` / `data/{GBPJPY,USDJPY,EURJPY}_5m_10y.csv`。

### ★結論: Grid実マネー化 検証完了 → Go=AUDCAD単独（2026-06-08）
Step0→C 完走。真値=Dukascopy 11年。**Go判定はAUDCAD 1ペアのみ**（GBPJPY/CHFJPY/NZDJPY全てNo-Go）。

- **Step0 データ真値（重要な前提修正）**: 同一エンジン・**同一timestamp集合**で yf-bars vs duk-bars 直接比較(`optimizer/grid_data_truth_diagnose.py`):
  GBPJPY 1.55↔1.58 / NZDJPY 2.37↔2.33 = **JPYペアは matched-bar で yf≈duk(ほぼ同一)**。CHFJPY 1.56↔0.82 / AUDCAD 3.50↔2.51 = データソースで乖離。
  → **旧『yfはヒゲ過小報告でPF過大』は不正確**: JPYペアのレンジ中央値比 duk/yf=0.96〜0.98でほぼ同等。AUDCADは逆に**yfがレンジを約1.8倍過大報告**(duk/yf=0.565, 下ヒゲ比0.22)=yf AUDCADデータ品質不良。
  GBPJPY等の「1.96→0.81崩落」の主因は**データ品質でなく窓**(2024-25 benign 2年 vs 11年harsh)。**結論は不変=真値はDukascopy**(JPYで一致・AUDCAD/CHFの過大を是正・常に保守側)。history.csv実約定は中央値乖離~11pip(JPY)でDukascopy価格水準と整合(弱いが矛盾なし)。
- **StepA 11年WFO(IS4yr→OOS1yr ローリング再最適化)が決定打**(`grid_dukas_stress_wfo.py`):

  | ペア | OOS PF中央値 | OOS PF最小 | OOS累積net | パラメータ安定 | 感応度崖 | 判定 |
  |------|---:|---:|---:|---|---|---|
  | **AUDCAD** | **2.04** | **1.33** | **+11.4M** | 概ね安定(atr2.0/lv7中心) | 崖なし(近傍PF1.1〜1.5) | **✅GO** |
  | NZDJPY | 0.88 | 0.42 | -3.69M | 不安定 | fs-500kでPF0.97 | ❌ |
  | CHFJPY | 0.89 | 0.72 | -0.25M | 不安定(atr/lv飛ぶ) | atr2.0でPF0.75 | ❌ |
  | GBPJPY | 1.03 | 0.65 | -0.56M | 4/7 foldでIS適格構成すら無し | 全PF<1 | ❌ |

  - **NZDJPYは11年フルではnet+2.84M(PF1.10)だがWFOでOOS崩壊=典型的過適合**(フル黒字≠頑健)。
  - AUDCAD v7 年度別: **9/12年 PF≥1.0**(フル年の負けは2018のみ。2015/2026は部分年)。worst単発-825k。
  - イベント窓ストレス: グリッドはテール局面の大半でCIゲートにより**休眠(無建玉)=テールを自動回避**。被弾は2018VolXmas/2020COVID等の散発のみ。
  - A4 ギャップ貫通: 全FSイベントで worst単発が float_stop設定を**100%超過**(中央値1.02〜1.05倍, 最大 GBPJPY1.83/NZD1.20/AUDCAD1.10/CHF1.02倍)。
- **StepB サイジング/破産確率**(`grid_sizing_ruin.py`, 月次ブロックブートストラップ20000回/60ヶ月, lot=1.0, AUDCAD円はCADJPY108概算):
  **AUDCAD**: 必要資本(破産<1%)=**req_cap_99≈310万円/lot1.0**(MC maxDD99%ile 3.10M が拘束。単発two-sided 1.36M はそれ以下)。req_cap_99.9≈405万。worst単発(gap込)907k。**安全lot=自己資本/310万**。
  月利30万円目標: AUDCAD **lot≈4.1 / 必要口座≈1,270万円**。他3ペアは負期待値で算定不要。
- **StepC Go/No-Go ゲート**(`grid_go_nogo.py`, 6ゲート G1 OOS PF中央値>1.2/G2 OOS最小>1.0/G3 OOS累積>0/G4 param安定/G5 崖なし/G6 req_cap<500万): **AUDCAD=oooooo(全通過)。他=全落第**。
- **AUDCAD 前方検証手順(段階的・少額)**: ①現行live=v7(magic20260034, ci65/atr1.0/lv5/fs-750k)を継続。②**lot=自己資本÷310万**で開始(例:口座100万→lot0.3、310万→lot1.0)。③期間=最低3ヶ月 **かつ TP決済≥30件 かつ float-stop/B48が最低1回発火するまで**(CHFJPY教訓=損切り発火前の黒字は生存者バイアス)。④継続条件: 損切り発火後も実現PF>1.2 ∧ FSスリッページ≤設定1.3倍。⑤撤退: 実現DD>MC中央値 ∨ 発火後PF<1.0 ∨ FS>設定1.5倍。⑥**2026は部分年で現在DD中(-517k)**=投入タイミングは現DD一服を確認後が無難。
- **任意の改善余地(forward-testで検証, 必須でない)**: AUDCAD ci_threshold 65→61.8 で同PF1.35のままnet約2倍(+4.8M→+10M)。atr_mult 1.0→1.5でPF1.35→1.51・DD低下。WFOはatr2.0/lv7/fs-1.5Mを選好(但しテール増)。
- **他ペア方針**: GBPJPY/CHFJPY/NZDJPY は実マネーGrid**不可(No-Go確定)**。demo継続は可だがスケール禁止。「GBPJPY最優先」方針は**完全撤回**。
- 成果物(全保持): `grid_data_truth_diagnose.py`(+_result/_matched.csv)/`grid_dukas_stress_wfo.py`(+events/wfo/wfo_summary/sensitivity/gap_dist.csv)/`grid_sizing_ruin.py`(+result.csv)/`grid_go_nogo.py`(+scorecard.csv)。エンジン`grid_floatstop_bt.py`にfs_events/b48_events返却を追加。

### ★重大: Grid v7 PFはDukascopy高品質データで再現しない（2026-06-08）
Grid実マネー化検証のため Dukascopy(無料/口座不要/深い履歴)で **1h 11年(2015-06〜2026-06, 各≈68,500本)** を取得(`optimizer/fetch_dukascopy_ohlc.py` 1h/4h/5m/D1汎用, 出力 `data/<pair>_1h_dukas.csv` でyfinance版温存)。確定v7をそのまま回した結果(`optimizer/grid_dukas_reconfirm.py`):

| ペア | yf確定PF | **Duk 2年同窓PF** | **Duk 11年PF** | 11yr net | worst単発(設定比) | 評価 |
|------|---:|---:|---:|---:|---:|---|
| GBPJPY | 1.96 | **1.21** | **0.81** | -11.5M | -2.75M(設定-1.5Mの1.83x) | ❌実マネー不可 |
| CHFJPY | 1.51 | **0.89**(赤字) | 0.95 | -1.3M | -1.53M | ❌均衡〜負 |
| NZDJPY | 2.36 | **1.20** | 1.10 | +2.84M | -1.19M | △限界的 |
| AUDCAD | 4.01 | **1.82** | **1.35** | +4.83M | -825k | ✅11年で唯一頑健 |

- **真因(推定確度高)**: yfinanceのFX時間足は**高値/安値(ヒゲ)を過小報告**→TPが綺麗に刺さりfloat-stop発動が過少=PF過大。Dukascopyは実tick由来の正OHLCで**実機MT5(Axiory/Exness)に近い→真値に近い**。確定PF(1.96等)はデータ品質アーティファクトだった疑い濃厚。
- **戦略判断の反転**: ①**「GBPJPY最優先」は誤り**(yf＋2024-25 benign窓の偶然。11年 PF0.81/net-11.5M)。②**AUDCADが実は最頑健**(11年PF1.35・大半の年黒)→優先順位逆転。③**float-stopは損を止めきれない**(worst単発が設定を最大1.83倍超過=ギャップ貫通)。OOS-benign懸念は実データで裏付け。④CHF unpeg(2015-01)は範囲外(2015-06開始)で最悪テール未捕捉→`--years 12`で再取得余地。
- **次アクション=プロンプトB(下記)で本格検証**: まず(0)yf↔Dukascopy乖離の真因をバー単位で確定しデータ真値を決める→(A)Dukascopy 11年でv7再最適化＋実テール局面ストレス→(B)テール→必要証拠金/安全lot/破産確率→(C)go/no-go。**実マネー投入は本検証完了まで全ペア保留**(特にGBPJPY)。
- 成果物保持: `fetch_dukascopy_ohlc.py`/`grid_dukas_reconfirm.py`/`grid_dukas_reconfirm_result.csv`/`data/*_1h_dukas.csv`(GBPJPY/CHFJPY/NZDJPY/AUDCAD/USDJPY)。

### Grid不感症ウィンドウ補完探索（案A CI逆ゲート / 案B bleedヘッジ）2026-06-08 ★結論
角度を変えた探索: 「Gridが効かない局面=不感症」に条件づけて稼ぐ補完スリーブを探す。Grid v7 4ペア(GBPJPY/CHFJPY/NZDJPY/AUDCAD)で `data/*_1h.csv` 2年・IS≤2025-06/OOS≥2025-07・next-bar fill。

**不感症ウィンドウ定義**(`optimizer/grid_insensitivity.py`, t-1のみ): idle=Grid建玉ゼロ&TPなし(完全休眠) / bleed=float-stop/B48発火 or 含み損が float_stop の30%超過 / flat=20日ローリングGrid損益の傾き≈0(非discriminating→union除外・診断用)。union=idle|bleed。
- **判明: Gridは大半の日が不感症**(idle|bleed: GBPJPY IS307/348・CHFJPY 333/348・NZDJPY 284・AUDCAD 218; OOS 175-231)。GridはCI>閾値のレンジ稀少バーストでのみ建て、純益はnormal(レンジ)日に集中。**不感症日のGrid IS損益はむしろ負**(GBPJPY -1.57M / CHFJPY -1.15M = bleed日のドラッグ)。→補完が稼ぐ余地は構造的にある。

| 案 | 設計 | 結果 | 判定 |
|----|------|------|------|
| **A CI逆ゲート・マルチペア trend** | 4ペアDonchian breakout+ATRトレイル, CI≤閾値(=トレンド)のみ稼働, next-bar | 全16構成 net_R負・PF0.54〜0.79・Sharpe全負(IS/OOS)。Grid相関≈0(0.05)だがエッジ無し。ブレンドはGrid劣化(IS PF1.85→1.50 / OOS5.20→2.32 @200k) | ❌**Close** |
| **B bleedヘッジ** | Grid建玉が含み損trig接近時に同トレンド方向へ小ロット建て梯子の損失相殺(grid+hedge同一ループ) | hedge_net 全ペア/全構成/IS&OOSで負(平均-77k〜-391k)。maxDD/worst単発は微減するがPF低下(GBPJPY1.23→1.16 / AUDCAD3.99→2.57) | ❌**Close** |
| C 金利差モメンタム | 外部FRED/VIX依存＋案Aと同根 | 未実施(A/B同根で結論不変・外部データ不要方針) | — 不採用 |

- **A真因**: 平均回帰ペアでtrend/breakoutレッグ自体に頑健エッジ無し(pullback案B/D/A単一GBPJPYのClose結論が**マルチペアでも不変**と確認)。CI逆ゲートで「いつ入るか(トレンド時のみ)」は制御できてもエッジは作れない。comp_normR=0=ゲートは機能(全PnLが不感症日に着地)するが負。
- **B真因**: 深いDD(価格が逆行伸長)でトレンド方向ヘッジ→直後に**平均回帰(=Gridが本来稼ぐ動き)でヘッジが踏まれる**。テールは機械的に圧縮できるが負EV。「DD圧縮を負EV保険で買う」典型。採用基準(DD縮小**かつ**Sharpe維持)を両立せず。
- **総括=「相関は狙い通り≈0だがエッジ無し」4例目**(配分A/レジームB/α三角/今回trend)。信号層・補完層・メタ層・不感症条件づけ層すべて新規採用ゼロ。Gridの稼ぎはレンジ稀少バーストに構造依存し、その隙間(トレンド)を埋める頑健エッジはこの平均回帰ペア群に存在しない。
- 成果物(全保持): `optimizer/grid_insensitivity.py`/`grid_insensitivity_complement.py`/`grid_bleed_hedge.py` + `grid_insensitivity_complement_result.csv`/`grid_insensitivity_complement_trades.csv`/`grid_bleed_hedge_result.csv`。
- **次アクション**: 不感症補完探索も終了。CLAUDE.md既存方針(Grid GBPJPY最優先・AUDCAD次点 + BB USDJPY実マネー蓄積)へ集約継続。

### ポートフォリオ・メタ層探索（配分A / レジームB）2026-06-07 ★結論
信号層が枯渇（BB/CORR/stat_arb三角/London/pullback/pre_event/session 全Close）したため、**既存の黒字エッジの上に乗せるメタ層**でrisk-adjusted return底上げを探索。素材=Grid v7 4ペア＋BB USDJPY。`data/*_1h.csv` 2年(729日)・IS≤2025-06/OOS≥2025-07・next-bar fill・全ウェイト/ゲートt-1のみ。エンジンはv7 PF(1.96/1.51/2.36/4.01)再現済み。

| 案 | 判定 | 根拠 |
|----|------|------|
| **A 配分層(vol-target/risk-parity)** | ⚠️**条件付きClose(弱パス)** | FULL/IS で+38%/+62% Sharpe・-26〜35% maxDD と機構は本物。だが**事前登録のOOS Sharpe+20%は未達**(OOSがGridにbenignすぎてbase OOS Sharpe=4.90→+20%=5.88に届かず)。IS最良(risk_parity_L20 IS3.55)はOOS3.39へ崩落=動的スキームの過適合も確認 |
| **B レジーム層(ADX/ERゲート)** | ❌**Close** | tail(worst単発)を消すにはforce-exitが必要だがnet retain20〜65%で基準割れ。noaddはnet改善(GBPJPY IS net2倍超)だが**worst単発不変**(-1.62M→-1.62M, float-stopは既存ポジで発動しゲート不可)＋2025春GBPJPY単一イベント依存＋OOS検出力ゼロ |
| C 相関レジーム | 保留 | A段階で**スリーブ間相関≈0**(最大0.27 GBPJPY×CHFJPY、他±0.08)判明→"崩れる相関"前提が薄く優先度低 |

- **最重要前提=相関≈0**: 「全部mean-reversionだからtailが同時に出る」仮説は外れ。各Gridは別ペア・別タイミング発火で分散が効く。**固定1ロットは非効率配分**: 最高Sharpe(2.93)・最低vol(41k)のAUDCADが最低Sharpe(1.06)・最高vol(122k)のGBPJPYと同ロット=GBPJPYにリスク偏重。
- **BB USDJPY**: 実マネー確定エッジ(PF1.42)だが2年5m無し(yfinance~3mo/MT5 0配信)→1h近似プロキシはPF0.84の負け(WR82%再現不能)。極小volで素朴な逆volだと最低volのBBに最大ウェイト=毒→配分層は正Sharpe4 Gridに限定。
- **唯一の低リスク示唆(A由来・動的不要)**: `vps/grid_monitor.py` の `LOT_PER_PAIR` を静的inverse-vol寄りに微調整(GBPJPYロット↓・AUDCAD/CHFJPY/NZDJPY↑)。動的リバランス層/新規monitorは過適合・運用複雑化に見合わず不要。OOS厳格基準は未通過のため実行は任意・少額前方検証推奨。
- 成果物(全保持): `optimizer/portfolio_meta_bt.py`/`portfolio_alloc.py`/`portfolio_regime.py` + `portfolio_meta_sleeves.csv`/`portfolio_alloc_result.csv`/`portfolio_regime_result.csv`。
- **次アクション**: メタ層探索も一旦終了。信号層・補完層・メタ層すべて新規採用ゼロ→ CLAUDE.md既存方針(Grid GBPJPY最優先・AUDCAD次点 + BB USDJPY実マネー蓄積)へ集約継続。

### 新規エッジ探索（案α / pre-event / session）3件棚卸し（2026-06-07）★結論
方針転換: Grid補完(案A/B)Close後、Gridと構造的に独立な領域で新規エッジを探索。3件をローカル2年データ＋現実的執行で再検証。

| 戦略 | 再検証条件 | 結果 | 判定 |
|------|-----------|------|------|
| **案α 非JPY三角stat_arb** | EURUSD/GBPUSD/EURGBP, OU平均回帰, IS/OOS, next-bar fill | 全81組合せ IS PF≤0.16/OOS≤0.30, 全Sharpe負 | ❌ **Close** |
| **pre_event vol_squeeze** | NFP/CPI実日付(news_events.csv)＋ローカル2年1h | 全8sweepセル PF≤0.72/Sharpe全負 | ❌ **Close** |
| **session_fakeout** | Dukascopy 2年5m(148k本)＋全16sweep | 全セル PF≤0.82/Sharpe全負(n最大629)。3moで見えたtp0.3正エッジは小標本の偶然 | ❌ **Close** |

- **案α真因**: 三角恒等式 EURGBP=EURUSD/GBPUSD はclose上ほぼ完全成立。乖離=非同期クォートノイズ(std1.3pip/半減期<1足)。当初「信号バー終値で約定」モデルがPF400+を出したが**バー内ルックアヘッド**。next-bar fillで全崩壊。独立性は完璧(Grid相関≈0)だが領域にエッジ無し→案A/B/αで「相関は狙い通りだがエッジ無し」3例目。スクリプト: `optimizer/stat_arb_nonjpy_bt.py`。
- **pre_event真因**: 近似カレンダーのズレ由来ではなく**本物の負エッジ**(実日付化でPF0.756→0.694と悪化)。NFP/CPI前ボラ縮小×BB逆張りにエッジ無し。FOMCはnews_events.csv未収録で実日付検証不可(旧FOMC-only PF1.57は近似日×n25×DD174%で未検証のまま放置)。`optimizer/pre_event_vol_squeeze_bt.py` に `--local`/`--real-cal` 追加済み。
- **session_fakeout知見(最終)**: 当初yfinance(58日/n27/PF0.33)はtp0.5の1点で見誤り→ローカル3mo(n≤72)では「tp0.3でfake_pips横断の正エッジ(PF1.15〜1.77)」に見えた。**だがDukascopy 2年5m(148k本)で再検証すると全16sweepセルが PF≤0.82・Sharpe全負(n最大629)。tp0.3の正エッジは小標本の順行窓アーティファクトと確定→Close**。「3moの正PFは2年で消える」典型例。
- **データ源教訓**: `data/*_1h.csv`=2年だが**`data/*_5m.csv`は元々~3moのみ**(update_data.pyがyfinance依存=5m遡及不可)。VPS MT5 demo(Axiory/Exness Trial)も**深い5mを配信せず copy_rates=0**(probe直近10本すら0)→ MT5依存は不可。**解決=Dukascopy(無料/口座不要/深い履歴)をローカル取得**: `optimizer/fetch_5m_dukascopy.py`(専用venv `.venv_dukas`)。`vps/export_5m_history.py`はMT5不可と判明したため非常用(成果物保持)。5m依存BTは今後Dukascopyで取得すること。
- **次アクション**: 案α/pre_event/session 3件すべてClose。新規エッジ探索は一旦終了し、CLAUDE.md既存方針(Grid GBPJPY最優先・AUDCAD次点 + BB USDJPY)へリソース集約。成果物BT/CSV/スクリプトは再検証用に全保持。

### Trend補完戦略（案B/D/A）全BT完了 → 打ち切り（2026-06-07）★結論
- **判定: Grid補完用 Trendレッグ（GBPJPY Donchian/ATRトレイル）は3案すべて不採用。アイデア打ち切り、Grid/BBへ集約。**
- 動機: GridはトレンドでDDを踏む。負相関のTrendをブレンドすればGrid単体よりPF改善/DD圧縮できるはず、を検証。
- スクリプト: `optimizer/pullback_trend_bt.py`(案B) / `optimizer/pullback_gated_trend_bt.py`(案D/A) / `optimizer/pullback_grid_complement.py`(補完指標)。IS 2024-04〜2025-06 / OOS 2025-07〜2026-05。
- 採用基準: IS[Grid DD上位5区間Trend損益>+5R & PF>1.1] / OOS[同>0R]。

| 案 | 設計 | Grid DD窓Trend損益(IS) | 単体PF | 結果 |
|----|------|----------------------:|-------:|------|
| B | 常時稼働 Donchian breakout | ≈+1.2R | <1.0 | ❌ DD窓"不在"（corr≈0）で補完不成立 |
| D | Grid含み損ゲート(残高比-2/-3/-5%) | **+9.6R** | <1.0(最高0.91) | ❌ 窓不在は解消も、単体PF<1.1でブレンドがGrid劣化 |
| A | ゲート深掘り(-10〜-25%=float-stop近傍) | +4〜6R(減少) | -25%でPF1.12 | ❌ OOS ddwin全0.00 / PF>1.1は開放n=1の過適合 |

- **3案共通の真因: GBPJPY trail-following のTrendレッグ自体に頑健エッジが無い。** ゲートで「いつ入るか」は最適化できてもエッジは作れない。
- 案A深掘りの知見: ①OOSのGrid DD窓は他ペア(CHF/NZD)主導で浅く、GBPJPY単体の深ゲートはそこで開かず→OOS補完=0.00。②深いDDでのトレンド順行ペイオフは過去2年で**2025春GBPJPYの単一イベント**のみ、OOS再現せず。③ブレンドPFはGrid単体(GBPJPY 1.87 / COMBINED 1.20)を全構成で劣化。相関は最良でも-0.28(目標<-0.3に未達)。
- **アクション: Trend補完は打ち切り。Grid(GBPJPY最優先・AUDCAD次点)とBB USDJPYにリソース集約**（CLAUDE.md既存方針と整合）。成果物BT/CSVはリポジトリ保持（再検証用）。

### Grid CHFJPY 実残高ベース評価（2026-06-02）★最優先タスクの結論
- **判定: 実マネー・エッジは未確認。demo口座の小サンプル × トレンド順行窓での額面利益にすぎず、スケール不可。**
- history.csv の額面 +187,768円（magic=20260032 / WR100% / n=8）の内訳:
  - 実ロット(1.0)は **5/28 21:03以降の約3.5日・実質5決済のみ**（それ以前は0.01の検証ロットで損益ほぼ0）
  - 期間中 CHFJPY は 201.9→204+ の上昇トレンド。buyが上昇を捕捉、sellは押し目の平均回帰で小利確 → **逆行トレンドでfloat-stopが発動した実績はゼロ（生存者バイアス）**
- WR=100%はグリッドの構造的必然（TPだけ計上、含み損は B48/float-stop まで保有）。実残高評価には**不利トレンドで損切りが発動する局面のデータが必須**だが未取得。
- **テールリスク（grid_monitor.py コメント記載値）**:
  - CHFJPY 1.0lot: FLOAT_STOP=**-1,500,000円/方向**、B48両建て最悪ケース **約-2,250,000円**
  - つまり実現+18.8万円に対し最悪-150〜225万円 → 実現益:テール ≈ 1:8〜1:12 の「ペニー拾い」プロファイル
- **結論アクション**: 実マネー移行は保留。最低でも(a)float-stop/B48が一度発動する不利局面を含むdemoデータ、(b)その損切り後も実現エッジが残るか、を確認するまでスケール禁止。NZDJPY/AUDCAD(lv7/1.0lot追加分)も同様に未検証。

### Grid float-stop込み2年BT 全5ペア（2026-06-02 / grid_floatstop_bt.py）★(a)(b)を過去データで実施
- ライブ設定(LOT_PER_PAIR / atr_mult / max_levels=7 / B48=48h / FLOAT_STOP_PER_PAIR)に忠実、各ペア_1h 2024-04〜2026-04(約12,330本)で検証。非JPYは quote_jpy 係数でJPY換算(NZDUSD≈155, AUDCAD CADJPY≈108)。検知=バー逆行extreme(保守的)。
- **(a)達成**: float-stopが全ペアで複数回発動(下表)= 不利トレンド局面を十分に内包。
- **(b)結論（PF=net・float-stop損込み）**:

| ペア | lot | PF | 2年net円 | TP | float-stop | worst単発 | maxDD | 判定 |
|------|----:|---:|---------:|---:|-----------:|----------:|------:|------|
| GBPJPY | 1.0 | **1.96** | +5,965,956 | 269 | 4回-6.20M | -1.62M | 3.40M | ✅生存(最良) |
| AUDCAD | 1.0 | **1.26** | +2,625,094 | 585 | 19回-10.27M | -0.60M | 1.08M | ✅生存(要CADJPY前提) |
| NZDUSD | 0.01 | 1.81 | +15,905 | 121 | 1回 | -0.02M | 0.02M | △microロット・額僅少 |
| NZDJPY | 1.0 | **0.96** | -428,291 | 562 | 18回-10.02M | **-0.75M** | 2.03M | ❌均衡〜負 |
| CHFJPY | 1.0 | **0.70** | -3,757,313 | 164 | 8回-12.46M | -1.62M | 5.72M | ❌非生存(最悪) |

- **PFはquote_jpy(FXレート想定)にほぼ不感**（TP/float-stop両方が同係数でスケールしgross比は不変）→ AUDCAD/NZDUSDの正エッジ判定は頑健。net円額のみレート想定で変動。
- NZDJPYは float_stop=-500k 設定だが worst単発-749k → **逆行extreme/ギャップで閾値を超過**。実機の単発損は閾値で完全には止まらない点に注意。
- B48は全ペアn=0（float-stopが先に発動）。
- 旧BT(CHFJPY IS PF=1.023/OOS 1.521)はfloat-stop未実装・lot0.02 → **float-stop現実でCHFJPY負に反転、過大評価だった**。
- **次アクション（2026-06-02完了）**: ①CHFJPY→v6/v7で再設計済み（ci65/atr1.0/lv3 BT PF=1.51 ✅反転）②NZDJPY→v7で最適化済み（ci61.8/atr1.5/lv7/fs-1.0M BT PF=2.36 ✅反転）③Grid実マネー候補は GBPJPY最優先・AUDCAD次点（DD吸収資金が前提）→ demo前方検証中。

### Grid パラメータ最適化（2026-06-02 / grid_param_sweep.py + grid_param_validate.py）★B48デッド対策
- **「B48 n=0」の真因**: max_levels=7 で最大深度の累積含み損が深く、48hを待たず float-stop(-1.5M) が先に発動 → B48デッド。b48_hours短縮(24/36h)はほぼ無効。
- **本丸の修正 = max_levels 7→3/5（+ ci_threshold 61.8→65）**: ラダーを浅くすると B48(マイルドな時間決済)が float-stop より先に効き(n_b48 が 0→8〜15)、**単発損とDDが激減・PFも改善**。負けペアが正に反転。
- IS/OOS 半々分割で両期間 net>0 & PF≥1.2 & 損失イベント実発生(過適合除外)を満たす頑健構成を選定。推奨（フル2年）:

| ペア | 変更(ci/atr/lv) | PF | net円 | n_tp | n_b48 | worst単発 | maxDD | vs LIVE |
|------|-----------------|---:|------:|-----:|------:|----------:|------:|---------|
| CHFJPY | 61.8→65 / 2.0→1.0 / 7→3 | **1.51** | +1,412,119 | 146 | 9 | -0.65M | 0.61M | -3.76M→+1.41M ✅反転 |
| NZDJPY | 61.8→65 / 1.0→1.5 / 7→5 | **1.65** | +1,915,527 | 190 | 1 | -0.58M | 0.66M | -0.43M→+1.92M ✅反転 |
| AUDCAD | 61.8→65 / 1.0 / 7→3 | **2.75** | +4,275,296 | 303 | 15 | -0.31M | 0.31M | +2.63M→+4.28M ✅大幅改善 |
| GBPJPY | — | 1.96 | +5,965,956 | 269 | 0 | -1.62M | 3.40M | LIVE維持が最良(PF最高) |

- **GBPJPY注意**: LIVE(lv7/atr1.5)が既にPF1.96で最良・IS/OOS両正。浅化(atr3.0/lv3)はDD 3.4M→1.74Mに圧縮できるがPFは1.26に低下 → **純益重視ならLIVE維持、DD抑制重視なら浅化**の選択（任意）。
- 検証注意: B48=48h据置(短縮効果なし)。worst単発はギャップで閾値超過しうる。AUDCAD net円はCADJPY想定でスケール(PFは不感)。
- **次アクション**: vps/grid_monitor.py の PAIR_CONFIG を CHFJPY/NZDJPY/AUDCAD で上記に変更 → demo再蓄積で前方検証。【v7で実装済み・下記参照】

### Grid float_stop結合最適化（2026-06-02 / grid_floatstop_sweep.py）★v7実装済み
- float_stop自体を最適化変数に追加。**知見: float_stopは深いラダーのテール保険としてのみ有効。浅いラダーでは緩め固定が正解**（きつくすると回復可能ポジを切りPF・DD悪化: CHFJPY lv3 fs-300k→PF0.98/DD980k vs fs-1.0M→PF1.51/DD608k）。
- **NZDJPY: float_stop -500k→-1.0M に緩めると lv7 が復活** → PF1.65→2.36 / net +1.92M→+4.55M（ci 65→61.8, lv 5→7）。テール -576k→-1.05M。
- **AUDCAD: lv3→5 / fs-500k→-750k** → PF2.75→4.01 / net +4.28M→+5.50M。テール -315k→-445k。
- CHFJPY/GBPJPY は v6据置（float_stop非発動/純益最良）。IS/OOS頑健性確認済み（NZDJPY IS2.19/OOS2.65, AUDCAD IS5.61/OOS2.92）。
- NZDJPY/AUDCAD は新float_stopに合わせ DD_DAY/DD_WEEK も緩和（ブレーカーが検証済み決済を先回りしないため）: NZDJPY DD_DAY-1.0M/DD_WEEK-2.0M, AUDCAD DD_DAY-750k。

### 取引実績集計（2026-06-03 / history.csv 4/24-6/2, 418決済）
- **実マネーで黒字エッジ確認は BB USDJPY 1本のみ**（PF1.42 / WR82.8% / n=64 / +11,960円）
- BB GBPJPY: PF0.95 / WR84.0% / n=25（σ=2.0後もまだPF<1.0、均衡圏）
- BB停止ペア(EURUSD/GBPUSD/USDCAD)の4月歴史的損失 -130k が総額を圧迫（現行非稼働）
- **SMA Squeeze: n=20 WR=0% -19,285円はv4.5以前（trailing有効時）のデータ。v4.5（trailing無効化・GBPJPY停止）適用後はサンプルリセット・n=0スタート。** v4.5でn=10以上蓄積後に再評価。
- 月次額面: 4月-67.5k / 5月+74.5k / 6月(2日時点)+71.5k だが、5月後半以降の黒字は**Grid CHFJPY(demo)が実体**。実マネー実体は概ね月±1万円台

### 現在の稼働状態（2026-06-03時点）
- **sma_squeeze**: v4.5 / axiory/exness（oanda停止中）
  有効ペア: USDJPY/EURUSD / 停止: GBPJPY（v4.5新規停止）・GBPUSD・EURJPY
- **bb_monitor**: v27 / 3ブローカー（Task Scheduler毎分実行）
  **【VPS未反映】git pull → bb_monitor再起動が必要**
- **trail_monitor**: v15 / axiory/exness・oanda（独立プロセス）
- **grid_monitor**: v7 / axiory/exness（NZDUSD停止 / GBPJPY 20260031 / CHFJPY 20260032 / NZDJPY 20260033 / AUDCAD 20260034）
  **【VPS未反映】v7 push → git pull → restart_grid.ps1 で全grid再起動が必要**
  v7確定: GBPJPY(61.8/1.5/7/-1.5M) CHFJPY(65/1.0/3/-1.5M) NZDJPY(61.8/1.5/7/**-1.0M**) AUDCAD(65/1.0/**5**/**-750k**)
- **news_monitor**: v1 / 未起動（news_monitor.bat 起動待ち）
- MT5端末起動順: OANDA→(60秒後)Axiory→Exness

### bb_monitor.py v27（2026-05-28完了）
- **GBPJPY bb_sigma 1.5→2.0**: BT全データ PF 1.019→1.275（N=268）達成
- BT実施: bb_analysis_bt.py（sigma sweep + SL×sigma グリッド）
- 実RR=0.12 の根本原因確定:
  - 実機: `rm.get_atr()` = H1足ATR14（ewm）
  - BT: `calc_atr(df_5m)` = 5m足ATR14（rolling mean）
  - H1/5m ATR比率: GBPJPY=3.73倍、USDJPY=3.91倍
  - → BT SL/TP は実機より約4倍小さい（BT sl=2.5 ≈ 実機 sl=10倍相当）
  - → 実稼働WR=82%は「H1 ATR基準の大きなSLが早期にストップされにくい」ため

### Phase1判定進捗（BB 全期間, 2026-06-08更新, magic=20250001）
| ペア   |    PF | 勝率  | n  | 総合  |
|--------|------:|------:|---:|-------|
| USDJPY | 1.479 | 83.3% | 66 | ⚠️ PF合格・n不足のみ（n=100まであと34件≈3週）、σ=2.0維持 |
| GBPJPY | 0.950 | 84.0% | 25 | ❌（PF未達/均衡圏）σ=2.0後も<1.0、PF>1.2転換 or 停止を7月判断 |
| EURJPY | 0.254 | 66.7% | **9** | ❌ **バグn=71除外・n=9からリセット（2026-06-08）**。正常サンプル蓄積中。バグ事件記録は上記参照 |
| EURUSD | — | — | — | enabled=False（BT PF<0.7/v20停止） |
| GBPUSD | — | — | — | enabled=False（実稼働PF=0.294/v20停止） |

### USDJPY Phase1確定への分析結果（2026-05-28）
- BT全データ PF=1.159（5m ATR基準）→ H1 ATR補正後は実稼働 PF=1.355 が正
- bb_sigma=2.0の妥当性: σ=1.5(PF=0.835) vs σ=2.0(PF=1.161) → σ=2.0が優位
- T_max=8h+exp Decay: OOS PF=1.137→1.211 (+6.5%)、v26実装根拠確認済み
- → n=100到達まで蓄積継続（現在n=66、目安あと3週間）

### 翌日確認事項
- **【最優先】VPS**: `git pull origin main` → bb_monitor.bat 再起動（v27反映）
- **【要対応】VPS**: `news_monitor.bat` 起動（axiory/exness）
- bb_monitor v27 動作確認: GBPJPY の `シグナル確定` ログで `BB_σ=2.0` を確認
- Phase1 USDJPY: n=100超えたら再判定（目安あと3週間）
- **EURJPY バグ後の正常稼働確認**: v29クールダウン修正が効いているか（15分以内の再エントリーが抑制されているか）ログ確認
- backtest.py BT精度向上（任意）: simulate_with_stage2をH1足ATRに切り替え
- **BB戦略 10年バックテスト**: ローカルClaudeCodeに依頼中（Dukascopyデータ取得 + 全ペアIS/OOS評価）

## 直近タスク
- [x] BB戦略 実稼働vsET乖離分析（2026-05-28: H1/5m ATR比率定量化）
- [x] GBPJPY bb_sigma最適化BT（2026-05-28: sigma=2.0でPF>1.2達成）
- [x] USDJPY Phase1補強分析（2026-05-28: σ=2.0維持・T_max有効性再確認）
- [x] bb_monitor v27: GBPJPY sigma 1.5→2.0・push済み（2026-05-28完了）
- [x] strategy_spec.md / strategy_spec.html 更新（2026-05-28完了）
- [ ] **VPS**: `git pull origin main` → bb_monitor.bat 再起動（v27反映）
- [ ] **VPS**: `news_monitor.bat` 起動（axiory/exness）
- [ ] Phase1 USDJPY: n=100超えたら再判定（あと36件≈3〜4週間）
- [ ] backtest.py BT精度向上: simulate_with_stage2をH1足ATRに切り替え（任意）
- [x] **Grid CHFJPY 実残高ベース評価（2026-06-02完了）**: demo小サンプル・トレンド順行窓・テール-1.5〜2.25M円 → 実マネー移行保留
- [x] **Grid float-stop込み2年BT 全5ペア（2026-06-02完了 / grid_floatstop_bt.py）**: GBPJPY PF1.96✅ / AUDCAD PF1.26✅ / NZDUSD PF1.81(micro) / NZDJPY PF0.96❌ / CHFJPY PF0.70❌
- [x] **Grid CHFJPY**: v6/v7で再設計完了（ci65/atr1.0/lv3/fs-1.5M → BT PF=1.51 ✅反転）demo前方検証中
- [x] **Grid NZDJPY**: v7で最適化完了（ci61.8/atr1.5/lv7/fs-1.0M → BT PF=2.36 ✅反転）demo前方検証中
- [ ] **Grid 実マネー候補選定**: GBPJPY最優先・AUDCAD次点。DD(3.4M/1.1M)・単発損(-1.62M/-0.60M)を吸収できる資金計画を策定
- [x] **Grid パラメータ最適化（2026-06-02完了 / grid_param_sweep.py + grid_param_validate.py）**: 真因=lv7でfloat-stop先行→B48デッド。lv7→3/5+ci65でCHFJPY/NZDJPY反転・AUDCAD改善（IS/OOS頑健確認）
- [x] **vps/grid_monitor.py v6 実装（2026-06-02完了）**: CHFJPY(ci65/atr1.0/lv3)・NZDJPY(ci65/atr1.5/lv5)・AUDCAD(ci65/atr1.0/lv3)。per-pair ci_threshold追加(CI_TH)。strategy_spec.md/html同時更新済み
- [x] **vps/grid_monitor.py v7 実装（2026-06-02完了）**: float_stop結合最適化。NZDJPY(61.8/1.5/7/-1.0M)・AUDCAD(65/1.0/5/-750k)更新+DD緩和。CHFJPY/GBPJPY据置。spec md/html・restart_grid.ps1同時更新
- [x] **VPS**: `git pull origin main` → restart_grid.ps1 で全grid再起動（v7反映）→ demo前方検証（2026-06-03完了）
- [ ] **GBPJPY浅化(任意)**: DD抑制重視なら atr3.0/lv3 化を検討（PFは1.96→1.26に低下）
- [ ] **SMA Squeeze 存続判定**: v4.5（trailing無効・GBPJPY停止）で新サンプルn=10到達まで蓄積、正転しなければ全停止しGrid/BBへリソース集約
- [ ] **BB GBPJPY**: 7月までにPF>1.2転換なければ停止、Phase1をUSDJPY単独合格で締める判断
- [x] **EURJPY BBバグポジション決済・nリセット（2026-06-08完了）**: バグ71件を除外、正常n=9からPhase1再蓄積。バグ事件・v29修正をCLAUDE.md/strategy_specに記録済み
- [ ] **BB戦略 10年バックテスト**: ローカルClaudeCodeに依頼。Dukascopyで10年5mデータ取得→現行v29パラメータ（GBPJPY/USDJPY/EURJPY）でIS/OOS評価。結果をもとにPhase1判定基準・パラメータの頑健性検証

## 作業スタイル
- 作業時間: 夜まとめて1〜2時間
- Chat: タスク設計・判断のみ（10〜15分）
- Code: 実装・実行・push（残り全て）
- Codeセッション開始前に必ずタスクリストを用意する

## 夜の終了チェックリスト（2026-05-28）
- [x] 変更ファイルをcommit/push済み
- [x] CLAUDE.mdのTop of mindを更新済み
- [x] 翌日Chatで確認すべき事項をメモ済み
- [x] bb_monitor v27: GBPJPY sigma 1.5→2.0・push済み
- [ ] VPS: git pull origin main → bb_monitor.bat 再起動（翌日手動対応）
- [ ] VPS: news_monitor.bat 起動（翌日手動対応）

## ロードマップ

### Phase1（現在）: 実稼働データ蓄積・完了判定
- 判定基準: PF>1.2 / 勝率>50% / DD<15%
- 対象ペア: GBPJPY/USDJPY/EURUSD/GBPUSD
- 完了条件: 全ペアで判定基準クリア
- 完了後タスク: USDCAD再評価BT実施

### Phase2: 戦略改善・追加
- BB戦略RR改善（Stage2 distance微調整継続）
- 200MA Pullback本格導入（USDJPY Pinbar候補）
- SMC_GBPAUD 実稼働評価
- stat_arb 評価・調整

### Phase3: スケールアップ
- 目標: 月利30万円達成
- ロット拡大・ペア追加
