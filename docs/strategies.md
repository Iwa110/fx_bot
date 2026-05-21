# 戦略一覧

最終更新: 2026-05-21（SMA Squeeze v4.1 ATR-adaptive trailing 追記）

---

## 1. BB戦略（ボリンジャーバンド逆張り）

| 項目 | 内容 |
|-----|-----|
| ファイル | `vps/bb_monitor.py` v22 |
| 起動 | `bb_monitor_all.bat` → タスクスケジューラ `FX_BB_Monitor_All`（毎分） |
| magic | 20250001 |
| ステータス | **稼働中**（GBPJPY / USDJPY / EURJPY のみ） |
| TF | 5分足エントリー |
| HTFフィルター | GBPJPY: 4h EMA20+RSI14<60(buy)/RSI14>55(sell) + 5m BBwidth(×1.2/lb=20) / USDJPY: 4h EMA20+RSI14<55(buy)/RSI14>45(sell) + 5m BBwidth(×0.8/lb=30) / EURJPY: 4h EMA20方向のみ |

### 対象ペア

| ペア | 有効 | SL_ATR倍率 | TP | HTFフィルター | 時間帯 | 備考 |
|-----|------|-----------|-----|------------|------|-----|
| GBPJPY | ✅ | 3.0 | SL×1.5（固定） | htf4h_rsi_bw（EMA20+RSI<60/>55 + BBwidth×1.2/lb=20） | 制限なし | v21: Stage2廃止→固定TP・RSI+BBwidthフィルター追加 |
| EURJPY | ✅ | 2.5 | SL×1.5（固定） | htf4h_only（4h EMA20方向のみ） | 9,17 UTC | v22: F1andF2廃止→固定TP・Stage2無効化 |
| USDJPY | ✅ | 3.0 | SL×1.5（固定） | htf4h_rsi_bw（EMA20+RSI<55/>45 + BBwidth×0.8/lb=30） | 制限なし | v21: Stage2廃止→固定TP・RSI+BBwidthフィルター追加 |
| EURUSD | ❌ | 1.2 | — | — | — | **停止中**（BT PF<0.7） |
| GBPUSD | ❌ | 1.2 | — | — | — | **停止中**（実稼働PF=0.397） |
| USDCAD | ❌ | 1.5 | — | — | — | **停止中** |

### BBパラメータ共通

| パラメータ | 値 |
|----------|---|
| period | 20 |
| sigma（エントリー） | 1.5 |
| exit_sigma | 1.0 |

### トレーリング設定（trail_monitor.py v13）

| ペア | stage2 | stage2_distance | stage3_activate | stage3_distance |
|-----|--------|----------------|----------------|----------------|
| BB_（共通） | ✅ | 0.3 | 1.2 | 0.8 |
| BB_GBPJPY | ❌ | — | 1.2 | 0.8 | <!-- v13: 固定TP移行のため無効化 -->
| BB_USDJPY | ❌ | — | 1.2 | 0.8 | <!-- v13: 固定TP移行のため無効化 -->
| BB_EURUSD | ✅ | 0.1 | 1.2 | 0.8 |
| BB_GBPUSD | ✅ | 1.0 | 1.2 | 0.8 |
| BB_EURJPY | ❌ | — | 1.2 | 0.8 | <!-- v22: 固定TP移行のため無効化 -->

### GBPJPY フィルター改善 BT結果（2026-05-14実施）

フィルター: `htf4h_rsi_bw`（4h EMA20方向 + 4h RSI<60/RSI>55 + 5m BBwidth > 20bar平均×1.2）

| 指標 | ベースライン(htf4h_only) | 採用フィルター | 改善幅 |
|-----|----------------------|------------|------|
| PF | 0.944 | 1.861 | +0.917 |
| WR | 40.0% | 59.8% | +19.8pt |
| N | 557 | 92 | −465（厳選） |
| MaxDD | 2,443.7 pips | 186.8 pips | −92% |

期間分割検証（全データを3等分）:

| 期間 | 日付 | PF | 判定 |
|-----|-----|----|-----|
| Period_A | 2026-02-02〜03-02 | 1.315 | ✅ |
| Period_B | 2026-03-02〜03-30 | 1.612 | ✅ |
| Period_C | 2026-03-30〜04-24 | 3.539 | ✅ |

**→ STABLE（全期間PF>1.0 / 過学習リスク低）**

BT根拠ファイル: `optimizer/gbpjpy_filter_bt.py` / `optimizer/gbpjpy_split_validation.py`

### USDJPY フィルター改善 BT結果（2026-05-14実施）

フィルター: `htf4h_rsi_bw`（4h EMA20方向 + 4h RSI<55/RSI>45 + 5m BBwidth > 30bar平均×0.8）

