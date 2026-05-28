# FX Bot 戦略仕様書

生成日: 2026-05-28  
対象: `C:\Users\Administrator\fx_bot\vps\` 以下のPythonスクリプト

---

## 目次

1. [BB逆張り戦略](#1-bb逆張り戦略-bb_monitorpy-v23)
2. [MOM モメンタム順張り](#2-mom-モメンタム順張り-daily_tradepy)
3. [CORR 平均回帰](#3-corr-平均回帰-daily_tradepy)
4. [STR 通貨強弱](#4-str-通貨強弱-daily_tradepy)
5. [TRI 三角裁定](#5-tri-三角裁定-daily_tradepy)
6. [SMA Squeeze Play](#6-sma-squeeze-play-sma_squeezepy-v42)
7. [stat_arb 統計的裁定](#7-stat_arb-統計的裁定-stat_arb_monitorpy)
8. [TOD 時間帯別平均回帰](#8-tod-時間帯別平均回帰-tod_monitorpy)
9. [200MA Pinbar](#9-200ma-pinbar-ma200_pin_barpy)
10. [SMC_GBPAUD](#10-smc_gbpaud-trail_monitorpy-管理)
11. [トレーリングストップ共通仕様](#11-トレーリングストップ共通仕様-trail_monitorpy)
12. [共通リスク管理](#12-共通リスク管理-risk_managerpy)
13. [COT極値×日足トレンド](#13-cot極値日足トレンド-cot_monitorpy-v1)
14. [グリッド戦略](#14-グリッド戦略-grid_monitorpy-v2)

---

## 1. BB逆張り戦略 (bb_monitor.py v27)

### 概要
ボリンジャーバンドの逆張り戦略。5分足でBBタッチを検出し、上位足フィルターを適用。

### 基本情報
| 項目 | 値 |
|------|-----|
| Magic番号 | 20250001 |
| 注文コメント | `BB_{PAIR}` (例: `BB_GBPJPY`) |
| 実行間隔 | 5分毎（Task Scheduler） |
| 最大総ポジション | 13 |

### 対象通貨ペア・設定

| ペア | 有効 | JPY系 | σ値 | max_pos | SL倍率(ATR) | TP倍率 | 動的決済 | ペア別フィルター | 時間帯制限(UTC) |
|------|------|-------|-----|---------|-------------|--------|----------|-----------------|----------------|
| GBPJPY | ✅ | ✓ | **2.0** | **2** | **2.5** | SL×1.5 | なし | htf4h_rsi_bw (Buy RSI<60/Sell RSI>55, BBwidth>20avg×1.2) | 制限なし |
| USDJPY | ✅ | ✓ | 2.0 | **2** | **2.5** | SL×1.5 | **T_max=8h + exp TP Decay(τ=8h)** | htf4h_rsi_bw (Buy RSI<55/Sell RSI>45, BBwidth>30avg×0.8) | 制限なし |
| EURJPY | ✅ | ✓ | 1.5 | 1 | 2.5 | SL×1.5 | **T_max=6h 強制決済** | htf4h_only (4h EMA20方向一致のみ) | 9h,17h |
| EURUSD | 停止中 | - | 1.5 | 1.2 | - | なし | - | (データ蓄積中) | 全停止 (v20) |
| GBPUSD | 停止中 | - | 1.5 | 1.2 | - | なし | - | なし | 全停止 (v20) |
| USDCAD | 停止中 | - | 1.5 | 1.5 | - | なし | - | なし | 全停止 |

**停止理由:**
- EURUSD (v20停止): BT全組合せ最高PF=0.681 (N=132)、BB戦略との相性不良
- GBPUSD (v20停止): 実稼働PF=0.397、BT最高PF=0.854 (N=153)、目標未達

**EURUSDパラメータ (v23 BT推奨値・稼働再開時参照用):**
- `sl_atr_mult=1.2`, `bb_width_th=0.002`, `rsi_buy_max=35`, `rsi_sell_min=65`
- BT根拠 (eurusd_bb_bt.py, 2026-05-19, 5mデータ3.5ヶ月): rsi=35/65 PF=2.906 n=12
- 注意: rsi_ok は calc_bb_signal 内で未チェック（意図的仕様）→ RSI値はログのみ反映

### エントリー条件（フィルター適用順）

**Step 1: 1時間足レンジフィルター**
- 1h足 BB(period=20, σ=1.5) のσ位置を確認
- |σ| > 1.0 → トレンド判定でスキップ（レンジ外）

**Step 2: 5分足BBタッチ確認**
- BB(period=20, σ=ペア別) の終値比較
- 終値 >= upper → SELL方向
- 終値 <= lower → BUY方向

**Step 3: RSIフィルター (RSI14)**
- BUY方向: RSI ≤ 40 (グローバルデフォルト)
- SELL方向: RSI ≥ 60 (グローバルデフォルト)
- **注意**: rsi_ok は `calc_bb_signal()` 内で**チェックされない**（意図的仕様）
  - RSI値はログ記録のみ。ペア別 rsi_buy_max/rsi_sell_min を設定しても live 動作には影響しない
  - エントリー可否は Step 5 の htf4h_rsi_bw フィルターで管理

**Step 4: ペア別追加フィルター（参照のみ・現行未使用）**
- v20以降: GBPJPY/USDJPY/EURJPY ともに filter_type=None

| フィルター | 内容 |
|-----------|------|
| F1 Momentum | 直近N本の終値モメンタム（BUY→下落中、SELL→上昇中） |
| F2 Divergence | 合成レートとの乖離確認（JPYペアのみ: EURJPY=EURUSD×USDJPY、GBPJPY=GBPUSD×USDJPY） |

**Step 5: 4時間足フィルター（ペア別）**

| ペア | フィルター種別 | Buy条件 | Sell条件 |
|------|--------------|---------|---------|
| GBPJPY | htf4h_rsi_bw | 4h EMA20上方 + RSI<60 + BBwidth>20avg×1.2 | EMA20下方 + RSI>55 + BBwidth |
| USDJPY | htf4h_rsi_bw | 4h EMA20上方 + RSI<55 + BBwidth>30avg×0.8 | EMA20下方 + RSI>45 + BBwidth |
| EURJPY | htf4h_only   | 4h終値 > EMA20 | 4h終値 < EMA20 |

### 決済条件
- **TP**: SL × fixed_tp_rr (ペア別・上表参照)
  - v21以降: Stage2トレーリングSL廃止 → 固定TP採用
- **SL**: ATR(H1 14本) × sl_atr_mult（ペア別、上表参照）
- trail_monitorによるトレーリングストップ管理（→ §11参照）

**動的決済（v26）**
| ペア | T_max | TP Decay | BT根拠 (bb_dynamic_exit_bt.py, IS=60%/OOS=40%) |
|------|-------|----------|------------------------------------------------|
| USDJPY | 8時間超過で強制決済 | 指数減衰 τ=8h: TP(t) = init × (1/3.75 + 2.75/3.75 × exp(-t/8)) | Baseline OOS PF=1.137 → **1.211** (+6.5%) |
| EURJPY | 6時間超過で強制決済 | なし（TP固定） | Baseline OOS PF=1.047 → **1.137** (+8.7%) |
| GBPJPY | なし | なし | T_max追加でOOS PF=1.130→1.079 に劣化のため対象外 |

**v27 GBPJPY bb_sigma 変更（2026-05-28）**
- GBPJPY bb_sigma: 1.5→**2.0**（USDJPYと同じ「より深い逆張り」設計）
- BT根拠 (bb_analysis_bt.py, 5m足全データ): σ=1.5 PF=1.019 → σ=2.0 PF=1.275 (N=268)
- 直近5000本でも PF=1.509→2.190 と改善確認

### ロット計算
- 基本: 残高×1.5% ÷ (SL価格差 × 100,000 × レート) → 0.01単位切り捨て
- 上限: 0.2lot / 下限: 0.01lot
- JPYペア: MAX_JPY_LOT=0.4 超過はスキップ

### その他のリスク管理
- クールダウン: SL後15分間同ペアのエントリーをスキップ（COOLDOWN_MINUTES=15）
- 同一ペア複数ポジション: GBPJPY/USDJPY は max_pos=2（BBタッチは独立イベント。BT: GBPJPY +0.051, USDJPY +0.127 PF改善確認）
- 日次損失上限: -50,000円 (BB戦略合計) → 超過で当日停止

---

## 2. MOM モメンタム順張り (daily_trade.py)

### 概要
日足のモメンタムを測定し、メインペアとフィルターペアが同方向に動いていればエントリー。

### 基本情報
| 戦略名 | Magic番号 | 注文コメント |
|--------|----------|-------------|
| MOM_JPY | 20240101 | `FXBot_MOM_JPY` |
| MOM_GBJ | 20240102 | `FXBot_MOM_GBJ` |
| MOM_ENZ | 20240105 | `FXBot_MOM_ENZ` |
| MOM_ECA | 20240106 | `FXBot_MOM_ECA` |
| MOM_GBU | 20240107 | `FXBot_MOM_GBU` |
| 実行タイミング | - | 毎朝7時 + 夕方19時 |

### 対象通貨ペア・パラメータ

| 戦略 | メインペア | フィルターペア | period | mom_th | filter_th | EMA200 | 月曜倍率 | BT結果 |
|------|-----------|--------------|--------|--------|-----------|--------|---------|--------|
| MOM_JPY | USDJPY | EURJPY | 10 | 0.015 | 0.005 | なし | ×1.5 | PF=1.571 n=58 |
| MOM_GBJ | GBPJPY | USDJPY | 7 | 0.015 | 0.002 | なし | ×1.5 | PF=1.446 n=45 |
| MOM_ENZ | EURNZD | EURUSD | 14 | 0.007 | 0.005 | なし | ×1.5 | PF=1.150 n=53 |
| MOM_ECA | EURCAD | USDCAD | 7 | 0.015 | 0.002 | あり | ×1.5 | PF=4.109 n=11 |
| MOM_GBU | GBPUSD | EURUSD | 10 | 0.007 | 0.002 | あり | ×1.0 | PF=1.427 n=56 |

### エントリー条件

```
mom  = (現在終値 - period日前終値) / period日前終値  [メインペア]
fmom = (現在終値 - period日前終値) / period日前終値  [フィルターペア]

