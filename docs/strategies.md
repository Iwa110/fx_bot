# 戦略一覧

最終更新: 2026-05-21

---

## 1. BB戦略（ボリンジャーバンド逆張り）

| 項目 | 内容 |
|-----|-----|
| ファイル | `vps/bb_monitor.py` v17 |
| 起動 | `bb_monitor_all.bat` → タスクスケジューラ（毎分） |
| magic | 20250001 |
| ステータス | **稼働中**（axiory / exness / oanda） |
| TF | 5分足エントリー |
| HTFフィルター | 4H BB（GBPJPY / USDJPY のみ `use_htf4h: True`） |

### 対象ペア

| ペア | 有効 | SL_ATR倍率 | フィルター | 備考 |
|-----|------|-----------|----------|-----|
| GBPJPY | ✅ | 3.0 | F2andF1 (f1=3, f2=10.0) | ALLOWED: 9,17 UTC / htf4h_only |
| EURJPY | ✅ | 2.5 | F1andF2 (f1=5, f2=10.0) | ALLOWED: 9,17 UTC |
| USDJPY | ✅ | 3.0 | F1 (f1=5, sigma=2.0) | htf4h_only |
| EURUSD | ✅ | 3.0 | なし | 時間制限なし |
| GBPUSD | ✅ | 2.0 | なし | 時間制限なし |
| USDCAD | ❌ | 1.5 | — | **停止中**（enabled=False） |

### BBパラメータ共通

| パラメータ | 値 |
|----------|---|
| period | 20 |
| sigma（エントリー） | 1.5 |
| RR | 1.0 |
| exit_sigma | 1.0 |

### トレーリング設定（trail_monitor.py）

| ペア | stage2 | stage2_distance | stage3_activate | stage3_distance |
|-----|--------|----------------|----------------|----------------|
| BB_（共通） | ✅ | 0.3 | 1.2 | 0.8 |
| BB_GBPJPY | ✅ | 1.0 | 1.2 | 0.8 |
| BB_USDJPY | ✅ | 0.7 | 1.2 | 0.8 |
| BB_EURUSD | ✅ | 0.1 | 1.2 | 0.8 |
| BB_GBPUSD | ✅ | 1.0 | 1.2 | 0.8 |
| BB_EURJPY | ✅ | 0.7 | 1.2 | 0.8 |

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
| 起動 | `daily_trade_all.bat` → タスクスケジューラ `FX Daily Trade`（週次 07:00） |
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
| ステータス | **稼働中** |
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
| ファイル | `vps/stat_arb.py` |
| magic | 20260001 |
| ステータス | **稼働中** |
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
| ファイル | `vps/smc_gbpaud.py` v4 |
| magic | 20260002 |
| ステータス | **稼働中** |
| 方向 | **Sell専用** |
| TF | 1H / HTF=1D |
| Session | 8〜20 UTC |
| MAX_POS | 1 |

### トレーリング設定（trail_monitor経由）

| stage2 | stage3_activate | stage3_distance |
|--------|----------------|----------------|
| ❌ | 1.0 | 0.7 |

---

## 7. SMA Squeeze Play戦略

| 項目 | 内容 |
|-----|-----|
| ファイル | `vps/sma_squeeze.py` v4.1 |
| 起動 | `vps/sma_squeeze_monitor.bat`（常駐デーモン、60秒ループ） |
| magic | 20260010 |
| STRATEGY_TAG | `SMA_SQ` |
| ステータス | **稼働中**（axiory / exness のみ。oanda停止中） |
| TF | USDJPY/EURUSD/EURJPY=4H、GBPJPY/GBPUSD=1H |
| ブローカー | axiory / exness |

### 戦略ロジック

**エントリー条件（すべて満たすこと）:**
1. ADX14 > 20（トレンド強度フィルター）
2. divergence_rate ≤ squeeze_th（SMA短期・長期のスクイーズ状態）
3. SMA長期スロープ単調（slope_period連続上昇 or 連続下降）
4. 日足SMAスロープが1H方向と一致（daily_sma filter）
5. 前バー終値がSMA短期を抜けた（スクイーズ解放）
6. 当バーがSMA長期の外側かつ強い陽線/陰線

**決済:**
- SL: ATR14 × sl_atr_mult（エントリー時固定）
- TP: SL × rr
- 強制決済: SMA長期ブレイク（逆方向）
- スロープ反転決済: SMA長期slope_exit本連続反転

### ペア別パラメータ（BT最適化済み）

| ペア | sma_short | sma_long | squeeze_th | slope_period | rr | sl_atr_mult | atr_trail_mult | 有効 |
|-----|----------|---------|-----------|------------|-----|------------|--------------|-----|
| USDJPY | 25 | 150 | 2.0 | 5 | 2.5 | 1.5 | 0.5 | ✅ |
| GBPJPY | 25 | 250 | 0.5 | 10 | 2.0 | 1.5 | 0.5 | ✅ |
| EURUSD | 25 | 200 | 2.0 | 10 | 2.5 | 1.0 | 0.5 | ✅ |
| GBPUSD | 15 | 250 | 1.5 | 20 | 2.0 | 1.0 | 1.5 | ❌（無効） |
| EURJPY | 15 | 150 | 2.0 | 20 | 2.5 | 1.5 | 0.0 | ✅ |

### ATR-adaptive trailing stop（v4 2026-05-21）

`manage_atr_trail()` でエントリー後のSLを動的に引き上げ。

| 項目 | 内容 |
|-----|-----|
| 計算式 | `trail_dist = ATR14 × atr_trail_mult` |
| 方向 | Long: SLは上方向のみ / Short: SLは下方向のみ（ラチェット） |
| EURJPY | atr_trail_mult=0.0（無効。固定TP維持がBT最優） |
| ログ | `[ATR_TRAIL] USDJPY LONG SL 149.50->149.80 locked=+0.30` |

**BT結果**（sma_squeeze_exit_bt.py 275runs、2026-05-21）:

| ペア | baseline PF | best PF | 改善幅 |
|-----|------------|---------|--------|
| USDJPY | 1.815 | 4.441 | +2.63 |
| EURUSD | 2.670 | 7.447 | +4.78 |
| GBPUSD | 0.713 | 1.418 | +0.71 |
| EURJPY | 3.673 | 3.673 | ±0（trail無効が最優） |
| GBPJPY | — | — | 1h BT未実施 |

### ポジション管理

| 設定 | 値 |
|-----|---|
| MAX_TOTAL_POS | 3 |
| MAX_JPY_LOT | 0.4 |
| COOLDOWN_MIN | 180分/ペア |
| LOOP_INTERVAL | 60秒 |

### 監視ログ

| ログ | 出力タイミング |
|-----|------------|
| `sma_squeeze v4 started broker=axiory` | 起動時 |
| `connected broker=axiory login=XXXXX` | MT5接続成功時 |
| `heartbeat alive pos=X/Y cycle=N` | 30分毎（30サイクル毎） |
| `SMA_SQ entry: USDJPY LONG lot=... entry=... sl=... tp=...` | エントリー時 |
| `[ATR_TRAIL] USDJPY LONG SL old->new locked=+X` | ATR trail SL更新時 |
| `SMA_SQ force-close: USDJPY LONG SMA150 break` | SMA長期ブレイク決済時 |
| `SMA_SQ slope-exit: USDJPY LONG SMA150 slope reversed` | スロープ反転決済時 |
| `loop error: ...` | 例外発生時 |

---

## 8. 200MA Pullback（Pin Bar）戦略

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
