# FX Bot 戦略仕様書

生成日: 2026-05-13  
対象: `C:\Users\Administrator\fx_bot\vps\` 以下のPythonスクリプト

---

## 目次

1. [BB逆張り戦略](#1-bb逆張り戦略-bb_monitorpy-v19)
2. [MOM モメンタム順張り](#2-mom-モメンタム順張り-daily_tradepy)
3. [CORR 平均回帰](#3-corr-平均回帰-daily_tradepy)
4. [STR 通貨強弱](#4-str-通貨強弱-daily_tradepy)
5. [TRI 三角裁定](#5-tri-三角裁定-daily_tradepy)
6. [SMA Squeeze Play](#6-sma-squeeze-play-sma_squeezepy-v2)
7. [stat_arb 統計的裁定](#7-stat_arb-統計的裁定-stat_arb_monitorpy)
8. [TOD 時間帯別平均回帰](#8-tod-時間帯別平均回帰-tod_monitorpy)
9. [200MA Pinbar](#9-200ma-pinbar-ma200_pin_barpy)
10. [SMC_GBPAUD](#10-smc_gbpaud-trail_monitorpy-管理)
11. [トレーリングストップ共通仕様](#11-トレーリングストップ共通仕様-trail_monitorpy)
12. [共通リスク管理](#12-共通リスク管理-risk_managerpy)

---

## 1. BB逆張り戦略 (bb_monitor.py v20)

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

| ペア | 有効 | JPY系 | σ値 | SL倍率(ATR) | ペア別フィルター | 時間帯制限(UTC) |
|------|------|-------|-----|-------------|-----------------|----------------|
| GBPJPY | ✅ | ✓ | 1.5 | 3.0 | なし (v20: F1削除) | 制限なし |
| USDJPY | ✅ | ✓ | 2.0 | 3.0 | なし (v20: F1削除) | 制限なし |
| EURUSD | 停止中 | - | 1.5 | 1.2 | なし | 全停止 (v20) |
| GBPUSD | 停止中 | - | 1.5 | 1.2 | なし | 全停止 (v20) |
| EURJPY | 停止中 | ✓ | 1.5 | 2.5 | F1andF2 (mom=5, div=10p) | 9h,17h |
| USDCAD | 停止中 | - | 1.5 | 1.5 | なし | 全停止 |

**v20停止理由:**
- EURUSD: BT全組合せ最高PF=0.681 (N=132)、BB戦略との相性不良
- GBPUSD: 実稼働PF=0.397、BT最高PF=0.854 (N=153)、目標未達

### エントリー条件（フィルター適用順）

**Step 1: 1時間足レンジフィルター**
- 1h足 BB(period=20, σ=1.5) のσ位置を確認
- |σ| > 1.0 → トレンド判定でスキップ（レンジ外）

**Step 2: 5分足BBタッチ確認**
- BB(period=20, σ=ペア別) の終値比較
- 終値 >= upper → SELL方向
- 終値 <= lower → BUY方向

**Step 3: RSIフィルター (RSI14)**
- BUY方向: RSI ≤ 40
- SELL方向: RSI ≥ 60

**Step 4: ペア別追加フィルター**
- v20: GBPJPY/USDJPY ともに filter_type=None（htf4h後はF1追加効果ゼロのためシンプル化）

| フィルター | 内容 |
|-----------|------|
| F1 Momentum | 直近N本の終値モメンタム（BUY→下落中、SELL→上昇中） |
| F2 Divergence | 合成レートとの乖離確認（JPYペアのみ: EURJPY=EURUSD×USDJPY、GBPJPY=GBPUSD×USDJPY） |
| F1andF2 / F2andF1 | 両フィルターAND結合（評価順が異なる） |

**Step 5: 4時間足EMA20フィルター（GBPJPY/USDJPY）**
- 4h終値 > EMA20 → BUYのみ許可
- 4h終値 < EMA20 → SELLのみ許可

### 決済条件
- **TP**: ATR(H1 14本)×3.0 → trail_monitorがStage2/Stage3でSLを移動
- **SL**: ATR(H1 14本)×sl_atr_mult（ペア別、上表参照）
- trail_monitorによるトレーリングストップ管理（→ §11参照）

### ロット計算
- 基本: 残高×1.5% ÷ (SL価格差 × 100,000 × レート) → 0.01単位切り捨て
- 上限: 0.2lot / 下限: 0.01lot
- JPYペア: MAX_JPY_LOT=0.4 超過はスキップ

### その他のリスク管理
- クールダウン: SL後15分間同ペアのエントリーをスキップ
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

## 6. SMA Squeeze Play (sma_squeeze.py v3)

### 概要
SMA200スロープフィルター + SMAスクイーズ解放でトレンドフォロー。v3で日足SMAスロープフィルター追加・COOLDOWN_MIN 60→180分・GBPUSD停止。

### 基本情報
| 項目 | 値 |
|------|-----|
| Magic番号 | 20260010 |
| 注文コメント | `SMA_SQ_{PAIR}` (例: `SMA_SQ_USDJPY`) |
| 実行間隔 | 60秒ループ |
| 最大総ポジション | 5（ペア数に合わせてBTと一致。各ペア最大1ポジション） |
| クールダウン | 180分/ペア (v3: 60→180) |

### 対象通貨ペア・パラメータ

| ペア | 有効 | TF | SMA短期 | SMA長期 | squeeze_th | slope_period | RR | SL倍率 | daily_sma | daily_sp | BT PF(フィルター後) |
|------|------|-----|--------|--------|-----------|-------------|-----|-------|-----------|----------|---------------------|
| USDJPY | ✅ | 4h | 25 | 150 | 2.0% | 5 | 2.5 | 1.5 | 20 | 3 | 1.928 |
| GBPJPY | ✅ | 1h | 25 | 250 | 0.5% | 10 | 2.0 | 1.5 | 20 | 3 | 1.522 |
| EURUSD | ✅ | 4h | 25 | 200 | 2.0% | 10 | 2.5 | 1.0 | 50 | 3 | 2.831 |
| GBPUSD | ✅ | 1h | 15 | 250 | 1.5% | 20 | 2.0 | 1.0 | 20 | 5 | 1.372 |
| EURJPY | ✅ | 4h | 15 | 150 | 2.0% | 20 | 2.5 | 1.5 | 20 | 5 | 3.748 |

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
| TP | エントリー ± ATR14×sl_atr_mult×rr | 自動 |
| SL | エントリー ± ATR14×sl_atr_mult | 自動 |
| 強制決済 (force-close) | SMA_long逆ブレイク (LONG→終値<SMA_long) | 最優先 |
| A-1 slope-exit | SMA_long傾き反転 (slope_exit=3本, v2) | 次優先 |
| B-1 BE move | 利益 >= 0.5×SL距離でSLを建値に移動 (be_r=0.5, v2) | 常時監視 |

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

## 11. トレーリングストップ共通仕様 (trail_monitor.py v12)

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
| BB_GBPJPY | ✅ | ATR×**1.0** | ATR×1.2 | ATR×0.8 |
| BB_USDJPY | ✅ | ATR×**0.7** | ATR×1.2 | ATR×0.8 |
| BB_EURUSD | ✅ | ATR×**0.1** | ATR×1.2 | ATR×0.8 |
| BB_GBPUSD | ✅ | ATR×**1.0** | ATR×1.2 | ATR×0.8 |
| BB_EURJPY | ✅ | ATR×**0.7** | ATR×1.2 | ATR×0.8 |
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