| 指標 | ベースライン(htf4h_rsi) | 採用フィルター(BBwidth追加) | 改善幅 |
|-----|----------------------|--------------------------|------|
| PF | 1.242 | 1.300 | +0.058 |
| WR | 45.6% | 49.7% | +4.1pt |
| N | 103 | 157 | +54（緩い閾値により増加） |
| MaxDD | 219.8 pips | 301.9 pips | +82.1（増） |

> 判定: CONDITIONAL（PF>1.1 かつ N>=80 かつ RSI_OK）。VPS実稼働でデータ蓄積後に再判定。

SELL閾値グリッド検証（`usdjpy_sell_grid.py`）での知見:
- SELL RSI閾値 sell>50 時: SELL N=20 → RSI_CAUTION（SELL過少）
- sell>45（ベースライン維持）でも BBwidth 追加で PF=1.300 / N=157 / SELL N=74（RSI_OK）
- RSI閾値は変更せず BBwidth フィルターのみ追加

BT根拠ファイル: `optimizer/usdjpy_filter_bt.py` / `optimizer/usdjpy_sell_grid.py` / `optimizer/usdjpy_split_validation.py`

### EURJPY フィルター改善 BT結果（2026-05-14実施）

フィルター: `htf4h_only`（4h EMA20方向のみ、既存F1andF2を廃止）

| 指標 | htf4h_only（採用） | rsi+bw（不採用） |
|-----|-----------------|----------------|
| PF（全データ） | 1.645 | 2.386 |
| WR | 50.0% | 50.0% |
| N | 30 | 26 |
| MaxDD | 294.0 pips | 343.9 pips |

期間分割検証（全データを3等分、1h足 2024-04-24〜2026-04-24）:

| 期間 | 日付 | baseline PF | RSI+BW PF | 判定 |
|-----|-----|-----------|---------|-----|
| Period_A | 2024-04-24〜2024-12-23 | 2.903 | 3.871 | ✅ |
| Period_B | 2024-12-23〜2025-08-22 | 1.078 | 3.358 | ✅ |
| Period_C | 2025-08-22〜2026-04-24 | 1.543 | 0.610 | ❌ |

**→ htf4h_only: STABLE（全期間PF>1.0）/ rsi+bw: UNSTABLE（Period_C過適合）→ htf4h_only を採用**

BT根拠ファイル: `optimizer/eurjpy_filter_bt.py` / `optimizer/eurjpy_split_validation.py`

### Phase1完了判定結果（2026-04-24〜2026-05-03, n=8〜41）

| ペア | PF | 勝率 | DD(絶対) | PF判定 | WR判定 | 総合 |
|----|----|-----|--------|-------|-------|-----|
| GBPJPY | 1.034 | 75.0% | 3,320円 | NG | OK | **不合格** |
| USDJPY | 0.692 | 64.7% | 3,900円 | NG | OK | **不合格** |
| EURUSD | 0.748 | 70.7% | 10,030円 | NG | OK | **不合格** |
| GBPUSD | 0.397 | 67.6% | 23,828円 | NG | OK | **不合格** |

判定基準: PF>1.2 / 勝率>50% / DD<15%。サンプル数不足のためデータ蓄積継続中。

---

## 2. MOM戦略（モメンタム）

| 項目 | 内容 |
|-----|-----|
| ファイル | `vps/daily_trade.py` v3.5 |
| 起動 | `run_daily.bat` → タスクスケジューラ `FX Daily Trade`（週次 07:00） |
| ステータス | **稼働中** |
| 実行タイミング | 毎朝 07:00 + 夕方 19:00 |

### ペア別設定

| 戦略名 | magic | ペア | フィルター | BT PF | BT n |
|------|-------|-----|----------|-------|------|
| MOM_JPY | 20240101 | USDJPY | EURJPY, period=10, mom_th=0.015 | 1.571 | 58 |
| MOM_GBJ | 20240102 | GBPJPY | USDJPY, period=7, mom_th=0.015 | 1.446 | 45 |
| MOM_ENZ | 20240105 | EURNZD | EURUSD, period=14, mom_th=0.007 | 1.150 | 53 |
| MOM_ECA | 20240106 | EURCAD | USDCAD, period=7, mom_th=0.015, EMA200 | 4.109 | 11 ⚠️n少 |
| MOM_GBU | 20240107 | GBPUSD | EURUSD, period=10, mom_th=0.007, EMA200 | 1.427 | 56 |

### トレーリング設定

| 戦略 | stage2 | stage2_distance | stage3_activate | stage3_distance |
|-----|--------|----------------|----------------|----------------|
| MOM_JPY | ✅ | 1.0 | 0.7 | 0.3 |
| MOM_GBJ | ✅ | 1.0 | 0.5 | 0.8 |
| MOM_ENZ | ✅ | 1.0 | 0.7 | 0.3 |
| MOM_ECA | ✅ | 1.0 | 0.5 | 0.3 |
| MOM_GBU | ✅ | 1.0 | 0.5 | 0.3 |