BUY:  mom > mom_th AND fmom > filter_th
SELL: mom < -mom_th AND fmom < -filter_th

月曜日: mom_th *= monday_th_mult
EMA200フィルター(use_ema200_filter=True の場合):
  BUY:  現在値 > EMA200(D1, 200本) のみ
  SELL: 現在値 < EMA200(D1, 200本) のみ
```

### 決済条件
- **TP/SL**: ATR(D1 14本)ベース、risk_manager.calc_tp_sl() で計算
  - デフォルト乗数は戦略共通 (tp=2.0×ATR, sl=1.5×ATR)
  - trail_monitorがStage2/Stage3でSLを移動（→ §11参照）

### ロット計算
- 残高×1.5%リスク方式（§12参照）

---

## 3. CORR 平均回帰 (daily_trade.py)

### 概要
AUDNZDのローリングZスコアが±2.0を超えたら平均回帰方向にエントリー。

### 基本情報
| 項目 | 値 |
|------|-----|
| Magic番号 | 20240103 |
| 注文コメント | `FXBot_CORR` |
| 対象ペア | AUDNZD |
| 時間足 | D1 |
| 実行タイミング | 毎朝7時 + 夕方19時 |

### パラメータ
| パラメータ | 値 |
|-----------|-----|
| corr_window | 60本 |
| z_entry | 2.0 |
| z_exit | 0.0 |
| hold_period | 5日 |

### エントリー条件
```
closes = 直近60本の終値
z = (closes[-1] - mean(closes)) / std(closes)

