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
│   └── stat_arb.py        # magic=20260001
├── optimizer\    # バックテスト・最適化
│   ├── loop_runner.py
│   ├── backtest.py
│   ├── evaluate.py
│   └── phase2_ai_analysis.py
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

## GitHub運用
- Repo: https://github.com/Iwa110/fx_bot (Public)
- VPS更新フロー: commit/push → VPS側でgit pull
- Raw URL: https://raw.githubusercontent.com/Iwa110/fx_bot/main/

## コーディング規約
- ASCIIクォートのみ(' と ")、スマートクォート禁止
- Pythonファイルのmagic番号体系を維持すること

## Top of mind（2026-05-08更新）
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
- 旧パラメータのデッドコード(sl_pct, rr)を削除済み

### 翌日Chat確認事項
- GBPUSDのPF=0.397は特に低い。Stage2 distanceやTP設定を再確認すべきか？
- EURUSDは41件でPF=0.748。RR改善（Stage2 distance=0.1）が効いていない可能性
- サンプル数100件超えたら再判定（目安: あと2〜3週間稼働後）
- CORR実稼働後のPF/WR推移を確認（BT: PF=1.924, WR=52.9%）

## 直近タスク
- [x] Phase1完了判定実行（2026-05-03: 全ペア不合格・データ蓄積継続）
- [x] CORR戦略 BT最適化＋Zスコア決済/hold_period実装（2026-05-08完了）
- [x] 動的ロットサイジング実装: dynamic_lot.py新規 / phase1_judgment.py新判定基準 / daily_report.py統合（2026-05-08完了）
- [ ] VPS: Task Schedulerに週次phase1_judgment（日曜7:05 JST）を追加登録
- [ ] USDCAD再評価(BT結果待ち)
- [ ] RR問題の深掘り（GBPUSD/EURUSD優先）

## 作業スタイル
- 作業時間: 夜まとめて1〜2時間
- Chat: タスク設計・判断のみ（10〜15分）
- Code: 実装・実行・push（残り全て）
- Codeセッション開始前に必ずタスクリストを用意する

## 夜の終了チェックリスト
- [ ] 変更ファイルをcommit/push済み
- [ ] CLAUDE.mdのTop of mindを更新済み
- [ ] 翌日Chatで確認すべき事項をメモ済み

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
