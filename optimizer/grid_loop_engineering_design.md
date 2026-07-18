# Grid戦略 生成AIループエンジニアリング — 設計文書（2026-07-19 確定）

生成AI（Claude Code）を回す「ループエンジニアリング」で Grid戦略を継続的に改善するシステムの設計文書。
セクション0-6 = 検討開始時の背景・論点（Claudeチャット検討用プロンプト原文）。
セクション7 = チャット検討を経た **設計確定事項**。

---

## 0. 検討の進め方
いきなり案を出さず、まず「このループシステムに必要な要素」を洗い出し、論点を詰めてから設計に入る。特に **過学習をどう機械的に防ぐか** を最重要制約として扱う（理由は後述の"墓場"参照）。

## 1. プロジェクト全体像
- FX自動売買システム。VPS(Windows Server 2022)で複数戦略を並行稼働。**月利30万円が目標**。
- リポジトリ: https://github.com/Iwa110/fx_bot (Public)。VPS更新フロー = ローカルで commit/push → VPS側で `git pull`。
- ディレクトリ: `vps/`(稼働ボット) / `optimizer/`(バックテスト・最適化) / `data/`(14ペア 1h/5m足 + Dukascopy長期データ)。
- 稼働戦略は複数あるが、**検証の結果、長期で頑健なエッジが確認できたのは「相関クロスのGrid平均回帰」がほぼ唯一**（他はBB逆張り/SMA Squeeze/順張り/stat_arb/crypto拡張など軒並みClose）。よってリソースはGridに集約している。

## 2. Grid戦略の現状仕様（`vps/grid_monitor.py` v8）
- **ロジック**: タイムフレーム H1。`grid_width = ATR(H1,14) * atr_mult`。SLなし。エントリーフィルタ = Choppiness Index(D1,14) > ci_threshold（レンジ相場のみ稼働）。無ポジ→建て、価格が min/max ± grid_width 超で追加。各レッグ TP = entry ± grid_width * tp_mult。
- **決済**: per-leg TP / float-stop（バスケット含み損 < float_stop）/ B48タイマー(48h) / worst-leg cull / DD日次・週次ブレーカー。
- **v8で追加した per-pair ノブ**（すべてBT検証済み、デフォルトOFFでv7互換）:
  - `mom_thr`(24h ATR正規化リターンゲート=不利方向トレンドへの追加建て抑止)、`mom120_thr`(120h版)、`cull_frac`(worst-legの段階的手仕舞い)、`taper`(深レッグのロット減衰)、`dir_mode`('both'/'long_only'/'regime_short')、`sma_period`(regime_short用)、`short_lot_mult`、`tp_mult`。
- **確定Go 4ペア + forward-test構成**（magic / 主要パラメータ）:
  - **AUDCAD** 20260034: R-SMA1200+combo, atr1.5/lv5/ci65/fs-750k, regime_short(1200)+mom2.0/cull0.5/taper0.7。**最優先・資本効率最高**(Step B req_cap_99≈734k)。
  - **CADCHF** 20260038: R-SMA1200(combo OFF)+cull0.6, atr1.5/lv5/ci65/fs-943k。2026-06-15にスクリーニングで発見した4本目のGo。
  - **EURGBP** 20260035: combo+short_lot0.5+mom120=4+tp0.8, atr1.5/lv5/ci65/fs-1.32M(req≈4.2M)。
  - **AUDNZD** 20260036: R-SMA1200+combo, atr1.5/lv5/ci65/fs-625k(限界的)。
  - carry系(**USDJPY** 20260037 / **NZDJPY** 20260033)は long-only+comboだが **スケール禁止**(carry-crashテールで高DD)。
  - Legacy/No-Go(GBPJPY/CHFJPY/NZDUSD)は magic保存のためv7挙動で残置、forward-testしない。
- **必要資本(月利30万・暦月honest基盤)**: DD圧縮後の等req_cap分散バスケットで **約2.80M**（AUDCAD単独4.96Mの0.56倍）。

