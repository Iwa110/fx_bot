# FX日次分析プロンプト（軽量ゲート版）

**使い方**: 自動ルーティン（毎朝 scheduled）/ 手動ともに本手順を実行する。
source of truth は `CLAUDE.md` Top of mind。flags 判定ロジックは `optimizer/daily_gate.py` に集約。

---

## STEP 0: ゲート実行（必須・これだけで終わるのが通常日）

```bash
git pull origin main
python3 optimizer/daily_gate.py
```

出力 JSON: `{"date", "flags": [{"type","detail"}...], "summary", "context"}`

- **flags が空** → `summary` をそのまま PushNotification で通知して**終了**。追加分析・ファイル読込はしない。
- **flags がある** → 下記「flag別の分析」のうち**該当 type の項目だけ**実施し、通知する。

---

## flag別の分析（該当 type のみ）

| type | 条件 | やること |
|------|------|----------|
| `script_error` | daily_step1 / grid_gate_review / mr_forward_review の異常終了、history.csv/snapshot 解析失敗 | `detail` のエラーを確認し原因（CSV破損・列追加・依存）を特定。直せる場合は修正案を示す |
| `fs_slippage` | LIVE Grid の FS 実損 > 設定(FLOAT_STOP×LIVE_LOT)×1.3 | 当該バスケットのレッグを history.csv で確認、ギャップ/週明け起因か執行不整合かを判定。plan撤退基準(FS>設定×1.5)への抵触有無 |
| `unexpected_trade` | LIVE で確定Grid 4本以外の magic/symbol（既知手動取引は除外済） | 取引内容を提示し、手動か bot 誤作動か要確認とユーザーに問う。bot起因なら停止推奨 |
| `margin` | LIVE ストップアウト `[so ...]` / `account_snapshot.csv` 維持率<150% | 発生時刻・建玉状況を確認し、ロット/入金の具体策を提示 |
| `mr_kill` | MR_AC 12moPF<1.0 or 実現maxDD>MC95(≒43万円) | `python3 optimizer/mr_forward_review.py` で詳細確認 → 停止&前提再検証を推奨 |
| `grid_promote` | LIVE Grid ペアが 3ヶ月∧TP≥30∧FS発火∧PF>1.2 を達成 | 当該ペアの S0→S1（lot×1.25, `vps/grid_monitor.py` LIVE_LOT_PER_PAIR）移行を提案 |

詳細が必要な時のみ `python3 optimizer/daily_gate.py --verbose`（3スクリプトの生出力を同梱）を使う。

**原則**: Grid アイドル（CI未達）は設計通りで異常ではない。損失/大DD時期は予測不能。BT探索は墓場確定済＝新戦略提案はしない。

---

## 通知（PushNotification, 200字以内）

- flags 空: `summary` をそのまま送る。
- flags あり: `summary` の末尾を「最重要点1行」（例: `AUDCAD FS実損15万>設定×1.3・週明けギャップ`）に置き換えて送る。

---

## 参照（変更時のみ更新）

- LIVE 想定 magic/symbol: AUDCAD 20260034 / CADCHF 20260038 / AUDNZD 20260036 / EURGBP 20260035（`daily_gate.py` LIVE_GRID）
- 既知手動取引の除外リスト: `daily_gate.py` KNOWN_MANUAL（CLAUDE.md「既知の手動取引」と同期）
- 維持率: VPS が `optimizer/account_snapshot.csv`（`date_jst,broker,margin_level`）を出力すれば自動判定。未出力なら `margin_level: na`（stop-out 検知のみ有効）
