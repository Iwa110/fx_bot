# Grid戦略 生成AIループ Phase 2 実装 — 新規セッション用プロンプト

設計文書: `optimizer/grid_loop_engineering_design.md`(設計確定 2026-07-19)
Phase 0完了: `optimizer/grid_loop_phase0_session_prompt.md`(基盤実装・2026-07-19完了)
Phase 1完了: `optimizer/grid_loop_phase1_session_prompt.md`(初回仮説ラン・2026-07-19完了、commit f289d50でmainにpush済み)
新規Claude Codeセッションに以下をコピペして開始する。

---

```
# 依頼: Grid戦略「生成AIループ」— 2件目の仮説ファミリー(Phase 2)

## 最初に読むファイル(この順で)
1. optimizer/grid_loop_engineering_design.md (設計文書。セクション7=確定事項)
2. optimizer/grid_loop_phase1_session_prompt.md (Phase1の依頼内容)
3. optimizer/loop/evaluate_candidate.py のdocstring + optimizer/loop/gate_config.json (ゲート閾値)
4. optimizer/loop/known_baselines.json (対象4ペアのbaseline cfg)
5. optimizer/loop/graveyard.json (既Closeファミリーの登録内容)
6. optimizer/loop/ledger.jsonl (本番台帳。Phase1で28仮説・32レコードが記録済み)

## リポジトリ状態の確認(最初に必ず実施)
- mainブランチは2026-07-19時点でcommit f289d50まで進んでおり、Phase0基盤・Phase1データ・gate4/gate5修正・
  CLAUDE.md記録が全てmainに反映済み。**作業開始前に`git fetch origin && git status`でローカルがこの状態に
  追いついているか確認すること**。過去のセッションでこの確認を怠り、Phase0成果物が未マージのブランチに
  取り残されているのに気づかず「存在しない」と誤認して混乱した事故が起きている。ローカルが古ければ
  `git pull --ff-only origin main`で追いつかせる(それでもコンフリクトする場合は作業を止めて報告)。
- `python3 optimizer/loop/hash_guard.py`を単体実行してコアBTの凍結ハッシュが一致することを確認する。

## 前提となる完了済み作業(Phase 0+1, 2026-07-19完了)
- Phase0: `optimizer/loop/`基盤(ledger.py/gates.py/mc_capital.py/evaluate_candidate.py/card.py/hash_guard.py)実装済み。
  コアBTに`tp_mult`/`tp_level_mults`/`ptp_frac`+`ptp_mult`実装済み・静的一致テストでSHA-256凍結済み。
- Phase1: gain側ファミリー`grid_ladder_depth_asymmetric_tp`(`tp_level_mults`, ラダー深さ別非対称TP)を
  AUDCAD/CADCHF/EURGBP/AUDNZDでexplore→confirm。**4ペア全てgate6(WFO年次最小PF≥1.0)で不合格、gate_passedゼロ**
  (詳細はCLAUDE.md Top of mind 2026-07-19付、またはledger.jsonlのH0005/H0010/H0016/H0027参照)。
  - AUDCAD: gate1-5全PASS・decay-0.10(OOSの方が良い)だが2026部分年fold(n=39)のみPF0.91で不合格。最も惜しい結果。
  - CADCHF/EURGBP: それぞれ2024/2022の**通年**foldがPF<1.0で不合格(部分年でない実質的な脆弱性)。
  - AUDNZD: coreBT表現(v8のregime_short/mom/cull/taper抜き)のbaseline自体がIS<1.0の既知の限界ペア。gate1にも抵触。
- **gate4/gate5をPhase1中に修正**(設計意図はそのまま、実装ギャップの訂正):
  - gate4: 月次OOSバジェットのカウントをhypothesis単位→**family_tag単位**に変更(同一ファミリーを複数ペアで
    検証しても1件)。
  - gate5: 台帳由来のclosed-family判定を**(family_tag, pair)にスコープ**(1ペアの確定closeが他ペアのexploreを
    ブロックしない)。**ただし同一(family_tag, pair)の再closeは引き続きブロックされる**=例えばAUDCADに対して
    `grid_ladder_depth_asymmetric_tp`を別のsteepness範囲で再探索したい場合、現在のfamily_tagのままではgate5に
    弾かれる。これは意図的な挙動(同一ファミリー内での際限ないパラメータ再挑戦=多重検定リスクを防ぐ)。
    再挑戦したい場合は新しい構造的理由を伴う別のfamily_tagとして扱うこと。
- **今月(2026-07)のOOSバジェット消費**: 1/4(`grid_ladder_depth_asymmetric_tp`で1件消費)。**残り3件**。
  月が変われば暦月`YYYY-MM`単位でリセットされる(`ledger.oos_budget_used`実装参照)。セッション実施日の月に
  応じて残バジェットを都度確認すること。

## 未解決の設計論点(このセッションで先に判断を仰ぐこと。勝手に変更しない)
AUDCADはgate1-5全PASSで、gate6も**n=39の部分年fold1本だけ**が不合格の原因だった(他4年は全て>1.0、decayは
OOSの方が良好)。現在のgate6実装(`evaluate_candidate.annual_wfo_folds`, `MIN_FOLD_DAYS=60`)は60日以上あれば
部分年でもそのままWFO判定に含めており、これが薄い部分年フォールド1本で有望な候補を落としている可能性がある。
この扱い(部分年を除外する/加重を下げる/最低取引数フロアを設ける等)を変えるべきか、それとも「部分年でも弱い
なら弱いというシグナルを額面通り受け取るのが正しい」で現状維持が適切か、着手前に確認すること。
`gate_config.json`や`gates.py`の変更が必要と判断したら、他の変更と同様に一度止めて提示すること。

## 今回のスコープ(Phase 2 = 2件目のgain側ファミリー)
1. **`ptp_frac`+`ptp_mult`(部分利確)ファミリーを新規に設計**。Phase0で使ったのは機構検証用のダミー構造理由
   (`test_evaluate_candidate_e2e.py`のtmp ledger)であり本番投入はまだ。本物の構造的理由を書くこと
   (「価格パターン単体」「低相関のみ」等のgate5禁止パターンに抵触しないこと)。
2. AUDCADでexplore→confirmをまず実行(`--ledger optimizer/loop/ledger.jsonl`、本番パス)。パラメータグリッドは
   内点3点以上・5点以上を推奨。
3. 残り3件のOOSバジェットに収まる範囲でCADCHF/EURGBP/AUDNZDにも展開するか判断(4ペア全部だと3件しか残って
   いないため、どれか1ペアは今月見送りになる)。どのペアを優先するか、または今月はAUDCAD/CADCHF/EURGBPの3ペア
   に絞るかを先に提示し承認を得ること。
4. gate_passedになった仮説があればカードを`review_queue/`に生成。closedも正直に記録すること(ゲートを緩めて
   通そうとしない)。
5. PRは明示的な許可なしに作成しないこと。カード生成までで一度立ち止まり、結果を一覧でまとめて提示し、
   PR作成の可否・CLAUDE.md更新・push可否を確認すること。

## 制約
- コード生成はASCIIクォート(' と ")のみ。スマートクォート禁止。
- `grid_floatstop_bt.py` / `test_grid_floatstop_static.py` / `grid_floatstop_static_baseline.json`は変更禁止。
- `gate_config.json`の閾値・`known_baselines.json`の既存4ペア設定は変更しないこと(上記のWFO部分年論点を除く。
  それも私の判断を仰いでから)。
- ライブ設定ファイル(`vps/`配下)への書き込みは一切禁止。

まず、①WFO部分年fold論点への回答待ちである旨、②`ptp_frac`ファミリーの構造的理由とパラメータグリッド案、
③今月実行する対象ペア(バジェット3件の配分案)を短く提示し、私の承認後に実行を開始して。
```
