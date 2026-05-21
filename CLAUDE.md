# FX Bot - Claude Code Context

## プロジェクト概要
FX自動売買システム。VPS(Windows Server 2022)で複数戦略を並行稼働。
月利30万円目標。現在Phase1完了判定フェーズ→Phase2移行検討中。

## ディレクトリ構成
```
C:\Users\Administrator\fx_bot\
├── vps\          # 稼働中ボット
│   ├── bb_monitor.py      # v17 magic=20250001
│   ├── trail_monitor.py   # v10
│   ├── smc_gbpaud.py      # v4 magic=20260002
│   ├── stat_arb.py        # magic=20260001
│   └── sma_squeeze.py     # v4 magic=20260010 (ATR-adaptive trailing)
├── optimizer\    # バックテスト・最適化
│   ├── loop_runner.py
│   ├── backtest.py
│   ├── evaluate.py
│   ├── phase2_ai_analysis.py
│   ├── sma_squeeze_bt.py             # エントリーパラメータ最適化BT
│   ├── sma_squeeze_exit_bt.py        # 決済パラメータ最適化BT ★新規
│   └── sma_squeeze_daily_filter_bt.py
└── data\         # 14ペア 1h/5m足
```

## 戦略別現状

### BB戦略
- PAIRS: GBPJPY/USDJPY/EURUSD/GBPUSD (USDCADは停止中)
- Stage2 distance: EURUSD=0.1、その他=0.3
- USDJPY許可時間帯: [21,22,5] UTC
- RR問題あり(実RR=0.31 vs 設計1.50)、改善実施済み・データ蓄積中

### trail_monitor v10
- STR/MOM_JPYペア別設定分離済み
- SMC_GBPAUD追加(activate=1.0, distance=0.7, Sell専用)

### SMC_GBPAUD v4
- Sell専用、TF=1h/HTF=1d、Session=8-20UTC、MAX_POS=1

### stat_arb
- GBPJPY/USDJPY・EURUSD/GBPUSD、MAX_POS=2ペア

### SMA Squeeze Play v4（稼働中）
- magic=20260010、STRATEGY_TAG='SMA_SQ'
- PAIRS: USDJPY/GBPJPY/EURUSD/EURJPY（GBPUSD enabled=False）
- ロジック: SMA200スロープフィルタ + SMAスクイーズ解放エントリー + 日足フィルター
  - エントリー条件: ADX14>20、divergence_rate≤squeeze_th、SMAスロープ単調 + 日足SMA方向一致
  - 決済: ATR×sl_atr_mult でSL、SL×rr でTP、SMA長期ブレイク強制決済 / slope-exit=3
  - ATR-adaptive trailing: trail_dist = ATR14 × atr_trail_mult（ペア毎設定）
  - クールダウン: 180分/ペア、MAX_TOTAL_POS=3、MAX_JPY_LOT=0.4

## GitHub運用
- Repo: https://github.com/Iwa110/fx_bot (Public)
- VPS更新フロー: commit/push → VPS側でgit pull
- Raw URL: https://raw.githubusercontent.com/Iwa110/fx_bot/main/

