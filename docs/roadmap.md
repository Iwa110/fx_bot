# FX Bot ロードマップ

最終更新: 2026-05-06

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

### 進行中
- [ ] 実稼働データ蓄積（Phase1完了判定用・サンプル100件超目標）

### Phase1完了判定結果（2026-04-24〜2026-05-03）

| ペア | PF | 勝率 | n | PF判定 | 総合 |
|----|----|-----|---|-------|-----|
| GBPJPY | 1.034 | 75.0% | 8 | NG | **不合格** |
| USDJPY | 0.692 | 64.7% | 17 | NG | **不合格** |
| EURUSD | 0.748 | 70.7% | 41 | NG | **不合格** |
| GBPUSD | 0.397 | 67.6% | 37 | NG | **不合格** |

全ペア不合格（主因: RR問題。勝率は全ペア合格）。  
サンプル不足のため2〜3週間後に再判定予定。

### 未着手
- [ ] Phase1完了判定（サンプル100件超え後）
- [ ] USDCAD再評価BT（Phase1完了後）

---

## Phase 2（戦略改善・拡張フェーズ）

### 優先課題
- [ ] GBPUSDのPF=0.397 深掘り（stage2_distance / TP設定の再確認）
- [ ] EURUSDのPF=0.748 改善検討（stage2_distance=0.1が効いているか検証）
- [ ] BB戦略 RR問題の根本対処（実RR≈0.31 vs 設計1.0）

### 戦略追加・改善
- [ ] 200MA Pullback 本格稼働判断（現在 DEMO_MODE=True / USDJPY）
- [ ] SMC_GBPAUD 実稼働評価（パフォーマンス確認）
- [ ] stat_arb 評価・trail設定調整
- [ ] MOM_ENZ 評価（BT PF=1.150でPF<1.2のため要注意）
- [ ] MOM_ECA 評価（BT n=11と極端に少ないため要注意）

### インフラ改善
- [ ] FX_TOD_Monitor エラー112 恒久解決確認（logs/作成後の動作確認）
- [ ] FX Trail Monitor をBootTriggerからTimeTriggerに変更検討（VPS再起動後のみ起動）

---

## Phase 3（スケールアップフェーズ）

- [ ] 月利30万円達成
- [ ] ロット拡大
- [ ] ペア追加
- [ ] 200MA Pullback 正式稼働

---

## タスクスケジューラ構成（現状）

| タスク名 | bat | スクリプト | スケジュール |
|---------|-----|----------|------------|
| FX BB Monitor | run_bb.bat | vps/bb_monitor.py | TimeTrigger 繰り返し |
| FX Trail Monitor | run_trail.bat | vps/trail_monitor.py | BootTrigger（起動時） |
| FX Daily Trade | run_daily.bat | vps/daily_trade.py | 週次 07:00 |
| FX Mail Monitor | run_mail.bat | vps/mail_monitor.py | TimeTrigger 繰り返し |
| FX Summary 07/12/16/21 | run_summary.bat | vps/summary_notify.py | 週次 4回 |
| FX_DailyReport | run_daily_report.bat | vps/daily_report.py | 毎日 07:00 |
| FX_TOD_Monitor | run_tod_monitor.bat | vps/tod_monitor.py | 毎時 00分 |
| FX_Data_Update | python直接 | update_data.py | 毎日 00:00 |
