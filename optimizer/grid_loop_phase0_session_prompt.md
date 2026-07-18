# Grid戦略 生成AIループ Phase 0 実装 — 新規セッション用プロンプト

設計文書: `optimizer/grid_loop_engineering_design.md`（設計確定 2026-07-19）
新規Claude Codeセッションに以下をコピペして開始する。

---

```
# 依頼: Grid戦略「生成AIループ」の基盤実装(Phase 0)

リポジトリ: https://github.com/Iwa110/fx_bot (ローカル作業→commit/push→VPSでgit pull)
ディレクトリ: vps/(稼働ボット) / optimizer/(BT・最適化) / data/(Dukascopy 1h/5m/4h/D1、11-12年分)
コアBT: optimizer/grid_floatstop_bt.py(実機一致をassert済みの唯一の正)
検証規律: IS=2015-21凍結 / OOS=2022-26 / 年次WFO / フルコスト / lookahead排除(t-1 shift・次足始値約定) / 月次ブロックブートストラップMC(20000回/60ヶ月)

## 設計確定事項(厳守)
- 3層分離: 仮説層(LLM) / 実験層(Python) / 反映層(PR・台帳)
- 評価は evaluate_candidate.py の単一経路のみ。grid_floatstop_bt.py と評価パイプラインはループからread-only(変更禁止。機能追加は人間の別セッションのみ)
- 過学習ゲート: (1)IS/OOS PF符号一致+decay率上限 (2)n_trades下限 (3)plateau要件(全パラメータ±1近傍の変動率上限) (4)ファミリー内最良1件ルール(代表=plateau幅最大、IS成績で選ばない)+月次OOSバジェット (5)墓場照合(構造タグ必須、価格パターン単体・低相関のみ理由は禁止)
- 人間レビューはゲート通過後1箇所: 候補カード(Markdown)を review_queue/ にPR提出、approve/reject/holdの3択
- ループ出力上限はdemo候補PRまで。live設定への書込禁止
- 第一弾はgain側1ファミリーのみ(機構検証が目的)

## 今回のスコープ(Phase 0 = ループ基盤のみ。仮説生成はまだやらない)
1. **台帳スキーマ設計と実装** optimizer/loop/ledger.jsonl + 操作モジュール
   - フィールド: hypothesis_id / family_tag / 構造的理由 / パラメータ / status(candidate→gate_passed→approved→demo→live_eligible→live) / 各メトリクス / ゲート判定結果 / Close理由 / OOSバジェット消費記録
2. **evaluate_candidate.py** grid_floatstop_bt.py をラップし IS→OOS→WFO→MC必要資本(grid_stepb_recompute.py相当を後段統合)→ゲート判定→台帳記録 を一本化
   - ゲート閾値は config(YAML/JSON)で外出し。初期値は提案してよいが私が確定する
3. **候補カード生成** 台帳→Markdown 1枚(構造的理由/主要メトリクス/plateau図/ゲート判定/墓場照合/req_cap変化/推奨demo設定)を review_queue/ に出力
4. **read-only保護** コアBT・評価パイプラインの起動時ハッシュ検証
5. 既存Go設定(AUDCAD magic 20260034)を1件、手動で台帳に通すE2Eテスト

## 制約
- コード生成はASCIIクォート(' と ")のみ。スマートクォート禁止
- grid_floatstop_bt.py 本体は変更しない(部分利確・非対称TP対応は別タスクとして私が指示する)
- 各ステップで実装前に設計を短く提示し、私の承認後に実装

まずPhase 0の実装計画(ファイル構成・台帳スキーマ案・ゲート閾値の初期値案)を提示して。
```
