# FX Bot - Claude Code Context

## プロジェクト概要
FX自動売買システム。VPS(Windows Server 2022)で複数戦略を並行稼働。
月利30万円目標。現在Phase1完了判定フェーズ→Phase2移行検討中。

## ディレクトリ構成
```
C:\Users\Administrator\fx_bot\
├── vps\          # 稼働中ボット
│   ├── bb_monitor.py         # v22 magic=20250001
│   ├── trail_monitor.py      # v10
│   ├── smc_gbpaud.py         # v4 magic=20260002
│   ├── stat_arb.py           # magic=20260001
│   ├── sma_squeeze.py        # v3 magic=20260010 (daily filter + COOLDOWN=180)
│   └── sma_squeeze_monitor.bat
├── optimizer\    # バックテスト・最適化
│   ├── loop_runner.py
│   ├── backtest.py
│   ├── evaluate.py
│   ├── phase2_ai_analysis.py
│   ├── make_4h_from_1h.py                  # 1h→4hリサンプル
│   ├── sma_squeeze_bt.py                   # グリッドサーチBT（エントリーパラメータ）
│   ├── sma_squeeze_exit_bt.py              # 決済パラメータ最適化BT
│   └── sma_squeeze_daily_filter_bt.py      # 日足フィルターBT ★新規
└── data\         # 14ペア 1h/5m足（*_1h.csvはgit管理）
```

## 戦略別現状

### BB戦略
- PAIRS: GBPJPY/USDJPY/EURJPY（EURUSD/GBPUSD/USDCADは停止中）
  - EURUSD: v20(2026-05-14)で停止（BT PF最高0.681、BB戦略との相性不良）
  - GBPUSD: v20(2026-05-14)で停止（実稼働PF=0.397、BT最高0.854で目標未達）
- htf4h_rsi_bwフィルター: GBPJPY/USDJPYに適用（4h EMA20方向 + RSI条件）
  - GBPJPY: Buy時RSI<60/Sell時RSI>55。GBP急騰局面ではRSI≥60で両方向ブロック注意
  - USDJPY: Buy時RSI<55/Sell時RSI>45。RSI中立ゾーン外でもブロック
  - RSI閾値は現状維持（意図的設計）
- rsi_ok未チェック: calc_bb_signal()内でrsi_filter()結果を参照しない実装は**意図的仕様**
  - RSIはログ記録のみ、エントリー可否はhtf4h_rsi_bwフィルターで判断する設計
- EURJPY: ALLOWED_HOURS_UTC=[9,17]（UTC9時台・17時台のみ）
- RR問題あり(実RR=0.31 vs 設計1.50)、改善実施済み・データ蓄積中

### trail_monitor v10
- STR/MOM_JPYペア別設定分離済み
- SMC_GBPAUD追加(activate=1.0, distance=0.7, Sell専用)

### SMC_GBPAUD v4
- Sell専用、TF=1h/HTF=1d、Session=8-20UTC、MAX_POS=1

### stat_arb
- GBPJPY/USDJPY・EURUSD/GBPUSD、MAX_POS=2ペア

### SMA Squeeze Play v3（稼働中）
- magic=20260010、STRATEGY_TAG='SMA_SQ'
- PAIRS: USDJPY/GBPJPY/EURUSD/GBPUSD/EURJPY（全5ペア有効）
- ロジック: SMA200スロープフィルタ + SMAスクイーズ解放エントリー + 日足フィルター
  - エントリー条件: ADX14>20、divergence_rate≤squeeze_th、SMAスロープ単調 + 日足SMA方向一致
  - 決済: ATR×sl_atr_mult でSL、SL×rr でTP、SMA長期ブレイク強制決済 / slope-exit / BE移動
  - クールダウン: 180分/ペア（v3: 60→180に変更）、MAX_TOTAL_POS=3、MAX_JPY_LOT=0.4
- BT結果（日足フィルター追加後、sma_squeeze_daily_filter_bt.py）:
  | pair   | tf | daily_sma | daily_sp |  PF(base) |  PF(filter) |  n  |
  |--------|----|-----------|----------|-----------|-------------|-----|
  | USDJPY | 4h | 20 | 3 | 1.815 | 1.928 | 27 |
  | GBPJPY | 1h | 20 | 3 | 1.462 | 1.522 | 47 |
  | EURUSD | 4h | 50 | 3 | 2.670 | 2.831 | 29 |
  | GBPUSD | 1h | 20 | 5 | 1.341 | 1.372 | 208 | (停止中) |
  | EURJPY | 4h | 20 | 5 | 3.673 | 3.748 | 29 |