BUY:  z <= -2.0  (過剰下落 → 平均回帰)
SELL: z >= +2.0  (過剰上昇 → 平均回帰)
```

### 決済条件
以下のいずれか早い方:
1. **Zスコア回帰**: |z| <= 0.0 になったとき
2. **hold_period**: エントリーから5日経過

- **TP/SL**: ATR(D1)ベース、tp=1.5×ATR, sl=2.0×ATR（risk_manager.MULTIPLIERS参照）

### ロット計算
- 残高×1.5%リスク方式（§12参照）

---

## 4. STR 通貨強弱 (daily_trade.py)

### 概要
10通貨ペアのリターンから最強・最弱通貨を特定し、最適ペアでエントリー。

### 基本情報
| 項目 | 値 |
|------|-----|
| Magic番号 | 20240104 |
| 注文コメント | `FXBot_STR` |
| 時間足 | D1 |
| 実行タイミング | 毎朝7時 + 夕方19時 |
| hold_period | 5日 |

### 対象通貨ペア（スコア計算用）
EURUSD, GBPUSD, AUDUSD, USDJPY, EURGBP, USDCAD, USDCHF, NZDUSD, EURJPY, GBPJPY

### パラメータ
| パラメータ | 値 |
|-----------|-----|
| lookback | 10日 |
| min_spread | 0.015 |
| BT最優PF | 1.749 (lb=10) |

### エントリー条件
```
各通貨のスコア = 直近lookback日間のリターン合計
  Base通貨 → +ret / Quote通貨 → -ret

strongest = 最高スコア通貨
weakest   = 最低スコア通貨
spread    = strongest - weakest スコア差

条件:
  spread >= 0.015 (min_spread)
  EMA200(D1)フィルター:
    BUY:  現在値 > EMA200
    SELL: 現在値 < EMA200

最適ペア選択: strongest-weakest の組み合わせに最もマッチするペアを選択
```

### 決済条件
- **hold_period**: エントリーから5日経過で強制クローズ
- **TP/SL**: ATR(D1)ベース、tp=2.5×ATR, sl=1.5×ATR

### ロット計算
- 残高×1.5%リスク方式（§12参照）

---

## 5. TRI 三角裁定 (daily_trade.py)

### 概要
EURUSD/GBPUSDの理論値からEURGBPの乖離を検出し、収束方向にエントリー。

### 基本情報
| 項目 | 値 |
|------|-----|
| Magic番号 | 20240108 |
| 注文コメント | `FXBot_TRI` |
| 対象ペア | EURGBP |
| 実行タイミング | 毎朝7時 + 夕方19時 |

### パラメータ
| パラメータ | 値 |
|-----------|-----|
| entry_th | 0.0007 |
| tp_ratio | 0.7 |
| sl_th | 0.002 |
| BT結果 | PF=3.272 n=113 |

### エントリー条件
```
theory = EURUSD_mid / GBPUSD_mid
actual = EURGBP_mid
diff   = actual - theory

BUY:  diff <= -0.0007  (実勢が理論値より安すぎる → 収束でBUY)
SELL: diff >= +0.0007  (実勢が理論値より高すぎる → 収束でSELL)
```

### 決済条件
- **TP**: |diff| × 0.7（乖離幅の70%を目標に収束）
- **SL**: 固定 0.002（200pips相当）

### ロット計算
- 残高×1.5%リスク方式（SL=0.002固定で逆算）

---

## 6. SMA Squeeze Play (sma_squeeze.py v4.4)

### 概要
SMA200スロープフィルター + SMAスクイーズ解放でトレンドフォロー。v3で日足SMAスロープフィルター追加・COOLDOWN=180分、v4でATR-adaptive trailing stop導入（be_r廃止）、v4.2でtrail_start_multインフラ追加（BT検証により0.0=即時開始が最適と確定）、v4.3でEURJPY停止・T_max=24h追加、v4.4でUSADJPY squeeze_th緩和（シグナル頻度改善）。

### 基本情報
| 項目 | 値 |
|------|-----|
| Magic番号 | 20260010 |
| 注文コメント | `SMA_SQ_{PAIR}` (例: `SMA_SQ_USDJPY`) |
| 実行間隔 | 60秒ループ |
| 最大総ポジション | 3（各ペア最大1ポジション） |
| クールダウン | 180分/ペア |
| ハートビート | 30サイクル毎（約30分）にログ出力 |

### 対象通貨ペア・パラメータ

| ペア | 有効 | TF | SMA短期 | SMA長期 | squeeze_th | slope_period | RR | SL倍率 | atr_trail_mult | trail_start_mult | tmax_hours | daily_sma | daily_sp |
|------|------|-----|--------|--------|-----------|-------------|-----|-------|---------------|-----------------|------------|-----------|----------|
| USDJPY | ✅ | 4h | 25 | 150 | **1.5%** | 5 | 2.5 | 1.5 | **0.5** | 0.0 | **24h** | 20 | 3 |
| GBPJPY | ✅ | 1h | 25 | 250 | 0.5% | 10 | 2.0 | 1.5 | **0.5** | 0.0 | **24h** | 20 | 3 |
| EURUSD | ✅ | 4h | 25 | 200 | 2.0% | 10 | 2.5 | 1.0 | **0.5** | 0.0 | **24h** | 50 | 3 |
| GBPUSD | ❌ | 1h | 15 | 250 | 1.5% | 20 | 2.0 | 1.0 | 1.5 | 0.0 | None | 20 | 5 |
| EURJPY | ❌ | 4h | 15 | 150 | 2.0% | 20 | 2.5 | 1.5 | **0.0** (無効) | 0.0 | None | 20 | 5 |

**GBPUSD停止理由:** live PF<1.0（データ蓄積中だが有効エントリーなし）
**EURJPY停止理由 (v4.3):** 実稼働WR=0%（n=2, -9,900円）。94.6h保有でSL到達。BT PF=3.673だが実稼働と乖離が大きいため一時停止。
**USDJPY squeeze_th変更 (v4.4):** 2.0→1.5。BT PF=1.972 WR=46.2%（現行2.0: PF=1.815）。実稼働直近3M月平均0.94件/月(<1.0閾値)のため緩和。EURUSD は2.0が最適PF=2.670のため維持。
**trail_start_mult=0.0:** BT415runs検証でstart=0.0 > start=0.5（USDJPY PF 4.441 vs 3.500）

**ATR trail BT結果（exit BT, 415runs, atr_trail_mult=0.5, keep_tp=True）:**
| ペア | baseline PF | ATR trail PF | 改善幅 |
|------|------------|-------------|--------|
| USDJPY | 1.815 | **4.441** | +2.63 |
| EURUSD | 2.670 | **5.193** | +2.52 |
| GBPJPY | 1.462 | **2.883** | +1.42 |
| EURJPY | 3.673 | 3.673 | ±0（trail無効が最適） |

### エントリー条件（確定足 iloc[-2] で判定）

```
1. ADX14 > 20  (トレンド強度フィルター)

