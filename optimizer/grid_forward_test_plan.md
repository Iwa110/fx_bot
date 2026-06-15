# 確定Grid forward-test 昇格/撤退プラン（2026-06-15）

確定Grid 4本（AUDCAD/CADCHF/AUDNZD/EURGBP）の forward-test 手順を1枚に集約。
実マネー投入は全構成BT由来のため **forward-test 完了が前提**。carry系（USDJPY/NZDJPY）は
P(5yr損)17-23%・スケール禁止のため本プランの分散バスケットには含めない（micro-lot蓄積のみ）。

数値根拠: `optimizer/grid_joint_stepb.py`（暦月基盤 Step B, lot=1.0）/ `grid_capheavy_ddcompress.py`（DD圧縮）。
検証規律・MC手法は既存（IS=2015-21凍結→OOS/WFO, 月次ブロックブートストラップ20000/60mo/block3）。

---

## 1. ペア別構成・必要資本テーブル（暦月基盤・lot=1.0）

| ペア | Tier | magic / tag | 現行v8デプロイ構成 | **推奨デプロイ構成**(候補2改善後) | req_cap_99/lot | net/yr/lot | P(5yr損) |
|---|---|---|---|---|---:|---:|---:|
| AUDCAD | 1 | 20260034 / GRID_AUC | R-SMA1200+combo (atr1.5/lv5/ci65/fs-750k) | 同左（変更なし） | **691k** | 501k | 0.000 |
| CADCHF | 2 | 20260038 / GRID_CDC | R-SMA1200 (atr1.5/lv5/ci65/fs-943k) | **+cull0.6** を追加 | **2.27M**（現3.03M） | 900k | 0.009 |
| AUDNZD | 2 | 20260036 / GRID_AUN | R-SMA1200+combo (atr1.5/lv5/ci65/fs-625k) | 同左（変更なし） | **1.25M** | 137k | 0.131 |
| EURGBP | 2 | 20260035 / GRID_EUG | combo+slot0.5+mom120=4+tp0.8 (fs-1.32M) | **fs×1.3(-1.72M)+taper0.6** | **2.28M**（現3.43M） | 339k | 0.085 |

注:
- req_cap_99/net/yr は**暦月基盤**（休眠月0埋め＝継続運用時の honest 値）。published/CLAUDE.md旧表は活動月basis（net/yr楽観・約2倍）。
- CADCHF/EURGBP の「推奨」は候補2のDD圧縮（IS-selectable∧全fold>1.0∧OOS維持を満たす clean Pareto）。**v8未反映＝次回vps更新で適用**。
- ⚠️ EURGBP: Step B/候補2 のbaseは `combo+slot0.5`。**デプロイv8は mom120=4+tp0.8 を追加**しており Step B 構成と差異あり。fs×1.3+taper0.6 を本番投入前に**デプロイ実構成（mom120/tp0.8込み）上で再検証**すること。

## 2. 安全lot（ロットサイジング）

- **per-pair**: `安全lot = (そのペアへの配分資本) ÷ (req_cap_99/lot)`。
- **分散バスケット（推奨）= 等req_cap配分**: 相対lot比 AUDCAD 1.0 / CADCHF 0.305 / AUDNZD 0.552 / EURGBP 0.303。
  - この比でのバスケット req_cap_99 = **0.74M / (AUDCAD lot=1単位)**、capEff 1.29、P(5yr損)0.000。
  - **月利30万円（360万/yr）= 必要資本 2.80M**（AUDCAD単独4.96Mの0.56倍、単純合算10.4Mの27%）。
    対応lot: AUDCAD≈3.77 / CADCHF≈1.15 / AUDNZD≈2.08 / EURGBP≈1.14。
- **資本最小ならAUDCAD集中**（単独 capEff 0.73, 月30万で4.96M）、**頑健性なら分散バスケット**（2.80M）。
- 口座資本に対する安全lot（分散バスケット, 相対比維持）: `スケール = 自己資本 ÷ 742k`（742k=AUDCAD lot=1単位でのバスケットreq_cap_99）。各ペアlot = スケール × 相対比。
  - 100万 → スケール≈1.35（AUDCAD≈1.35/CADCHF≈0.41/AUDNZD≈0.74/EURGBP≈0.41、想定月収≈10.7万）
  - 300万 → スケール≈4.04（想定月収≈32万）、 500万 → スケール≈6.74（想定月収≈54万）。

## 3. 昇格（forward-test 合格）条件 — 全て満たす

1. 稼働 **3ヶ月以上**。
2. **TP決済 ≥ 30件**（薄標本回避）。
3. **float-stop / B48 が最低1回発火**（損切り発火前の黒字は生存者バイアス。CHFJPY教訓）。
4. 発火後も **実現PF > 1.2**。
5. FSスリッページ ≤ 設定の1.3倍（ギャップ貫通が想定内）。

合格後にロットを漸増（自己資本÷req_capの範囲内）。Tier順に投入: **Tier1 AUDCAD最優先** → Tier2（CADCHF/AUDNZD/EURGBP）を分散目的で追加。

## 4. 撤退条件 — いずれか該当で停止/縮小

- 実現DD > MC中央値（暦月基盤）。各ペアの MC中央DD は `grid_joint_stepb.py` 出力で確認。
- float-stop 発火後の実現PF < 1.0。
- FSスリッページ > 設定の1.5倍。
- AUDNZD/EURGBP は P(5yr損)が高め（0.085-0.131）＝撤退基準をより厳格に運用。

## 5. ペア別 forward-test 状態（2026-06-15）

| ペア | 状態 | 開始条件 |
|---|---|---|
| AUDCAD | forward-test 継続中（Go筆頭） | 既稼働。lot=自己資本÷691k の範囲で漸増。 |
| CADCHF | **新Go・未開始** | v8デプロイ済→稼働開始。cull0.6 反映後が望ましい。3ヶ月∧TP≥30∧FS発火∧PF>1.2。 |
| AUDNZD | forward-test 継続中（限界的） | 既稼働。撤退基準厳格。 |
| EURGBP | forward-test 継続中 | 既稼働。fs×1.3+taper0.6 はデプロイ実構成上で再検証後に反映。 |

## 6. 次アクション（候補3=vps実装, 別セッション）

- v8は既に4本デプロイ済（CADCHF magic20260038含む, cull/taper/R-SMA1200/mom実機ロジック実装済）。
- **未反映の改善2点を次回vps更新で適用**:
  1. CADCHF に `cull_frac=0.6` を追加（req_cap -25%, net/yr↑, nFS17→1）。
  2. EURGBP の float_stop を -1.32M → **-1.72M（×1.3）** + `taper=0.6`（req_cap -32%, OOS/WFO↑）。
     ※ デプロイ実構成（mom120/tp0.8込み）での再検証を先に行う。
- `strategy_spec.md` / `strategy_spec.html` も同時更新（規約）。
