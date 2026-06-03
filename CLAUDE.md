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

## Top of mind（2026-06-03 更新）

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

### Phase1判定進捗（BB 全期間, 2026-06-02時点, magic=20250001）
| ペア   |    PF | 勝率  | n  | 総合  |
|--------|------:|------:|---:|-------|
| USDJPY | 1.42  | 82.8% | 64 | ⚠️ PF合格・n不足のみ（n=100まであと36件≈3〜4週）、σ=2.0維持 |
| GBPJPY | 0.95  | 84.0% | 25 | ❌（PF未達/均衡圏）σ=2.0後も<1.0、PF>1.2転換 or 停止を7月判断 |
| EURUSD | — | — | — | enabled=False（BT PF<0.7/v20停止） |
| GBPUSD | — | — | — | enabled=False（実稼働PF=0.294/v20停止） |

### USDJPY Phase1確定への分析結果（2026-05-28）
- BT全データ PF=1.159（5m ATR基準）→ H1 ATR補正後は実稼働 PF=1.355 が正
- bb_sigma=2.0の妥当性: σ=1.5(PF=0.835) vs σ=2.0(PF=1.161) → σ=2.0が優位
- T_max=8h+exp Decay: OOS PF=1.137→1.211 (+6.5%)、v26実装根拠確認済み
- → n=100到達まで蓄積継続（現在n=62、目安あと3〜4週間）

### 翌日確認事項
- **【最優先】VPS**: `git pull origin main` → bb_monitor.bat 再起動（v27反映）
- **【要対応】VPS**: `news_monitor.bat` 起動（axiory/exness）
- bb_monitor v27 動作確認: GBPJPY の `シグナル確定` ログで `BB_σ=2.0` を確認
- Phase1 USDJPY: n=100超えたら再判定（目安あと3〜4週間）
- backtest.py BT精度向上（任意）: simulate_with_stage2をH1足ATRに切り替え

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