2. スクイーズフィルター:
   divergence_rate = |SMA_short - SMA_long| / SMA_long × 100
   divergence_rate <= squeeze_th  (スクイーズ状態)

3. スロープフィルター (1h/4h足):
   SMA_long 直近slope_period本が厳密単調増加 → slope=True(UP)
   SMA_long 直近slope_period本が厳密単調減少 → slope=False(DN)
   どちらでもない → スキップ

4. 日足SMAスロープフィルター (v3 2026-05-16):
   日足close のrolling(daily_sma)SMA の直近daily_slope_period本の傾き
   1h UP かつ 日足DN → スキップ
   1h DN かつ 日足UP → スキップ
   日足不定 → 通過（フィルターなし）
   日足データ: 1h足からresample_1d()で生成

5. 方向別エントリー条件:
   LONG  (slope=True):
     現在終値 > SMA_long
     前足終値 < SMA_short  (スクイーズ内)
     現在終値 > SMA_short  (ブレイクアウト)
     現在足が陽線 (close > open)

   SHORT (slope=False):
     現在終値 < SMA_long
     前足終値 > SMA_short
     現在終値 < SMA_short
     現在足が陰線 (close < open)
```

### 決済条件

| 条件 | 内容 | 優先度 |
|------|------|--------|
| TP | エントリー ± ATR14×sl_atr_mult×rr | 自動（MT5） |
| SL | エントリー ± ATR14×sl_atr_mult（初期値）| 自動（MT5、trail更新あり） |
| 強制決済 (force-close) | SMA_long逆ブレイク (LONG→終値<SMA_long) | 最優先 (manage_positions) |
| T_max強制決済 | tmax_hours超過で成行決済 `[TMAX]` ログ出力 | slope-exitより優先 |
| A-1 slope-exit | SMA_long傾き反転 slope_exit=3本連続 | 次優先 (manage_positions) |
| ATR-adaptive trail | trail_dist=ATR14×atr_trail_mult、SLを有利方向にラチェット | 常時監視 (manage_atr_trail, 60秒毎) |

**ATR trail詳細 (v4):**
- `trail_dist = ATR14_current × atr_trail_mult`（ボラ拡大時に自動拡幅）
- Long: `new_sl = bid - trail_dist`。`new_sl > p.sl` の時のみSL更新
- Short: `new_sl = ask + trail_dist`。`new_sl < p.sl` の時のみSL更新
- `trail_start_mult=0.0`（即時開始）がBT最適。trail_start_mult>0は保護的だがPF低下
- ログ: `[ATR_TRAIL] USDJPY LONG SL 149.50->149.80 locked=+0.30 atr=1.00 mult=0.5 ticket=X`
- EURJPY: `atr_trail_mult=0.0`（無効、固定TP/SL維持がBT最適）

### ロット計算
- 残高×1.5%リスク方式（§12参照）
- JPYペア: MAX_JPY_LOT=0.4 超過はスキップ

---

## 7. stat_arb 統計的裁定 (stat_arb_monitor.py)

### 概要
ペア間のローリングOLSによるスプレッドZスコアで、統計的裁定取引を行う。

### 基本情報
| 項目 | 値 |
|------|-----|
| Magic番号 | 20260001 |
| 注文コメント | `stat_arb` |
| 時間足 | H1 |
| 実行間隔 | 10秒ループ |
| 最大ペア数 | 2 |

### 対象ペア
| Leg-A | Leg-B | 有効 |
|-------|-------|------|
| GBPJPY | USDJPY | ✅ |
| EURUSD | GBPUSD | ✅ |

### モデルパラメータ
| パラメータ | 値 |
|-----------|-----|
| OLS_WINDOW | 500本 |
| ZSCORE_WINDOW | 100本 |
| ENTRY_Z | 2.0 |
| TP_Z | 0.5 |
| SL_Z | 3.5 |
| COOLDOWN | 15分 |

### エントリー条件
```
β = ローリングOLS回帰係数 (window=500)
spread = close_A - β × close_B
z = (spread - mean(spread, 100)) / std(spread, 100)

BUY_A/SELL_B: z >= +2.0  (spread過大 → Aが割高、Bが割安)
SELL_A/BUY_B: z <= -2.0  (spread過小 → Aが割安、Bが割高)

前提条件:
  月次共和分チェック (Engle-Granger ADF, p < 0.05)
```

### 決済条件
```
TP: z回帰 <= 0.5 (方向1) または >= -0.5 (方向-1)
SL: z拡大 >= 3.5 (方向1) または <= -3.5 (方向-1)
片脚状態検出 → 強制クローズ
```

### ロット計算
- Leg-A: LOT_A = 0.01固定
- Leg-B: round(|β| × 0.01, 0.01) → JPYペア上限0.4lot、非JPY上限1.0lot

---

## 8. TOD 時間帯別平均回帰 (tod_monitor.py)

### 概要
過去730日間の時間帯別リターン統計からZスコアが閾値超えで逆張りエントリー。

### 基本情報
| 項目 | 値 |
|------|-----|
| Magic番号 | 20250002 |
| 注文コメント | `TOD_{SYMBOL}` |
| 時間足 | H1 |
| 実行タイミング | 毎時0分 (Task Scheduler) |
| 最大総ポジション | 13 (共有) |
| 日次損失上限 | -30,000円 |

### 対象通貨ペア・パラメータ

| ペア | entry_sigma | tp_atr_mult | sl_atr_mult | BT PF | BT WR |
|------|------------|-------------|-------------|-------|-------|
| EURUSD | 2.5 | 1.0 | 1.5 | 1.232 | 62.8% |
| GBPUSD | 2.5 | 1.0 | 2.0 | 1.201 | 70.3% |

### エントリー条件
```
1. 時間帯統計 (23hキャッシュ使用):
   weekday別・JST時間別のclose-to-closeリターン統計（mean, std）
   統計期間: 過去730日間 / 平日のみ

2. 現在のリターン計算:
   ret = (bars[-2].close - bars[-3].close) / bars[-3].close

3. Zスコア:
   z = (ret - mean[hour_jst]) / std[hour_jst]

4. シグナル:
   BUY:  z <= -entry_sigma (過剰下落 → 平均回帰)
   SELL: z >= +entry_sigma (過剰上昇 → 平均回帰)