- **v3追加 (2026-05-16)**: 日足SMAスロープフィルター（1h方向と日足方向が不一致→スキップ）
  + COOLDOWN_MIN 60→180 / GBPUSD enabled=False
- **注意**: VPS側でgit pull後、sma_squeeze_monitor.batを手動再起動すること

## GitHub運用
- Repo: https://github.com/Iwa110/fx_bot (Public)
- VPS更新フロー: commit/push → VPS側でgit pull
- Raw URL: https://raw.githubusercontent.com/Iwa110/fx_bot/main/

## コーディング規約
- ASCIIクォートのみ(' と ")、スマートクォート禁止
- Pythonファイルのmagic番号体系を維持すること

## Top of mind（2026-05-19 夜更新）
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

### SMA Squeeze v2 決済改善（2026-05-12完了）
- A-1 SMA_long slope reversal exit (slope_exit=3): 傾き反転で強制決済（force-closeより先に発動）
- B-1 breakeven move (be_r=0.5): profit≥0.5×原SL距離でSLを建値移動（order_modify SLTP）
- BT結果 (sma_squeeze_exit_bt.py, 80 runs): be_r=0.5が全ペア最優先。slope_exit=3はGBPJPYに効果
- ヘルパー関数: _close_position() / _check_breakeven() 追加済み

### SMA Squeeze v3 日足フィルター（2026-05-16完了）
- 問題: GBP急騰局面でSELL方向に9連敗（5/13-14実稼働）。1h slopeだけでは転換検知が遅い
- 解決: 日足SMA傾きと1h方向が不一致→スキップ（daily_slope_map）
- BT (sma_squeeze_daily_filter_bt.py, 35 runs): 全ペアでPF改善確認
  - USDJPY: 1.815→1.928 / GBPJPY: 1.462→1.522 / EURUSD: 2.670→2.831 / EURJPY: 3.673→3.748
- COOLDOWN_MIN 60→180（連続エントリー抑制強化）
- GBPUSD: enabled=True（BT PF=1.372で継続稼働）
- 変更ファイル: vps/sma_squeeze.py (v3) / optimizer/sma_squeeze_daily_filter_bt.py (新規)

### BB戦略 実RR問題改善（2026-05-19完了）
- **根本原因**: trail_monitor(5m ATR) vs bb_monitor(H1 ATR)のATRミスマッチ
  - Stage3発動 = 5m_ATR×1.2 ≈ H1_ATRベースTP前3-5%地点で早期発動
  - 実稼働70件: TP到達わずか2件(2.9%)、trail/SL勝ちの平均=+687円（設計TP大幅未達）
  - 実RR=0.276 vs 設計1.5 → 81.6%未達
- **対策1 (主)**: trail_monitor v14: BB_GBPJPY/USDJPY/EURJPY の stage3_activate=1.2→99（実質無効化）
  - TP一本勝負に変更。BT(trail無効): GBPJPY PF=1.105 / USDJPY 1.147 / EURJPY 1.058
- **対策2 (副)**: bb_monitor v24: GBPJPY/USDJPY sl_atr_mult=3.0→2.5
  - BT: GBPJPY PF=1.105 / USDJPY 1.147（sl=3.0時比 +0.09/+0.008 改善）
  - RR引き上げ(案B)はWR低下でPF悪化→採用せず
- **変更ファイル**:
  - vps/trail_monitor.py (v14): BB_GBPJPY/USDJPY/EURJPY stage3_activate=99
  - vps/bb_monitor.py (v24): GBPJPY/USDJPY sl_atr_mult=3.0→2.5
  - optimizer/backtest.py: BB_PAIRS_CFG GBPJPY/USDJPY sl_atr_mult=2.5
  - optimizer/bb_rr_analysis.py / bb_rr_bt.py（新規）
  - strategy_spec.md: §1テーブル・§11テーブル更新

### 翌日Chat確認事項
- sma_squeeze_log_axiory.txtで「daily_slope=DN/UP vs 1h」ログが出ているか確認
- BB戦略: v24適用後、trail_logに Stage3 ログが出なくなっているか確認（VPS git pull + trail_monitor再起動必要）
- BB戦略: TP到達件数が増えているか history.csv で確認（目安1〜2週間後）
- サンプル数100件超えたら再判定（目安: あと2〜3週間稼働後）
- CORR実稼働後のPF/WR推移を確認（BT: PF=1.924, WR=52.9%）

## 最新パフォーマンス統計（自動更新）
<!-- AUTO_STATS_BEGIN -->
更新日時: 2026-05-19 07:10 JST

### 本日 2026-05-19
取引なし

### 直近7日（2026-05-13〜2026-05-19）
総損益: -23,585円  PF=0.432  WR=69.6%  n=23

| ペア | 損益 | PF | WR | n | |
|------|------|----|----|---|---|
| EURJPY | +1,280円 | inf | 100.0% | 1 | ✅ |
| EURUSD | -11,937円 | 0.316 | 75.0% | 8 | ⚠️ |
| GBPJPY | -15,740円 | 0.074 | 25.0% | 4 | ⚠️ |
| GBPUSD | +7,292円 | 116.746 | 83.3% | 6 | ✅ |
| USDJPY | -4,480円 | 0.358 | 75.0% | 4 | ⚠️ |

### 日次推移（直近7日）
- 2026-05-13: -6,876円  n=20
- 2026-05-14: -4,834円  n=16
- 2026-05-15: -13,100円  n=2
- 2026-05-16: +1,217円  n=3

### 戦略別（直近7日）
| 戦略 | 損益 | PF | WR | n |
|------|------|----|----|---|
| BB | -19,622円 | 0.477 | 80.0% | 20 |
| SMA_SQ | -3,963円 | 0.000 | 0.0% | 3 |

### BB戦略 Phase1進捗（全期間）
判定基準: PF>1.2 / WR>50%
| ペア | PF | WR | n | 判定 |
|------|----|----|---|------|
| AUDJPY | 0.049 | 33.3% | 3 | ❌ NG |
| EURJPY | 0.708 | 71.4% | 7 | ❌ NG |
| EURUSD | 0.432 | 68.0% | 75 | ❌ NG |
| GBPJPY | 0.294 | 70.0% | 10 | ❌ NG |
| GBPUSD | 0.294 | 60.0% | 55 | ❌ NG |
| USDCAD | 0.266 | 38.9% | 54 | ❌ NG |
| USDJPY | 1.541 | 82.0% | 50 | ✅ OK |

### 自動アラート
- ⚠️ EURUSD: PF=0.316（直近7日 n=8）→ パラメータ見直し要
- ⚠️ GBPJPY: PF=0.074（直近7日 n=4）→ パラメータ見直し要
- ⚠️ USDJPY: PF=0.358（直近7日 n=4）→ パラメータ見直し要

<!-- AUTO_STATS_END -->

## 直近タスク
- [x] Phase1完了判定実行（2026-05-03: 全ペア不合格・データ蓄積継続）
- [x] CORR戦略 BT最適化＋Zスコア決済/hold_period実装（2026-05-08完了）
- [x] 動的ロットサイジング実装: dynamic_lot.py新規 / phase1_judgment.py新判定基準 / daily_report.py統合（2026-05-08完了）
- [x] VPS Task Schedulerウィンドウ非表示化・trail_monitor多重起動修正（2026-05-10完了）
- [x] OANDA MT5接続問題解消・全ブローカー稼働化（2026-05-11完了）
- [x] SMA Squeeze Play v1 実装・BT完了・PAIRS_CFG最適化（2026-05-12完了）
- [x] SMA Squeeze v2 決済改善 A-1+B-1 実装・BT最適化（2026-05-12完了）
- [x] SMA Squeeze v3 日足フィルター実装・BT・push（2026-05-16完了）
- [x] bb_monitor v20: EURUSD/GBPUSD停止（2026-05-14完了）
- [x] bb_monitor v21/v22: GBPJPY/USDJPY htf4h_rsi_bwフィルター追加・EURJPY改善（2026-05-14以降）
- [x] BB戦略 実RR改善: trail_monitor v14(Stage3無効化) + bb_monitor v24(sl縮小)（2026-05-19完了）
- [ ] VPS: git pull + trail_monitorを再起動（bb_monitor v24/trail_monitor v14 反映）
- [ ] VPS: sma_squeeze_monitor.bat再起動確認（v3稼働中か確認）
- [ ] VPS: Task Schedulerに週次phase1_judgment（日曜7:05 JST）を追加登録
- [ ] USDCAD再評価(BT結果待ち)

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
