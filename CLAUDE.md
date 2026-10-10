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

## 既知の手動取引（日次ルーティン誤検知除外用）
自動売買と無関係と確認済みの手動取引。日次分析で異常として再指摘しない（これ以外の新規magic=0/想定外取引は通常通り指摘する）。
- 2026-09-07 11:07:45 LIVE(oanda_live) magic=0 USDJPY buy 0.20lot -51,620円（ストップアウト、手動取引の失敗）
- 2026-10-08 02:14:14 LIVE(oanda_live) magic=0 USDJPY buy 0.02lot -42円（意図した手動取引）

## Top of mind（2026-10-10 圧縮版 / 圧縮前の全文は commit a8fd929 の CLAUDE.md 参照）

### 現役1: Grid 確定4本（相関クロス平均回帰, `vps/grid_monitor.py` v8）
- 構成: AUDCAD 20260034(R-SMA1200+combo) / CADCHF 20260038(R-SMA1200, DD圧縮案cull0.6) / AUDNZD 20260036(R-SMA1200+combo) / EURGBP 20260035(combo+short_lot0.5+mom120=4+tp0.8)。combo=mom2.0+cull0.5+taper0.7。
- エッジ根拠: 同一ドライバ共有クロス→独立トレンド無し→構造的レンジ。Dukascopy 11年 IS2015-21/OOS2022-26/年次WFOで頑健。確定構成は Pareto フロンティア(動的化/露出cap/legstop/地合い予測は全てinert or net-worse)。
- 資本: 暦月basis req_cap_99/lot = AUDCAD691k / CADCHF2.27M / AUDNZD1.25M / EURGBP2.28M。等req_cap分散バスケットで月利30万=必要資本2.80M(相対lot 1.0/0.305/0.552/0.303)。
- LIVE: 国内OANDA 2026-06-24 go-live(25倍・証拠金律速)。S0 lot AUDCAD0.15/CADCHF0.05/AUDNZD0.08/EURGBP0.05、維持率ガード150%。FS/DD閾値は LIVE lot でスケール。
- 昇格(ペア別): 3ヶ月∧TP≥30∧FS最低1回発火∧実現PF>1.2 → lot×1.25(S1)。撤退: FS>設定×1.5 / 発火後PF<1.0 / 現DD>req_cap_99。
- 2026-10-10 LIVE進捗: AUDCAD 88d/TP58/FS0/PF1.09・CADCHF 93d/TP26/PF0.88・AUDNZD 44d/TP9・EURGBP 107d/TP16/PF0.72(B48損)。全ペアFS未発火=昇格判定まだ不可。
- 大半の日はCI(D1,14)>65未達でアイドル=設計通り。損失/大DDの時期は予測不能。

### 現役2: MR_AC（AUDCAD 4h 平均回帰・3段不等分割, `vps/mr_monitor.py`, magic=20260050, demo）
- Z(SMA40/SD40) 2.0/2.5/3.0 で 0.2/0.3/0.5lot、MA一括決済、z_stop4.5、max_hold48本、ATR pct≥0.7でlot×0.5。BT full PF1.61 / OOS2.57(順風) / IS1.17。
- demo実績(2026-10-10, クラスタ単位): n26 PF2.88 maxDD4.6万。昇格: 3ヶ月∧30約定∧SL発火∧PF>1.2。キルスイッチ: 12moPF<1.0 or maxDD>MC95≒43万円/lot。LIVE_LOT_SCALE=0(live拒否)。
- 候補改善: deep-run TP `tp_zs≈[0.3,0,-0.3]`(PF1.61→1.75)はforward後に検討。scale-outはAUDCADに有害。

### 現役3: live監視（日次ルーティン）
- `optimizer/daily_gate.py` が daily_step1 / grid_gate_review / mr_forward_review を実行し flags+summary をJSON出力。flags空なら通知のみで終了(`prompts/daily_analysis.md`)。
- flags: FSスリッページ>設定×1.3 / LIVE想定外magic・symbol / stop-out・維持率<150%(`optimizer/account_snapshot.csv` 任意) / MR_ACキル / Grid LIVE昇格達成 / スクリプトエラー。
- 既知手動取引の除外は `daily_gate.py` KNOWN_MANUAL と上記「既知の手動取引」を同期させること。

