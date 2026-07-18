# Grid戦略 生成AIループ Phase 0 実装 — 新規セッション用プロンプト

設計文書: `optimizer/grid_loop_engineering_design.md`（設計確定 2026-07-19 / 準備タスク#1完了 2026-07-18）
新規Claude Codeセッションに以下をコピペして開始する。

---

```
# 依頼: Grid戦略「生成AIループ」の基盤実装(Phase 0)

## 最初に読むファイル(この順で)
1. optimizer/grid_loop_engineering_design.md (設計文書。特にセクション7=確定事項)
2. optimizer/grid_floatstop_bt.py のdocstring (コアBT。2026-07-18に部分利確ptp_frac/ptp_mult・非対称TP tp_level_mults/tp_mult を追加済み)
3. optimizer/test_grid_floatstop_static.py (静的一致テスト。エンジン凍結の根拠)
4. optimizer/loop/protected_hashes.json (コアBT・テスト・基準JSONのSHA-256凍結値)

## リポジトリ・検証規律
- リポジトリ: https://github.com/Iwa110/fx_bot (ローカル作業→commit/push→VPSでgit pull)
- ディレクトリ: vps/(稼働ボット) / optimizer/(BT・最適化) / data/(1h/5m/4h/D1)
- コアBT: optimizer/grid_floatstop_bt.py が唯一の正。派生BT多数が import grid_floatstop_bt as G で依存
- 検証規律: IS=2015-21凍結 / OOS=2022-26 / 年次WFO / フルコスト / lookahead排除(t-1 shift・次足始値約定) / 月次ブロックブートストラップMC(20000回/60ヶ月, grid_stepb_recompute.py 参照)
- 長期データ: data/AUDCAD_1h_dukas.csv 等のDukascopy 11年ファイルが真値。無ければ optimizer/fetch_dukascopy_ohlc.py で再取得可(専用venv .venv_dukas)。無い環境では2年ファイル(data/AUDCAD_1h.csv)で機構E2Eのみ先行してよい

## 設計確定事項(厳守)
- 3層分離: 仮説層(LLM) / 実験層(Python) / 反映層(PR・台帳)
- 評価は optimizer/loop/evaluate_candidate.py の単一経路のみ
- 過学習ゲート(全通過必須):
  (1) IS/OOS PF符号一致 + decay率(1 - PF_OOS/PF_IS)上限
  (2) n_trades下限(年あたり規定)
  (3) plateau要件(全パラメータ±1ステップ近傍の変動率上限=崖スパイク排除)
  (4) ファミリー内最良1件ルール(OOS評価権は代表1件のみ。代表=plateau幅最大で選ぶ。IS成績では選ばない) + 月次OOS評価バジェットを台帳でカウント
  (5) 墓場照合(構造タグ+構造的/経済的理由フィールド必須。「価格パターン単体」「低相関のみを理由とする採用」はスキーマで禁止)
- 人間レビューはゲート通過後1箇所のみ: 候補カード(Markdown 1枚)を review_queue/ にPR提出、approve/reject/holdの3択
- ループ出力上限はdemo候補PRまで。live設定への書込禁止
- 第一弾の探索ファミリーはgain側1本のみ(非対称TP tp_level_mults または 部分利確 ptp_frac/ptp_mult。コアBTに実装済み)。目的はエッジ改善よりも機構検証

## 前提となる完了済み作業(準備タスク#1, 2026-07-18)
- コアBTに tp_mult / tp_level_mults / ptp_frac+ptp_mult を実装済み。全キー cfg.get 読み取りでデフォルトOFF=既存挙動と完全一致(test_grid_floatstop_static.py で凍結基準に対しOFF/NEUTRAL完全一致・ON発火をassert済み)
- コアBT・テスト・基準JSONは凍結済み: optimizer/loop/protected_hashes.json のSHA-256と照合できる

## 今回のスコープ(Phase 0 = ループ基盤のみ。仮説生成はまだやらない)
1. **台帳スキーマ設計と実装** optimizer/loop/ledger.jsonl + 操作モジュール
   - フィールド: hypothesis_id / family_tag / 構造的理由 / パラメータ / status(candidate→gate_passed→approved→demo→live_eligible→live) / 各メトリクス / ゲート判定結果 / Close理由 / OOSバジェット消費記録
2. **evaluate_candidate.py** grid_floatstop_bt.py をラップし IS→OOS→WFO→MC必要資本(grid_stepb_recompute.py 相当を後段統合)→ゲート判定→台帳記録 を一本化
   - **起動時に protected_hashes.json とコアBT実ファイルのSHA-256を照合し、不一致なら実行拒否**(read-only保護)
   - ゲート閾値は config(YAML/JSON)で外出し。初期値は提案してよいが私が確定する
3. **候補カード生成** 台帳→Markdown 1枚(構造的理由/主要メトリクス/plateau図/ゲート判定/墓場照合/req_cap変化/推奨demo設定)を review_queue/ に出力
4. **E2Eテスト**: AUDCADのコアBTで表現可能なbaseline構成(atr_mult=1.5/max_levels=5/ci65/float_stop=-750k)を1件、手動で台帳→評価→カード生成まで通す
   - 注意: live v8のAUDCAD(magic 20260034)は regime_short/mom/cull/taper 併用だが、これらはコアBT未実装(派生エンジン側)。Phase 0のE2Eはbaseline構成で機構を検証すればよい。コアBTへのv8ツールキット移植が必要になったら準備タスク#2として私に依頼を返すこと(勝手に実装しない)

## 制約
- コード生成はASCIIクォート(' と ")のみ。スマートクォート禁止
- **grid_floatstop_bt.py / test_grid_floatstop_static.py / grid_floatstop_static_baseline.json は変更禁止(凍結済み)**。変更が必要と判断したら作業を止めて理由を提示し私の判断を仰ぐ
- 各ステップで実装前に設計を短く提示し、私の承認後に実装

まずPhase 0の実装計画(ファイル構成・台帳スキーマ案・ゲート閾値の初期値案)を提示して。
```
