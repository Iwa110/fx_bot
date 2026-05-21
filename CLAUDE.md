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
│   └── sma_squeeze.py     # v4.1 magic=20260010 (ATR-adaptive trailing + heartbeat)
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

### SMA Squeeze Play v4.1（稼働中）
- magic=20260010、STRATEGY_TAG='SMA_SQ'
- PAIRS: USDJPY/GBPJPY/EURUSD/EURJPY（GBPUSD enabled=False）
- ブローカー: axiory/exness（oanda停止中）
- ロジック: SMA200スロープフィルタ + SMAスクイーズ解放エントリー + 日足フィルター
  - エントリー条件: ADX14>20、divergence_rate≤squeeze_th、SMAスロープ単調 + 日足SMA方向一致
  - 決済: ATR×sl_atr_mult でSL、SL×rr でTP、SMA長期ブレイク強制決済 / slope-exit=3
  - ATR-adaptive trailing: trail_dist = ATR14 × atr_trail_mult（ペア毎設定）
  - クールダウン: 180分/ペア、MAX_TOTAL_POS=3、MAX_JPY_LOT=0.4
- atr_trail_mult: USDJPY=0.5 / GBPJPY=0.5 / EURUSD=0.5 / GBPUSD=1.5(無効) / EURJPY=0.0
- 監視: heartbeat log 30分毎（`heartbeat alive pos=X/Y`）

## GitHub運用
- Repo: https://github.com/Iwa110/fx_bot (Public)
- VPS更新フロー: commit/push → VPS側でgit pull
- Raw URL: https://raw.githubusercontent.com/Iwa110/fx_bot/main/

