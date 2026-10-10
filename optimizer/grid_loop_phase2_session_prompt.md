# Grid戦略 生成AIループ Phase 2 実装 — 新規セッション用プロンプト（自律実行版）

設計文書: `optimizer/grid_loop_engineering_design.md`（設計確定 2026-07-19）
Phase 0完了: `optimizer/grid_loop_phase0_session_prompt.md`（基盤実装・2026-07-19完了）
Phase 1完了: `optimizer/grid_loop_phase1_session_prompt.md`（初回仮説ラン・2026-07-19完了、commit f289d50でmainにpush済み）

**2026-10-10 更新**: 本ファイルは2026-07-19作成時点では未実行のまま残っていた。ユーザーが夜間（翌朝まで）
自律的に継続できる形で実行することを明示的に承認した上で、下記の内容に更新した（旧版との差分は
「今回のスコープ」以降・特に自律実行の境界線）。新規Claude Codeセッション（ローカル/cloud問わず）に
以下をコピペして開始する。

---

```
# 依頼: Grid戦略「生成AIループ」— 2件目の仮説ファミリー(Phase 2, 自律実行)

## 最初に読むファイル(この順で)
1. optimizer/grid_loop_engineering_design.md (設計文書。セクション7=確定事項、特に7.5の出力上限)
2. optimizer/grid_loop_phase1_session_prompt.md (Phase1の依頼内容)
3. optimizer/loop/evaluate_candidate.py のdocstring + optimizer/loop/gate_config.json (ゲート閾値)
4. optimizer/loop/known_baselines.json (対象4ペアのbaseline cfg)
5. optimizer/loop/graveyard.json (既Closeファミリーの登録内容)
6. optimizer/loop/ledger.jsonl (本番台帳。Phase1で28仮説・32レコードが記録済み)

## リポジトリ状態の確認(最初に必ず実施)
- `git fetch origin && git status` でローカルがmain最新に追いついているか確認し、古ければ
  `git pull --ff-only origin main`で追いつかせる(コンフリクトする場合は作業を止めて報告)。
- `python3 optimizer/loop/hash_guard.py` を単体実行してコアBTの凍結ハッシュが一致することを確認する。
- `cd optimizer/loop && python3 -c "import ledger; print(ledger.oos_budget_used('<今月のYYYY-MM>', 'ledger.jsonl'))"`
  で**実行時点の暦月**の消費済みバジェットを必ず自分で算出すること。**過去の記録(CLAUDE.md等)に書かれた
  「残り3件」は2026-07時点の値で古い**。gate4は暦月`YYYY-MM`単位でリセットされ、2026-10-10時点では
  2026-10の消費は0件(2026-07の1件はカウント対象外)。実行する月によって再計算すること。

## 前提となる完了済み作業(Phase 0+1, 2026-07-19完了)
- Phase0: `optimizer/loop/`基盤(ledger.py/gates.py/mc_capital.py/evaluate_candidate.py/card.py/hash_guard.py)実装済み。
  コアBTに`tp_mult`/`tp_level_mults`/`ptp_frac`+`ptp_mult`実装済み・静的一致テストでSHA-256凍結済み。
- Phase1: gain側ファミリー`grid_ladder_depth_asymmetric_tp`(`tp_level_mults`, ラダー深さ別非対称TP)を
  AUDCAD/CADCHF/EURGBP/AUDNZDでexplore→confirm。**4ペア全てgate6(WFO年次最小PF≥1.0)で不合格、gate_passedゼロ**
  (詳細はledger.jsonlのH0005/H0010/H0016/H0027参照)。
  - AUDCAD: gate1-5全PASS・decay-0.10(OOSの方が良い)だが2026部分年fold(n=39)のみPF0.91で不合格。最も惜しい結果。
  - CADCHF/EURGBP: それぞれ2024/2022の**通年**foldがPF<1.0で不合格(部分年でない実質的な脆弱性)。
  - AUDNZD: coreBT表現(v8のregime_short/mom/cull/taper抜き)のbaseline自体がIS<1.0の既知の限界ペア。gate1にも抵触。
- gate4(月次OOSバジェットの数え方)とgate5(墓場照合のスコープ)はPhase1中に(family_tag)単位/(family_tag,pair)単位へ
  訂正済み。**gate4は「同一family_tagを何ペアで確認しても月1件」**(`ledger.oos_budget_used`のdocstringに実装根拠あり、
  2026-10-10時点で実コードを読んで再確認済み)。つまり**ptp_frac+ptp_mult を4ペア全部でconfirmしても、消費は
  family_tag単位で1件のみ**。Phase2旧版が想定していた「4ペア回すと3件しか残っていないので1ペアは見送り」という
  前提は誤り(gate4の実装はペア単位でなくfamily_tag単位でカウントする)。**今月バジェット4件のうち使うのは実質1件
  (ptp_frac+ptp_mult という1ファミリー)。4ペア全部を今夜実行してよい**。

## 解決済みの設計論点(2026-10-10、ユーザーが即答済み。変更不要)
Phase1でAUDCADの唯一の不合格原因は2026年の薄い部分年WFO fold(n=39, PF0.91)だった。「gate6のWFO判定で60日以上
ある部分年foldをそのまま含める現状仕様(`evaluate_candidate.annual_wfo_folds`, `MIN_FOLD_DAYS=60`)を変えるか」を
ユーザーに確認済み: **「現状維持」**(部分年でも弱いなら弱いというシグナルを額面通り受け取る)。
**`gate_config.json`/`evaluate_candidate.py`のゲートロジックは一切変更しないこと。**

## 今回のスコープ(Phase 2 = 2件目のgain側ファミリー、4ペア×自律実行)

### 1. ファミリー定義(承認済み、以下で進めてよい・構造的理由の案はあるが自分で精査し必要なら調整してよい)
`ptp_frac`+`ptp_mult` (部分利確)。`grid_floatstop_bt.py`のセマンティクス:
`ptp_mult`=部分利確ターゲットの距離(グリッド幅gw×この倍率, デフォルト0.5)、`ptp_frac`=そのターゲットに到達した時に
決済するレッグlotの割合(0<f<1, デフォルトNone=OFF)。残りのlotは通常のTPへ向けて保有継続。

**構造的理由の下書き(そのまま使うか、精査の上で調整すること。gate5は「価格パターン単体」「低相関のみ」を
明示的に禁止パターンとしている=これらの語を含めない・それらに実質的に等しい理由にしないこと)**:
「グリッドの各レッグは平均回帰が部分的に進行した時点(グリッド幅の一部)で既に往復の一部を捕捉している。
その時点でlotの一部を確定させることで、再度の逆行(価格が元の往復を割り込み未実現益を消す/float_stopへ近づく)
に対する当該レッグの残存エクスポージャーを下げつつ、逆行せず回帰が継続した場合は残りlotが通常TPまでの
捕捉を維持する。これはAUDCAD 3段階Z-score平均回帰エンジンで判明した『回帰速度(T_reg)と決済スタイル(一括 vs
部分利確)の対応関係』[[project_mr_exit_depth_scaleout_20260716]]と機構的に同根だが、**あちらは別エンジン
(z-score・絶対水準でないラダー)での結果であり、同じ結論(速い回帰→部分利確が有利/遅い回帰→一括が有利)が
Gridのラダー深さという別の構造軸でも成立するかは未検証で、むしろ逆(部分利確はnet-worseだった)の可能性も
示されている。それを確認することが本仮説の目的である」という誠実な記述にすること。結論を先取りしない。

### 2. パラメータグリッド(提案。IS結果を見て再センタリングしてよい。内点3点以上必須)
2段階sweep(1段目でptp_fracを探索、2段目でその代表値を固定しptp_multを探索。両方ともexploreであり
budgetは消費しない。budgetを消費するのはconfirmのみ):
- **Stage A** (`param`="ptp_frac", `extra_params`={"ptp_mult": 0.5}): 候補値 `[0.2, 0.35, 0.5, 0.65, 0.8]`
  (center_index=2)。
- **Stage B** (`param`="ptp_mult", `extra_params`={"ptp_frac": <Stage Aの代表値>}): 候補値
  `[0.3, 0.4, 0.5, 0.6, 0.7]` (center_index=2)。
- Stage A/Bそれぞれの代表値(plateau最良点)を組み合わせた`{"ptp_frac": ..., "ptp_mult": ...}`を最終paramsとし、
  その組み合わせでconfirmを実行する(confirmは`--hypothesis-id`で単一hypothesisを指定する仕様のため、Stage Bの
  代表hypothesisのparamsがStage Aの代表値を正しく含んでいることを確認してから`confirm`すること)。
- AUDCADでStage A→Bを実行し組合せを確定したら、**同じ組合せ(同一parasm)をそのままCADCHF/EURGBP/AUDNZDにも
  適用してconfirmしてよい**(Phase1と同じやり方: 再チューニングなしでの転移性確認。ペア毎に別のStage A/Bを
  回し直す必要はない。むしろ再チューニングすると過適合のリスクが増すため、AUDCAD基準の固定paramsを他3ペアに
  そのまま当てるのが望ましい)。但しAUDCADのStage A/B結果(IS PF)が妥当な範囲(gate1のis_pf_min=1.0近辺以上)に
  収まらない場合は、センタリングを1回だけ再調整してよい(過度な試行錯誤はしないこと)。

### 3. 実行順序
1. AUDCADでStage A explore → 代表値確認 → Stage B explore → 代表値確認。
2. 確定した`{ptp_frac, ptp_mult}`でAUDCADをconfirm(budgetを1件消費、以降CADCHF/EURGBP/AUDNZDは同一family_tagなので
   追加消費なし)。
3. 同一paramsでCADCHF/EURGBP/AUDNZDをconfirm(explore省略可。baseが各ペアのbaseline、paramsはAUDCAD確定値で固定)。
4. gate_passedになった仮説があれば`evaluate_candidate.py card`で`review_queue/`にカード生成。closedも正直に記録
   すること(ゲートを緩めて通そうとしない)。

## 自律実行の境界線(今回は人間が夜間不在。ここが従来のPhase1/2プロンプトとの最大の違い)
- **上記1-4は承認済みなので、ファミリー選定やペア配分について止まって確認を求める必要はない**(gate6部分年論点も
  解決済み)。実行しながら以下のみ厳守すること:
  - `grid_floatstop_bt.py` / `test_grid_floatstop_static.py` / `grid_floatstop_static_baseline.json` は変更禁止
    (`protected_hashes.json`との不一致はhash_guardが検知し実行を拒否する。もし拒否されたら、コアBTを書き換えようと
    せず作業を止めて報告すること)。
  - `gate_config.json`の閾値・`known_baselines.json`の既存4ペア設定は変更しないこと。
  - ライブ設定ファイル(`vps/`配下)への書き込みは一切禁止。
  - コード生成はASCIIクォート(' と ")のみ。スマートクォート禁止。
  - **mainへの直接push・PRのmergeは禁止**。作業は新しいブランチ(例:
    `grid-loop-phase2-ptp-frac-ptp-mult`)で行い、完了したら**PRを作成するところまでは自分で進めてよい**
    (`grid_loop_engineering_design.md`7.5「ループの出力上限=demo forward-test候補のPR作成まで」のとおり)。
    **ただしPRのmergeは絶対に行わないこと**(人間が翌朝レビューして判断する)。
  - PR本文には最低限: ①4ペアそれぞれのIS/OOS/WFO/6ゲート判定結果の一覧 ②gate_passed/closedの内訳と
    closedの場合はどのゲートで落ちたか ③今月の消費budget(1件/4件になるはず) ④gate_passedが1件以上あれば
    strategy_spec.md/CLAUDE.mdへの追記案(CLAUDE.mdのTop of mind節の既存エントリの書式に合わせる)を含めること。
    gate_passedがゼロでも正直にその旨とclose理由をまとめること(過去の探索と同じく「不採用でも記録として価値がある」)。
  - **作業中に判断に迷う・想定外のエラーが出た場合**: コアBTや既存ゲート設定の変更が必要だと判断したらそこで
    作業を止めてPR説明(またはブランチ上のメモファイル)にその旨を明記し、変更せずに次に進めるタスクがあれば
    それを優先する。人間が不在でも安全側に倒すこと(=疑わしい時は変更せず記録して先に進む)。
  - 全ステップ完了後(またはこれ以上進められない状態になった後)は新たな作業を始めず待機してよい。

このプロンプト自体が承認済みの実行指示であり、人間は夜間不在で応答できない。追加の確認は求めず、
Stage A explore(AUDCAD)から直ちに実行を開始して。
```