市場クローズフィルター:
  金曜22:00UTC以降 / 土日 / 月曜06:00UTC以前はスキップ
```

### 決済条件
- **TP**: ATR(H1 EMA14) × tp_atr_mult
- **SL**: ATR(H1 EMA14) × sl_atr_mult
- trail_monitorによるトレーリングSL管理なし（独立TP/SL）

### ロット計算
- 残高×1.5%リスク方式（§12参照）

---

## 9. 200MA Pinbar (ma200_pin_bar.py)

### 概要
200MAへのプルバック + ピンバーパターンで指値エントリー。未稼働（評価中）。

### 基本情報
| 項目 | 値 |
|------|-----|
| Magic番号 | 20260003 |
| 注文コメント | `ma200_pinbar` |
| 対象ペア | USDJPY |
| 時間足 | H1 |
| ロット | 0.02固定 |
| RR | 1.5 |

### インジケーター
- **MA**: (SMA200 + EMA200) / 2
- **タッチバンド**: MA ± 0.03% (TOUCH_BAND=0.0003)
- **スロープ閾値**: ±0.0001

### エントリー条件（確定足 sig = iloc[-2] で判定）
```
LONG:
  sig.close > MA (価格がMAより上)
  |sig.low - MA| <= MA × 0.0003 (下ヒゲがMAにタッチ)
  MA slope >= +0.0001 (上昇傾向)
  ピンバー条件:
    下ヒゲ >= レンジの60%
    ボディ <= レンジの30%
  → 発注: sig.high + 1pip でBUY指値 (ブレイクアウト確認)
  → SL: 直近20本スウィング安値 - spread
  → TP: entry + SL距離 × 1.5
  クールダウン: long方向60分

SHORT:
  sig.close < MA
  |sig.high - MA| <= MA × 0.0003
  MA slope <= -0.0001
  ピンバー条件:
    上ヒゲ >= レンジの60%
    ボディ <= レンジの30%
  → 発注: sig.low - 1pip でSELL指値
  → SL: 直近20本スウィング高値 + spread
  → TP: entry - SL距離 × 1.5
  クールダウン: short方向60分