## コーディング規約
- ASCIIクォートのみ(' と ")、スマートクォート禁止
- Pythonファイルのmagic番号体系を維持すること

## Top of mind（2026-05-21 深夜更新）

### 現在の稼働状態（2026-05-21時点）
- **sma_squeeze**: axiory/exness（oanda停止中）。v4.1稼働中（git pull済み）
- **trail_monitor**: axiory/exness（trail_watcher管理）、oanda（独立プロセス）
- **bb_monitor**: 3ブローカー（Task Scheduler毎分実行）
- MT5端末起動順: OANDA→(60秒後)Axiory→Exness

### sma_squeeze.py v4.1（2026-05-21完了）
- **v4からの変更**: 30サイクル（約30分）毎にハートビートログ追加（監視用）
  - `heartbeat  alive  pos=X/Y  cycle=N`
- **重要**: v4の本体はエンコーディング破損（`\n`リテラル化）があり、VPSで動作しない状態だった
  - Writeツールで再構築し正常化（792行、syntax OK確認済み）
  - main branch: `dda8e36` / feature branch: `173fb4a`
- **ログ読み方**: エントリーなし・決済なしの間は30分毎のheartbeatのみ出力（正常）
  - エントリー時: `SMA_SQ entry: USDJPY LONG lot=... entry=... sl=... tp=...`
  - ATR trail更新時: `[ATR_TRAIL] USDJPY LONG SL old->new locked=+X`
  - 異常時: `loop error: ...`

### sma_squeeze v4 ATR-adaptive trailing stop（2026-05-21完了）
- **BT結果**（sma_squeeze_exit_bt.py 275runs）:
  | ペア | 最優手法 | PF(baseline) | PF(best) | 改善幅 |
  |------|---------|------------|---------|--------|
  | USDJPY | atr_trail mult=0.5 | 1.815 | 4.441 | +2.63 |
  | EURUSD | atr_trail mult=0.5 | 2.670 | 7.447 | +4.78 |
  | GBPUSD | atr_trail mult=1.5 | 0.713 | 1.418 | +0.71 |
  | EURJPY | baseline（trailなし） | 3.673 | 3.673 | ±0 |
  | GBPJPY | atr_trail mult=0.5 | - | - | (1h BT未実施) |
- trail_dist = ATR14 × atr_trail_mult、SLは有利方向のみラチェット
- EURJPY: atr_trail_mult=0.0（無効）、固定TP維持

### sma_squeeze v4.2 trail_start_mult BT検証（2026-05-21完了）
- **問題**: 「途中でプラスに転じたのに損切りになった」件の根本原因を特定
  - ATR trail はエントリー直後から動作するため、含み益がATR×0.5未満の段階では
    SLがまだBE未満のまま。その時点で反転するとマイナス決済になる（**意図的な動作**）
  - 例: USDJPY ATR=1.0 → 価格+0.30上昇 → trail SL=−0.20 → 反転で−0.20円の損切り
- **BT結果（415runs、全5ペア）**:
  - `trail_start_mult=0.5`（BE保証）はWRを上げるが**PFを大幅に下げる**
  | ペア | start=0.0 PF | start=0.5 PF | ΔPF | WR変化 |
  |------|-------------|-------------|------|--------|
  | USDJPY | **4.441** | 3.500 | −0.94 | 61%→70% |
  | EURUSD | **5.193** | 3.222 | −1.97 | 58%→69% |
  | GBPJPY | **2.883** | 1.955 | −0.93 | 63%→70% |
  | GBPUSD | **2.445** | 2.065 | −0.38 | 56%→64% |
  - trail_start_mult=0.0（元の動作）が全ペアで最適
  - **「プラス→マイナス」はBT上では損失より大きな利益でカバーされている**
- **最終設定**: trail_start_mult=0.0 に戻す（v4と同一動作）
- **その他対応**:
  - BT Critical bug fix: `adv_v` → `adx_v`（NameErrorで全BT無効化されていた）
  - trail_start_mult infra（コード・ログ）は残置（将来のper-pair調整用）
  - debug log追加: `[ATR_TRAIL] no-update new_sl=X` / `trail-wait` でtrail状態が完全可視化
- **ベストBT結果（keep_tp=N）**: USDJPY PF=4.767 / EURUSD PF=7.447 / GBPJPY PF=3.471
  - keep_tp=Nの方がYより高PF（TP上限なしでtrailに任せる）→将来検討余地あり

### .bat kill-before-restart 対応（2026-05-21完了）
- 全batファイルに`wmic process where ... delete`を追加（kill→wait→start）
- 対象: sma_squeeze_monitor.bat / trail_monitor_all.bat / bb_monitor_all.bat / daily_trade_all.bat / daily_report_all.bat

### OANDA MT5接続問題・全ブローカー稼働化（2026-05-11完了）
- MT5端末起動順制御（OANDA先行+60秒遅延）でIPC競合解消
- vps/broker_config.py（oanda: path_only=True）で誤接続防止
- 正常動作確認: axiory[OK] / exness[OK] / oanda[OK]

### Phase1完了判定結果（history.csv: 2026-04-24〜2026-05-03, magic=20250001）
| ペア   |    PF | 勝率  | DD(絶対) | n  | 総合  |
|--------|------:|------:|---------:|---:|-------|
| GBPJPY | 1.034 | 75.0% |  3,320円 |  8 | 不合格 |
| USDJPY | 0.692 | 64.7% |  3,900円 | 17 | 不合格 |
| EURUSD | 0.748 | 70.7% | 10,030円 | 41 | 不合格 |
| GBPUSD | 0.397 | 67.6% | 23,828円 | 37 | 不合格 |
- 全ペア不合格（PF未達）。サンプル100件超えたら再判定。

### CORR戦略パラメータ最適化（2026-05-08実施）
- 最終パラメータ: corr_window=60, z_entry=2.0, z_exit=0.0, hold_period=5
- PF=1.924 / WR=52.9% / n=34

### 翌日確認事項
- **【要対応】VPS**: `git pull origin main` → `sma_squeeze_monitor.bat` 再起動（v4.2反映）
  - trail_start_mult=0.0（元のv4動作）に戻り済み。BT adv_v typo修正も含まれる
- v4.2動作確認: `sma_squeeze_log_axiory.txt` で `[ATR_TRAIL] no-update` / SL更新ログ確認
- **「プラス→マイナス」について**: BT上は意図的な動作。trail_start_mult=0.0が最適PF
  - もしWR優先したいなら trail_start_mult=0.5 に戻すことも可（WR+8%、PF−21%の交換）
- GBPJPY: atr_trail_mult=0.5はUSDJPYから流用。1h BT data取得後に再検証推奨
- サンプル数100件超えたら再判定（目安: あと2〜3週間稼働後）

## 直近タスク
- [x] Phase1完了判定実行（2026-05-03: 全ペア不合格・データ蓄積継続）
- [x] CORR戦略 BT最適化＋Zスコア決済/hold_period実装（2026-05-08完了）
- [x] 動的ロットサイジング実装: dynamic_lot.py新規 / phase1_judgment.py新判定基準 / daily_report.py統合（2026-05-08完了）
- [x] VPS Task Schedulerウィンドウ非表示化・trail_monitor多重起動修正（2026-05-10完了）
- [x] OANDA MT5接続問題解消・全ブローカー稼働化（2026-05-11完了）
- [x] SMA Squeeze v4 ATR-adaptive trailing BT+実装（2026-05-21完了）
- [x] .gitignore更新: optimizer/sma_squeeze_bt_result.csv の大容量自動生成ファイルを除外（2026-05-21完了）
- [x] 全batファイル kill-before-restart 追加（2026-05-21完了）
- [x] sma_squeeze.py エンコーディング破損修正 + heartbeat追加 v4.1（2026-05-21完了）
- [x] main branch push完了（`dda8e36`）
- [x] SMA Squeeze v4.2 trail_start_mult BT検証・最終設定0.0確定（2026-05-21完了）
- [ ] **VPS**: `git pull origin main` → `sma_squeeze_monitor.bat` 再起動（v4.2: BT fix + debug log）
- [ ] VPS: Task Schedulerに週次phase1_judgment（日曜7:05 JST）を追加登録
- [ ] USDCAD再評価(BT結果待ち)
- [ ] GBPJPY: 1h BT data取得後にatr_trail_mult再検証

## 作業スタイル
- 作業時間: 夜まとめて1〜2時間
- Chat: タスク設計・判断のみ（10〜15分）
- Code: 実装・実行・push（残り全て）
- Codeセッション開始前に必ずタスクリストを用意する

## 夜の終了チェックリスト（2026-05-21）
- [x] 変更ファイルをcommit/push済み（main: dda8e36 / feature: 173fb4a）
- [x] CLAUDE.mdのTop of mindを更新済み
- [x] 翌日Chatで確認すべき事項をメモ済み
- [x] sma_squeeze.py エンコーディング破損修正・heartbeat追加・push済み
- [ ] VPS: git pull origin main → sma_squeeze_monitor.bat 再起動（翌日手動対応）

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