### 未決事項
- MR_CC(20260051, CADCHF MR, demo): 2026-10-10 daily_gate監視へ追加(`mr_forward_review.py --pair CADCHF`, キルのみflag)。クラスタn30 PF2.29で単独ゲートは形式上クリア。MC95算出完了(2026-10-10, `mr_tiered_transfer_bt.py --mc`流用): MC95=435 lot-pip≈805,317円(lot_scale=1.0)。
- MR_AC+MR_CC バスケット昇格設計(2026-10-10完了, `optimizer/mr_joint_basket_mc.py`, strategy_spec.md§16): BTトレード列を時系列マージしshuffle/ブロックブートストラップ両手法で合算MC95算出。月次相関+0.073〜0.13(弱)で**バスケットMC95は単純合算より35-38%小さい**→`basket_req_cap_99=949,907円`(保守側採用, lot_scale=1.0)を基準に両ペア**同一lot_scale**で昇格・個別キルスイッチに上乗せするバスケット・バックストップ(合算ローリング12moPF<1.0 or 合算maxDD>basket_req_cap_99)を設計。**実装完了(2026-10-10)**: `mr_forward_review.py --basket`(両magic生レッグ連結→クラスタ集約→kill_check()再利用)を追加し、`daily_gate.py`が個別判定に加えてBASKETも自動チェック(発火時は`mr_kill`フラグにBASKETラベルで記録)。回帰テスト`test_mr_forward_review_killswitch.py`[6]で24件PASS。**副発見のvol_throttle不整合も修正完了(2026-10-10)**: `audcad_stress_test.py`の`_Args`/`dynamic_lot_mr_bt.base_cfg()`に`vol_throttle_th`/`vol_throttle_mult`(=0.70/0.5, vps/mr_monitor.py PAIR_CONFIG['AUDCAD']と同値)を配線(他の既存呼び出し元は`getattr`既定値1.01/1.0でOFF維持=非破壊)。再計算後MC95=411 lot-pip(≈410.7, `mr_joint_basket_mc.py`と別エンジン経路でクロスチェック一致)。`mr_forward_review.PAIRS['AUDCAD']['mc95_lotpip']`を398.0→410.7に更新(AUDCADキル閾値 429,840円→443,556円)。回帰ガード`test_dynamic_lot_mr_base_cfg.py`で7件PASS。
- `vps/account_snapshot.py`(1h毎, FX_Account_Snapshot)→ sync_historyで同梱push → daily_gate維持率flag(窓内最小値)。VPSで `register_account_snapshot.bat` 実行要。
- Grid生成AIループ(`optimizer/loop/`, ledger/6ゲート) Phase2(2026-10-10夜間自律実行, ブランチ`grid-loop-phase2-ptp-frac-ptp-mult`, PR未マージ=人間レビュー待ち): gain側2件目のファミリー`grid_partial_takeprofit`(ptp_frac+ptp_mult, 部分利確)をAUDCADで2段階explore(Stage A: ptp_frac代表値0.35 / Stage B: ptp_mult代表値0.5, いずれもplateau variation<4%)→確定した`{ptp_frac:0.35, ptp_mult:0.5}`をCADCHF/EURGBP/AUDNZDへ再チューニング無しで転用してconfirm。**CADCHF(H0041)のみ全6ゲートPASS**(IS1.3978/OOS1.5032/decay-0.075/wfoMin1.3626=全5fold>1.2/req_cap_99 3.70M, baseline比-5.2%)→`review_queue/H0041_CADCHF_grid_partial_takeprofit.md`としてPR提出、**未デモ投入(vps/未変更、人間のapprove待ち)**。AUDCAD(H0036)はgate1-5全PASSだがgate6のみ不合格(2026部分年fold n=67 PF0.83、Phase1の非対称TPと同型の脆弱性・decayは逆に良好-0.10)。EURGBP(H0046)はgate1(OOS1.14<1.2)とgate6(2022通年fold PF0.63)が不合格、かつ転用値(0.5)はEURGBP自身のplateau最良点(0.4)から外れておりvariation16%(自身選択時3.8%)=転用コストが明確に見える例。AUDNZD(H0051)はgate1(IS0.96<1.0=既知の限界ペア)とgate6(2026薄n=42 fold PF0.43)が不合格。今月(2026-10)のOOSバジェット消費=**1/4**(family_tag単位でカウントするため4ペア分confirmしても1件のみ消費、残3)。次アクション=人間がPRをレビューし、CADCHF demo config(`ptp_frac=0.35, ptp_mult=0.5`を既存CADCHF baseline atr1.5/ci65/lv5/fs-943kに追加)の投入可否を判断。
- BB USDJPY / SMA_SQ / stat_arb / carry Grid(NZDJPY/USDJPY): demo・micro限定、スケール禁止(10年BTで頑健エッジ無し or carry-crashテール)。

### 墓場（Close確定・再提案しない / 詳細は commit a8fd929）
- COT(20260020): IS/OOS未検証・axiory n5 PF0.41 net-2.9万 → 2026-10-10 停止完了。`vps/cot_monitor.py` v3に`--close-only`追加、`vps/stop_cot.bat`でVPS上のデーモン停止済み(Task Scheduler登録は元々無し)・MT5で該当ポジション無しを確認済み。再稼働するには明示的な再承認が必要(`cot_monitor.bat`/`restart_cot.bat`に警告ヘッダあり)。
- BB逆張り10年BT(全ペアOOS PF<1.0) / SMA Squeeze 10年BT(IS赤字) / 順張り4戦略1h・日足週足トレンド / Grid救済不可(GBPJPY/CHFJPY/EURUSD/EURCHF)
- Grid改善系: 動的パラメータ化 / 地合い持続予測E1 / ドライバスプレッドゲート / 合算露出cap / legstop・cull_drain / イベントブラックアウト / バスケットTP・トレール / クールダウン / セッションゲート / ラダー深さ別非対称TP(loop Phase1, gate6全滅) / D2確認エントリー / B2 scale-out(AUDCAD)
- 補完・メタ層: 不感症trend補完・bleedヘッジ / 非発火窓ドリフト補完 / Trend補完(案B/D/A) / 配分層・レジーム層 / 構造健全性H(脚相関)自動停止
- 新エッジ探索: 三角stat_arb / pre_event / session_fakeout / pairs共和分 / 横断キャリー / carry-crashヘッジ / 円安構造ドライバ / コモディティ→FXリードラグ / crypto(ETH/BTC MR・ベーシス・トレンド=税で敗北)

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