```

### 決済条件
- **TP**: entry ± SL距離 × 1.5（指値注文時に設定）
- **SL**: スウィング安値/高値 + スプレッド（最低10pips保証）
- 発注形式: 指値注文（PENDING）、前足の更新で既存指値をキャンセル・再発注

---

## 10. SMC_GBPAUD (trail_monitor.py 管理)

### 概要
GBPAUD対象のスマートマネーコンセプト戦略。Sell専用。エントリーは別プロセス(smc_gbpaud.py v4)が担当し、SL管理はtrail_monitorが行う。

### 基本情報
| 項目 | 値 |
|------|-----|
| Magic番号 | 20260002 |
| 注文コメント | `SMC_GBPAUD` |
| 方向 | Sell専用 |
| 時間足 | H1 (エントリー) / D1 (HTF) |
| 稼働時間帯 | 8-20 UTC |
| MAX_POS | 1 |

### トレーリング設定（trail_monitorより）
| パラメータ | 値 |
|-----------|-----|
| stage2 | なし（Stage2 SL移動は非適用） |
| stage3_activate | ATR×1.0以上でトレーリング開始 |
| stage3_distance | ATR×0.7（トレーリング幅） |

> **注意**: smc_gbpaud.py はリポジトリに含まれていない可能性あり。詳細なエントリー条件はそちらを参照。

---

## 11. トレーリングストップ共通仕様 (trail_monitor.py v15)

### 概要
BB/MOM/STR/SMC_GBPAUDポジションのSLを動的に更新する常駐プロセス。

### 基本設定
| 項目 | 値 |
|------|-----|
| 更新間隔 | 30秒 |
| ATR計算 | 5分足 EMA14 |
| 最小更新幅 | ATR×0.05 |
| Stage2 発動 | 利益 >= ATR×0.7 + コスト(pips) |
| ハートビート出力 | 5分ごと |

### Stage2/Stage3 設定（戦略別）

| 戦略 | stage2 | stage2_distance | stage3_activate | stage3_distance |
|------|--------|-----------------|-----------------|-----------------|
| BB_(共通) | ✅ | ATR×0.3 | ATR×1.2 | ATR×0.8 |
| BB_GBPJPY | ❌ | - | **ATR×99（無効）** | ATR×0.8 |
| BB_USDJPY | ❌ | - | **ATR×99（無効）** | ATR×0.8 |
| BB_EURUSD | ✅ | ATR×0.1 | ATR×1.2 | ATR×0.8 |
| BB_GBPUSD | ✅ | ATR×1.0 | ATR×1.2 | ATR×0.8 |
| BB_EURJPY | ❌ | - | **ATR×99（無効）** | ATR×0.8 |
| MOM_JPY | ✅ | ATR×1.0 | ATR×0.7 | ATR×0.3 |
| MOM_GBJ | ✅ | ATR×1.0 | ATR×0.5 | ATR×0.8 |
| MOM_ENZ | ✅ | ATR×1.0 | ATR×0.7 | ATR×0.3 |
| MOM_ECA | ✅ | ATR×1.0 | ATR×0.5 | ATR×0.3 |
| MOM_GBU | ✅ | ATR×1.0 | ATR×0.5 | ATR×0.3 |
| STR（全ペア） | ✅ | ATR×0.2 | ATR×0.8 | ATR×0.6 |
| SMC_GBPAUD | ❌ | - | ATR×1.0 | ATR×0.7 |

### Stage定義
- **Stage2**: 利益 >= ATR×0.7+コスト → SLを entry + ATR×stage2_distance に移動（利益確保）
- **Stage3**: 利益 >= ATR×stage3_activate → SLを現在値 - ATR×stage3_distance に移動（トレーリング）
- 優先度: Stage3 > Stage2（より有利なステージが常に優先）

### BB_GBPJPY/USDJPY/EURJPY Stage3無効化の根拠（v14確定, v15 BT再確認 2026-05-19）

**根本原因（v14）:**
- bb_monitorはH1 ATRでTP設定（SL = H1_ATR × 2.5, TP = SL × 1.5 = H1_ATR × 3.75）
- trail_monitorのStage3判定は5m ATRベース（`profit_dist >= 5m_ATR × 1.2`）
- H1 ATR ≈ 5〜8倍 × 5m ATR → Stage3発動 ≈ TP前3-5%の地点で起動
- 実稼働70件: TP到達2件（2.9%）、trail/SL勝ちの平均=687円（設計TP大幅未達）
- → stage3_activate=99 でtrail事実上無効化

**BT再確認（v15, 2026-05-19, optimizer/trail_redesign_bt.py）:**

TP比率ベースtrail（選択肢C）グリッドサーチ: activate=[0.3-0.8] × distance=[0.05-0.20]

| ペア | trail無効(baseline) | 最良trail設定 | 判定 |
|------|-------------------|-------------|------|
| GBPJPY | **PF=1.105**  WR=42.6%  n=850 | act=0.80,dist=0.20  PF=1.028 | trail無効がベスト |
| USDJPY | **PF=1.147**  WR=41.9%  n=422 | act=0.80,dist=0.20  PF=1.079 | trail無効がベスト |
| EURJPY | **PF=1.058**  WR=41.3%  n=889 | act=0.80,dist=0.20  PF=1.014 | trail無効がベスト |

- activateを上げるほど（=trail発動を遅らせるほど）PF改善し、極限=trail無効が最良
- BB平均回帰エントリーはtrail途中確定が常に不利（TP到達 or SL到達の二択構造が最適）
- → stage3_activate=99（trail実質無効）を確定設定とする

---

## 12. 共通リスク管理 (risk_manager.py v2)

### ロット計算方式
```python
risk_amount  = balance × 0.015          # 残高の1.5%
loss_per_lot = sl_dist × 100,000 × rate # JPYペア: rate=1.0, 非JPY: rate=150.0(USDJPY想定)
lot = risk_amount / loss_per_lot
lot = round(lot / 0.01) × 0.01          # 0.01ロット単位
lot = clamp(lot, MIN=0.01, MAX=0.2)
```

### ATR計算
| 戦略 | 時間足 | 本数 |
|------|--------|------|
| BB系 | H1 | 14本 EWM |
| その他 (M5) | M5 | 20本 EWM |
| D1戦略 (MOM/CORR/STR/TRI) | D1 | 14本 単純平均 |

### ATR下限値 (Floor)
| ケース | 値 |
|--------|-----|
| JPYペア 5分足 | 0.005 |
| 非JPYペア 5分足 | 0.00002 |
| TRI | 0.0003 |
| MOM_JPY | 0.30 |
| MOM_GBJ | 0.50 |
| CORR / STR | 0.0003 |

### TP/SL乗数（calc_tp_sl）
| 戦略 | TP乗数 | SL乗数 |
|------|--------|--------|
| BB | 3.0 | 2.0 (ペア別sl_atr_multで上書き) |
| TRI | 1.5 | 4.0 |
| MOM_JPY | 3.0 | 1.0 |
| MOM_GBJ | 1.0 | 0.5 |
| CORR | 1.5 | 2.0 |
| STR | 2.5 | 1.5 |
| デフォルト | 2.0 | 1.5 |

---

## Magic番号・コメント早見表

| 戦略 | Magic | コメント形式 | スクリプト |
|------|-------|-------------|-----------|
| BB逆張り | 20250001 | `BB_{PAIR}` | bb_monitor.py |
| TOD時間帯 | 20250002 | `TOD_{SYMBOL}` | tod_monitor.py |
| MOM_JPY | 20240101 | `FXBot_MOM_JPY` | daily_trade.py |
| MOM_GBJ | 20240102 | `FXBot_MOM_GBJ` | daily_trade.py |
| CORR | 20240103 | `FXBot_CORR` | daily_trade.py |
| STR | 20240104 | `FXBot_STR` | daily_trade.py |
| MOM_ENZ | 20240105 | `FXBot_MOM_ENZ` | daily_trade.py |
| MOM_ECA | 20240106 | `FXBot_MOM_ECA` | daily_trade.py |
| MOM_GBU | 20240107 | `FXBot_MOM_GBU` | daily_trade.py |
| TRI | 20240108 | `FXBot_TRI` | daily_trade.py |
| stat_arb | 20260001 | `stat_arb` | stat_arb_monitor.py |
| SMC_GBPAUD | 20260002 | `SMC_GBPAUD` | smc_gbpaud.py (別管理) |
| 200MA Pinbar | 20260003 | `ma200_pinbar` | ma200_pin_bar.py |
| SMA Squeeze | 20260010 | `SMA_SQ_{PAIR}` | sma_squeeze.py |
| COT極値 | 20260020 | `COT_{PAIR}` | cot_monitor.py |
| Grid NZDUSD | 20260030 | `GRID_NZD` | grid_monitor.py |
| Grid GBPJPY | 20260031 | `GRID_GBP` | grid_monitor.py |
| Grid CHFJPY | 20260032 | `GRID_CHF` | grid_monitor.py |

---

## 13. COT極値×日足トレンド (cot_monitor.py v1)

### 概要
CFTC TFF（Traders in Financial Futures）レポートのLeveraged Funds COT Index（156週ローリング）が極値（>90 or <10）に達したペアで、D1 EMA50トレンドと一致する方向にスイングエントリー。週次ファンダメンタル×テクニカルの複合戦略。

### 基本情報
| 項目 | 値 |
|------|-----|
| Magic番号 | **20260020** |
| 注文コメント | `COT_{PAIR}` |
| ループ間隔 | 3600秒（1時間） |
| 最大総ポジション | 3（1ペアにつき1） |
| 最大保有日数 | 14日（max_hold_days強制決済） |
| 稼働ブローカー | oanda（週次シグナルのため1ブローカーで十分） |

### COTデータ仕様
| 項目 | 値 |
|------|-----|
| データ源 | CFTC Socrata API (`publicreporting.cftc.gov/resource/gpe5-46if.json`) |
| レポート種別 | TFF FutOnly（Leveraged Funds Net）|
| COT Index計算 | 156週ローリング (s - min) / (max - min) × 100 |
| 更新タイミング | 毎週金曜 20:30 UTC（火曜引け・3日ラグ） |
| キャッシュ | `cot_cache.json`（7日間有効・金曜夜に自動更新） |
| 強制更新 | `--refresh-cot` フラグ |

### 対象ペア・設定
| ペア | CFTC コード | Sign | COT High閾値 | COT Low閾値 | 有効 |
|------|------------|------|------------|-----------|------|
| EURUSD | 099741 | +1 | >90 → SHORT | <10 → LONG | ✅ |
| GBPUSD | 096742 | +1 | >90 → SHORT | <10 → LONG | ✅ |
| USDJPY | 097741 | -1 | >90 → LONG | <10 → SHORT | ✅ |

**Signの意味:**
- +1: 先物ロングネット増加 = 通貨高 = 価格上昇方向
- -1: JPY先物は USD/JPY と逆向き（JPY先物Long = USDJPY下落）→ 符号反転

### エントリー条件（順番）
1. **COT Index極値確認**: >90（ロング偏り過剰→逆張りSHORT）または <10（ショート偏り過剰→逆張りLONG）
2. **D1 EMA50フィルター**: 終値がEMA50より上 → LONGのみ許可 / EMA50より下 → SHORTのみ許可
3. **ポジション確認**: 同一ペアにポジションなし、総ポジション < 3

### 決済条件
| 条件 | 詳細 |
|------|------|
| TP | エントリーから 3.0×ATR(14, D1) |
| SL | エントリーから 1.5×ATR(14, D1) |
| 強制決済 | 14日経過 → `check_max_hold()` でクローズ |

### バックテスト結果（cot_extreme_bt.py、2023-07-14〜2026-02-27）
| ペア | n | 勝率 | PF | 備考 |
|------|---|------|----|------|
| EURUSD | 16 | 75% | 1.940 | |
| GBPUSD | 17 | 94% | 9.739 | LONG方向特に強い |
| USDJPY | 17 | 71% | 1.958 | |
| **全体** | **50** | **80%** | **1.968** | |

**感度分析（COT閾値）:**
| 閾値 | n | 勝率 | PF |
|-----|---|------|----|
| >90/<10 | 21 | 81% | 6.344 |
| >80/<20 | 50 | 80% | 1.968 |

**注意事項:**
- LONG方向PF=5.888 vs SHORT方向PF=0.983（2023-2026のUSD強含み相場影響）
- SHORT方向の実稼働パフォーマンスを重点監視すること
- 週1〜2回程度のエントリー頻度（低頻度・高期待値型）

### 起動方法
```bat
REM VPS上で実行
C:\Users\Administrator\fx_bot\vps\cot_monitor.bat