## 3. VPS・自動売買システム構成
- VPS: Windows Server 2022。MT5端末で複数ブローカー(axiory / exness、oanda停止中)に接続。
- 実行: Task Scheduler / bat・ps1(`restart_grid.ps1`等)。grid_monitorはH1足確定時/5分pollで動作。
- 状態管理: `grid_monitor_state_{PAIR}.json`、ログ `grid_log_{PAIR}_{broker}.txt`、ゲート判断ログ `grid_gate_log.csv`。
- 実約定履歴は `history.csv` に集約(sync_history)。demo口座で forward-test中、live投入は forward-test完了(3ヶ月∧TP≥30∧float-stop最低1回発火∧実現PF>1.2)が前提。

## 4. バックテスト・検証インフラ（`optimizer/`）
- **真値データ = Dukascopy**（実tick由来。yfinanceはヒゲ過小報告でPF過大というバグを確認済み）。1h/5m/4h/D1を11〜12年分取得済み(`fetch_dukascopy_ohlc.py`)。
- **Grid専用BTエンジン** `grid_floatstop_bt.py`（float-stop/B48/cull/taper/dir_mode等を実機と一致させて再現、静的一致をassertで検証）。派生に `grid_dd_reduction_bt.py` / `grid_dirbias_improve_bt.py` / `grid_toolkit_allpairs_bt.py` / `grid_stepb_recompute.py`(MC必要資本) など多数。
- **検証規律（厳守）**: IS=2015-21凍結 → OOS=2022-26 → 年次WFO → フルコスト(スプレッド往復差引) → Lookahead排除(特徴量t-1 shift/次足始値約定) → 月次ブロックブートストラップMC(20000回/60ヶ月)で必要資本・破産確率。

## 5. これまでの"墓場"から得た最重要教訓（ループ設計の制約になる）
- **価格パターン単体に頑健エッジは無い**。効くのは構造的/経済的理由を持つもの(相関クロス=同一ドライバ→独立トレンド無し→構造的レンジ)。
- **過学習のsignature**: ①IS↔OOSのPF符号反転/無相関 ②薄標本(n過小)での高PF ③崖状パラメータ(隣接値で崩壊)スパイク。これらを出す改善は全てClose してきた。
- **サイジング/動的化はエッジ生成器ではない**（PF不変でDDだけスケール）。**ゲート/フィルタは"いつ張るか"は制御できてもエッジは作れない**。
- **「相関≈0だがエッジ無し」を何度も踏んだ**(配分A/レジームB/三角stat_arb/trend補完)。低相関は採用理由にしない。
- 残る上積み余地として自認しているのは **gain側(ラダー深さ別の非対称TP / 部分利確)とエントリー精度(平均回帰確認エントリー)** 程度。

## 6. 検討時に設定した論点（→ セクション7で確定）
1. **ループの全体アーキテクチャ**: Claude Code(またはAgent)が「仮説生成→BT実装→検証規律に沿った評価→採否判定→CLAUDE.md/コードへの反映」を自律的に回す構成。既存の `optimizer/` 資産と検証規律をどう組み込むか。
2. **過学習ガードレールの自動化**: 過学習signature(IS↔OOS逆相関・薄標本・崖スパイク)を **機械的な採否ゲート**として実装する方法。人間のレビューをどこに挟むか。
3. **仮説の探索空間**: 枯渇気味のエッジ探索で、ループに何を探索させるか(既存ノブの組合せ最適化 / gain側の未探索領域 / 新ペアスクリーニング等)。墓場の再訪を避ける仕組み。
4. **ループの終了条件・コスト管理**: 何をもって1イテレーション完了/ループ全体終了とするか。無駄なAPI消費を避ける設計。
5. **成果物の反映と安全性**: BT採用 → demo forward-test → live という段階的投入ゲートを、ループにどう組み込むか(勝手にlive投入しない安全弁)。

---

## 7. 設計確定事項（2026-07-19）

