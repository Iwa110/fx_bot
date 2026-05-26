# FX Bot - Claude Code Context

## プロジェクト概要
FX自動売買システム。VPS(Windows Server 2022)で複数戦略を並行稼働。
月利30万円目標。現在Phase1完了判定フェーズ→Phase2移行検討中。

## ディレクトリ構成
```
C:\Users\Administrator\fx_bot\
├── vps\          # 稼働中ボット
│   ├── bb_monitor.py      # v26 magic=20250001
│   ├── trail_monitor.py   # v15
│   ├── smc_gbpaud.py      # v4 magic=20260002
│   ├── stat_arb.py        # magic=20260001
│   ├── sma_squeeze.py     # v4.3 magic=20260010 (ATR-adaptive trailing + T_max)
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
- Stage2 distance: EURUSD=0.1、その他=0.3
- USDJPY許可時間帯: [21,22,5] UTC
- RR問題あり(実RR=0.31 vs 設計1.50)、改善実施済み・データ蓄積中

### trail_monitor v15
- STR/MOM_JPYペア別設定分離済み
- SMC_GBPAUD追加(activate=1.0, distance=0.7, Sell専用)

### SMC_GBPAUD v4
- Sell専用、TF=1h/HTF=1d、Session=8-20UTC、MAX_POS=1

### stat_arb
- GBPJPY/USDJPY・EURUSD/GBPUSD、MAX_POS=2ペア

### SMA Squeeze Play v4.3（稼働中、2026-05-26）
- magic=20260010、STRATEGY_TAG='SMA_SQ'
- **有効ペア**: USDJPY/GBPJPY/EURUSD
- **停止ペア**: GBPUSD（enabled=False / BT PF<1.0）、EURJPY（enabled=False / 実稼働WR=0% -9,900円）
- ブローカー: axiory/exness（oanda停止中）
- ロジック: SMA200スロープフィルタ + SMAスクイーズ解放エントリー + 日足フィルター
  - エントリー条件: ADX14>20、divergence_rate≤squeeze_th、SMAスロープ単調 + 日足SMA方向一致
  - 決済: ATR×sl_atr_mult でSL、SL×rr でTP、SMA長期ブレイク強制決済 / slope-exit=3
  - ATR-adaptive trailing: trail_dist = ATR14 × atr_trail_mult（ペア毎設定）
  - **T_max=24h**: USDJPY/GBPJPY/EURUSDで最大保有24時間超過で強制成行決済
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

## Top of mind（2026-05-26 更新）

### 現在の稼働状態（2026-05-26時点）
- **sma_squeeze**: v4.3 / axiory/exness（oanda停止中）
  有効ペア: USDJPY/GBPJPY/EURUSD / 停止: GBPUSD・EURJPY
- **bb_monitor**: v26 / 3ブローカー（Task Scheduler毎分実行）
- **trail_monitor**: v15 / axiory/exness・oanda（独立プロセス）
- **grid_monitor**: v2 / axiory/exness（GBPJPY=20260031, CHFJPY=20260032）
- **news_monitor**: v1 / 未起動（VPS git pull → news_monitor.bat 起動待ち）
- MT5端末起動順: OANDA→(60秒後)Axiory→Exness

### sma_squeeze.py v4.3（2026-05-26完了）
- **EURJPY enabled=False**: 実稼働WR=0%（n=2）、94.6h保有で-9,900円SL到達。
  BT PF=3.673だが実稼働と乖離が大きいため一時停止。
- **T_max=24h追加**（USDJPY/GBPJPY/EURUSD）: 保有24時間超過で強制成行決済
  ログ: `[TMAX] USDJPY LONG hold=26.3h>24h force-close ticket=X`
- **VPS対応**: `git pull origin main` → `sma_squeeze_monitor.bat` 再起動が必要

### Phase1判定進捗（BB 全期間, 2026-05-26時点, magic=20250001）
| ペア   |    PF | 勝率  | n  | 総合  |
|--------|------:|------:|---:|-------|
| USDJPY | 1.355 | 82.3% | 62 | ✅ PF合格（蓄積継続） |
| GBPJPY | 0.840 | 81.0% | 21 | ❌（n不足・PF未達） |
| EURUSD | — | — | — | enabled=False（BT PF<0.7/v20停止） |
| GBPUSD | — | — | — | enabled=False（実稼働PF=0.294/v20停止） |

### GRID戦略（2026-05-26 確認）
- GBPJPY（20260031）: 直近7日 +1,791円 WR=100% n=7（好調）
- CHFJPY（20260032）: 直近7日 +9円 WR=100% n=4
- magic 20260030-32 / 20260040 を docs/strategies.md magic番号テーブルに追記済み（v4.3と同コミット）

### 翌日確認事項
- **【要対応】VPS**: `git pull origin main` → `sma_squeeze_monitor.bat` 再起動（v4.3反映）
- **【要対応】VPS**: `news_monitor.bat` 起動（axiory/exness）
- sma_squeeze v4.3 動作確認: `[TMAX]` ログが出ないことを確認（正常時は24h以内に決済済み）
- VPS M1データ取得後: `news_event_bt.py` 再実行 → PARAMS更新
- Phase1 USDJPY: n=100超えたら再判定（目安あと3〜4週間）

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
- [x] SMA Squeeze v4.2 trail_start_mult BT検証・最終設定0.0確定（2026-05-21完了）
- [x] 経済指標戦略 news_monitor.py v1 実装・push（2026-05-24完了）
- [x] sma_squeeze v4.3: EURJPY停止 + T_max=24h追加（2026-05-26完了）
- [x] strategy_spec.md / docs/strategies.md 更新（2026-05-26完了）
- [x] CLAUDE.md更新（2026-05-26完了）
- [ ] **VPS**: `git pull origin main` → `sma_squeeze_monitor.bat` 再起動（v4.3反映）
- [ ] **VPS**: `news_monitor.bat` 起動（axiory/exness）
- [ ] VPS M1データ → `news_event_bt.py` → PARAMS更新
- [ ] GBPJPY: 1h BT data取得後にatr_trail_mult再検証
- [ ] Phase1 USDJPY: n=100超えたら再判定（目安あと3〜4週間）

## 作業スタイル
- 作業時間: 夜まとめて1〜2時間
- Chat: タスク設計・判断のみ（10〜15分）
- Code: 実装・実行・push（残り全て）
- Codeセッション開始前に必ずタスクリストを用意する

## 夜の終了チェックリスト（2026-05-26）
- [x] 変更ファイルをcommit/push済み
- [x] CLAUDE.mdのTop of mindを更新済み
- [x] 翌日Chatで確認すべき事項をメモ済み
- [x] sma_squeeze v4.3: EURJPY停止・T_max追加・push済み
- [ ] VPS: git pull origin main → sma_squeeze_monitor.bat 再起動（翌日手動対応）
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
