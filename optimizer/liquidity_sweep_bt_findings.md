# Liquidity Sweep (流動性スイープ・逆張り) バックテスト 早期切り分けレポート

スクリプト: `optimizer/liquidity_sweep_bt.py`
結果CSV : `optimizer/liquidity_sweep_bt_result.csv`
実行日 : 2026-06-29

## 1. 実装内容 (仕様書準拠)
- 基準ライン: 前日足の PDH/PDL を `shift(1)` で当日へ付与 (lookahead 排除)。
- セッション窓: データtz=UTC の hour で可変 (`--sess-start/--sess-end`)。既定 06-16 UTC ≒ 日本時間 15:00-25:00 (London前後〜NY前後)。
- エントリー: スイープ(ライン抜け)+ rejection(終値レンジ内回帰)を確定足で確認 → **次足始値**で成行。
- SL: スイープ足極値 ±X pips。TP: `rr`(固定RR) / `mid`(レンジ中央値) / `opposite`(反対ライン)を切替。
- コスト: spread+slippage を往復 pips で差引 (クロス既定 2.0-2.5pip)。
- 同足で SL/TP 両ヒット時は SL 優先(保守的)。1ポジション同時保有(ピラミッディングなし)。

## 2. データ上の制約 (重要)
- 本リモート環境は network policy で **Dukascopy 取得不可**(`freeserv.dukascopy.com` が 403)。
- そのため 15m/5m データを新規取得できず、**リポジトリ同梱の 1h(yfinance, 2024-04〜2026-06 の約2年)で検証**。
- 検証できたペアは **AUDCAD / EURGBP のみ**(AUDNZD / CADCHF は同梱データなし)。
- 本戦略はバー内の sweep+rejection を捉える性質上、本来 5m/15m が前提。**1h は粗く保守的(過小評価寄り)**。
  Dukascopy にアクセスできる環境で `--tf 15m --pairs AUDCAD EURGBP AUDNZD CADCHF` を再実行すれば本来の精度で評価可能(fetch スクリプトに 15m 対応を追加済み)。

## 3. 早期切り分け: スイープ確認 on/off × セッション窓 on/off
焦点 = この2フィルタが Sneaky Pivot 型(確認なしフェード)の PF を反転/改善させるか。

### tp_mode=opposite (反対ライン回帰 = レンジ回帰, 最良) / 1h
| pair | sweep | session | PF | net(pip) | n | WR |
|---|---|---|---:|---:|---:|---:|
| AUDCAD | ON | ON | **0.914** | -374 | 351 | 30.2% |
| AUDCAD | ON | OFF | 0.686 | -2419 | 600 | 25.3% |
| AUDCAD | OFF | ON | 0.755 | -1869 | 673 | 16.3% |
| AUDCAD | OFF | OFF | 0.688 | -3331 | 913 | 14.9% |
| EURGBP | ON | ON | 0.696 | -770 | 304 | 30.3% |
| EURGBP | OFF | OFF | 0.673 | -1796 | 778 | 17.2% |

### tp_mode=rr 1.5 / 1h
| pair | sweep | session | PF |
|---|---|---|---:|
| AUDCAD | ON | ON | 0.721 |
| AUDCAD | OFF | OFF | 0.739 |
| EURGBP | ON | ON | 0.631 |
| EURGBP | OFF | OFF | 0.582 |

## 4. 結論
1. **2フィルタの方向性は仮説どおり有効**: ほぼ全セルで `sweep=ON > OFF`・`session=ON > OFF`。
   特に **rejection 確認 + London/NY 窓 + レンジ回帰TP(opposite)** の組合せが一貫して PF/DD を最も改善し、
   AUDCAD では PF 0.69→**0.91**(net -3331→-374pip)へ。Sneaky Pivot 型(確認なしフェード)よりは明確に優位。
2. **だが絶対 PF は <1.0 = 1h では頑健エッジ未確認**。最良の AUDCAD opposite+両フィルタでも 0.914 止まり。
   IS/OOS でも両期 <1.0(AUDCAD opposite は IS0.89/OOS0.94 と損益分岐近傍だが正にならず)。
3. これは CLAUDE.md の既存結論(FXメジャー/クロスの 1h 逆張り/順張りに頑健エッジ無し、効くのは
   相関クロスの Grid 平均回帰のみ)と整合。**1h ではフィルタで損失を圧縮できるが正エッジは作れない**。
4. **次アクション**: 5m/15m データを取得できる環境で再検証するのが本筋。閾値が損益分岐に近い AUDCAD(opposite TP)は
   細かい足で改善余地があり得るが、1h の現時点では実投入候補にならない。リソースは確定 Grid 4本へ集約継続。
