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

## Top of mind（2026-05-28 更新）

### 現在の稼働状態（2026-05-28時点）
- **sma_squeeze**: v4.4 / axiory/exness（oanda停止中）
  有効ペア: USDJPY/GBPJPY/EURUSD / 停止: GBPUSD・EURJPY
- **bb_monitor**: v27 / 3ブローカー（Task Scheduler毎分実行）
  **【VPS未反映】git pull → bb_monitor再起動が必要**
- **trail_monitor**: v15 / axiory/exness・oanda（独立プロセス）
- **grid_monitor**: v2 / axiory/exness（GBPJPY=20260031, CHFJPY=20260032）
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

### Phase1判定進捗（BB 全期間, 2026-05-26時点, magic=20250001）
| ペア   |    PF | 勝率  | n  | 総合  |
|--------|------:|------:|---:|-------|
| USDJPY | 1.355 | 82.3% | 62 | ✅ PF合格（蓄積継続）、σ=2.0維持 |
| GBPJPY | 0.840 | 81.0% | 21 | ❌（n不足・PF未達）→v27でσ=2.0に変更 |
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
- [ ] Phase1 USDJPY: n=100超えたら再判定（目安あと3〜4週間）
- [ ] backtest.py BT精度向上: simulate_with_stage2をH1足ATRに切り替え（任意）

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
