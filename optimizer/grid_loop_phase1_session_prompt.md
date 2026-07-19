# Grid戦略 生成AIループ Phase 1 実装 — 新規セッション用プロンプト

設計文書: `optimizer/grid_loop_engineering_design.md`（設計確定 2026-07-19）
Phase 0完了: `optimizer/grid_loop_phase0_session_prompt.md`（基盤実装・2026-07-19完了）
新規Claude Codeセッションに以下をコピペして開始する。

---

```
# 依頼: Grid戦略「生成AIループ」— 初の実仮説ラン(Phase 1)

## 最初に読むファイル(この順で)
1. optimizer/grid_loop_engineering_design.md (設計文書。セクション7=確定事項)
2. optimizer/grid_loop_phase0_session_prompt.md (Phase 0の依頼内容)
3. optimizer/loop/evaluate_candidate.py のdocstring + optimizer/loop/gate_config.json (ゲート閾値)
4. optimizer/loop/known_baselines.json (対象4ペアのbaseline cfg)
5. optimizer/loop/graveyard.json (既Closeファミリーの登録内容)

## 前提となる完了済み作業(Phase 0, 2026-07-19完了)
- `optimizer/loop/` に台帳(ledger.py)・6ゲート判定(gates.py, 過学習ゲート5種+wfo_min_pf)・MC必要資本(mc_capital.py)・
  評価CLI(evaluate_candidate.py: explore/confirm/card サブコマンド)・カード生成(card.py)・ハッシュガード(hash_guard.py)を実装済み。
- ゲート閾値確定済み(`gate_config.json`): gate1 is_pf_min=1.0/oos_pf_min=1.2/decay_max=0.5、
  gate2 n_trades_per_year_min=15、gate3 plateau変動率上限30%、gate4 月次OOSバジェット上限4件(全ファミリー合算)、
  gate5 墓場照合(structural_reason最低20文字+禁止パターン+graveyard.json)、gate6 wfo_min_pf>=1.0。
- `known_baselines.json` に確定Go4ペア(AUDCAD/CADCHF/EURGBP/AUDNZD)のbaseline cfgを登録済み
  (コアBT表現可能な範囲のみ=atr_mult/ci_threshold/max_levels/float_stop/quote_jpy。v8のregime_short/mom/cull/taperは未対応)。
- **Dukascopy 11年データを4ペア全て取得済み**: `data/{AUDCAD,CADCHF,EURGBP,AUDNZD}_1h_dukas.csv`
  (2015-07-22〜2026-07-17、各約68,450〜68,513本、.gitignore対象だが`git add -f`で強制コミット済み)。
- **実データでのE2E検証はAUDCADのみ実施**: `explore`→`confirm`→`card`を通し、IS n=804件・OOS n=704件で
  本物のIS(2015-21)/OOS(2022-26)/年次WFO/MCパイプラインが動作することを確認。このときの仮説(ptp_frac機構検証用)は
  gate6(wfo_min_pf, 2026年foldがPF0.71)で不合格=正しく`closed`。**CADCHF/EURGBP/AUDNZDは実データでのconfirm未実施**
  (known_baselines.jsonへの登録のみ)。
- Phase 0で使ったledgerは全て`--ledger`で指定した使い捨てtempファイル。**リポジトリ本番の`optimizer/loop/ledger.jsonl`は
  まだ存在しない**(実運用の台帳としてはこのセッションが実質1件目)。
- `review_queue/`は`.gitkeep`のみでカードは未コミット。

## リポジトリ・検証規律
- リポジトリ: https://github.com/Iwa110/fx_bot (ローカル作業→commit/push→VPSでgit pull)
- コアBT: `optimizer/grid_floatstop_bt.py`。**変更禁止(凍結済み)**。`optimizer/loop/protected_hashes.json`と
  SHA-256が一致することを`evaluate_candidate.py`が毎回起動時に検証する(read-only保護)。
- 検証規律: IS=2015-21 / OOS=2022-26 / 年次WFO / フルコスト / lookahead排除 / 月次ブロックブートストラップMC。既にコードに実装済み、変更不要。

## 今回のスコープ(Phase 1 = 初の実仮説ラン。機構検証ではなく本番運用)
1. **gain側ファミリーを1本選定**: `tp_level_mults`(非対称TP)または`ptp_frac`+`ptp_mult`(部分利確)の**どちらか一方**
   (設計書7.4「第一弾の探索ファミリーはgain側1本のみ」)。Phase 0のptp_frac機構検証とは別物として扱い、
   **本物の構造的理由**(価格パターン単体でも低相関のみでもない、グリッドのラダー深さ・平均回帰の構造に根差した理由)を
   `structural_reason`に記述すること。gate5(墓場照合: 最低20文字+禁止パターン+graveyard.json照合)を通る内容にすること。
2. **AUDCADでexplore→confirmをまず実行**(実データでの動作実績がある唯一のペア)。パラメータグリッドは
   内点(plateau判定に必要な両隣接点)が3点以上になるよう5点以上を推奨。
   `--ledger optimizer/loop/ledger.jsonl`(本番パス、使い捨てtempではない)で実行すること。
3. **CADCHF/EURGBP/AUDNZDでも同様にexplore→confirmを実行**(同一family_tag、pairのみ変更)。
   **注意**: 月次OOSバジェット上限は全ファミリー合算で4件/月。4ペア全てをconfirmすると
   **今月分のバジェットを使い切る**。これが意図通りか(初回バッチとして4件使う)は自分で判断してよいが、
   バジェット消費が発生することを実行前に明記しておくこと。
4. gate_passedになった仮説があれば`evaluate_candidate.py card`でカードを`review_queue/`に生成。
   closedになった仮説も正直に記録されるはず(それが正常。ゲートを緩めて通そうとしないこと)。
5. **PRは私の明示的な許可なしに作成しないこと**。設計書7.5の「ループ出力上限=demo候補PRまで」は将来の自動化の
   到達点であり、今回のセッションでは**カード生成までで一度立ち止まり**、生成されたカードの内容(構造的理由・
   IS/OOS/WFO・ゲート判定・req_cap変化)をセッション終了時にまとめて提示し、PR作成の可否を私に確認すること。
6. 4ペア分の実行結果(gate_passed/closedの内訳、各ペアのIS/OOS/WFO要点)を最後に一覧でまとめること。

## 制約
- コード生成はASCIIクォート(' と ")のみ。スマートクォート禁止。
- `grid_floatstop_bt.py` / `test_grid_floatstop_static.py` / `grid_floatstop_static_baseline.json`は変更禁止。
- `gate_config.json`の閾値・`known_baselines.json`の既存4ペア設定は変更しないこと。変更が必要と判断したら
  作業を止めて理由を提示し私の判断を仰ぐこと。
- `known_baselines.json`のfloat_stop/quote_jpy値は設計書由来の初期値であり、`grid_stepb_recompute.py`等の
  既存資産との数値的な突き合わせは未実施。今回の実行で違和感のある結果(req_cap等)が出た場合は、
  その旨を指摘した上で処理を続けてよい(値の書き換えは私の判断を仰ぐ)。
- ライブ設定ファイル(`vps/`配下)への書き込みは一切禁止。

まず、選定するgain側ファミリー(非対称TP or 部分利確)とその構造的理由、パラメータグリッド案を短く提示し、
私の承認後に実行を開始して。
```