---

## 3. STR戦略（統計的裁定・スプレッド回帰）

| 項目 | 内容 |
|-----|-----|
| ファイル | `vps/daily_trade.py` v3.5（MOM戦略と同ファイル） |
| magic | 20240104 |
| ステータス | **稼働中** |
| TF | 1H（lookback=10, min_spread=0.015, hold_period=5） |

### 対象ペア
EURUSD / GBPUSD / AUDUSD / USDJPY / EURGBP / USDCAD / USDCHF / NZDUSD / EURJPY / GBPJPY

### トレーリング設定

| ペア | stage2 | stage2_distance | stage3_activate | stage3_distance |
|-----|--------|----------------|----------------|----------------|
| STR_*（共通） | ✅ | 0.2 | 0.8 | 0.6 |

---

## 4. TOD戦略（時間帯別平均回帰）

| 項目 | 内容 |
|-----|-----|
| ファイル | `vps/tod_monitor.py` v2 |
| 起動 | `run_tod_monitor.bat` → タスクスケジューラ `FX_TOD_Monitor`（毎時 00分） |
| magic | 20250002 |
| ステータス | **稼働中**（Error 112 要確認 → logs/ディレクトリ作成で解消予定） |
| TF | 1H（close-to-close統計） |
| 統計キャッシュ | 23時間TTL（`tod_stats.json`） |

### ペア別設定（BT結果反映済み）

| ペア | entry_sigma | TP×ATR | SL×ATR | BT PF | BT WR | BT n |
|-----|------------|--------|--------|-------|-------|------|
| EURUSD | 2.5 | 1.0 | 1.5 | 1.232 | 62.8% | 843,691 |
| GBPUSD | 2.5 | 1.0 | 2.0 | 1.201 | 70.3% | 843,691 |

---

## 5. stat_arb戦略（ペアトレーディング）

| 項目 | 内容 |
|-----|-----|
| ファイル | `vps/stat_arb_monitor.py` |
| magic | 20260001 |
| ステータス | **稼働中**（batなし・手動起動） |
| TF | 1H |

### パラメータ

| パラメータ | 値 |
|----------|---|
| OLS window | 500 |
| Z-score window | 100 |
| Entry Z | 2.0 |
| SL Z | 3.5 |
| TP Z | 0.5 |
| MAX_POS | 2ペア |

### 対象ペア

| ペアA | ペアB | 有効 |
|------|------|-----|
| GBPJPY | USDJPY | ✅ |
| EURUSD | GBPUSD | ✅ |

---

## 6. SMC_GBPAUD戦略

| 項目 | 内容 |
|-----|-----|
| ファイル | `vps/trail_monitor.py` v13（トレーリング管理のみ） |
| magic | 20260002 |
| ステータス | **稼働中**（trail_monitor内で管理） |
| 方向 | **Sell専用** |
| TF | 1H / HTF=1D |
| Session | 8〜20 UTC |
| MAX_POS | 1 |

### トレーリング設定

| stage2 | stage3_activate | stage3_distance |
|--------|----------------|----------------|
| ❌ | 1.0 | 0.7 |

---

## 7. 200MA Pullback（Pin Bar）戦略

| 項目 | 内容 |
|-----|-----|
| ファイル | `vps/ma200_pin_bar.py` |
| magic | 20260003 |
| ステータス | **テスト段階**（DEMO_MODE=True） |
| ペア | USDJPY |
| TF | 1H |
| RR | 1.5 |
| Lot | 0.02（固定・デモ用） |

### エントリー条件
- Pin bar: 下ヒゲ≥60% かつ ボディ≤30% → Bullish
- 上ヒゲ≥60% かつ ボディ≤30% → Bearish
- MA: (SMA200 + EMA200) / 2
- Slope filter: ≥0.0001（Long）/ ≤-0.0001（Short）
- SL: 直近20本のSwing高安 + spread
- TP: entry ± SL距離 × 1.5

---

## 8. SMA Squeeze Play戦略

| 項目 | 内容 |
|-----|-----|
| ファイル | `vps/sma_squeeze.py` v4.1 |
| 起動 | `vps/sma_squeeze_monitor.bat`（常駐デーモン、60秒ループ） |
| magic | 20260010 |
| STRATEGY_TAG | `SMA_SQ` |
| ステータス | **稼働中**（axiory / exness のみ。oanda停止中） |
| 方向 | Long / Short |
| TF | USDJPY/EURUSD/EURJPY=4H、GBPJPY/GBPUSD=1H |
| MAX_TOTAL_POS | 3 |
| MAX_JPY_LOT | 0.4 lot |
| COOLDOWN_MIN | 180分／ペア |