REM COTデータ強制更新して起動する場合
pythonw.exe cot_monitor.py --broker oanda --refresh-cot
```

### ログファイル
- `cot_monitor_log_oanda.txt`（メインログ）
- `cot_cache.json`（COTデータキャッシュ）

---

## 14. グリッド戦略 (grid_monitor.py v3)

### 概要
双方向グリッド（Long/Short 同時稼働）。ATR幅でグリッドを形成し、Choppiness Index（レンジ相場フィルター）が高い時のみエントリー。最大7レベルに達した後48時間タイマーで強制決済する B48 Exit を採用。

### 対象ペアと確定パラメータ

| ペア | Magic | atr_mult | max_levels | CI_threshold | Exit | BT PF (Full) | BT n | LOT |
|------|-------|----------|------------|--------------|------|--------------|------|-----|
| NZDUSD | 20260030 | 2.0 | 7 | 61.8 | B48h | — | — | 0.01 |
| GBPJPY | 20260031 | 1.5 | 7 | 61.8 | B48h | 3.857 | 218 | **0.02** |
| CHFJPY | 20260032 | 2.0 | 7 | 61.8 | B48h | IS:1.023/OOS:1.521 | 38 | **0.02** |

### 基本情報

| 項目 | 値 |
|------|-----|
| スクリプト | grid_monitor.py v3 |
| TF | H1（ATR計算）/ D1 resample（CI計算）|
| ロット | ペア別（LOT_PER_PAIR 参照） |
| ループ間隔 | 60秒 |
| ハートビート | 30サイクル毎（約30分） |
| 稼働ブローカー | axiory / exness |

### ロット設計根拠（v3）

| ペア | LOT | BT MaxDD(JPY) | B48最悪ケース(両方向,JPY) | 月次期待PnL |
|------|-----|--------------|--------------------------|------------|
| GBPJPY | 0.02 | ~53,180 | ~42,070 | +6,329 |
| CHFJPY | 0.02 | OOS: ~25,540 | ~45,094 | +1,900 |

- B48最悪ケース = 両方向7レベル全発動時の含み損合計 (Σ0〜6 × gw × 2,000 units)
- CHFJPY full_DD=65k はIS期不調が主因 → OOS_DD=12.77%を実運用基準として採用

### グリッドロジック

| 条件 | 詳細 |
|------|------|
| grid_width | ATR(H1, 14) × atr_mult |
| Long追加 | 最安エントリーから grid_width 下落時 |
| Short追加 | 最高エントリーから grid_width 上昇時 |
| TP | エントリー ± grid_width（1ステップ先） |
| SL | なし |

### エントリーフィルター（新規発注のみ適用）

| 条件 | GBPJPY | CHFJPY | NZDUSD |
|------|--------|--------|--------|
| CI フィルター | CI(D1,14) > 61.8 | CI(D1,14) > 61.8 | CI(D1,14) > 61.8 |
| 日次 DD | ≥ −10,000 JPY | ≥ −10,000 JPY | ≥ −5,000 JPY |
| 週次 DD | ≥ −30,000 JPY | ≥ −30,000 JPY | ≥ −15,000 JPY |

### B48 Exit（最大レベル到達後タイマー決済）

- Long / Short それぞれ独立したタイマー
- max_levels（7）到達時刻を記録
- 48時間経過 → その方向の全ポジションを成行決済
- TP が発火してカウントが max_levels を下回ると → タイマーリセット

### バックテスト結果詳細

**GBPJPY（IS/OOS検証）**

| 期間 | PF | n |
|------|----|---|
| Full | 3.857 | 218 |
| IS（前半） | 2.741 | — |
| OOS WF1 | 1.542 | — |

**CHFJPY（IS/OOS検証）**

| 期間 | PF | n |
|------|----|---|
| IS | 1.023 | — |
| OOS | 1.521 | 38 |

### 起動方法

```bat
REM VPS上で実行
C:\Users\Administrator\fx_bot\vps\grid_monitor.bat

