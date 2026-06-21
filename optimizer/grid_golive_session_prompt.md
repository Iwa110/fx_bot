# 新規セッション用プロンプト: 確定Grid 4本の実口座 go-live（口座開設→VPS反映→検証）

以下をそのまま新しい Claude Code セッションに貼り付けてください。このプロンプトは
本会話の記憶を持たない前提で自己完結しています。

---

```
確定Grid 4本（AUDCAD/CADCHF/AUDNZD/EURGBP）を demo フォワードテストから「実口座 go-live」へ
進める実作業を案内・実装してほしい。初期投資50万円・複利成長で月利30万を目指す運用の第一歩。

## 最初に必ず読むファイル（数値・構成の正）
- optimizer/grid_deployment_plan.md   ← 運用プラン本体（ステージ表/lot式/§2c 可変risk_fracスケジュール/killswitch）
- optimizer/grid_forward_test_plan.md ← ペア別req_cap・昇格/撤退・未反映の改善2点（§6）
- fx_bot/CLAUDE.md の Top of mind      ← 確定構成・必要資本2.80M・各種結論
- vps/grid_monitor.py                  ← 稼働ボット v8。PAIR_CONFIG/LOT_PER_PAIR/FLOAT_STOP_PER_PAIR
- vps/broker_utils.py                  ← connect_mt5 / is_live_broker / ブローカー資格情報の所在
- vps/restart_grid.ps1                 ← VPS再起動スクリプト

## 重要な前提（誤解厳禁）
- 拘束条件は証拠金でなく req_cap_99（DDに耐える資本）。lot は常に「自己資本÷req_cap基準」でサイズ。
- demo lot=1.0 は BT lot=1.0 と一致させるための検証ロット。実口座は real-money lot に変える。
- demo フォワードテストは止めない。実口座（live broker）と並行で4本回す（評価を溜め続ける）。
- ⚠️実マネー：トレード執行・入金・送金は私（Claude）が行わない。口座開設/入金/最終発注ボタンは
  必ずユーザー本人が実施。私はコード変更・設定・デプロイ手順の案内と検証のみ。

## やること（順序）

### STEP 1. 実口座の準備（ユーザー作業・チェックリストを提示）
- Axiory または Exness の【実口座（real money）】を開設・KYC・初回入金 50万円。
  （現 demo と同じブローカーの本番口座が運用継続しやすい）
- 高レバ口座だが、§0 の通りレバは律速でない。lot は req_cap 基準でしか上げない旨を再確認。
- 実口座の MT5 ログイン情報（login/password/server）を控える（次STEPで VPS に設定）。
- ユーザーに上記をチェックリスト形式で提示し、完了を待ってから STEP2 以降へ進む。

### STEP 2. VPS に live ブローカー接続を設定
- vps/broker_utils.py を読み、demo/live ブローカーの資格情報がどう管理されているか確認
  （is_live_broker の判定・--broker の choices=axiory/exness/oanda）。
- 実口座の MT5 アカウントを live ブローカーとして接続できるよう設定方針を提示。
  資格情報のハードコードは避け、既存の管理方式に合わせる。実際の認証情報入力はユーザーに依頼。
- grid_monitor.py は --broker でアカウントを切替える設計。demo と live を別 --broker キーで
  並行起動できるか（プロセス/ログ/state ファイルが衝突しないか grid_log_{PAIR}_{broker}.txt /
  grid_monitor_state_{PAIR}.json で分離されるか）を確認し、必要なら分離を提案。

### STEP 3. 未反映の DD圧縮改善を適用（grid_forward_test_plan.md §6）
- CADCHF: PAIR_CONFIG の cull_frac を None → 0.6 に変更（req_cap -25%・net/yr↑・nFS17→1。
  grid_capheavy_ddcompress_result.csv で検証済の clean win）。
- EURGBP: float_stop -1,320,000 → -1,720,000（×1.3）+ taper 0.7 → 0.6。
  ⚠️ただし Step B/候補2の base は combo+slot0.5 で、デプロイ実構成は mom120=4+tp0.8 を追加済。
  **デプロイ実構成（mom120/tp0.8込み）上で再検証してから反映**すること
  （.venv_dukas の既存BTスクリプトで確認。新規エッジ探索でなく既存改善の再確認）。
  再検証で clean Pareto win が崩れる場合は EURGBP は据置のまま go-live し、改善は follow-up に回す
  （S0 の薄ロットでは req_cap 圧縮は安全クリティカルでない）。
- 変更は static 一致を壊さないこと。strategy_spec.md / strategy_spec.html も同一コミットで更新（規約）。

### STEP 4. 実口座の初期ロット（S0: risk_frac=0.5）を設定
- サイジング式: `lot = risk_frac × 自己資本 ÷ 742,000 × 等req_cap相対比`
  等req_cap相対比 = AUDCAD 1.0 / CADCHF 0.305 / AUDNZD 0.552 / EURGBP 0.303。
- 自己資本 50万・S0（risk_frac=0.5・全ペアまだ forward-test 未合格）の初期ロット:
    AUDCAD 0.34 / CADCHF 0.10 / AUDNZD 0.19 / EURGBP 0.10  （broker最小0.01刻み）
- これを live broker 用の LOT_PER_PAIR（または live 専用の設定経路）に反映。
  demo 側の LOT_PER_PAIR=1.00 は評価継続のため変更しない（demo と live を分離）。
- magic は既存を踏襲: AUDCAD 20260034 / CADCHF 20260038 / AUDNZD 20260036 / EURGBP 20260035。
  ※実口座とdemoで同一magicでも口座が別なら衝突しないが、broker_utils/接続単位で分離されることを確認。

### STEP 5. デプロイ（commit/push → VPS反映 → 再起動）
- ローカルで commit/push（コーディング規約: ASCIIクォートのみ・magic体系維持）。
  ブランチ運用に従い、main へのマージ確認まで行う。
- VPS（Windows Server 2022, C:\Users\Administrator\fx_bot\）での手順をユーザーに案内:
    1. git pull origin main
    2. powershell -ExecutionPolicy Bypass -File C:\Users\Administrator\fx_bot\vps\restart_grid.ps1
  ※PowerShell コマンドは文字化け防止で UTF-8 指定（chcp 65001）を含める。
  ※live broker を起動対象に含める方法（restart_grid.ps1 の --broker 展開）を確認し、必要なら
    スクリプト側に live ブローカーの起動分岐を追加提案。

### STEP 6. 検証（go-live 直後）
- live ログ grid_log_{PAIR}_{live_broker}.txt で:
    - connected broker=... login=<実口座番号> を確認（demo でなく実口座に繋がっているか）
    - heartbeat alive pos=X/Y が30分毎に出るか
    - 初回の建玉が出た場合、ロットが S0 設定（0.34/0.10/0.19/0.10）通りか
- demo 側 4本も従来通り稼働継続しているか（評価が止まっていないか）を確認。
- ⚠️Grid はアイドルが正常（高CIレンジ窓でのみ建つ）。建玉ゼロが続いてもバグと即断しない
    （AUDCAD は2026-06に3週間ゼロ=ゲート設計通りの実績）。4本とも60日完全ゼロなら CI計算/
    データ供給を点検（grid_deployment_plan.md §6-5）。

### STEP 7. 運用ループの確認（昇格/降格）
- grid_deployment_plan.md §2c の S0→S1→S2 可変 risk_frac スケジュールをユーザーと確認:
    - S0(rf0.5) → §3 forward-test 合格（3ヶ月∧TP≥30件∧float-stop/B48が最低1回発火∧実現PF>1.2）
      でそのペアを S1(rf1.0) へ。さらに3ヶ月維持で任意 S2(rf1.25-1.5、絶対上限1.5)。
    - 昇格判定はペア単位。降格もする（撤退条件 §7 に触れたら rf を一段下げる/停止）。
    - 複利: 月初に前月の【実現】損益を自己資本に反映し全ペア lot 再計算（含み益は使わない）。
    - 急ぐなら入金優先（rf を上げずに scale を上げる最も安全な加速）→ それでも足りなければ S2。

## 進め方
- STEP1（口座開設・入金）はユーザー作業。チェックリストを出して完了を待つ。
- STEP2-6 のコード/設定変更は私が実装し、VPS実行コマンドはユーザーに案内。
- 各 STEP 完了ごとに何を確認すべきか明示。実マネーなので不可逆操作の前は必ず確認を取る。
- 新規エッジ探索・新規パラメータBTは不要（構成は確定済み）。STEP3 の EURGBP 再検証のみ既存BT。
```

---

## 補足（このプロンプトを渡す人＝あなたへ）
- このプロンプトは「go-live の第一歩」用。実口座開設・入金は**あなた自身の操作**が必要です。
- 急ぎたい場合の加速順序は **①入金（最安全）→ ②各ペア昇格後に risk_frac を S1→S2 へ**。
  レバ（risk_frac>1）は forward-test で FS発火まで確認してから。絶対上限 1.5（§2b の絶壁回避）。
- go-live は全ペア S0（risk_frac=0.5・薄ロット）開始。req_cap 圧縮（STEP3）は資本効率の話で、
  S0 の薄ロットでは安全クリティカルでないため、EURGBP 再検証が長引くなら据置で go-live して可。