## コーディング規約
- ASCIIクォートのみ(' と ")、スマートクォート禁止
- Pythonファイルのmagic番号体系を維持すること

## Top of mind（2026-05-21 夜更新）
### OANDA MT5接続問題・全ブローカー稼働化（2026-05-11完了）
- **問題**: Axiory/Exnessに取引がなく、OANDAはterminal.trade_allowed=False
- **根本原因1（OANDA IPC失敗）**: OANDAのMT5ログで `IPC failed to initialize IPC` / `IPC dispatcher not started` + ヒストリーファイルのERROR_SHARING_VIOLATION[32]を確認。Axiory/ExnessがIPCを先に確保するため
  - **解決**: FX_MT5_OANDA_Startup（ONLOGON即時）+ FX_MT5_Delayed_Startup（+60秒後にAxiory/Exness起動）で起動順制御
- **根本原因2（複数端末でのattach誤接続）**: `attach=True`（引数なしmt5.initialize）が複数端末起動時に最後起動のExnessに接続していた
  - **解決**: oandaを`path_only=True`に変更。`mt5.initialize(path=...)`でOANDA端末を特定、credentials渡しなし（credentials渡しがtrade_allowed=Falseの原因だったため）
- **trail_watcher再起動ループ修正**: oanda_demo→oandaへの切替後もport=17004の外部プロセスがあるとspawnが毎回「Already running」でcode=0終了→無限再起動になる問題を修正。portバインド確認でskip処理を追加
- **変更ファイル（mainにマージ済み）**:
  - vps/broker_config.py（oanda: attach→path_only、path追加）
  - vps/broker_utils.py（path_onlyモード・attach後ログイン検証追加）
  - vps/register_brokers.bat（OANDA先行起動タスク + Delayed起動タスク追加）
  - vps/mt5_delayed_startup.bat（新規: ping 60秒waitでAxiory/Exness遅延起動）
  - vps/trail_watcher.py（oanda_demo→oanda、portバインド確認でloop停止）
  - vps/bb_monitor_all.bat / daily_trade_all.bat / daily_report_all.bat / trail_monitor_all.bat（oanda_demo→oanda）
- **正常動作確認**: test_trade_execution.py --all → axiory[OK] / exness[OK] / oanda[OK]

### 現在の稼働状態（2026-05-11時点）
- trail_monitor: axiory/exness（trail_watcher管理）、oanda（独立プロセス・クラッシュ時watcher再起動）
- bb_monitor: 3ブローカー（Task Scheduler毎分実行）
- MT5端末起動順: OANDA→(60秒後)Axiory→Exness

### Phase1完了判定結果（history.csv: 2026-04-24〜2026-05-03, magic=20250001）
| ペア   |    PF | 勝率  | DD(絶対) | n  | PF判定 | WR判定 | 総合  |
|--------|------:|------:|---------:|---:|--------|--------|-------|
| GBPJPY | 1.034 | 75.0% |  3,320円 |  8 | NG     | OK     | 不合格 |
| USDJPY | 0.692 | 64.7% |  3,900円 | 17 | NG     | OK     | 不合格 |
| EURUSD | 0.748 | 70.7% | 10,030円 | 41 | NG     | OK     | 不合格 |
| GBPUSD | 0.397 | 67.6% | 23,828円 | 37 | NG     | OK     | 不合格 |
- 全ペア不合格（PF未達。勝率は全ペア合格）。サンプル100件超えたら再判定。

### CORR戦略パラメータ最適化（2026-05-08実施）
- BT期間: AUDNZD D1 2022-01-01〜2024-12-31、240+12組グリッドサーチ
- **最終パラメータ**: corr_window=60, z_entry=2.0, z_exit=0.0, hold_period=5
- **MULTIPLIERS**: tp=1.5, sl=2.0 → PF=1.924 / WR=52.9% / n=34
- z_exit=0.0（Z回帰決済は無効、hold_period=5日で管理）

### SMA Squeeze v4 ATR-adaptive trailing stop（2026-05-21完了）
- **課題**: 単純なSLトレーリングでは早期利確・ノイズ損切りが発生
- **解決**: 移動平均・チャート傾き・ボラティリティを加味した複数決済手法をBTで比較
- **BT**: sma_squeeze_exit_bt.py 275runs（6手法: baseline/fixed_trail/atr_trail/slope_exit/div_tighten/combined）
- **BT結果**（vs baseline固定SL/TP）:
  | ペア | 最優手法 | PF(baseline) | PF(best) | 改善幅 |
  |------|---------|------------|---------|--------|
  | USDJPY | atr_trail mult=0.5 | 1.815 | 4.441 | +2.63 |
  | EURUSD | atr_trail mult=0.5 | 2.670 | 7.447 | +4.78 |
  | GBPUSD | atr_trail mult=1.5 | 0.713 | 1.418 | +0.71 |
  | EURJPY | baseline（trailなし） | 3.673 | 3.673 | ±0 |
  | GBPJPY | atr_trail mult=0.5 | - | - | (1h BT未実施) |
- **実装 (sma_squeeze.py v4)**:
  - `manage_atr_trail(broker)`: trail_dist = ATR14 × atr_trail_mult（ペア毎設定）
  - SLは有利方向にしかラチェットしない。ボラ高時は自動的に広がる
  - EURJPY: atr_trail_mult=0.0（無効）、固定TP維持
  - BreakEven(`be_r`)廃止→ATR trailに統一
  - v3機能（daily SMA filter、COOLDOWN=180、slope_exit=3）は維持
  - Log: `[ATR_TRAIL] USDJPY LONG SL 149.50->149.80 locked=+0.30 atr=... ticket=...`
- **変更ファイル**: vps/sma_squeeze.py (v4) / optimizer/sma_squeeze_exit_bt.py (新規)
- **注意**: feature branch `claude/sma-squeeze-strategy-ct71p` → main マージ後にVPS git pull

### 翌日Chat確認事項
- **【要対応】PR作成**: `claude/sma-squeeze-strategy-ct71p` → main のPRを作成してマージ → VPS `git pull` でv4を反映
- VPS再起動後: OANDA→(60s)Axiory/Exness の起動順でIPC確保。trail_watcher.logで3ブローカーのHBを確認
- OANDAのIPC問題が起動順制御で本当に解消されたか、次回VPS再起動後に trail_log_oanda.txtで確認
- **SMA Squeeze v4**: sma_squeeze_log_oanda.txtで `[ATR_TRAIL]` ログが出ているか確認（PRマージ+git pull後）
- GBPJPY: atr_trail_mult=0.5はUSDJPYから流用。1h BT data取得後に再検証推奨
- サンプル数100件超えたら再判定（目安: あと2〜3週間稼働後）
- CORR実稼働後のPF/WR推移を確認（BT: PF=1.924, WR=52.9%）

## 直近タスク
- [x] Phase1完了判定実行（2026-05-03: 全ペア不合格・データ蓄積継続）
- [x] CORR戦略 BT最適化＋Zスコア決済/hold_period実装（2026-05-08完了）
- [x] 動的ロットサイジング実装: dynamic_lot.py新規 / phase1_judgment.py新判定基準 / daily_report.py統合（2026-05-08完了）
- [x] VPS Task Schedulerウィンドウ非表示化・trail_monitor多重起動修正（2026-05-10完了）
- [x] OANDA MT5接続問題解消・全ブローカー稼働化（2026-05-11完了）
- [x] SMA Squeeze v4 ATR-adaptive trailing BT+実装（2026-05-21完了）
- [x] .gitignore更新: optimizer/sma_squeeze_bt_result.csv の大容量自動生成ファイルを除外（2026-05-21完了）
- [ ] **PRマージ**: feature branch `claude/sma-squeeze-strategy-ct71p` → main（VPS git pullのために必要）
- [ ] VPS: git pull (main) + sma_squeeze_monitor.bat再起動（v4反映）
- [ ] VPS: Task Schedulerに週次phase1_judgment（日曜7:05 JST）を追加登録
- [ ] USDCAD再評価(BT結果待ち)
- [ ] GBPJPY: 1h BT data取得後にatr_trail_mult再検証

## 作業スタイル
- 作業時間: 夜まとめて1〜2時間
- Chat: タスク設計・判断のみ（10〜15分）
- Code: 実装・実行・push（残り全て）
- Codeセッション開始前に必ずタスクリストを用意する

## 夜の終了チェックリスト（2026-05-21）
- [x] 変更ファイルをcommit/push済み（branch: claude/sma-squeeze-strategy-ct71p）
- [x] CLAUDE.mdのTop of mindを更新済み
- [x] 翌日Chatで確認すべき事項をメモ済み
- [ ] PRマージ → VPS git pull（翌日手動対応）

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