### 7.1 全体アーキテクチャ: 3層分離
- **仮説層**(LLM/Claude Code) / **実験層**(Python評価パイプライン) / **反映層**(PR・台帳)を分離。
- 評価は `optimizer/loop/evaluate_candidate.py` の**単一経路のみ**。コアBT `grid_floatstop_bt.py` を唯一の正とし、IS→OOS→WFO→フルコスト→MC必要資本(stepb相当を後段ステージとして統合)→ゲート判定→台帳記録を一本のパイプラインで実行。
- **コアBT・評価パイプラインはループからread-only**(ブランチ保護+起動時ハッシュ検証)。機能追加は人間のみ、静的一致テスト追加を必須とする。
- **実験台帳(ledger)**: 全仮説・パラメータ・結果・Close理由を構造化記録(JSON/JSONL)。"墓場"のデータ化であり、ループの状態の中核。CLAUDE.md=方針、台帳=事実。

### 7.2 過学習ゲート(機械判定・全通過が必須)
1. **IS/OOS整合**: PF符号一致 かつ decay率(1 - PF_OOS/PF_IS)が上限以下。
2. **標本数**: バスケット単位の n_trades 下限(年あたりで規定)。
3. **plateau要件**: 全パラメータ±1ステップ近傍でのメトリクス変動率が上限以下(崖スパイク排除)。
4. **多重検定対策(確定)**: **ファミリー内最良1件ルール**を主軸。仮説は構造タグでファミリー化し、IS内探索の後、**plateau幅最大(頑健性基準。IS成績では選ばない)**の代表1件のみがOOS評価権を得る。保険として**月次OOS評価バジェット**(上限回数)を台帳でカウント。deflated Sharpe等の試行数補正指標は**参考記録のみ**とし採否には使わない。
5. **墓場照合**: 仮説スキーマに構造タグと「構造的/経済的理由」フィールドを必須化。台帳の既Closeファミリーと照合し再訪を弾く。「価格パターン単体」「低相関のみを理由とする採用」はスキーマ禁止。

### 7.3 人間レビュー(1箇所・軽量)
- 位置は**全ゲート通過後・demo投入前の1箇所のみ**。
- ループが台帳から**候補カード**(Markdown 1枚)を自動生成し `review_queue/` + PRとして提出。カード内容: 構造的理由 / IS・OOSのPF・decay・n / plateau図 / ゲート判定一覧 / 墓場照合結果 / req_cap変化 / 推奨demo設定。
- 人間の操作は3択のみ: **approve**(PRマージ=demo投入) / **reject**(理由1行→台帳へ自動記録) / **hold**。週1バッチ、1件5分目標。

### 7.4 探索空間・終了条件・コスト
- **第一弾スコープ(確定)**: gain側1ファミリーのみ(非対称TP または 部分利確のどちらか)。目的はエッジ改善よりも**台帳・ゲート・カード生成という機構自体の検証**。
- 1イテレーション = 1仮説の「実装→ハーネス評価→台帳記録」。週N仮説のバジェット制。
- 全体終了: 連続M件Close(枯渇判定)、または改善幅がreq_cap/DDでx%未満に収束。
- コスト設計: パラメータスイープはローカルPython(API不要)。LLMの役割は実装と結果解釈に限定。

### 7.5 反映と安全弁
- ループの出力上限 = **demo forward-test候補のPR作成まで**。live設定ディレクトリへの書込権限は物理分離(別ディレクトリ+ブランチ保護)。
- 台帳ステータス遷移: `candidate → gate_passed → approved → demo → live_eligible → live`。live_eligible判定は既存基準(3ヶ月∧TP≥30∧float-stop最低1回発火∧実現PF>1.2)を自動判定、**liveへの遷移だけは人間の明示操作のみ**。
- **準備タスク#1(人間側・ループ稼働前)**: コアBT `grid_floatstop_bt.py` に部分利確・非対称TP(ラダー深さ別TP)を実装し、静的一致テストを追加する。

---

実装フェーズ(Phase 0)の開始プロンプト: `optimizer/grid_loop_phase0_session_prompt.md`
