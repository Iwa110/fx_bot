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

## 直近タスク
- Phase1完了判定(PF/勝率/DD統合評価)
- USDCAD再評価(BT結果待ち)

## 作業スタイル
- 作業時間: 夜まとめて1〜2時間
- Chat: タスク設計・判断のみ（10〜15分）
- Code: 実装・実行・push（残り全て）
- Codeセッション開始前に必ずタスクリストを用意する

## 夜の終了チェックリスト
- [ ] 変更ファイルをcommit/push済み
- [ ] CLAUDE.mdのTop of mindを更新済み
- [ ] 翌日Chatで確認すべき事項をメモ済み