### エントリー条件（全条件を満たすこと）

1. ADX14 > 20（トレンド強度フィルター）
2. divergence_rate ≤ squeeze_th（SMA短期・長期のスクイーズ状態）
3. SMA長期スロープ単調（slope_period連続上昇 or 連続下降）
4. 日足SMAスロープが1H方向と一致（v3 daily_sma filter）
5. 前バー終値がSMA短期を抜けた（スクイーズ解放）
6. 当バーがSMA長期の外側かつ強い陽線/陰線

### ペア別パラメータ（v4.1現在）

| ペア | TF | SMA短/長 | sq_th | slope_period | RR | SL×ATR | atr_trail_mult | 有効 |
|-----|-----|---------|-------|------------|-----|--------|--------------|-----|
| USDJPY | 4h | 25/150 | 2.0 | 5 | 2.5 | 1.5 | 0.5 | ✅ |
| GBPJPY | 1h | 25/250 | 0.5 | 10 | 2.0 | 1.5 | 0.5 | ✅ |
| EURUSD | 4h | 25/200 | 2.0 | 10 | 2.5 | 1.0 | 0.5 | ✅ |
| GBPUSD | 1h | 15/250 | 1.5 | 20 | 2.0 | 1.0 | 1.5 | ❌（無効） |
| EURJPY | 4h | 15/150 | 2.0 | 20 | 2.5 | 1.5 | 0.0 | ✅ |

エントリーBT: 2024-04-24〜2026-04-24 / 9720 runs（`optimizer/sma_squeeze_bt.py`）

### 決済ロジック

| 機能 | 詳細 |
|-----|-----|
| 固定SL/TP | SL = ATR14 × sl_atr_mult / TP = SL × rr（エントリー時設定） |
| SMA長期ブレイク強制決済 | 確定足がSMA長期を逆方向に抜けたら即クローズ（最優先） |
| スロープ反転決済（A-1） | SMA長期がslope_exit=3本連続で反転したら決済 |
| ATR-adaptive trailing（v4） | `manage_atr_trail()`: trail_dist = ATR14 × atr_trail_mult（60秒毎更新） |

### v4 ATR-adaptive trailing stop（2026-05-21実装）

BreakEven（be_r）を廃止し、ATR連動トレーリングに置き換え。SLは有利方向にのみラチェット。

**BT結果**（`optimizer/sma_squeeze_exit_bt.py` 275runs、2026-05-21）:

| ペア | baseline PF | atr_trail最優 | best PF | 改善幅 |
|-----|------------|-------------|---------|--------|
| USDJPY | 1.815 | mult=0.5 | 4.441 | +2.63 |
| EURUSD | 2.670 | mult=0.5 | 7.447 | +4.78 |
| GBPUSD | 0.713 | mult=1.5 | 1.418 | +0.71 |
| EURJPY | 3.673 | trail無効 | 3.673 | ±0 |
| GBPJPY | — | mult=0.5（USDJPY流用） | — | 1h BT未実施 |

ログ確認キーワード: `[ATR_TRAIL]` / `slope-exit:` / `force-close:`

### 監視ログ（v4.1）

| ログ | 出力タイミング |
|-----|------------|
| `sma_squeeze v4 started broker=axiory` | 起動時 |
| `connected broker=axiory login=XXXXX` | MT5接続成功時 |
| `heartbeat alive pos=X/Y cycle=N` | **30分毎**（エントリーなし時の生存確認） |
| `SMA_SQ entry: USDJPY LONG lot=...` | エントリー時 |
| `[ATR_TRAIL] USDJPY LONG SL old->new locked=+X` | ATR trail SL更新時 |
| `SMA_SQ force-close: USDJPY LONG SMA150 break` | SMA長期ブレイク決済時 |
| `SMA_SQ slope-exit: USDJPY LONG SMA150 slope reversed` | スロープ反転決済時 |
| `loop error: ...` | 例外発生時 |

---

## magic番号体系

| magic | 戦略 |
|-------|-----|
| 20240101 | MOM_JPY (USDJPY) |
| 20240102 | MOM_GBJ (GBPJPY) |
| 20240103 | CORR |
| 20240104 | STR |
| 20240105 | MOM_ENZ (EURNZD) |
| 20240106 | MOM_ECA (EURCAD) |
| 20240107 | MOM_GBU (GBPUSD) |
| 20240108 | TRI（廃止） |
| 20250001 | BB戦略 |
| 20250002 | TOD戦略 |
| 20260001 | stat_arb |
| 20260002 | SMC_GBPAUD |
| 20260003 | 200MA Pullback |
| 20260010 | SMA Squeeze Play |
| 20260020 | COT戦略（COT極値×日足トレンド） |
