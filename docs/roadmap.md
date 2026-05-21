# FX Bot ロードマップ

最終更新: 2026-05-21

---

## Phase 1（完了判定フェーズ）

**目標**: 全ペアで PF>1.2 / 勝率>50% / DD<15% をクリア

### 完了済み
- [x] BB戦略 stage2_distance ペア別最適化（trail_monitor v11→v12）
- [x] htf4h_only フィルター導入（GBPJPY / USDJPY）
- [x] MOM戦略 ペア別設定分離（trail_monitor v10）、3ペア追加（ENZ/ECA/GBU）
- [x] SMC_GBPAUD 追加（trail_monitor v6、Sell専用）
- [x] TOD戦略 新規導入（tod_monitor v2、BT PF=1.232/1.201）
- [x] データ更新スクリプト整備（update_data.py / FX_Data_Update タスク）
- [x] daily_report.py 毎日07:00 自動レポート
- [x] Git未管理ファイル補完（run_daily_report.bat / run_tod_monitor.bat）
- [x] logs/ ディレクトリ Git管理下追加
- [x] vps/archive/ 整理（旧バージョン・fixスクリプト約62ファイル退避）
- [x] 動的ロットサイジング実装（dynamic_lot.py / phase1_judgment.py / daily_report.py統合）
- [x] VPS Task Schedulerウィンドウ非表示化・trail_monitor多重起動修正
- [x] OANDA MT5接続問題解消・全ブローカー稼働化（MT5起動順制御・path_only=True）
- [x] BB戦略 GBPJPY/USDJPY/EURJPY フィルター改善（v21→v22、htf4h_rsi_bw導入）
- [x] SMA Squeeze Play 導入（v4.1: ATR-adaptive trailing + 日足フィルター + heartbeat）
- [x] 全batファイル kill-before-restart 追加（重複プロセス防止）

### 進行中
- [ ] 実稼働データ蓄積（Phase1完了判定用・サンプル100件超目標）

### Phase1完了判定結果（2026-04-24〜2026-05-03）

| ペア | PF | 勝率 | n | PF判定 | 総合 |
|----|----|-----|---|-------|-----|
| GBPJPY | 1.034 | 75.0% | 8 | NG | **不合格** |
| USDJPY | 0.692 | 64.7% | 17 | NG | **不合格** |
| EURUSD | 0.748 | 70.7% | 41 | NG | **不合格** |
| GBPUSD | 0.397 | 67.6% | 37 | NG | **不合格** |

全ペア不合格（主因: PF未達。勝率は全ペア合格）。  
サンプル不足のため2〜3週間後に再判定予定。

### 未着手
- [ ] Phase1完了判定（サンプル100件超え後）
- [ ] USDCAD再評価BT（Phase1完了後）
- [ ] VPS Task Schedulerに週次phase1_judgment（日曜7:05 JST）を追加登録

---

## Phase 2（戦略改善・拡張フェーズ）

### 優先課題
- [ ] BB戦略 RR問題の根本対処（実RR≈0.31 vs 設計1.0）
- [ ] GBPJPY SMA Squeeze: atr_trail_mult=0.5はUSDJPY流用値→1h BT後に再検証
- [ ] SMA Squeeze 全ペアのサンプル蓄積後に実稼働PF評価

### 戦略追加・改善
- [ ] 200MA Pullback 本格稼働判断（現在 DEMO_MODE=True / USDJPY）
- [ ] SMC_GBPAUD 実稼働評価（パフォーマンス確認）
- [ ] stat_arb 評価・trail設定調整
- [ ] MOM_ENZ 評価（BT PF=1.150でPF<1.2のため要注意）
- [ ] MOM_ECA 評価（BT n=11と極端に少ないため要注意）
- [ ] SMA Squeeze GBPJPY 1h BT実施・atr_trail_mult最適化

---

## Phase 3（スケールアップフェーズ）

- [ ] 月利30万円達成
- [ ] ロット拡大
- [ ] ペア追加
- [ ] 200MA Pullback 正式稼働

---

## タスクスケジューラ構成（2026-05-21時点）

| タスク名 | bat | スクリプト | スケジュール |
|---------|-----|----------|------------|
| FX_BB_Monitor_All | `bb_monitor_all.bat` | `vps/bb_monitor.py` | 毎分（axiory/exness/oanda） |
| FX Trail Monitor | `trail_monitor_all.bat` | `vps/trail_monitor.py` | 常駐デーモン（axiory/exness/oanda） |
| FX Daily Trade | `daily_trade_all.bat` | `vps/daily_trade.py` | 週次 07:00（axiory/exness/oanda） |
| FX_DailyReport | `daily_report_all.bat` | `vps/daily_report.py` | 毎日 07:00（axiory/exness/oanda） |
| FX_Data_Update | python直接 | `update_data.py` | 毎日 00:00 |
| FX SMA Squeeze | `sma_squeeze_monitor.bat` | `vps/sma_squeeze.py` | 常駐デーモン 60秒ループ（axiory/exness） |
| FX_Phase1_Judgment（予定） | — | `phase1_judgment.py` | 週次 日曜 07:05 JST（**未登録**） |

### MT5端末起動順（OANDA IPC競合対策）
1. **OANDA** → ログオン時即時起動（`FX_MT5_OANDA_Startup`）
2. **Axiory / Exness** → 60秒後に起動（`FX_MT5_Delayed_Startup` / `mt5_delayed_startup.bat`）

---

## 現在の稼働状態（2026-05-21時点）

| 戦略 | ブローカー | ステータス |
|------|---------|---------|
| BB戦略 v22 | axiory / exness / oanda | ✅ 稼働中（GBPJPY/USDJPY/EURJPY） |
| trail_monitor v13 | axiory / exness / oanda | ✅ 稼働中 |
| SMA Squeeze Play v4.1 | axiory / exness | ✅ 稼働中（oanda停止中） |
| daily_report | axiory / exness / oanda | ✅ 稼働中 |
| stat_arb | — | 稼働中 |
| SMC_GBPAUD | trail_monitor経由 | 稼働中 |
| 200MA Pullback | — | テスト中（DEMO_MODE） |