REM 個別ペア・ブローカー起動例
pythonw.exe grid_monitor.py --pair GBPJPY --broker axiory
pythonw.exe grid_monitor.py --pair CHFJPY --broker exness
```

### ログファイル / Stateファイル

| ファイル名 | 説明 |
|-----------|------|
| `grid_log_{PAIR}_{broker}.txt` | 取引ログ（例: grid_log_GBPJPY_axiory.txt） |
| `grid_monitor_state_{PAIR}.json` | B48タイマー・日次/週次PnL永続化 |

### ログ出力仕様

```
entry LONG lot=0.02 price=... grid_width=... level=X/7
tp_close LONG price=... pnl=+XXXX JPY hold=Xh
b48_close LONG positions=X total_pnl=+XXXX JPY
filter_block ci=XX.X (threshold=61.8)
heartbeat alive long_pos=X/7 short_pos=X/7 ci=XX.X
loop_error ...
```

---

## 15. 経済指標戦略 (news_monitor.py v1)

### 概要
ForexFactory JSON から高インパクト経済指標をリアルタイム取得し、B条件(サプライズZスコア)とC条件(値動き確認)の複合シグナルでエントリーするニューストレード戦略。

### 基本情報

| 項目 | 値 |
|------|-----|
| スクリプト | news_monitor.py v1 |
| magic | 20260040 |
| STRATEGY_TAG | NEWS |
| ループ間隔 | 60秒 |
| ハートビート | 30サイクル毎（約30分） |
| 稼働ブローカー | axiory / exness |
| データソース | ForexFactory JSON (今週分) |

### 対象指標・ペア

| 指標 | 通貨 | ペア | 方向 | 条件 |
|------|------|------|------|------|
| Non-Farm Employment Change (NFP) | USD | USDJPY | LONG | USD+サプライズ |
| CPI m/m (US) | USD | USDJPY | LONG | USD+サプライズ |
| CPI m/m (US) | USD | EURUSD | SHORT | USD+サプライズ |
| CPI y/y (GB) | GBP | GBPUSD | LONG | GBP+サプライズ |
| CPI y/y (GB) | GBP | GBPJPY | LONG | GBP+サプライズ |

### エントリー条件 (B+C複合)

| 条件 | 詳細 |
|------|------|
| B条件 | サプライズZ >= surprise_z_th (forecast がある場合に適用) |
| C条件 | 発表後 delay_min 分後の値動き >= move_th_pips (方向一致) |
| エントリー | B AND C (forecast あり) / C のみ (forecast なし) |
| Z計算 | surprise_raw = actual - forecast (なければ actual - previous) |
| Z窓 | 同一指標種別の過去 surprise_window 件 |

### 確定パラメータ (2026-05-24 Mac暫定BT / 1h精度)

| パラメータ | 値 | 備考 |
|-----------|---|------|
| delay_min | 2 | 発表後エントリー遅延(分) |
| move_th_pips | 5.0 | C条件: 値動き閾値(pips) |
| surprise_z_th | 0.5 | B条件: Zスコア閾値 |
| sl_pips | 5.0 | SL(固定pips) |
| rr | 3.0 | TP = move_th × rr |
| hold_max_min | 30 | 最大保有(分) / 強制決済 |
| surprise_window | 12 | Zスコア計算ウィンドウ |
| lot | 0.1 | 最大ロット (rm.calc_lot でリスク計算後にキャップ) |
| max_pos | 1 | 同時エントリー上限 |

**スリッページ想定 (指標発表時)**

| ペア | スリッページ |
|------|------------|
| USDJPY | 3.0 pips |
| EURUSD | 2.0 pips |
| GBPUSD | 2.5 pips |
| GBPJPY | 4.0 pips |

### バックテスト結果 (Mac/1h精度, n=31, 2024-05〜2026-05)

⚠️ forecast=前回値(naive近似)のため精度限定。VPS M1+実forecast取得後に再BT予定。

| 指標 | PF | WR | n |
|------|----|----|---|
| 全体 | 2.146 | 58.1% | 31 |
| NFP (USDJPY) | 5.14 | 67% | 3 |
| US CPI (USDJPY/EURUSD) | 11.13 | 67% | 6 |
| GB CPI (GBPUSD/GBPJPY) | 999 | 100% | 2 |

### パラメータ更新手順 (VPS BT完了後)

```
1. VPS で: python optimizer/news_event_bt.py
2. optimizer/news_bt_result.csv の上位行 (PF>1.3, n>=15) を確認
3. news_monitor.py の PARAMS セクション「BT最適化対象」を上位値で更新
4. コメント「最終更新:」日付を更新
5. git commit/push -> VPS: git pull -> news_monitor.bat で再起動
```

### 起動方法

```bat
REM VPS上で実行 (axiory + exness 同時起動)
C:\Users\Administrator\fx_bot\vps\news_monitor_all.bat

REM 個別ブローカー起動例
pythonw.exe news_monitor.py --broker axiory

REM ロジック確認 (MT5発注なし)
python news_monitor.py --broker axiory --dry-run
```

### ログファイル / Stateファイル

| ファイル名 | 説明 |
|-----------|------|
| `news_monitor_log_axiory.txt` | axiory ログ |
| `news_monitor_log_exness.txt` | exness ログ |
| `news_surprise_cache.json` | Zスコア計算用サプライズ履歴 (共有) |
| `data/news_history_{broker}.csv` | 決済済みトレード履歴 (phase1_judgment.py 互換) |

### ログ出力仕様

```
NEWS scheduled: 2026-06-06_Non-Farm_USD_USDJPY z=1.45 pre_px=154.200 entry_check=12:32:00 UTC
NEWS entry: USDJPY LONG lot=0.1 z=1.45 move=6.2pips entry=154.820 sl=154.570 tp=155.770
NEWS exit:  USDJPY LONG pnl=+15.0pips profit=1500JPY reason=TP
NEWS skip (B cond fail): ... z=0.23 th=0.5
NEWS skip (max_pos): ...
NEWS force-close: USDJPY LONG hold=30.5min ticket=XXXXXXX
heartbeat alive pos=0/1 pending=0 cycle=30
loop error: ...
```
