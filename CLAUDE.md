# FX Bot - Claude Code Context

## プロジェクト概要
FX自動売買システム。VPS(Windows Server 2022)で複数戦略を並行稼働。
月利30万円目標。現在Phase1完了判定フェーズ→Phase2移行検討中。

## ディレクトリ構成
```
C:\Users\Administrator\fx_bot\
├── vps\          # 稼働中ボット
│   ├── bb_monitor.py      # v27 magic=20250001
│   ├── trail_monitor.py   # v15
│   ├── smc_gbpaud.py      # v4 magic=20260002
│   ├── stat_arb.py        # magic=20260001
│   ├── sma_squeeze.py     # v4.5 magic=20260010 (trailing無効化 / USDJPY・EURUSD有効)
│   └── news_monitor.py    # v1 magic=20260040 (経済指標B+C複合戦略)
├── optimizer\    # バックテスト・最適化
│   ├── loop_runner.py
│   ├── backtest.py
│   ├── evaluate.py
│   ├── phase2_ai_analysis.py
│   ├── sma_squeeze_bt.py             # エントリーパラメータ最適化BT
│   ├── sma_squeeze_exit_bt.py        # 決済パラメータ最適化BT
│   └── sma_squeeze_daily_filter_bt.py
└── data\         # 14ペア 1h/5m足
```

## 戦略別現状

### BB戦略
- PAIRS: GBPJPY/USDJPY/EURUSD/GBPUSD (USDCADは停止中)
- GBPJPY bb_sigma: v27で1.5→2.0（BT全データPF: 1.019→1.275）
- USDJPY: bb_sigma=2.0、T_max=8h+exp TP Decay(τ=8h)
- EURJPY: bb_sigma=1.5、T_max=6h
- 実RR乖離の原因確定: 実機はH1足ATR(比率≈3.7倍)、BT誤差あり
- **10年BT(2016-2026, Dukascopy 5m, v29)結論: 全3ペア頑健エッジ無し**（下記Top of mind参照）。IS/OOS両方でPF>1.2の頑健条件を満たすペアは皆無。BBは長期では均衡〜負。実マネーのUSDJPY黒字(PF1.42)は短期窓の現象であり10年では再現しない。

#### ★ EURJPY バグポジション事件（2026-06-05〜06-08）記録
- **根本原因（v29修正済）**: `is_in_cooldown` が `DEAL_REASON_SL` のみ対象だったため、T_max強制決済（`DEAL_REASON_EXPERT` / comment=`BB_time_stop`）後にクールダウンが発動せず、未反転BBシグナルへ毎分(Task Scheduler)即時再エントリー→T_max決済→再エントリーを反復。
- **発生日時**: 2026-06-05 UTC 11〜12時（NFP発表時間帯）
- **被害**: EURJPY magic=20250001 で71件・-549,300円（同日USDJPY+1,660円で実質-547,638円）
- **バグポジション決済**: 2026-06-08 JST 00:12 に magic=0（手動/外部決済）で59件・+1,331,620円として決済
  - 1件あたり22,320〜22,980円の均一利益（sellグリッドが相場下落でTP一斉ヒット）
  - この利益は実力ではなく偶発的な相場動向による回収
- **v29修正内容**: `is_in_cooldown` に `BB_time_stop` コメント判定を追加。SL/T_max どちらの決済後も `COOLDOWN_MINUTES(15分)` の再エントリー禁止を適用。2026-06-05 実装・push済み。
- **EURJPY nリセット**: バグ71件は評価対象外として除外。**正常サンプル n=9（PF=0.254, WR=66.7%）からリセット**。Phase1は n=9 から再蓄積。

### trail_monitor v15
- STR/MOM_JPYペア別設定分離済み
- SMC_GBPAUD追加(activate=1.0, distance=0.7, Sell専用)

### SMC_GBPAUD v4
- Sell専用、TF=1h/HTF=1d、Session=8-20UTC、MAX_POS=1

### stat_arb
- GBPJPY/USDJPY・EURUSD/GBPUSD、MAX_POS=2ペア

### SMA Squeeze Play v4.5（稼働中、2026-06-02更新）
- magic=20260010、STRATEGY_TAG='SMA_SQ'
- **有効ペア**: USDJPY / EURUSD
- **停止ペア**: GBPJPY（v4.5: BT PF=0.999<1.2 even w/o trail + 実稼働損失）、GBPUSD（BT PF<1.0）、EURJPY（実稼働WR=0% -9,900円）
- ブローカー: axiory/exness（oanda停止中）
- ロジック: SMA200スロープフィルタ + SMAスクイーズ解放エントリー + 日足フィルター
  - エントリー条件: ADX14>20、divergence_rate≤squeeze_th、SMAスロープ単調 + 日足SMA方向一致
  - 決済: ATR×sl_atr_mult でSL、SL×rr でTP、SMA長期ブレイク強制決済 / slope-exit=3
  - **ATR trailing無効**: atr_trail_mult=0.0（全ペア）。v4.5で無効化（intrabar trailing が RR勝ちトレードを早期カットしていた真因）
  - **T_max=24h**: USDJPY/EURUSDで最大保有24時間超過で強制成行決済
  - クールダウン: 180分/ペア、MAX_TOTAL_POS=3、MAX_JPY_LOT=0.4
- 監視: heartbeat log 30分毎（`heartbeat alive pos=X/Y`）

## GitHub運用
- Repo: https://github.com/Iwa110/fx_bot (Public)
- VPS更新フロー: commit/push → VPS側でgit pull
- Raw URL: https://raw.githubusercontent.com/Iwa110/fx_bot/main/

## コーディング規約
- ASCIIクォートのみ(' と ")、スマートクォート禁止
- Pythonファイルのmagic番号体系を維持すること

## Top of mind（2026-07-11 更新）

### ★crypto拡張C0-C3: 候補A(ETH/BTC比率MR)=構造的エッジ皆無でClose / 候補B(ファンディング・ベーシス)=本物だが税+海外リスクで薄すぎ・海外拡張は非推奨（2026-07-11）
FX botの土日無稼働を埋めるcrypto拡張。設計はChatで確定(memory `[[project_crypto_extension_plan_20260711]]`)=検証規律とBTエンジンを転用しMT5→ccxt置換、国内現物のみ、候補A/B並行BT、税ハンデ(雑所得55%・繰越なし)を最初から織り込む。
- **C0 基盤**: `.venv_crypto`(ccxt4.5)+`optimizer/fetch_crypto_ohlc.py`(Binance公開API・口座不要=Dukascopy相当の真値役)。ETHBTC/BTCUSDT/ETHUSDT 4h・1d を9年取得(ETHBTCは現物直接上場=真の比率OHLC, 2017-07〜2026-07・19,691本)。data/はgitignore(再取得可)。
- **C1 候補A=NO-GO(構造的エッジなし)**: `optimizer/crypto_ratio_mr_bt.py`=AUDCAD確定エンジン`run_bt_tiered3`(3段不等分割0.2/0.3/0.5+vol throttle+MA一括/部分利確)を**一切改変せず**ETH/BTC比率へ差し替え。IS=2017-21凍結/OOS=2022-26。コストは比率が0.016-0.117と広く動くため**各区間の中央価格×cost_frac(0.8%往復)でcost_pips換算**しcross-regime歪みを排除。**全スイープ(exit A/B×z_stop×vol_throttle)・全暦年(IS/OOS)でPF<1.0**。決定打=**ゼロコストでもgross PF 0.60(full)/0.44(IS)/0.97(OOS)**=コスト問題でなく信号自体にエッジ無し。WR55%・TP率66%(小さい+24勝多数)だが方向テール損(z_stop-70/time-232)が食い潰す典型的MR死。回帰速度T_reg中央値26本(AUDCAD21と大差なし=「遅い回帰」が主因でもない)。**真因=ETHとBTCは数年単位で乖離(2021アルトシーズン等)しAUDCADの「同一ドライバ→独立トレンド無し→構造的レンジ」が無い**。→FX墓場がcryptoで反復するとの事前予測どおりClose。税引後ゲート以前に構造で落選。
- **C2 候補B=構造は本物だが薄い(研究のみ・国内執行不可)**: `optimizer/fetch_funding_rates.py`(Binance funding履歴7年)+`optimizer/crypto_basis_research_bt.py`=デルタ中立(現物long+perp short)ファンディング収穫。**全年プラス圏・maxDD僅少1.6-1.9%・市場中立=carry long-only Gridのcrypto版として構造は健全**(BTC損失年0/8)。**但し税引後年率中央=2.6%(gross6.5%→net5.7%→55%課税で2.6%)、OOS中央2.6%・最悪年0.2%**。2020-21の高利回り(gross17-37%)は強気相場の産物で直近OOSは4-13%gross。**counterparty ヘアカット3%/yrを課すとBTC税引後OOS中央1.2%・最悪-2.6%・損失年2/8に崩壊**。
- **C3 判定**: 候補A=Close(構造的エッジ皆無)。候補B=**海外拡張は非推奨**(carryは本物でDD極小だが、税引後2.6%→ヘアカット後1.2%は海外取引所リスク=破綻/出金停止/規制(FTX級=-100%)を負うに値しない)。土日稼働の動機だけでは正当化不能。**現時点でvps実装候補ゼロ**。将来もし海外執行を再考するならヘアカット前提を精緻化(取引所分散・保険的サイジング)してから。リソースは確定FXエッジ(AUDCAD MR+確定Grid4本+CADCHF追加)へ集約継続。成果物: `optimizer/{fetch_crypto_ohlc,crypto_ratio_mr_bt,fetch_funding_rates,crypto_basis_research_bt}.py`(+_result/_yearly.csv)、`data/{ETHBTC,BTCUSDT,ETHUSDT}_{4h,1d}.csv`・`data/FUNDING_{BTC,ETH}USDT_binance.csv`(再利用可・gitignore)。

### ★★新戦略確定: AUDCAD(4h)平均回帰・3段不等分割エントリー → 配分(Allocation)がエッジを強化する初の実証・実運用計画+vps実装完了（2026-06-30）
Grid(平均回帰の自動ナンピン)とは別建付けで、**相関クロスの素のZ-score平均回帰に「資本配分」を最適化**した新戦略。YouTube由来アイデアをChat(Gemini)と往復しPhase1-7+ストレステストで検証。**核心の発見=平均回帰の改善は「エントリーの選別(フィルタ)」でなく「エッジ濃度(高Z)への資本配分」**。検証規律(IS=2015-21凍結/OOS=2022-26/年次WFO/フルコスト/Lookahead排除/次足始値約定)は全Grid踏襲。成果物は全て `optimizer/` 下。
- **Phase1-2(死因分析+動的ロット)**: MFE/MAE分布(`mfe_mae_analysis.py`)で前回の逆張り敗因を解明=負けの54.5%が即逆行(死因B=方向にエッジ無し)。動的ロット(`dynamic_lot_mr_bt.py`)=Z-scale/voladjはnetとDDを比例スケールするだけで**PF不変(dPF一様±0.03)**=サイジングはエッジ生成器でない(Grid動的化Closeと同型)。但し**|Z|↔PF正相関+0.62**を発見(乖離大ほど期待値高=平均回帰圧力)。
- **Phase3,5(フィルタ系)=全て無効**: 反転確認フィルタ(EMA/ローソク足/RSI)もHTFレジームゲート(4h ADX/SMA傾き)も**勝率は上げるがPF不変〜悪化**(確認待ち=エントリー価格劣化で利幅相殺/ゲートはnを減らすだけ)。「高勝率≠高PF」を定量実証。
- **Phase4(分布解析`mr_distribution_analysis.py`)**: 保有時間別PFのビン分析で「13-24本でPF最大・25-48本でPF崩壊」だが**max_holdスイープで実検証すると短縮は逆効果**(早期決済が回復益を殺す=Grid float_stop教訓と同型)。ビン別PFは生存条件付きで読み違え注意の警告を実装。
- **Phase6-7(層化エントリー)=唯一の純改善**: 高Z域に資本を寄せる不等分割。**3段(Z2.0=0.2/Z2.5=0.3/Z3.0=0.5lot, 最大合計1.0)+MA一括決済(構成A)**がベスト。決済A vs B(部分利確)はペア/TF依存=4hは反転がクリーンで両ペアA優位。
- **4h移行でエッジが質的に強化**: AUDCAD baseline PF 1h1.48→4h1.77、tier3+A で **OOS PF 2.60 / wfoMin 0.92→1.81(フォールド頑健性が劇的向上=ノイズ排除)**。但し|Z|↔PF相関は4hで弱化(0.64→0.47, サンプル減)。保有時間はbar-count基準で不変(~14本)=タイムストップ48本据置(実時間8日に自然延長)。AUDNZDは限界的(PF1.13)、EURGBPは1hでIS<1.0除外、CADCHFは素のz-MRでPF<1(エッジはGrid側)。
- **ストレステスト(`audcad_stress_test.py`)=合格だが重要caveat**: ①イベント窓(USD指標/RBA第1火曜±24h)もPF>2で破滅せず、高ボラ(ATR上位20%)もPF1.35で黒字維持=破滅的逆行なし。②期待値分布は歪度-2.46(損失側テール, 平均回帰の構造)。③**不等分割は負けトレードMAE中央値を同TF比-67%**(浅Zで0.2lotのみ=含み損が物理的に小)。④MC(10000シャッフル)maxDD 95%ile=628 lot-pip。**⚠️最重要=ヘッドラインOOS PF2.60は順風レジーム限定、IS(2015-21)PF1.17・純損失年2015/2017/2020(マクロ高ボラ年)・全11年PF1.56**。
- **高ボラ・ロットスロットル採用(Gemini助言を実測検証)**: フィルタでなく配分で年次DD抑制。**ATRパーセンタイル≥0.7でエントリー全段lot×0.5**(エントリーは止めない=エッジ維持)→maxDD 553→322(-42%)/MC95 627→398(-37%)/full PF 1.56→**1.61**(向上)/OOS 2.57維持/2020年-214→-98。全throttle変種がDD下げPF維持=構造的risk-reductionでcurve-fitでない。
- **実運用計画(`audcad_mr_deployment_plan.md`)**: ①基準=OOS2.60でなく**全期間PF1.61+MC95DD**でサイジング(安全lotスケール=許容DD額÷43万円, AUDCAD 1lot=10CAD≒1080円)。②キルスイッチ=ローリング12ヶ月PF<1.0 or 現DD>MC95(43万円/lot) or AUD/CAD構造関係崩壊→停止&前提再検証。③段階投入=demoフォワード(3ヶ月∧30約定∧SL発火)→micro-lot→漸増。マクロ高ボラ年の年次DDは平均回帰の構造的コスト(保険料)として受容。
- **vps実装完了(`vps/mr_monitor.py` v1)**: **magic=20260050 / tag='MR_AC' / 単一クラスタ管理(Grid並行ラダーと別)**。H4足・確定足z(SMA40/SD40)・ATR percentile(500本)・3段不等分割・vol throttle・MA一括決済(構成A)・タイムストップ48本/ハードストップ|Z|≥4.5。**指標計算はBTエンジンとz/atr_pct一致を機械精度(差~1e-13)で検証済**。demo(axiory/exness)はLOT_SCALE=1.0でBT比較可、live(LIVE_LOT_SCALE=0でdemoフォワード完了まで拒否)。実行=4h足確定時/5分poll(exit responsive)。詳細メモリ`[[project_audcad_mr_tiered_strategy_20260630]]`。
- **次アクション**: ①CLAUDE.md稼働状態にMR追加 ②VPSへ git pull → mr_monitor.py をdemo起動(タスクスケジューラ/bat) ③demoフォワードでBT乖離・スリッページ監視 ④strategy_spec.md/html更新。

### ★★Grid動的化深掘り(動的ロット/TP/SL/エントリー + 地合い予測) → 5案連続Closeで確定4本は Pareto フロンティアと確定・損失側最適化完了（2026-06-16）
確定Grid 4本(AUDCAD/CADCHF/AUDNZD/EURGBP, combo+R-SMA1200+2026-06-15 DD圧縮)を更に動的化/リスク構造/地合い予測で上積みできるか、4観点を検証。**結論=損失/サイジング/予測のどの軸を足しても inert か net-worse=確定構成は平均回帰Gridの genuine な Pareto フロンティアに在る**。検証規律(静的一致assert→IS=2015-21凍結→OOS/WFO→暦月MC, 失敗signature点検)は既存踏襲。詳細は `[[project_grid_regime_persistence_e1_20260616]]`。
- **E1 地合い持続性テスト(`optimizer/grid_regime_persistence.py`)=動的地合い系全Close**: trailing 状態指標(path_eff/variance-ratio/trend-z/CI gate share, K=3/6/12ヶ月)が forward grid PnL(H=3/6/12ヶ月)を予測するかを IS/OOS別Spearman検定。**採用signature通過 1/36のみ(窓重複偽陽性)・IS/OOS符号反転率42%≈純ノイズ・|rho|中央0.065(r²~0.4%)**。year-diagの「path_eff↔PF -0.41」は**coincident(同一窓内)であって predictive でない**と確定。動的化Close(`[[project_grid_dynamic_param_20260608]]`)と同型のIS↔OOS逆相関。→ **A2 nowcast-lotスロットル/C3 hostile手仕舞い/E2 VR/Hurstゲート/E3 throttle/E4 配分チルトは全て前提を欠きClose**。「予測力ゼロでも静的CIで十分」を採択。
- **D1 ドライバ・スプレッド・ゲート=退化(独立軸でない)**: 相関クロスの driver-spread = log(AUDUSD)+log(USDCAD) は**恒等的に log(AUDCAD)**(実測 corr=1.0000, 残差1.6bp)。スプレッドz = AUDCAD自身の価格水準z = 距離/Bollinger信号で、CI+グリッド間隔が既にencode済=独立エントリー軸でない。CADCHF/AUDNZD/EURGBPも同様(全て2脚USDメジャーの積)。非退化版は broad-basket/コモディティ参照が必要=外生ドライバ墓場(`[[project_commodity_fx_exogenous_20260615]]`)の領域。
- **A1 クロスペア合算エクスポージャー上限(`optimizer/grid_joint_exposure_cap.py`)=構造的にinert**: 確定4本を統一タイムライン上で同時シミュレートする joint エンジン(cap=None で各ペア月次がDB.run_btと完全一致=静的assert合格)。**合算 open 含み損は11年で最悪 -1.65M止まり**(per-pair最悪openの単純合算 -3.57M ≫ 実測合算 -1.65M=4本の深DD時点が**重ならない**=joint_stepb corr≈0 を intraday でも確認)。→ cap を -1.8M以深に置いても一度も bind せず全variant **net/req99 ±0.0%**。basket req_cap(≈825k)は「**realized 月次損失が60ヶ月に渡り累積/連続**」現象で「瞬間 joint open 露出」でないため、open-露出 cap では原理的に req_cap を動かせない。realized DD への cap(=DDデリスク)は carry デリスク・オーバーレイと同型で既Close。
- **C1/C2 realized リスク構造(`optimizer/grid_risk_structure_bt.py`)=float_stop トレードオフでcapEff悪化Close**: C2=per-leg 破局stop(entry∓m×gw, basket FSより前に個別決済)は req_cap/worst単発を下げる(CADCHF req99 3.03M→1.79M, worst -1.04M→-281k)が、**回復可能な平均回帰ラダーを切るため net/PF が req_cap 以上に落ち capEff 悪化**(AUDCAD baseline PF2.84/net5.6M→ls8 1.58/3.1M, CADCHF net/yr-55%, **EURGBP net負転**)。**全 legstop セルで IS<baseline=IS-selectable不合格**。gapテール(req_cap_999)を bound する唯一の効用も net 35-55%減で capEff破壊=不採用。C1=cull_drain は既存 per-bar 単発cull がドレイン追随済で**全ペア±0.0%(inert)**。既存 combo(cull0.5+taper0.7)が realized リスク制御の適量。
- **総括と次アクション**: 損失側(予測/露出/realizedリスク)の BT 最適化は**完了**。残る上積み余地は **gain側(B1 ラダー深さ別非対称TP / B2 部分利確)とエントリー精度(D2 平均回帰確認エントリー)のみ**で別セッション。**リソースは確定4本の forward-test/実投入へ集約**(構成・必要資本は2026-06-15のジョイントStep B=月利30万必要資本2.80M が不変、本セッションで上書きする改善は無し)。成果物: `grid_regime_persistence.py` / `grid_joint_exposure_cap.py` / `grid_risk_structure_bt.py`(各+_result.csv)。

### ★★実務フェーズ: ジョイントStep B + 資本重ペアDD圧縮 → 月利30万の必要資本を分散+DD圧縮で 5.0M→2.8M に半減（2026-06-15）
探索は墓場確定済み(FX内生/外生とも頑健エッジは相関クロスGrid平均回帰のみ)。本作業は**新エッジでなく確定Grid 4本(AUDCAD/CADCHF/AUDNZD/EURGBP)の資本効率最大化**。検証規律(IS=2015-21凍結→OOS/WFO)とMC手法(月次ブロックブートストラップ20000/60mo/block3/seed42)は既存を踏襲。
- **候補1 ジョイントStep B(`optimizer/grid_joint_stepb.py`)**: 既存Step Bは各ペア**単独**req_cap。4本同時保有のバスケットmaxDD分布を相関保持ジョイントMC(バスケット月次=M@wを先に合算→同一行ブロックでbootstrap=月内同時点相関を自動保持)で算定。
  - **重要な手法修正=暦月基盤(carry_crash_hedge と同じ)**: grid の `monthly` は close発生月しか記録しない(CIゲートで休眠する月多数→活動月66-90のみ)。アイドル月のPnL=0を補完しないとMCが活動月だけに圧縮されmaxDD過大評価∧ジョイント整列も不能(intersection 16ヶ月に崩壊)。全ペア共通の暦月レンジ(134ヶ月 2015-04〜2026-05)にreindexし0埋め。**これが継続運用時の honest な月次収益率基盤**。
  - **エンジン検証**: 活動月basisで再現するとpublished値と一致(AUDCAD req734k・net/yr完全一致)→差は手法のみ。**暦月basisはnet/yrを約半減**(活動月basisは活動年で割るため income過大)=brief/published の net/yr(AUDCAD1.02M等)・必要資本260万/572万は活動月basisの楽観値。honest(暦月)では AUDCAD単独で月利30万に約5.0M要る。
  - **相関は暦月基盤でほぼゼロ**(全ペア間 |corr|≤0.19, AUDNZD×EURGBP -0.17等。旧0.42/0.65は co-active月だけ重ねた見かけ)→**分散効果=バスケットreq99÷単純合算 で 60-73%削減**。最良=等req_cap配分(w∝1/req_cap)。
- **候補2 資本重ペアDD圧縮(`optimizer/grid_capheavy_ddcompress.py`)**: バスケット資本を支配するEURGBP/CADCHFのreq_cap圧縮。IS-selectable∧全fold>1.0∧OOS>1.2∧崖/薄標本棄却。
  - **✅EURGBP: fs×1.3 + taper0.6 = clean Pareto win**: req99 3.40M→**2.30M(-32%)** / IS1.23→1.33✓ / OOS2.56→2.67✓ / wfoMin1.50→1.61✓ / nFS1→0 / net/yr維持 / worst単発1.33M→893k。**float_stopを緩めるとDDが下がる**(回復可能ラダーを切らない=平均回帰でグリッドが本来稼ぐ動きを殺さない)=既知「float_stopは深ラダーのテール保険のみ・締めると回復可能ポジ切りPF/DD悪化」を定量裏付け。
  - **✅CADCHF: +cull0.6 (R-SMA1200にcull追加) = clean win**: req99 3.03M→**2.27M(-25%)** / net/yr **UP**(870k→900k) / nFS17→1 / OOS1.39→1.46 / wfoMin1.31→1.35 / P5 0.021→0.009。IS1.56→1.45は下がるがOOS/WFO/net全改善=**過適合の逆**(IS↔OOS inversionでなくISのピークが取れただけ)。lv4スタックは逆効果→cull0.6単独が最良。
  - 失敗例: fs締め(×0.6/0.75)はIS<base非selectable・max_lvはIS落ち・taper強めは逆にDD増。
- **結論(月利30万円・暦月基盤・honest)**: DD圧縮後の**等req_cap分散バスケット**で月利30万=**必要資本2.80M**(AUDCAD単独4.96Mの**0.56倍**/単純合算10.4Mの27%)・P(5yr損)0.000・バスケットcapEff1.29。相対lot=AUDCAD1.0/CADCHF0.305/AUDNZD0.552/EURGBP0.303。ベースライン構成の分散バスケット(3.53M)からさらに-21%。
  - **候補4=forward-testプラン完了(`optimizer/grid_forward_test_plan.md`)**: ペア別req_cap(暦月basis: AUDCAD691k/CADCHF2.27M/AUDNZD1.25M/EURGBP2.28M per lot)・安全lot(scale=自己資本÷742k×等req_cap相対比 AUDCAD1.0/CADCHF0.305/AUDNZD0.552/EURGBP0.303)・昇格(3ヶ月∧TP≥30∧FS発火∧PF>1.2)・撤退ルールを1枚化。
  - **候補3=vps実装は大半完了**: `vps/grid_monitor.py`は既にv8で4本全デプロイ済(CADCHF magic20260038/GRID_CDC・AUDCAD R-SMA1200+combo・EURGBP combo+slot0.5+mom120+tp0.8・AUDNZD R-SMA1200+combo、combo/R-SMA1200/cull/mom実機ロジック実装済)。**未反映は候補2のDD圧縮2点のみ**=①CADCHF cull_frac=0.6追加 ②EURGBP fs-1.32M→-1.72M+taper0.6(⚠️デプロイ実構成 mom120/tp0.8込みで再検証後に反映)。strategy_spec md/html同時更新要。forward-test完了が実マネー投入の前提。成果物: `grid_joint_stepb.py`(+_result/_improved/_30man*.csv) / `grid_capheavy_ddcompress.py`(+_result.csv) / `grid_forward_test_plan.md`。

### ★結論: コモディティ→資源通貨 外生ドライバ(金/原油/銅のリード/ラグ) → 同時点relationのみ・取引可能なリード/ラグ無しでClose(Stage0却下)（2026-06-15）
初の**FX内生でない外生ドライバ**探索(墓場Bは全てFX内生=価格/金利のみ)。鉄則「効くのは構造的/経済的理由を持つもの」に最も整合する未探索フロンティアとして、コモディティ→資源通貨の構造関係(原油→CAD/金→AUD・CHF/銅→AUD・NZD)がD1で取引可能なリード/ラグ or 共和分を持つか検証。Dukascopyでコモディティ D1 12年を新規取得(`fetch_dukascopy_ohlc.py`にXAU/XAG/WTI(E_LIGHT)/BRENT/COPPER追加, 各≈3,100-3,700本 2014-2026)。本BT前の**関門=Stage0リード/ラグ診断**(`commodity_fx_lag_diag.py`)で却下=本BT不要。
- **決定打=完全な同時点relation(取引不能)**: 日次logret相互相関(IS 2015-21, n≈2,100)。**lag0(同時点)=0.22〜0.42で強い本物の経済関係**(COPPER→AUD0.42/XAG→AUD0.41/BRENT→CAD0.39/WTI→CAD0.37/XAU→CHF0.36)だが、**lag+1(コモディティが1日先行=t-1特徴で翌日FX予測=取引可能)=0.003〜0.042で全てノイズ**(相関SE≈0.022, 2σ≈0.044を全て下回る)。lag+2/+3も同様にゼロ。負ラグ(FX先行)もゼロ。
- **★主軸の金属-原油スプレッド→AUDCAD方向も同断**: METAL-OIL→AUDCAD lag0=0.110/lag+1=0.026, COPPER-OIL→AUDCAD lag0=0.141/lag+1=0.015, OIL-METAL→CADCHF lag0=0.329/lag+1=0.042=全てlag0のみ。「AUD(金属)とCAD(原油)が乖離する時AUDCADがトレンド化=Grid出血窓を外生信号で説明/ヘッジ」という(b)併用仮説も、乖離が翌日FXを予測しないため成立せず。
- **モメンタム仮説も否定(早すぎるClose回避の追加点検)**: コモディティ W日モメンタム(t-1確定, W∈{1,5,20}) → 資源通貨 H日先リターン(H∈{1,5,20}) の45通り相関(`commodity_fx_mom_diag.py`)= **|corr|最大0.077・大半ノイズ域・0.05超の数例は符号が負で不安定**(コモディティ上昇→FX僅か反転=経済的に逆)。経済的に期待される正の継続予測力はゼロ。
- **総括=外生ドライバでも"同時点で織り込み済み"なら取引不能(墓場7例目・初の外生例)**。構造的理由が明快=コモディティもFXも24時間取引され日中に裁定されるため、今日のコモディティの動きは今日のFXに即座に織り込まれt-1に予測余地を残さない。「価格パターン単体に頑健エッジ無し・効くのは構造的理由を持つもの」の鉄則は、外生ドライバ版でも「構造関係が**本物でも同時点なら取引不能**」と精緻化。採用バー(a)はそもそも予測力ゼロで本BT進行の意味なし→Stage0で却下。リソースは確定エッジ(AUDCAD R-SMA1200+combo最優先 + EURGBP/AUDNZD/CADCHF相関クロス)のforward-test/実投入へ集約継続。**残るVIX/SPX→JPY・金利変化率→FX等の外生ドライバも同じ同時点relationの罠が濃厚(リスクオフ瞬間にJPY/CHFは即時反応)=外生探索の限界効用も逓減**。vps実装候補ゼロ。成果物: `optimizer/commodity_fx_lag_diag.py`(+_result.csv) / `commodity_fx_mom_diag.py`(+_result.csv) / `data/{XAUUSD,XAGUSD,WTI,BRENT,COPPER}_D1_dukas.csv`(再利用可)。

### ★結論: 日足ペアトレード(相関クロスのスプレッド共和分平均回帰) → 共和分はOOS崩壊∧IS共和分ペアはAUDNZD縮約・採用ゼロでClose / 三角stat_arbルックアヘッド是正後の最終確認（2026-06-15）
確定エッジ「相関クロス=同一ドライバ共有→構造的レンジ」を、絶対水準のGrid(AUDCAD/EURGBP/AUDNZD/CADCHF)でなく**相対水準=2銘柄スプレッドのOU平均回帰**で刈れば独立した別エッジ源になるか検証(`optimizer/pairs_cointegration_screen.py`=Stage A共和分プレスクリーン / `pairs_spread_bt.py`=Stage BフルBT)。過去Close「三角stat_arb」(`[[project_new_edge_exploration_20260607]]`)の敗因①1h②三角恒等式の非同期クォートノイズ③バー内ルックアヘッドを全是正=**日足 / 三角恒等式除外 / シグナルt-1 close→全約定next-bar open / 2脚それぞれにフルbid-ask差引**。既存CORR戦略(`[[project_corr_optimization]]`=単一クロスAUDNZDのローリングz)とも別物=共和分残差のz-band・ドル中立2脚。
- **Stage A(IS=2015-21でEngle-Granger共和分∧ヘッジβ∧OU半減期∧IS→OOS安定性, 11候補)= 合格ゼロ**。決定的な2点:
  - **①IS共和分が成立するのは antipodean のみ(AUDUSD/NZDUSD EGp0.009 / AUDCAD/NZDCAD0.019 / AUDCHF/NZDCHF0.007)だが、全て β≈0.9 = COLLAPSE**: spread=logA-β·logB が β≈1 で **log(AUDNZD)=既存Goグリッドに縮約**(同様に EURCHF/GBPCHF→EURGBP, AUDUSD+USDCAD→AUDCAD)。「独立した別エッジ源」でなく**既存エッジの言い換え**。
  - **②その共和分が OOS で完全崩壊**: IS残差ADF p 0.002→OOS(β凍結)0.890 / 0.004→0.941 / 0.001→0.857(残差がrandom walk化)・半減期 49d→240d・z凍結平均が+0.9〜+1.1へドリフト=**pairs-tradeの典型死因(共和分のOOS破断)**。
  - **③真に独立な2脚(β が±1から離れる EURCHF/GBPCHF・EURCHF/CADCHF・EURCAD/GBPCAD)は IS ですら共和分不成立**(EGp0.07-0.75・β drift16-19倍)。「同一ドライバ→相対水準は更に強く共和分」の仮説は**データで否定**(共和分するのは縮約ペアだけ)。
- **Stage B(Stage A合格ゼロだが実証のためIS共和分上位7候補を実BT, β/z統計IS凍結→OOS/年次WFO, z_in2.0/z_out0.5/z_stop3.5/maxhold60d)= 採用ゼロ**。z_in 1.0-2.5 全域で点検し失敗signatureを全確認:
  - **antipodean(唯一の真IS共和分)は全z閾値でOOS損**: OOS PF 0.33-1.03・Sharpe ≤0(AUDCAD/NZDCAD IS PF68.99→OOS0.46 = 露骨なIS↔OOS逆相関)。**OOS破断した残差をフェードして系統的にbleed**。
  - **OOS PF>1の3ペア(AUDCHF/CADCHF・NZDCHF/CADCHF・NZDUSD/USDCAD)は全て見せかけ**: ①IS n=1-5・IS PF=inf=**選択不能の薄標本**(z2.5でAUDCHF/CADCHF IS n=1/OOS n=4/PF227) ②Stage Aで**IS共和分不成立**のペア=OOSの勝ちは単一窓の運 ③β±1近傍のCOLLAPSEペア。WFO各fold正も不成立(NZDUSD/USDCAD 2/5)。
  - **標本が十分になるz_in=1.0(IS n~25/OOS n~36-73)では全ペア OOS PF≈0.69-1.32・Sharpe≈0=エッジ皆無**。「エッジ」は標本が薄すぎて無意味な高z域でしか出ない。**コスト1.5倍感応度も無関係に負**(そもそも正エッジ不在)。
- **総括=相関クロスのスプレッド平均回帰も日足/next-bar/2脚コスト後では頑健エッジ無し(6例目)・相対水準にも刈れるエッジ無し**。決定打=**共和分するスプレッドは既存クロス(AUDNZD/EURGBP/AUDCAD)への縮約に過ぎず独立でない∧それすらOOSでrandom walk化**。長い半減期(50-240d)ゆえ±2σ逸脱が稀(11年でIS round-trip 4-7回)=日足では統計的に選択可能なエッジを構成できない。三角stat_arbのルックアヘッド是正後の最終確認として**pairs-trade系を打ち切り**。Go無し→Step B不要・vps実装候補ゼロ。リソースは確定エッジ(Grid AUDCAD最優先+EURGBP/AUDNZD/CADCHF)へ集約継続。成果物: `optimizer/pairs_cointegration_screen.py`(+_result.csv) / `pairs_spread_bt.py`(+_result.csv)。

### ★結論: carry系(USDJPY/NZDJPY)の carry-crashテールヘッジ → 採用バー(b)頑健性不達でClose・micro-lot限定不変（2026-06-15）
確定エッジ carry long-only Grid(USDJPY/NZDJPY)の唯一の弱点「キャリー・クラッシュ・テール(リスクオフ円高での巨大DD→高req_cap99 4.3-4.5M=micro-lot限定の真因)」を状態条件付きヘッジで塞ぎ、req_cap激減でスケール可能化できるか検証(`optimizer/carry_crash_hedge_bt.py`)。新エッジ探索でなく**確定エッジの実用化(束縛条件解除)**の試み。過去Close「bleedヘッジ」(`[[project_grid_insensitivity_complement_20260608]]`)との違い=対象がランダムDDでなく**システマティックで反復するcarry-crash(2015-01 CHF unpeg/2018-12/2020-03 COVID/2022-09/2024-07 円キャリー巻戻し)**・状態条件付き・評価は単体PFでなく資本効率/テール改善(保険コスト差引後)。sleeve PnLは `grid_dirbias_improve_bt.run_bt`(long-only+combo)と完全一致assert・MCは `grid_stepb_recompute` の月次ブロックブートストラップ踏襲。検証規律=IS=2015-21凍結→OOS/年次WFO・全特徴t-1・コスト差引・**leave-one-crash-out(LOCO)**で単一イベント過適合を排除。
- **リスクオフ検出器(FX内生・t-1)**: D1 セーフヘイブン強度(JPY+CHF vs AUD/NZD/CADの短期相対リターンz) / D2 実現volスパイク(AUDJPY) / D3 ①横断キャリー・ファクター急落(`carry_xsec_daily.csv`)。IS分位q=0.90で閾値凍結。**D1/D3 は recall 0.8(5crash中4捕捉, coverage~9%)・D2は0.2(無力)**。
- **重要な前提修正(req_cap)**: 旧Step B(`[[project_grid_stepb_deployment_20260613]]`)の req_cap99 4.3-4.5M は**active月のみMC**(分母≈3.4年, 60ヶ月パスを全活動月で埋める)で算定=5年maxDDを過大評価。本BTは**暦月基盤(ゼロ月含む, variant間で一貫)**で再算定→honestなbaseline=USDJPY capEff3.4%/req2.62M, NZDJPY capEff4.7%/req4.05M(net/yrも~1/3に是正)。memory値は保守的だった。
- **採用バー判定(暦月・公平基盤)**:
  - **A デリスク・オーバーレイ(リスクオフ時に新規long停止)=clean Close**: net低下(USDJPY -49k/yr)・**req_cap99はむしろ悪化or不変**(2.62M→2.56-2.59M, NZDJPY 4.05M→4.0-4.4M)・capEff全て低下。**FS損は既存ラダーが轢かれて出る→新規long停止では防げず、稼ぐレンジ再建てだけ失う**(レジーム層Close `[[project_grid_directional_bias_20260613]]` と同型=ゲートはエッジ生成器でない)。
  - **B 能動ヘッジ(リスクオフ時にペアshort)=(a)は通すが(b)頑健性で全滅**: USDJPY/NZDJPYの複数lotで capEff向上∧P(5yr損)低下(例NZDJPY B_cal: capEff4.7→6.7%/req4.05→3.22M/P5 0.267→0.227)。**だが(b)で崩れる**: ①**最大crash 2015-01(CHF unpeg -8.4%)は252日履歴不足+突発ジャンプで信号NaN=構造的に検出不能→最悪テールはヘッジ不能**(req_capの本丸が守れない)。②**LOCO: D1/D3とも残りcrash較正閾値で 2015-01 ∧ 2022-09(実OOS crash)を未捕捉**=5crash中2つ(OISS含む)が単一イベント過適合。③**USDJPYヘッジは平常時損益>0(+51k〜+512k)=保険でなく方向ベット**(リスクオフ窓が2022-26のUSD/JPY方向と一致, req_capも下がらずむしろ上昇=「USDJPYは直近レジーム運」の追認)。④**NZDJPYは実保険(平常bleed -0.6〜-2.5M)だがOOS Sharpe全lotで悪化(0.79→0.39-0.72)**=テールを取る代わりにrisk-adjusted return毀損。
  - **① carry-off統合(横断キャリーのshort側をヘッジに)=(c)付加価値なし**: USDJPY≈無効果(carry-off較正≈0)・NZDJPYは capEff6.2%/req3.58M で**FX内生B(best 7.1%)に劣後**→①統合不採用。
- **総括=carry-crashテールは状態条件付きヘッジでも頑健に塞げず(保険コスト/方向ベット/最悪crash検出不能/LOCO不達)→carry系(USDJPY/NZDJPY)は micro-lot限定・スケール禁止 不変**。決定打=**最も守りたい最悪テール(2015-01級の突発政策ジャンプ)はFX内生リスクオフ信号では原理的に事前検出できず、検出可能なcrashへのヘッジはin-sampleサイジング+レジーム運**。リソースは確定エッジ(AUDCAD R-SMA1200+combo最優先 + EURGBP/AUDNZD/CADCHF相関クロス)へ集約継続。vps実装候補ゼロ。成果物: `optimizer/carry_crash_hedge_bt.py`(+_result.csv)。

### ★★新Goペア: CADCHF を相関クロス銘柄スクリーニングで発見(確定エッジの初の横展開成功)（2026-06-15）
確定エッジ「相関クロス=同一ドライバ共有→独立トレンド無し→構造的レンジ→Grid平均回帰が刈る」を未検証クロスへスケールできるか、2段階スクリーニングで検証(`optimizer/grid_corrcross_screen.py`=StageA構造プレスクリーン / `grid_corrcross_screen_bt.py`=StageB v8ツールキット / `grid_corrcross_stepb.py`=Step B+相関)。候補11クロス(NZDCAD/GBPCHF/AUDCHF/NZDCHF/CADCHF/EURCAD/GBPCAD/EURAUD/EURNZD/GBPAUD/GBPNZD)をDukascopy 11.5年1hで新規取得(各≈71,500本, 2014-12〜2026-06)。較正アンカー=Go(AUDCAD/EURGBP/AUDNZD)+No-Go(CHFJPY/GBPJPY/EURCHF/EURUSD)。
- **Stage A(構造プレスクリーン)**: 年次 trend_atr(純変位/ATR) / path_eff(経路効率) / fs_per_yr(強制決済頻度) / tp_fs_ratio / gate_share を算出しGoエンベロープ(trend_atr_med≤39 ∧ path_eff≤0.050 ∧ 〜)と照合。**GBPJPY(trend_atr48,env1/4)・EURCHF/CHFJPY(env2/4)を正しく排除**、EURAUD/GBPAUD(env2/4)も足切り→候補9本をStage Bへ。
- **Stage B(v8ツールキット, 再チューニング無し, IS=2015-21凍結→OOS/WFO, 各ペアでG.run_backtest一致assert)**: baseline/long-only/R-SMA1200/soft short_lot0.5/combo/long-only+combo/R-SMA1200+combo を適用。**採用バー(IS-selectable ∧ OOS>1.2 ∧ wfoMin>1.0)をクリーンに通過したのは CADCHF のみ**。
  - **✅CADCHF R-SMA1200 = 新Goペア**: full PF1.43→**1.44** / IS1.43→**1.50(selectable)** / OOS**1.39** / **wfoMin0.90→1.39(全fold>1.2: [1.43,1.44,2.70,1.39])** / DD1.87M→**1.44M** / net+9.71M。baselineが既にIS=OOS=1.43の対称形(IS↔OOS乖離無し=過適合signatureの逆)で頑健。short側が構造的に強い(short-only PF1.87)ためレジーム条件付きshort(close>SMA1200で新規short停止)が最適=AUDCAD/AUDNZDと同型の救済パターン②(相関クロスgrid)。
  - **代替 R-SMA1200+combo**: PF1.44/IS1.41/OOS1.49/**wfoMin1.55(全fold>1.5)**/DD1.48M/**nFS20→1**/worst-1.04M→-957k=テール制御版(強制決済激減で運用安全)だが資本効率は落ちる(下記)。
  - **❌他8候補は全滅**: AUDCHF(全変種wfoMin<1.0, degenerate fold0.54) / NZDCHF(baseline IS0.79=OOS1.72との乖離・全変種IS<1.0=典型的IS↔OOS過適合, OOS光るがISが予測せず) / **GBPCAD(構造スコア最良env4/4だがbaseline IS1.61/OOS0.98=IS↔OOS逆乖離で崩落)** / NZDCAD(「最有力」だがwfoMin0.50-0.98) / GBPCHF/EURCAD/EURNZD/GBPNZD(全てfull PF<1.2 or IS<1.0)。
- **Step B(MC 20000回/60ヶ月/月ブロック3, lot1.0)**: **CADCHF R-SMA1200 は既存Go群で AUDCAD に次ぐ2番目の資本効率**:

  | ペア | 構成 | PF | net/yr | req_cap_99 | 資本効率 | P(5yr損) |
  |---|---|---:|---:|---:|---:|---:|
  | AUDCAD | R-SMA1200+combo | 2.84 | 1,017,588 | 728,196 | **1.40** | 0.0% |
  | **CADCHF** | **R-SMA1200** | 1.44 | 1,309,488 | 3,051,367 | **0.43** | **0.3%** |
  | AUDNZD | R-SMA1200+combo | 1.32 | 251,540 | 1,391,163 | 0.18 | 5.7% |
  | EURGBP | combo+slot0.5 | 1.52 | 594,662 | 4,133,760 | 0.14 | 8.6% |

  - CADCHF資本効率0.43はEURGBP(0.14)/AUDNZD(0.18)を上回りP(5yr損)0.3%も最小級(AUDCAD除く)。req_cap3.05M/lotは重め(EURGBP級)だがnet/yr1.31Mが高くカバー。combo版はreq_cap2.66Mと軽いがnet譲り効率0.30。
  - **相関(月次)**: CADCHF vs AUDCAD-0.09/AUDNZD-0.12(弱負=分散良)/EURGBP+0.36。**但し等ロット・ブレンドはSharpe4.24→3.40悪化・maxDD169k→349k増**=CADCHF単独DDが大きく等ロットでは非効率(教訓どおり低相関は採用理由にせず)。CADCHFは**自力の採用バー通過+資本効率2位**で採用、分散効果は副次。
- **構造スクリーンの予測力(重要な考察)**: Stage Aは「極端トレンド型No-Go(GBPJPY/EURCHF)の排除」には有効だが、**レンジ的な候補群の中で唯一の頑健エッジ(CADCHF)を構造メトリクスだけでは識別できなかった**。構造最良(GBPCAD env4/4=AUDCAD同等)がIS↔OOS乖離で落ち、構造中位のCADCHFが通過。決定打はやはりStage BのIS/OOS/WFO規律。**ただしCADCHFは候補中 gate_share最高(10%)・黒字年率最高(75%)・tp_fs最高級(37)** で「Gridが実際に多く建て・大半の年勝つ」軸では候補トップ=この2軸を構造スクリーンに加重すれば予測力が上がる(基準精緻化の余地)。また**a-priori経済ランク(資源NZDCAD最有力/欧州GBPCHF)は外れ、「要構造確認」のCHFクロスCADCHFが勝った**=横展開は経済ストーリーよりデータ規律。正直な留保: CADCHFの「同一ドライバ共有」ストーリーは資源/欧州ブロックより弱く(CAD=資源/CHF=safe-haven)、Go根拠は経験的IS/OOS/WFO頑健性に依拠。
- **次アクション(実投入はforward-test完了が前提)**: ①**CADCHF を Tier2 分散候補として forward-test 昇格**(推奨構成=R-SMA1200, テール制御優先ならR-SMA1200+combo)。手順=3ヶ月∧TP≥30∧FS最低1回発火∧実現PF>1.2。②`vps/grid_monitor.py` 追加方針: **magic=20260038 / tag='GRID_CDC'** / atr1.5/lv5/ci65/fs-943k(qj170)/dir=regime_short(SMA1200)。③Tier付けは不変=**Tier1 AUDCAD最優先**(効率1.40)、Tier2分散=CADCHF/EURGBP/AUDNZD(CADCHFが効率2位で分散候補の筆頭)。確定3ペアへの集約方針は維持しつつCADCHFを4本目のGoに追加。成果物: `grid_corrcross_screen.py`(+_result.csv/_rank.csv) / `grid_corrcross_screen_bt.py`(+_result.csv) / `grid_corrcross_stepb.py`(+_result.csv) / `data/{11クロス}_1h_dukas.csv`(再利用可)。

### ★結論: 横断的キャリー・ファクター(高金利long/低金利short ドル中立LS) → 採用バー(a)不達でClose・横断形でも頑健エッジ無し ∧ テール過大（2026-06-15）
「価格パターンに頑健エッジ無し・効くのは構造的/経済的理由を持つもの」の唯一の例外候補=キャリーを“本来の横断ファクター形”(数十年の学術的証拠を持つリスクプレミアム)で取り直す初の価格非依存戦略を11.5年D1で検証(`optimizer/carry_xsec_bt.py`)。8通貨(USD/EUR/GBP/CHF/JPY/AUD/NZD/CAD)を7本のUSDメジャーD1(Dukascopy=真値, USDCHF/USDCADを`fetch_dukascopy_ohlc.py`に追加取得)でスパン、対USDトータルリターン=spot+キャリー利回り(金利差/252)。signal=政策金利差(`data/policy_rates.csv`を`build_policy_rates.py`で8中銀のdecision-date履歴から自前構築、announce+1発効=lookahead無し)。月末/週次に金利差ランク→上位N long/下位N short ドル中立・vol-target10%・往復2bp差引。IS=2015-21凍結/OOS=2022-26/年次WFO・全t-1。
- **採用バー(a)複数不達でClose**(事前登録=OOS Sharpe>0 ∧ OOS Calmar>0.5 ∧ WFO各年正(大半) ∧ IS-selectable)。
  - **IS非selectable(決定打)**: 実運用するL/S全構成でIS(2015-21) Sharpe ≤ +0.01(N2 -0.01〜-0.07/N3 -0.20〜+0.01)=ISは平坦〜負。OOS Sharpe 0.40-0.65は別レジーム(2023/2025/2026部分年)の産物でISが予測しない。2015 CHF unpeg・2018・2019・2020 COVIDの逆張りキャリー被弾でIS期は損益分岐以下。
  - **OOS Calmar 0.39<0.5 不達**(最良N2_W_diff OOS Sharpe0.65/Calmar0.37/maxDD-17〜-20%)。**WFO年次過半負**(IS yr_winrate0.25-0.50・符号頻繁反転、2026 Sharpe~2.0はJan-Jun部分年の水増し)。**long-only高金利バスケット/等金額ベンチは全域負**(Sharpe -0.19〜-0.38, USD利上げ期に非USDロング負け)。
  - **cross-variant corr(IS,OOS Sharpe)=+0.77は罠**(long-onlyクラスタが両期負で正に出るだけ。実取引するL/S群はIS≈0で固まり選択不能)。
- **carry-crash診断(N3_M_equal 月次worst, vol10%基準)**: 2015-01 **-8.4%**(CHF unpeg)・2024-07 **-8.3%**(円キャリー巻き戻し)・2020-03 -7.6%(COVID)・2018-12 -7.2%・2022-09 -6.7%。**最悪テールが円高リスクオフに集中=既存の確定carry sleeve(USDJPY/NZDJPY long-only Grid `[[project_grid_directional_bias_20260613]]`)のcarry-crash弱foldと同一イベントでテール重複**→分散価値ゼロ、Step B資本は重なる。よってブレンドバー(b)前提(maxDD非悪化)も成立せず検証打ち切り。
- **総括**: キャリーを横断ファクター形で取り直しても10年スケールでは頑健化せず(プレミアムがIS期のクラッシュ群に食われ、OOS黒字はレジーム運)。我々が既に踏んでいる **per-pair long-only carry-grid(USDJPY/NZDJPY)が事実上の最良の取り方**であり、それも「スケール禁止・P(5yr損)17-23%」止まり `[[project_grid_stepb_deployment_20260613]]`。vps実装候補ゼロ。リソースは確定エッジ(AUDCAD R-SMA1200+combo最優先 + EURGBP/AUDNZD相関クロス)へ集約継続。honest caveat=policy_rates.csvは知識ベース再構成(bps微差より相対順位重視、IS非selectableの結論は2015-21クラッシュ群=史実で頑健)。成果物: `optimizer/carry_xsec_bt.py`/`build_policy_rates.py`/`carry_xsec_bt_result.csv`/`carry_xsec_yearly_sharpe.csv`/`carry_xsec_daily.csv`/`data/policy_rates.csv`(再利用可)。

### ★結論: Grid非発火窓の方向ドリフト補完スリーブ → 5ペア全Close・補完窓にも頑健エッジ無し(5例目)（2026-06-13）
Grid(平均回帰)が休眠/出血する「非発火窓(CI低=トレンド局面)」を、各ペアの構造的longドリフト(JPYクロスのキャリー/資源・欧州クロスの上方ドリフト)で刈る補完スリーブを5ペア(AUDCAD/EURGBP/AUDNZD/USDJPY/NZDJPY)で検証(`optimizer/grid_complement_drift_bt.py`)。仮説=「Gridがレンジを刈り補完がトレンドを刈る分業」。過去Closeの案A(汎用Donchian, 方向を持たずエッジ無し)との違い=**ペア固有の確定ドリフト方向(long)に限定 ∧ CI<=閾値のGrid休眠窓に限定**。IS=2015-21凍結→OOS/年次WFO, 特徴量t-1, 約定next-bar open, vol-target sizing(risk一定), chandelier ATR crash stop, スプレッド差引。Grid v8 per-bar PnLはDB.run_btとfull net一致をassert。
- **採用バー該当ゼロ(事前登録: (a)単体OOS PF>1.2∧Sharpe>0∧WFO全fold正 (b)ブレンドでOOS Sharpe向上∧maxDD非悪化)**。
- **S1 ドリフトlong(CI-idle窓限定)**: 全ペア OOS PF 0.34-0.74・Sharpe全負(-0.9〜-5.2)・WFO全fold<1.0。stop拡大(3.0→5.0)でPF 0.4→0.7に寄るが1.0を越えず=churn減だがエッジ非生成(古典的signature)。USDJPY stop5.0が最良もOOS PF0.74止まり・WFO[1.15,0.62,0.86,0.59]=2022(USD強気1年)だけ>1.0=既知「USDJPYは直近レジーム運」の追認。
- **トレード診断(USDJPY)**: WR=21-22% / W/L payoff=2.0-2.75。WR22%のトレンドフォローは損益分岐に payoff>~3.5 が要るが届かず=系統的負け。`ci`(レンジ回帰=Gridへ返す)exitは8回のみ・大半は chandelier stop(trend whipsaw)で死亡=**CI低窓は1hでは持続トレンドでなくchoppy/往復**で刈れる構造的ドリフトが無い。順張り4戦略10年BT(84通り全PF<1.0)・日足/週足トレンド探索(IS↔OOS≈0)と完全整合。
- **S2 片側不在フェード(short過熱z>=2)**: long_only/regimeで恒常的に空くshort側を過熱時フェード→全ペア OOS PF 0.43-0.61・Sharpe全負。空く窓に別ロジックのエッジも無し→即Close。
- **S3 常時稼働(対照)**: CIゲート無しはS1より更に悪化(OOS PF0.36-0.57/Sh-3〜-5)=ゲートは露出を減らすがエッジは作れない(「いつ張るか」は制御できてもエッジ非生成、案A同型)。
- **ブレンド**: 全例で Grid単体比 OOS Sharpe悪化(例EURGBP 1.14→-0.82)・maxDD激増(523k→3.0M)。スリーブ-Grid日次相関は狙い通り≈0(-0.06〜+0.01)だが**低相関は採用理由にならない教訓を再確認**=「相関≈0だがエッジ無し」5例目(配分A/レジームB/三角stat_arb/不感症trendに続く)。
- **総括=補完窓にも頑健エッジ無し(5例目)・Grid単体運用に集約**。Grid非発火窓(CI低)は構造的に存在し非発火日のGrid損益は負だが、その窓を方向ドリフトでもフェードでも刈れない=FXメジャー/クロス1hにトレンド側の頑健エッジが無いという既存結論が補完文脈でも不変。リソースは確定エッジ(AUDCAD R-SMA1200+combo最優先 + EURGBP/AUDNZD + carry long-only USDJPY/NZDJPY)へ集約継続(Step B実投入計画参照)。成果物: `optimizer/grid_complement_drift_bt.py`(+_result.csv)。

### ★結論: Grid救済不可3ペア(GBPJPY/CHFJPY/EURUSD)の日足/週足トレンド探索 → 全滅・採用ゼロ（2026-06-13）
Grid(平均回帰)で構造的に救済不可と確定した3ペアに「別の時間軸/別の建付け(=日足レジーム・ゲート付きトレンドフォロー)」でエッジがあるか検証(`optimizer/daily_regime_trend_bt.py`)。経済的仮説=救済不可の原因が「レンジしない=年単位トレンド/政策ジャンプ」なら、平均回帰の真逆=トレンドフォローが本来の土俵のはず。素の順張りの敗因(choppyでのmomentumフリップ往復=whipsaw)を「トレンド進行中(ADX/効率比が高い)のバーだけ建てる」レジーム・ゲートで除去できるか、を起点=日足TSMOM_100(既検証で唯一の弱い正方向: CHFJPY IS1.53/OOS1.17, EURUSD IS1.14/OOS1.30)から検証。IS=2015-21凍結→OOS/年次WFO, t-1 shift, next-bar fill, スプレッド差引。
- **採用ゼロ(480構成)が決定的**: 3ペア各96構成のうち **IS≥1.2 ∧ OOS>1.2 を同時に満たす構成はゼロ**。さらに **IS↔OOS PFの相関≈0(GBPJPY0.12/CHFJPY0.02/EURUSD0.19)=ISがOOSを全く予測しない**=本プロジェクトが繰り返し棄却してきた非頑健シグネチャそのもの。OOSで光る構成(CHFJPY long full1.31/OOS1.46, GBPJPY long OOS1.32)は**例外なくIS PF<1.2(選択不能)**で、好成績は2022-24のキャリー/USD強気トレンド1局面に集中=学習可能なedgeでなくレジーム運。
- **週足でも同じ(より露骨)**: CHFJPY週足long-tiltはOOS5.0/2.5と巨大だが**IS~1.17(バー未達)・n14-16(薄標本)**=典型的thin-sample×IS↔OOS逆相関。EURUSD週足lb26は**IS1.21/OOS0.32=逆方向の符号反転**(ISで通りOOSで崩落)。
- **long-tiltは一貫してOOSを改善(構造的だが選択不能)**: ほぼ全ペアでlong方向がshort/both比でOOS良=JPYキャリーの上方ドリフト/方向バイアス知見(`[[project_grid_directional_bias_20260613]]`)と整合。但し3ペアではIS側がバー未達のままで**ex-anteに選べない**。転移対照のUSDJPY(既救済)もlong-tiltでOOS2.0+だがIS<1.0=既知「USDJPYは直近レジーム運」を追認(エンジンは正常動作)。NZDJPYは順張り全敗(=NZDJPYのエッジはlong-only GRID=平均回帰側であり順張りに転移しない)。
- **vol-target sizing/chandelierでは越えられない**: 採用バーはPFベース。sizingは各トレードのpip重み/DD/Sharpeを変えるがsub-1.2のIS PFを1.2以上に変換できない=リスク管理オーバーレイでありエッジ生成器でない(chandelierトレイルは既にbaselineに実装済)。
- **総括=3ペアはエッジ無しを再確認**: BB逆張り/Grid平均回帰/1h順張り4戦略/方向バイアス/stat_arb/pre-event/session に続き**日足・週足トレンドフォローも採用ゼロ**。GBPJPY/CHFJPY/EURUSDはGridでもトレンドでも、どの時間軸でも頑健エッジを持たない(IS↔OOS無相関が共通の証拠)。年次PF診断の「マクロ/政策ドライバで定期的にトレンドが来る=パラメータでは直せない」結論を別建付けからも追認。**リソースは確定エッジ(AUDCAD/EURGBP/AUDNZD + carry long-only USDJPY/NZDJPY)へ集約継続**。最も「弱いが正方向」止まりはGBPJPY日足lb100+ADX20+ER0.40+long(full1.25/OOS1.32/wfoMin1.15/yr+73%だがIS1.13<バー・oos_n21薄)=forward専用・スケール禁止としてのみ言及可、実投入候補ではない。成果物: `optimizer/daily_regime_trend_bt.py`(+_result.csv)。

### ★★Step B 再算定 & 実投入計画: 改善構成でAUDCAD必要資本が旧v7の24%に激減（2026-06-13）
forward-test候補(2026-06-13確定構成)をMC再算定(`optimizer/grid_stepb_recompute.py`, 手法は旧grid_sizing_ruin.py踏襲=月次ブロックブートストラップ20000回/60ヶ月, lot1.0, 破産<1%基準=req_cap_99)。cull+taperでDD/worst単発が半減した効果を反映。
- **資本効率(net/yr÷req_cap_99)が投入優先度を決める=AUDCADが圧倒的**:

  | ペア | 構成 | PF | net/yr | req_cap_99(lot1) | 資本効率 | P(5yr損) |
  |---|---|---:|---:|---:|---:|---:|
  | **AUDCAD** | R-SMA1200+combo | 2.84 | 1,017,588 | **734,282** | **139%/yr** | **0%** |
  | AUDNZD | R-SMA1200+combo | 1.32 | 251,540 | 1,412,170 | 18%/yr | 6% |
  | EURGBP | combo+short_lot0.5 | 1.52 | 594,662 | 4,206,384 | 14%/yr | 9% |
  | NZDJPY | long-only+combo | 1.28 | 323,304 | 4,343,498 | 7%/yr | 17% |
  | USDJPY | long-only+combo | 1.39 | 285,731 | 4,545,388 | 6%/yr | 23% |

- **AUDCAD必要資本=旧v7比24%(3.10M→734k), NZDJPY=56%(7.72M→4.34M)**: cull+taperのDD半減が資本要件に直結。**AUDCADは資本効率139%/yr・P(5yr損)0%で他を圧倒**(net大×新DD極小)。
- **carry系(USDJPY/NZDJPY)はWFO PFは通ったが資本効率が壊滅的(6-7%/yr)+P(5yr損)17-23%**=carry-crashテールが高DD99(4.3-4.5M)を生む。**実マネー本線にはせず、分散/学習目的のmicro-lotに留める(スケール禁止を資本面からも追認)**。EURGBPも「DD~3M=資本3-4倍」問題が再算定でも残存(req4.2M)。
- **月利30万シナリオ(劇的改善)**: (A)AUDCAD単独=**lot3.54 / 必要資本260万**(旧v7試算1270万から1/5)。(B)分散(AUDCAD lot3.0+EURGBP lot0.5+AUDNZD lot1.0)=月30万 / 必要資本572万(保守=単純合算, 相関≈0なら実効はより小)。**資本最小ならAUDCAD集中、頑健性なら分散**。
- **段階的投入計画**: ①**Tier1=AUDCAD R-SMA1200+combo を最優先**(安全lot=自己資本÷734k/lot。例:口座100万→lot1.3, 300万→lot4.0)。②forward-test合格(3ヶ月∧TP≥30∧FS最低1回発火∧実現PF>1.2)後にロット漸増。③**Tier2=EURGBP/AUDNZD を分散目的で追加**(AUDCADと相関≈0)。④**Tier3=USDJPY/NZDJPY carryは最小・スケール禁止**(P(5yr損)高)。⑤実投入は全ペアforward-test完了が前提(BT由来のため)。成果物: `grid_stepb_recompute.py`(+_result.csv), `grid_dirbias_improve_bt.py`にcollect(月次/イベント収集)追加。

### ★★全ペア総当たり: USDJPY long-only と AUDNZD が新たに採用バー通過 / 救済パターンが2類型に確定（2026-06-13）
開発済みツール一式(mom/cull/taper/方向バイアス/レジームshort/combo)を未検証5ペアに総当たり(`optimizer/grid_toolkit_allpairs_bt.py`, テンプレ再チューニング無し, IS凍結→OOS/WFO)。採用バー=IS≥base ∧ OOS>1.2 ∧ wfoMin>1.0。
- **✅USDJPY long-only=クリーン通過(NZDJPYより頑健)**: PF1.09→**1.65** / IS1.11→**1.36** / OOS1.07→**2.14** / **wfoMin0.25→1.38(全fold>1.2)** / DD2.50M→1.51M / net+2.02M。USD/JPYキャリー(USD高金利+2022-24円安)=NZDJPYと同じcarry-grid。**NZDJPY long-only(wfoMin0.58)より頑健**だがnTP219(11.5年, 低頻度=CIゲートが稀にしか開かない)に留意。**USDJPYは実マネーBB稼働中→long-only carry-gridを少額forward-test候補に昇格**(スケール禁止)。
- **✅AUDNZD R-SMA1200+combo=通過(限界的)**: PF1.08→**1.32** / IS0.94→**1.27(selectable)** / OOS1.31→1.43 / **wfoMin0.98→1.05** / **DD1.51M→650k(半減)** / worst-886k→-650k。相関アンティポデアン・クロス(AUDCAD型=独立トレンドなし)。**従来「△限界的」→フルツールでバー通過**。但しwfoMin1.05は薄め・long-only単独はIS0.56で過適合(degenerate)=R-SMA+comboの規律ある形のみ採用可。
- **❌救済不可3ペア(構造的理由が一致)**: CHFJPY=long-onlyでfull1.22だが**IS0.97<base**(SNB政策/B48出血で長側もIS弱・wfoMin<1.0)。EURCHF=R-SMA1200でIS1.31/OOS1.27だが**wfoMin0.86**(SNBペッグのジャンプ体制が弱fold残す, 既知「ゲート有害」と整合)。EURUSD=long-only **IS2.06だがOOS0.75=典型的IS↔OOS符号反転の過適合**(キャリー方向なし・mean-reversionエッジ皆無=年次診断「テンプレ不適合」を追認)。
- **救済の2類型が確定**: ①**JPYクロスcarry-grid(long-only)**=NZDJPY/USDJPY(高金利通貨をlong, 押し目グリッド。carry-crashがテール)。②**相関クロスgrid(両側/レジーム+combo)**=AUDCAD/AUDNZD/EURGBP(共通ドライバで独立トレンドなし)。**非救済**=CHFJPY(SNB政策)/EURCHF(ペッグ)/EURUSD(キャリー無+エッジ無)/GBPJPY(極端トレンド)は構造的理由で不可=ツールでは直せない。実マネー候補は AUDCAD(Go) + EURGBP/AUDNZD(相関クロス) + USDJPY/NZDJPY(carry long-only, スケール禁止) に拡張。成果物: `grid_toolkit_allpairs_bt.py`(+_result.csv)。

### ★★方向バイアス改善: レジーム条件付きショートがAUDCADで最良構成 / hard long-onlyの上位互換（2026-06-13）
hard long-only の2課題(AUDCAD=short分散喪失でWFOmin悪化, NZDJPY=carry-crash弱fold)を「shortを賢く制御」で解決(`optimizer/grid_dirbias_improve_bt.py`, 全機能OFFで静的一致assert, IS凍結→OOS/WFO)。
- **R レジーム条件付きshort=AUDCADの決定打**: 「close>SMA_N(t-1)=上昇レジームのバーのみ新規short停止」。**structural bleedの源(上昇トレンド中の逆張りshort)だけを断ち、レンジ/下落ではshortを残して分散を回収**。
  - **AUDCAD R-SMA1200**: full PF1.51→**2.03** / **net+5.58M→+6.20M(hard long-onlyの3.58Mと違いnet維持)** / IS1.46→1.62(selectable) / OOS1.58→**2.89** / DD881k→841k / **wfoMin1.32→1.23(全fold>1.2)**。hard long-onlyのwfoMin0.68崩壊を完全回避。プラトー確認=SMA480/1200/2400全てIS1.60-1.62・OOS改善・wfoMin>1.1(崖でない)。経済的=年次診断で判明した「上昇トレンド中のshortラダー焼き」をピンポイント遮断。
  - **AUDCAD R-SMA1200 + combo(mom+cull+taper)=現時点の最良構成**: full PF**2.84** / net+5.60M / IS**3.60** / OOS**2.21** / DD**452k** / worst-344k / nFS0 / **全WFO fold>1.2(min1.20)**。両側combo(PF2.27/DD410k/wfoMin1.36)比でPF/IS/OOS大幅上、DD同等、wfoMinは僅差(1.20 vs 1.36)。**AUDCAD forward-test本線をこれに更新推奨**。
- **NZDJPYはhard long-onlyが正解(レジーム不可)**: R-SMA1200はPF0.99に悪化(shortを下落局面で復活させてもcarry反転で焼ける)。**long-only+comboが依然最良**(OOS1.70/全fold>1.2/IS1.08)。
- **EURGBPはソフトなshort_lot0.5が最適**: IS1.28→1.39(selectable)/OOS1.22→1.61/net維持(5.4M)。hard long-only(IS1.27)やレジームgate(IS<1.28)より良い。EURGBPのshortはAUDCAD/NZDほど壊滅的でないため「薄く残す」が正解。
- **総括=方向制御はペア毎に最適形が違う(short構造的弱さの度合いに依存)**: AUDCAD=レジーム条件付きstop(中程度の弱さ→トレンド時だけ断つ)/NZDJPY=hard long-only(壊滅的→全停止)/EURGBP=soft lot縮小(軽度→薄く残す)。いずれもIS-selectableで過適合signatureなし。**次アクション: ①AUDCAD=R-SMA1200+combo に更新 ②NZDJPY=long-only+combo(前回どおり, スケール禁止) ③EURGBP=両側combo+mom120+tp0.8 に short_lot0.5 を追加検討**。成果物: `grid_dirbias_improve_bt.py`(+_result.csv)。

### ★★新発見: 方向バイアス(long-only)が構造的エッジ / NZDJPY(No-Go)を初めて救済（2026-06-13）
未検証の3次元(方向非対称/強制決済後クールダウン/セッション)を検証(`optimizer/grid_novel_bt.py`, 全機能OFFで静的一致assert, IS=2015-21凍結→OOS/WFO)。**最大の収穫=long/shortを対称扱いしてきた前提が誤りで、long側が全ペアで構造的に強い**。
- **N1 方向バイアス=今までで最も有望な新軸**: long側PFがIS/OOS両半期で一貫してshortを上回る(=単なる1レジームのドリフトでなく構造的)。AUDCAD L1.77/S1.32・EURGBP L1.43/S1.11・NZDJPY **L1.48(net+5.2M)/S0.87(-2.4M)**・GBPJPY L-2.2M/**S-9.4M(short壊滅)**。経済的裏付け=AUD/NZD/EURの対quote上方ドリフト+JPYクロスのキャリー・プレミアム。
  - **NZDJPY long-only = No-Goペア初の救済**: PF1.10→**1.48** / IS1.06→**1.71(=IS-selectable)** / OOS1.15→1.26 / net+2.84M→**+5.19M** / DD3.73M→2.67M / nFS24→9。さらに **long-only+combo(mom+cull+taper)で全WFO fold>1.2(min0.58→1.29)・DD→1.72M・nFS→1**(但しIS1.08に低下=モメンタムゲートがISで過抑制)。long-only単独はIS強い/弱fold残、+comboはWFO頑健/IS marginal=トレードオフ。**carry-crash(2024-25円高局面)が弱foldの正体**=経済的に整合する実テールでノイズでない。
  - **AUDCADはlong-only非推奨**: full PFは上がる(1.51→1.77)が**WFOminが悪化(1.32→0.68, comboでも0.55)**=両側comboのwfoMin1.36の方が頑健。方向集中はAUDCADの分散を削る。**AUDCADは両側combo据置**。
- **N2 強制決済後クールダウン=Close**: cd24-168h全て僅かに悪化(AUDCAD1.51→1.50, NZDJPY1.10→1.08)。**理由=GridはCIゲート+グリッド間隔で既に即時再建てを構造的に抑止済**(BBの毎分再エントリー・バグとは別物)。良いレンジ再建てを削るだけ。
- **N3 セッション・ゲート=限定的/overlapは過適合**: LDN+NY(7-20UTC)はAUDCAD/EURGBPでIS-selectable改善(EURGBP PF1.26→1.72/net4.6M→8.8M)だがOOS伸び小・wfoMin悪化。overlap(12-16)はPFスパイク(AUDCAD2.70/GBPJPY1.20)だが**全fold inf・OOS5.77=薄標本の崖スパイク→棄却**。LDN+NYは任意の弱い改善止まり。
- **総括と次アクション**: ①**NZDJPYを「long-only carry-grid」として forward-test 候補に昇格**(combo併用でWFO頑健化, **スケール禁止**=carry-crash実挙動を観測するまで少額)。No-Go全撤回の方針に対する初の例外候補。②AUDCAD/EURGBPは両側combo据置(long-onlyは分散を削り非推奨)。③クールダウン/overlapセッションは不採用。成果物: `grid_novel_bt.py`(+_result.csv), `grid_dd_reduction_bt.py`にallow_long/short追加。

### ★結論: 決済条件・ロット構造のPF改善 → 構造的(転移する)勝者なし / EURGBPのみ combo+tp0.8 が上積み（2026-06-13）
採用候補(mom24+cull+taper, エントリー/ポジション側)と直交する「決済条件・ロット」でPFを上げられるか検証(`optimizer/grid_exit_lot_bt.py`, 全機能OFFで静的baseline完全一致をassert)。AUDCAD/EURGBP, IS=2015-21凍結→OOS/WFO。
- **X1 TP距離倍率(tp_mult)=ペア依存のcurve-fit**: AUDCADは狭めtp0.6が強い(PF1.51→**1.72**/IS1.46→1.58=selectable/OOS1.58→**1.93**/nTP738→1197=レンジ回収増)が、**EURGBPには転移せず**(tp0.6で1.26→1.23・EURGBPはむしろ広めtp1.5を好むがOOS悪化)。**AUDCAD↔EURGBPでIS/OOSの符号が反転=mom-gateと違い構造的でない**。単一ペアのIS/OOSは通るが転移バー不合格。
- **X2 バスケットTP / X3 バスケット・トレール=Close(効かない)**: per-legTP(=gw距離)が既にタイトに利確するためバスケット含み益が積み上がらず(5レッグ満玉でも最大≈103k JPY)、|fs|比0.15-0.4のバスケットTPは発火ゼロ。閾値を5-30k迄下げると発火するがPFはbaseline以下(1.46-1.53)。「グリッドの決済はper-legで既に最適化済み、バスケット決済は上積みしない」。
- **X4 B48時間=ペア依存で符号反転**: AUDCADは短縮(24-36h)でOOS/WFO改善だがIS低下(1.46→1.35)=selectableでない。EURGBPは逆に延長(72-96h)を好む。方向逆=非構造的。
- **L2 ロット・ピラミッド(逆テーパー)=対照として悪化を確認**: 両ペアでDD/worst悪化(AUDCAD worst-812k→-1.14M)。**テーパー(深レッグ減ロット)の方向が正しい**ことの裏付け。
- **C 採用comboへの上積み**: AUDCADはcombo単独(PF2.27)が最良でtp/b48を足すと同等以下。**EURGBPのみ combo+tp0.8 がクリーンに上積み**: PF1.48→**1.59**/OOS1.96→**2.09**/IS1.29→1.41(=selectable)/DD2.97M→**2.72M**/全WFO fold>1.2(min1.65)。EURGBPの広いバンドを狭め直すと約定頻度↑(nTP704→885)でレンジ回収が増える。
- **総括**: 決済条件・ロット単独でPFを底上げする「転移する」改善は無し(=過去の全param-tuning Closeと同型のcurve-fit signature)。**構造的勝者は引き続きエントリー/ポジション側combo**。実用上の上積みは2点のみ=①**EURGBP forward-test構成に tp_mult=0.8 を追加**(combo+mom120=4+tp0.8 が現時点のEURGBP最良)②AUDCAD tp0.6は単一ペアでは有効だが転移しないため**任意・スケール禁止**。成果物: `grid_exit_lot_bt.py`(+_result.csv)。

### ★結論: イベント・ブラックアウトは無効 / 長期momゲート(mom120)はEURGBP限定で有効（2026-06-11）
「重要指標発表前後のエントリー制限でトレンドを乗り切る」案を検証(`optimizer/grid_event_trend_gate_bt.py`)。NFP=第1金曜13:30UTC決定論ルールで11.5年生成(ルックアヘッド無し)+実カレンダー(news_events.csv 2022-26, NFP/CPI/GBP CPI 206件)±24/48h で新規建て/追加を全面停止。対象=Go 2ペア+GBPJPY/USDJPY。
- **E イベント・ブラックアウト=Close(全ペアで無効〜有害)**: AUDCAD ±24hフラット(PF1.51→1.54)・±48h悪化(1.37/DD1.39M)。GBPJPY悪化。USDJPY ±48hで赤転。EURGBPのみfull PF微増(1.38-1.43)だがwfo_min悪化(0.77→0.69)・実カレンダー版はOOS悪化=一貫性なし。**理由は年次診断の通り: Gridを殺すのは「数週間〜数ヶ月の持続トレンド」でありイベントの数時間ではない**(テール局面はCIゲートが既に休眠で自動回避済み、という過去のイベント窓ストレス結論とも整合)。USDJPY実カレンダー±24hでOOS 1.07→1.34はあるが2022-26窓のみ・IS凍結不可の診断値=採用根拠にならない。
- **T 長期モメンタムゲート mom120(5日リターン/ATR, t-1, mom24と同型)**: ①AUDCAD=mom24に追加効果なし(T2≤T0, comboに重ねると劣化)=AUDCADのトレンドはmom24で十分捕捉。②**EURGBP=mom120=4が有効**: mom24+mom120=4でPF 1.64→**1.92**/OOS 1.94→**2.58**/IS 1.50→1.67(=IS-selectable)/worst改善。**combo+mom120=4 → PF1.62/OOS2.36/DD 2.97M→2.61M/全WFO fold≥2.16**(combo単独のmin1.62から大幅底上げ)。EURGBPの持続ドリフト(2019/2022の負け年)に長期地平線が効く。③GBPJPY/USDJPYの救済はやはり無し。
- **留保**: mom120閾値はthr3が悪化・4最良・6中間の「丘」でプラトーは中程度。EURGBPのみ採用=ペア別チューニングだがIS基準で選択可能な範囲。**推奨=EURGBP forward-test構成を「combo+mom120=4」に更新、AUDCADはcombo据置、ブラックアウトは不採用**。成果物: `grid_event_trend_gate_bt.py`(+_result.csv)。

### ★結論: Grid No-Goペアの年次PF診断 → 負けの正体は「年単位トレンドによる強制決済の頻度差」（2026-06-11）
PF<1.2の7ペア(GBPJPY/CHFJPY/NZDJPY/AUDNZD/EURCHF/USDJPY/EURUSD)+Go 2ペア対照で年次PF×相場特性を診断(`optimizer/grid_yearly_pf_diag.py`, 108ペア・年)。
- **年次PFはトレンド性と明確に逆相関**: logPF vs path_eff(経路効率) Spearman **-0.41**(No-Go) / trend_atr -0.36。負け年の特性(中央値)=trend_atr 47 vs 勝ち年25(約2倍)・n_tp 56 vs 86(TPも減る)・nFS 1 vs 0。
- **GoとNo-Goの差は「トレンドの中央値」ではなく「強制決済の頻度」**: 年トレンド中央値はGo≈No-Go(32 vs 29 ATR)でほぼ同じ。決定的な差= **FS+B48発火頻度: Go 1.7-1.8回/年 vs GBPJPY 3.0(FS35回)/CHFJPY 6.3(B48 71回)/EURCHF 2.9**。TP/強制決済比はGo≈100:1、No-Go 35-68:1。Gridの構造(小さいTP多数−まれな大損)では**この比率≈100:1が損益分岐の目安**。
- **ペア別の負け方**: ①GBPJPY=典型的トレンド焼け(負け年2015/16/18/21/23は全てtrend_atr 47-88・eff0.10, JPYキャリー)+worst-2.75M(ギャップ貫通)。②CHFJPY=FSでなく**B48出血**(lv3浅ラダー→6.3回/年の時間切れ損, 2022 SNB政策転換・2015 unpeg余波)。③USDJPY=マクロサイクル依存(負け年2022/23/24=円安トレンドtrend 47-75, 勝ち年2025 PF3.37=平均回帰局面のみ)=BB USDJPYと同じレジーム運。④EURCHF=管理通貨でCIが「レンジ」と誤判定(gate開放10-12%と高いのに方向性クリープで負け)。⑤**EURUSDだけ異質**=負け年と勝ち年でトレンド差が無い(38 vs 36)・gate開放2.7%のみ・11.5年でn_tp501=**テンプレ自体が不適合(エッジ不在)**でトレンド焼けですらない。
- **含意**: No-Goの原因は「マクロ/政策ドライバで年を通すトレンドが定期的に来る通貨」であること自体=パラメータでは直せない(CI/atr/動的化/ゲート/cull全て検証済みで格上げゼロと整合)。AUDCAD/EURGBPがGoなのは相関経済クロスで年間純変位が構造的に小さく強制決済が稀(<2回/年)なため。**Grid追加ペア探索の事前スクリーニング基準として「年間トレンド距離(ATR比)の分布」と「想定FS頻度」が使える**。成果物: `grid_yearly_pf_diag.py` / `grid_yearly_pf_diag.csv`。

### ★結論: Grid DD/テール削減 → mom2.0+cull0.5+taper0.7 併用で maxDD/worst半減・PF維持（2026-06-11）
モメンタムゲート採用候補の残課題「テール/worst単発(-811k)とmaxDD(880k)が不変」をポジション側のリスク構造で解決。FSイベント診断(`optimizer/grid_dd_reduction_bt.py`)で**最悪レッグ1本がFS総損失の中央値42%を占める**と確認→3案検証(D1 worst-leg cull / D2 lot taper / D3 spacing widen, IS=2015-21凍結→OOS/WFO, エンジンは静的baselineと完全一致検証済)。
- **単独では全て不採用**: D1 cullのみ=FS消えるがDD悪化(880k→1.1M, 損の早期実現でOOS PF低下)・D2 taperのみ=PF/DD両悪化(回復TPが縮む)・D3 widenのみ=PF上がるがnFS増(8→10)・DD悪化。
- **併用(mom_thr2.0 + cull_frac0.5 + taper0.7)が全指標同時改善**: AUDCAD atr1.5基準で **full PF 1.51→2.27 / maxDD 880k→410k(-53%) / worst -811k→-344k(-58%) / nFS 8→0 / IS 2.56(=IS-selectable, 格子内IS PFほぼ最高・IS DD最小) / OOS 1.95 / 全WFO fold>1.2(min1.36)**。機構=taperで深レッグの露出を先に削り、cullが最悪レッグを-340k級で段階実現→一斉FS(-750k超)に到達しない。リスク調整(net/DD)はbaseline 6.3→mom単独9.8→**併用15.1**。
- **構造性確認**: EURGBP(テンプレatr1.5, 再チューニング無し)に転移→ **PF 1.26→1.48 / OOS 1.22→1.96 / oosDD 1.68M→694k(-59%) / worst -1.56M→-1.33M / 全WFO fold>1.2(min1.62)**。EURGBPの「DD~3Mで必要資本3-4倍」問題をOOSで大幅緩和。
- **プラトー確認**: cull0.4-0.6×taper0.6-0.85の全12セルで PF≥1.75・DD<880k・worst≤-500k(=リスク改善は格子全体で頑健)。mom_thr1.5-3.0でもPF2.09-2.43/DD410-460k/wfo_min全て>1.2と安定。
- **正直な留保**: ①net 8.63M(mom単独)→6.18Mとnetは譲る(DD半減との交換)。②「全fold>1.2」は推奨点近傍の一部セルで2023 foldが0.8-1.15に沈む=点固有の性質、リスク削減のみが格子頑健。③2023年が引き続き最弱(mom系共通)。④necessary capital(Step B)はDD半減により再算定で大幅縮小見込みだがMC再実行が必要。
- **実装方針(forward-test候補)**: `vps/grid_monitor.py`に (1)mom gate=24h ATR正規化リターン(t-1)が不利方向>2.0で新規/追加見送り (2)lot taper=レベルkのロット×0.7^(k-1) (3)cull=バスケット含み損がfloat_stop予算50%超で最悪レッグ1本成行決済(b48タイマーリセット)。成果物: `optimizer/grid_dd_reduction_bt.py` / `grid_dd_reduction_plateau.py` / `grid_dd_reduction_bt_result.csv` / `grid_dd_reduction_plateau.csv`。
- **全9ペア転移検証(2026-06-11, `grid_dd_reduction_transfer.py`)**: 併用案を再チューニング無しで7ペア追加適用(GBPJPY/CHFJPY/NZDJPY=v7設定, AUDNZD/EURCHF=AUDCADテンプレ, USDJPY/EURUSD=探索的ATRスケールfs)。**①リスク削減(maxDD)は8/9ペアで機能**(GBPJPY 17.9M→5.5M(-69%)/CHFJPY 2.78M→1.21M(-57%)/EURUSD -32%/USDJPY -33%/NZDJPY -24%/AUDNZD -23%。例外=EURCHF full DD悪化3.36M、oosDDは改善)=cull+taperのリスク制御は構造的に頑健。**②しかしNo-Go→Goの格上げはゼロ**: どのNo-GoペアもIS/OOS両>1.2に未達(最良CHFJPY 1.17/1.05, AUDNZD 1.23/1.15)。エッジが無いペアにエッジは作れない(過去の全Close結論と整合)。③worstはNZDJPY/USDJPYで微悪化(taperで深レッグ縮小→最古フルロットレッグがFSまでに損失を余計に蓄積する副作用、lv7深ラダーで顕著)。④EURCHFは今回もmom系が有害(IS 1.23→0.89)=ペッグ/管理通貨に効かない既知の留保を再確認。**結論=採用対象はGo 2ペア(AUDCAD/EURGBP)のみ・No-Goペアの稼働判断は不変**。成果物: `grid_dd_reduction_transfer.py`(+_result.csv)。

### ★結論: 順張り(トレンドフォロー)4戦略 10年BT → 全滅・JPYクロスでもエッジ無し（2026-06-09）
「BB逆張り/Grid平均回帰が負ける=トレンド性ペア(JPYクロス=キャリーで一方向に伸びる)こそ順張りの出番では」という仮説を11年Dukascopy 1hで検証(`optimizer/trend_10y_bt.py`)。共通執行(next-bar fill / シャンデリアATRトレイル trail3.0+初期SL2.0 / time stop / スプレッドJPY2.0pip往復差引 / ドテン)で4戦略×7ペアを回す。戦略=**DON**(Donchianブレイク)/**DON200**(+200SMAレジーム)/**EMAX**(EMAクロス)/**TSMOM**(時系列モメンタム)。対象=USDJPY/GBPJPY/NZDJPY/CHFJPY/EURUSD/EURGBP/AUDCAD。IS=2015-21/OOS=2022-26。採用ハードル=IS/OOS両方PF>1.2∧n>100。

- **1h足: 84通り全て full PF<1.0(最良GBPJPY DON ~0.98)・採用ゼロ**。勝率は全戦略~35%(典型的トレンドフォロー=低WRで大勝ち期待)だが、**勝ち幅がコスト+トレイル損を賄えず**ネット負。TSMOMはSharpe -5〜-10の壊滅。→1h順張りは明確にエッジ無し。
- **日足リサンプルで再検証(本来の土俵)でも採用ゼロ**: full PF>1.0は数例出るが、PF高い組(GBPJPY/CHFJPY/NZDJPY EMAX_50_200 PF1.4-1.8)は**全てn=15〜28で統計的に無意味**。n十分(>130)で唯一マシなのは日足**TSMOM_100(100日モメンタム)**= CHFJPY(full1.34/IS1.53/OOS1.17/年次黒字75%/Sh1.36, n139)・EURUSD(full1.21/IS1.14/OOS1.30, n163)。だが**PF>1.2を両側で超えず=ハードル未達**。弱い光明止まり。
- **総括**: BB(逆張り)・SMA Squeeze・Grid trend補完(案A/B/D全Close)・不感症trendレッグに続き**順張りも新規採用ゼロ**。これら主要/クロスFXペアは1hでは平均回帰でもトレンドでも頑健エッジを持たない(チョッピー)。トレンドフォローの古典的エッジ(日足/月次・先物商品)はFXメジャー1hには乗らない。仮説「JPYクロス=トレンド性で順張り有利」は**データで否定**(JPYクロスのDON/TSMOMもfull PF<1.0)。
- **次アクション**: 順張り探索も終了。リソースは確定エッジ(Grid AUDCAD + EURGBP)へ集約継続。日足TSMOM_100は「弱いが正方向」=もし将来追うなら日足のみ・スケール禁止・forward専用。成果物(全保持): `optimizer/trend_10y_bt.py` / `trend_10y_bt_result.csv`(1h) / `trend_10y_bt_daily_result.csv`(daily)。

### ★結論: SMA Squeeze 10年BT(2015-2026) → 頑健エッジ無し・エッジは直近USD相場レジーム依存（2026-06-09）
live v4.5/v4.6 を忠実に10.5年Dukascopy 1h(→4h resample)で検証(`optimizer/sma_squeeze_10y_bt.py`)。daily filter/T_max=24h/slope_exit=3/intrabar SL-TP/SMA_long break/cooldown180min/トレール無効 を全実装。IS=2015-06〜2022末(7.5yr)/OOS=2023〜2026.06(3.5yr)。EURUSD 1h dukasは本検証で新規取得(`data/EURUSD_1h_dukas.csv`, 68,515本)。

| ペア | FULL PF | IS PF | OOS PF | IS net | 黒字年 | 判定 |
|------|---:|---:|---:|---:|---:|---|
| USDJPY | 1.41 | **0.57** | 3.33 | **負** | 6/12(直近2023-26は4/4黒) | ❌IS赤字・OOSはレジーム依存 |
| EURUSD | 0.83 | 0.79 | 0.93 | 負 | 4/12 | ❌IS/OOS共<1.0=エッジ皆無 |
| GBPJPY(停止中) | 0.89 | 0.86 | 0.92 | 負 | 4/12 | ❌停止判断は正しい |

- **決定的所見=IS↔OOS逆相関(Grid動的化/BBと同型の過適合signature)**: USDJPYは**どのパラメータ(tmax 12-None/rr 1.5-4.0/squeeze 0.5-3.0)でもIS PFが0.6前後に張り付き正にできない**。OOSの高PF(2-3.3)は2023-26のUSD強気トレンド1局面に集中(年次: 2018-2022全敗→2023-26全勝)。**学習可能なstate→edgeでなくレジーム運**。実マネーUSDJPY黒字(PF1.42)はBBと全く同じ「短期窓の現象」。
- **構造的欠陥=T_max主導の24h保有モメンタム化**: USDJPY決済の87/159がT_max強制決済、TP到達はわずか6%(n=10)。「スクイーズ解放→トレンドフォロー(RR2.5でTP)」の建付けが**実際には機能しておらず**、本質は「24時間モメンタムベット」。T_max延長(48-120h/None)でTP率は上がるがPFは改善せず=トレンドが伸びていない。
- **EURUSDは即停止候補**: 全パラメータでIS<1.0、OOSも辛うじて0.93。tmax=None/rrで僅かにOOS>1.2に届くがIS=0.84で選択不能。**現在live有効だが10年エッジ皆無→停止が妥当**。
- **改善案の honest な結論**: rr 2.5→3.0 + squeeze_th 1.5 が FULL/OOS最良だが**IS赤字は不変**=curve-fit。Grid AUDCAD/BB と同じ「tail/PFは動かせるが頑健EVは作れない」構図で**新規採用ゼロ**。リソースは Grid AUDCAD/EURGBP へ集約すべき。USDJPYは現行micro-lot蓄積に留め**スケール禁止**、EURUSDは停止推奨。
- 成果物(全保持): `optimizer/sma_squeeze_10y_bt.py` / `_result.csv` / `_yearly.csv` / `_trades.csv`、`data/EURUSD_1h_dukas.csv`(再利用可)。

### Top of mind（2026-06-08 までの記録）

### ★結論: Grid 第2の有望ペア=EURGBP + モメンタム・ゲートの構造性確定（2026-06-08）
グリッド特性(平均回帰=レンジで稼ぎトレンドで焼ける。AUDCADがGoなのはAUD/CADが同じ資源ドライバ→クロスが独立トレンドを持たない為。No-Goは全てJPYクロス=キャリーでトレンド)から**相関通貨クロス3本**をDukascopy 11.5年1hで新規取得・検証(`optimizer/fetch_dukascopy_ohlc.py`にAUDNZD/EURGBP/EURCHF追加, `optimizer/grid_newpairs_bt.py`)。AUDCAD静的最良テンプレ(atr1.5/ci65/lv5, float_stopはquote_jpy比でprice距離一致)+モメンタムゲート(thr2.0, **再チューニングせず**)を適用。

| ペア | グリッド・エッジ(baseline) | +モメンタムゲート | 判定 |
|---|---|---|---|
| **EURGBP** | full PF **1.26**(atr2.0で1.57)/OOS1.22-1.70 | full→1.64/OOS→**1.94**/**全WFO fold>1.2**(min0.77→1.42)/nFS8→5 | ✅**第2の有望ペア** |
| AUDNZD | full1.08(atr1.5)/1.21(atr1.0)/OOS1.31-1.43 | full→1.23/IS→1.27/DD・nFS減だがWFOmin0.98残存 | △限界的(要atr1.0) |
| EURCHF | full1.22/OOS1.21/WFOmed1.07 | full→**1.01**/**DD2.7M→5.6M悪化** | ❌No-Go+ゲート有害 |

- **モメンタム・ゲートの構造性確定**: EURGBPに**閾値も再チューニングせず**適用して機能=AUDCAD curve-fitでない決定的証拠。プラトー(atr2.0×mom2.0-2.5): full PF~1.45-1.50/OOS**~2.47-2.51**/WFOmed2.2-2.5/**WFOmin1.30-1.43・全fold>1.2**。ゲート無しの弱fold(WFOmin0.66)を**ゲートが修復して全fold通過**=「トレンドへの追加建てを止める」狙い通り。閾値1.5-3.0で一貫(崖でない)。
- **重要な留保**: ①**EURCHFでゲートは有害(DD倍増)**=SNBペッグ→ジャンプ体制は平均回帰と異質。ゲートは「真に平均回帰する相関クロス」専用で管理通貨/ペッグには効かない=万能でない。②**EURGBPはDD高い(~3M)・worst単発-1.4〜1.9M**→AUDCAD(DD880k)比で必要資本約3-4倍。エッジは本物だがサイジング重い=**Step B再算定が必要**。③AUDNZDはatr1.5テンプレで弱く自然backbone=atr1.0・WFO限界的=二次候補。
- **総括**: GridのGoペアは AUDCAD単独 → **AUDCAD + EURGBP(要大きめ資本)** に拡張可能。モメンタム・ゲートは未チューニングで第2ペアに転移し弱foldを修復=構造的エッジ確定。**次アクション(任意)**: EURGBP forward-test前にStep B(必要資本/安全lot, DD~3Mで再算定) + EURGBPの自然backbone atr2.0採用可否を確定。
- **EURGBP 5m再検証(2026-06-09)**: バー内のTP/float-stop/B48発火順序を5mで解決するハイブリッド(1h意思決定/5m約定, `optimizer/grid_eurgbp_5m_bt.py`)。**エッジは1hアーティファクトでないと確定**=5m-hybridは1h(from5m)とほぼ同値(atr1.5 baseline 5m PF1.28/OOS1.20≈1h PF1.26/OOS1.22)、**worst単発はどの構成も5mで軽くなる**(-1.96M→-1.80M等)=1hが約定順序で楽観していた事実なし。モメンタムゲートも5mで同じく機能(thr2.0が弱fold min0.66-0.78→全fold>1.2 min1.34-1.40)。推奨=atr2.0+ゲート(5m OOS2.54/全fold>1.2)or atr1.5+ゲート(full1.68/OOS1.94/DD3.0M)。DD~3M・worst~-1.8MはAUDCAD比重い=Step B再算定前提。
- **データ資産追加(再利用可)**: `data/{AUDNZD,EURGBP,EURCHF}_1h_dukas.csv`(各≈71.5k本)+ `data/EURGBP_5m_dukas.csv`(858,583本, 2014-12〜2026-06)。成果物: `optimizer/grid_newpairs_bt.py`(+_result.csv) / `grid_eurgbp_5m_bt.py`(+_result.csv)。

### ★結論: Grid エントリー改善 → モメンタム・ゲートが初の頑健な改善候補（2026-06-08）
静的最良 AUDCAD(atr1.5)の負けパターンをエントリー条件で診断(`optimizer/grid_entry_analysis.py`, ポジション単位で文脈記録・集計は静的エンジンと一致)し改善案を検証。**結論: 「トレンドに逆らう追加建て」を抑止するモメンタム・ゲートが、過去全Close群と違い IS/OOS/WFO/構造性の全チェックを通過した初の採用候補**。
- **負けパターン(全損失は強制決済83ポジに集中, 全体WR90.1%)**:
  - **P1 不利トレンドへの追加**: 24hリターン(ATR正規化,t-1)がラダー逆方向に>2の建ては **PF1.02・gross_loss最大3.40M**(net+64k)=「ナイフ掴み」。
  - **P2 CIギリギリ建て(65-67)**: PF1.06(CI67-70はPF2.70)=境界エントリーは薄エッジ。
  - 根因=ゲートのCIは日足チョピネス＝遅く、数時間〜数日で育つトレンドを見逃す。負けポジは保有276h(塩漬け)vs勝ち66h。
- **改善案の検証(IS=2015-21凍結→OOS/WFO, baseline atr1.5基準)**:

  | 案 | full PF | full net | DD | nFS | OOS PF | 判定 |
  |---|---:|---:|---:|---:|---:|---|
  | baseline atr1.5 | 1.51 | 5.58M | 880k | 8 | 1.58 | 基準 |
  | **F1 モメンタム・ゲート(thr2.0)** | **2.48** | **8.63M** | 884k | 5 | **2.34** | ✅**前方検証候補** |
  | F2 CIファーム化(ci67) | 2.62 | 6.25M | 797k | 2 | 4.22 | ❌崖スパイク棄却 |

  - **F2棄却(過適合の罠)**: CI崖スキャンで PF 65→1.51/67→2.62/**70→1.35**・nFS8→**2**(11年で2回)=既知の「ci67.5スパイク警告」と完全同型。
  - **F1採用根拠(罠を全通過)**: ①プラトー(thr1.0-2.5でfull PF2.2-2.5・OOS2.2-2.6, 崖でない) ②薄標本でない(trade738→655) ③**IS-selectable(IS PF1.46→2.58)** ④**構造的**=mom2.0を他ペアv7に適用→CHFJPY net -1.3M→**+1.4M黒字反転**・GBPJPY損失縮小・nFS全般減=AUDCAD専用curve-fitでない。
- **正直な留保**: ①**テール/worst単発(-811k)とDDは不変→必要資本は変わらない**(改善は中bleedラダー除去であって破滅防止でない) ②2023年のみbaseline比悪化(1.44→1.01)・NZDJPYはfull PF低下=万能でない ③WFO中央の高値は無損失年(2022 inf)で水増し、信頼できるのはfull PF≈2.48。
- **実装方針(forward-test候補, 即liveでない)**: `vps/grid_monitor.py` エントリー判定に1条件追加=新規建て/レベル追加直前に24h ATR正規化リターン(t-1)を算出し、ラダー不利方向に thr(2.0-2.5)×超なら当該方向見送り(long:ret24≤-thr抑止 / short:ret24≥+thr抑止)。**CIファーム化(ci67)は不採用**。Step B必要資本はDD不変につき再算定不要。
- **モメンタム以外のエントリー改善案も検証(`grid_entry_filter2_bt.py`, baseline atr1.5)**:

  | 案 | full PF | OOS PF | IS PF | DD | nFS | 評価 |
  |---|---:|---:|---:|---:|---:|---|
  | B ADX(H1,t-1)ゲート thr35(>35で新規建て停止) | 1.77 | **1.92** | **1.67** | 882k(フラット) | 6 | ✅2番手 |
  | A 含み損アドオン抑止 dd0.5(ラダー含み損>予算50%で追加見送り) | 1.59 | 1.82 | 1.44 | 907k | 8 | ◯wfo_min1.41で最頑健だがIS flat |
  | C CI傾きゲート | 1.56 | 1.68 | 1.48 | 953k | 7 | ❌WFO中央低下・degenerate fold |

  - **⚠️A・Bは atr1.5 backbone 限定**: 現行live atr1.0 に乗せると無効(AUDCAD A1.32/B1.34≒base1.35)。クロスペアもまちまち(B:NZDJPY改善GBPJPY悪化 / A:CHFJPY改善NZDJPY悪化)。**モメンタム・ゲートだけが atr1.0/1.5 両backbone+他ペアで方向一致=最もbackbone-robust**。
  - **推奨**: 第一=モメンタム・ゲート(backbone非依存)。atr1.5採用時はB(ADX35)を重ねると追加リスク調整リターン最大(OOS1.58→1.92・DDフラット)。ワーストfold底上げ優先ならA(dd0.5)。Cは不採用。成果物: `grid_entry_filter2_bt.py`(+_result.csv)。

### ★結論: Gridパラメータ動的化 → 静的最良(AUDCAD atr1.5)を超えず＝不採用/Close（2026-06-08）
「10年同一パラメータ」を状態適応(atr_mult/float_stop/lot/max_levels/ci)に置換して静的baselineを risk-adjusted で超えられるか検証。**結論: 動的化は不要。静的 atr1.5 が Pareto最良のまま**。
- 手法: 専用エンジン `optimizer/grid_dynamic_bt.py`(=`grid_floatstop_bt.run_backtest`を1:1踏襲・バー毎パラメータ可変・**const paramで静的と完全一致を検証済**・lotはポジション毎保持・float-stopはper-position集計)。適応マッピングのHP(分位境界/写像値)は **IS=2015-2021のみで凍結**→OOS=2022-2026とWFO(純OOS年2022-25)で評価。状態量はt-1(shift済)。評価=`optimizer/grid_dynamic_eval.py`(5案)/診断=`grid_dynamic_diag.py`。

| 案(AUDCAD) | IS PF | OOS PF | WFO med | WFO min | full DD | 判定 |
|---|---:|---:|---:|---:|---:|---|
| **静的baseline atr1.5** | **1.46** | **1.58** | **1.64** | 1.32 | **880k** | 基準(Pareto最良) |
| 1 vol適応atr (IS最良=高vol狭め[1.5,1.5,1.0]) | **1.69** | 1.48 | 1.43 | 1.24 | 943k | ❌IS最良なのにOOS/WFO/DD全て劣化 |
| 2 vol-target lot/fs (clip0.7-1.3) | 1.22 | 1.63 | 1.61 | 1.41 | **2.03M** | ❌IS<base・DD2.3倍 |
| 3 vol連動max_levels [7,5,5] | 1.28 | 1.80 | 1.77 | 1.32 | 1.01M | ❌OOS光るがIS<base=選択不能・DD増 |
| 3 vol連動max_levels [7,5,3] | 1.21 | 1.77 | 2.83 | 1.63 | 1.40M | ❌同上(WFO spikeは薄標本) |
| 4 CI適応ゲート(分位点) | 1.00-1.22 | 1.23-1.33 | 1.32-1.38 | — | 2.8-3.2M | ❌全て劣化・DD激増 |
| 5(対照) ローリング再最適化 | — | **WFOmed1.02 / min0.76 / >1.2率0.25** | | | | ❌崩落=再最適化は過適合(既知) |

- **決定的論点=「IS↔OOSの逆相関」**: OSS/WFOで光る動的ルール(案2/3)は**例外なく IS PF が baseline(1.46)を下回る(1.21〜1.34)**→「マッピングはISで凍結」の規律下では**ex-anteに選べない**。その好成績は2022-25 benign窓のレジーム運(高volで浅くした副作用)であり学習可能なstate→param エッジではない。逆に唯一IS-selectableなルール(案1 高vol狭めIS PF1.69)はOOS1.48/WFO1.43/DD943kで**baselineに全敗**。
- **maxDD ガードレールも全案不通過**: baselineの880k(=半減済・必要資本160万の根拠)を全動的案が悪化(1.0M〜3.2M)。診断(`grid_dynamic_diag.py`)で案3のDD増は2016-17の低vol年に深ラダーが累積するため(2018/2020テールではなく)と確認=「benign局面で深くする」副作用そのもの。
- **NZDJPY(No-Go)救済も否**: dyn vol-maxlv[5,5,3]はOOS1.49/WFOmed1.49/min1.25とGo閾値を跨ぐが **IS PF=0.86(ISで負け)** =選択不能。No-Go不変。
- **総括**: 信号層・補完層・メタ層・不感症層に続き**動的(パラメータ適応)層も新規採用ゼロ**。レジーム層Close(`portfolio_regime.py`)と同じ「tail/DDは機械的に動かせるが頑健EVは作れない」構図。**実装方針=現状維持(静的 atr1.5 を forward-test 後に採用、vps/grid_monitor.pyは静的のまま)。動的オーバーレイは追加しない**。Step B再算定不要(baseline atr1.5のDD/必要資本が引き続き有効)。
- 成果物(全保持): `optimizer/grid_dynamic_bt.py` / `grid_dynamic_eval.py` / `grid_dynamic_diag.py` / `grid_dynamic_eval_result.csv`。

### ★結論: BB戦略 10年BT(2016-2026) → 全3ペア頑健エッジ無し（2026-06-08）
現行ローカルは約2年のみ。Dukascopyで **5m足10.5年(2015-12〜2026-06, 各≈78万本)** を取得(`data/{GBPJPY,USDJPY,EURJPY}_5m_10y.csv`)し、v29パラメータをIS(2016-2022 7年)/OOS(2023-2026.06 3.5年)分割で検証(`optimizer/bb_10y_bt.py`)。**ATRは1h足ATR14(ewm)＝実機(risk_manager.get_atr)整合**, next-bar fill, JPYスプレッド2.0pip往復差引。

| ペア | IS PF | IS n | OOS PF | OOS n | OOS net(pip) | OOS Sharpe | 判定 |
|------|---:|---:|---:|---:|---:|---:|---|
| GBPJPY | 1.048 | 2121 | **0.927** | 1286 | -4,904 | -0.43 | ❌OOS<1.0 |
| USDJPY | 0.914 | 8105 | **0.904** | 3953 | -6,707 | -1.12 | ❌IS/OOS共<1.0 |
| EURJPY | 0.935 | 5721 | **0.863** | 2812 | -6,244 | -1.75 | ❌IS/OOS共<1.0 |

- **判定基準(IS/OOS両方 PF>1.2 ∧ n>200)を満たすペアは皆無**。GBPJPYのみIS辛うじてPF1.048だがOOSで0.927へ崩落=エッジ非持続。USDJPY/EURJPYはISすら<1.0。
- **年次黒字率**: GBPJPY 8/12年・USDJPY 3/12年・EURJPY 1/12年。GBPJPYは「負けない年が多いが利幅が薄く累積で均衡〜負」、USDJPY/EURJPYは構造的に負。
- **戦略含意**: BB逆張り(5mバンド+1h ATR SL/TP+htf4h方向ゲート)は **10年スケールで頑健な期待値を持たない**。実マネーのBB USDJPY黒字(PF1.42/WR82.8%/n=64)は **短期窓(数ヶ月)の現象**であり長期では再現しない(10年OOS WR47.9%/PF0.90に回帰)。Grid AUDCADの「フル黒字でもWFOで崩落」と同種の過適合構図。
- **BTの限界(過小評価でない方向)**: 本BTは仕様書に従い **htf4h=方向一致のみ(RSI/BBwidthサブフィルタ省略)** の簡略v29。実機のRSI<60/BBwidth等の追加ゲートは「入る回数を減らす」方向で、母集団PFを劇的に正へ反転させる効果は薄い(既存知見と整合)。よって「フィルタ簡略のせいでPFが低く出た」可能性は低く、**長期エッジ不在の結論は頑健**。
- **次アクション**: ①Phase1のBB GBPJPY停止判断(7月)は本結果で補強=停止寄り。②USDJPYは実マネーn=100到達で再判定だが、10年BTは継続を支持しない=**過大ロット投入は禁止・現行少額蓄積に留める**。③BB戦略全体のスケールアップは非推奨、リソースはGrid AUDCADへ。
- 成果物(全保持): `optimizer/bb_10y_bt.py` / `bb_10y_bt_result.csv` / `bb_10y_monthly.csv`。
- **データ資産(再利用可・リポジトリ同梱)**: `data/{GBPJPY,USDJPY,EURJPY}_5m_10y.csv.gz`(各≈11MB, gzip, `data/`はgitignoreだが`git add -f`で強制追加済)。10.5年5m足の貴重な長期データ(再DLは約18分)。今後の5m依存BTはこれを流用すること。
  - 読込: `bb_10y_bt.py` の `load_5m()` は生CSV優先→無ければ`.gz`を透過読込(`pd.read_csv`が自動解凍)。他スクリプトからも `pd.read_csv('data/<SYM>_5m_10y.csv.gz', parse_dates=['datetime'])` で直接読める。
  - 再生成(必要時): `.venv_dukas/bin/python optimizer/fetch_dukascopy_ohlc.py --tf 5m --years 10.5 --pairs GBPJPY USDJPY EURJPY --suffix _10y`。

### ★結論: Grid実マネー化 検証完了 → Go=AUDCAD単独（2026-06-08）
Step0→C 完走。真値=Dukascopy 11年。**Go判定はAUDCAD 1ペアのみ**（GBPJPY/CHFJPY/NZDJPY全てNo-Go）。

- **Step0 データ真値（重要な前提修正）**: 同一エンジン・**同一timestamp集合**で yf-bars vs duk-bars 直接比較(`optimizer/grid_data_truth_diagnose.py`):
  GBPJPY 1.55↔1.58 / NZDJPY 2.37↔2.33 = **JPYペアは matched-bar で yf≈duk(ほぼ同一)**。CHFJPY 1.56↔0.82 / AUDCAD 3.50↔2.51 = データソースで乖離。
  → **旧『yfはヒゲ過小報告でPF過大』は不正確**: JPYペアのレンジ中央値比 duk/yf=0.96〜0.98でほぼ同等。AUDCADは逆に**yfがレンジを約1.8倍過大報告**(duk/yf=0.565, 下ヒゲ比0.22)=yf AUDCADデータ品質不良。
  GBPJPY等の「1.96→0.81崩落」の主因は**データ品質でなく窓**(2024-25 benign 2年 vs 11年harsh)。**結論は不変=真値はDukascopy**(JPYで一致・AUDCAD/CHFの過大を是正・常に保守側)。history.csv実約定は中央値乖離~11pip(JPY)でDukascopy価格水準と整合(弱いが矛盾なし)。
- **StepA 11年WFO(IS4yr→OOS1yr ローリング再最適化)が決定打**(`grid_dukas_stress_wfo.py`):

  | ペア | OOS PF中央値 | OOS PF最小 | OOS累積net | パラメータ安定 | 感応度崖 | 判定 |
  |------|---:|---:|---:|---|---|---|
  | **AUDCAD** | **2.04** | **1.33** | **+11.4M** | 概ね安定(atr2.0/lv7中心) | 崖なし(近傍PF1.1〜1.5) | **✅GO** |
  | NZDJPY | 0.88 | 0.42 | -3.69M | 不安定 | fs-500kでPF0.97 | ❌ |
  | CHFJPY | 0.89 | 0.72 | -0.25M | 不安定(atr/lv飛ぶ) | atr2.0でPF0.75 | ❌ |
  | GBPJPY | 1.03 | 0.65 | -0.56M | 4/7 foldでIS適格構成すら無し | 全PF<1 | ❌ |

  - **NZDJPYは11年フルではnet+2.84M(PF1.10)だがWFOでOOS崩壊=典型的過適合**(フル黒字≠頑健)。
  - AUDCAD v7 年度別: **9/12年 PF≥1.0**(フル年の負けは2018のみ。2015/2026は部分年)。worst単発-825k。
  - イベント窓ストレス: グリッドはテール局面の大半でCIゲートにより**休眠(無建玉)=テールを自動回避**。被弾は2018VolXmas/2020COVID等の散発のみ。
  - A4 ギャップ貫通: 全FSイベントで worst単発が float_stop設定を**100%超過**(中央値1.02〜1.05倍, 最大 GBPJPY1.83/NZD1.20/AUDCAD1.10/CHF1.02倍)。
- **StepB サイジング/破産確率**(`grid_sizing_ruin.py`, 月次ブロックブートストラップ20000回/60ヶ月, lot=1.0, AUDCAD円はCADJPY108概算):
  **AUDCAD**: 必要資本(破産<1%)=**req_cap_99≈310万円/lot1.0**(MC maxDD99%ile 3.10M が拘束。単発two-sided 1.36M はそれ以下)。req_cap_99.9≈405万。worst単発(gap込)907k。**安全lot=自己資本/310万**。
  月利30万円目標: AUDCAD **lot≈4.1 / 必要口座≈1,270万円**。他3ペアは負期待値で算定不要。
- **StepC Go/No-Go ゲート**(`grid_go_nogo.py`, 6ゲート G1 OOS PF中央値>1.2/G2 OOS最小>1.0/G3 OOS累積>0/G4 param安定/G5 崖なし/G6 req_cap<500万): **AUDCAD=oooooo(全通過)。他=全落第**。
- **AUDCAD 前方検証手順(段階的・少額)**: ①現行live=v7(magic20260034, ci65/atr1.0/lv5/fs-750k)を継続。②**lot=自己資本÷310万**で開始(例:口座100万→lot0.3、310万→lot1.0)。③期間=最低3ヶ月 **かつ TP決済≥30件 かつ float-stop/B48が最低1回発火するまで**(CHFJPY教訓=損切り発火前の黒字は生存者バイアス)。④継続条件: 損切り発火後も実現PF>1.2 ∧ FSスリッページ≤設定1.3倍。⑤撤退: 実現DD>MC中央値 ∨ 発火後PF<1.0 ∨ FS>設定1.5倍。⑥**2026は部分年で現在DD中(-517k)**=投入タイミングは現DD一服を確認後が無難。
- **任意の改善余地(forward-testで検証, 必須でない)**: AUDCAD ci_threshold 65→61.8 で同PF1.35のままnet約2倍(+4.8M→+10M)。atr_mult 1.0→1.5でPF1.35→1.51・DD低下。WFOはatr2.0/lv7/fs-1.5Mを選好(但しテール増)。

#### ★CI閾値ペア別最適化 検証（2026-06-08 / grid_ci_optimize.py）
ci_thresholdをペア別にスイープ(50〜70)し full11年 + IS/OOS + WFO-OOS(CIのみ可変)で評価。判定軸=full_PFでなく**OOS_PF と wfoOOSmed の同時維持/向上**(full_PF最大CIは過適合の罠)。
- **AUDCAD(Go): ci 65→62.5 推奨(安定プラトー上)**。net +4.8M→+8.6M(+78%) / OOS PF 1.39→1.42 / wfoMin 1.19(全7foldほぼ>1.2) / maxDD同等。61.8はnet最大(+10M)・maxDD最小(1.48M)だが1foldがPF0.88に沈む。**ci67.5はPF1.92と高いが70で崩壊する崖隣接スパイク(nFS=2・取引660件のみ)=過適合の疑い濃厚、不採用**。PF自体の頑健な底上げはCI単独では限定的(プラトー60-65=1.21〜1.35)。PF向上はむしろ atr1.0→1.5(1.35→1.51)が本命。
- **CHFJPY: 現行ci=65が誤設定**。65はfull PF0.95(net赤字)だが **ci55-60へ下げるとPF1.08-1.12へ反転**(ci60: full1.12/IS1.10/OOS1.14/wfoMin0.94, ci50: full1.08/net+11.4M/wfoMin1.05)。ただし全CIで<1.2=**No-Go据置**(損失は止まるがGo基準未達)。demo継続するなら ci55-60 が strictly better。
- **NZDJPY/GBPJPY: CI最適化で救済不可**。両者ともIS↔OOSがCIで逆相関(過適合signature)。GBPJPYは全CIで full net 負(最良ci65でも-1.6M)。NZDJPYはci65でOOS2.56と光るがIS0.74(=OOS幸運)・wfoMin0.45で頑健性なし。No-Go不変。
- 結論: **CI最適化が効くのはAUDCAD(net/DD改善)とCHFJPY(損失停止)のみ。No-Go→Goへの格上げは生まない**。CI単独ではAUDCADのPFは底上げされない(下記atrが本命)。成果物: `grid_ci_optimize.py`/`grid_ci_optimize_result.csv`。

#### ★★atr_mult ペア別最適化 検証（2026-06-08 / grid_atr_optimize.py）= 最重要改善
同規律(full11年+IS/OOS+WFO-OOS, atr_multのみ可変)でスイープ(0.75〜3.0)。**CIより効果大。AUDCADで全指標同時改善の真の当たり**:
- **AUDCAD atr 1.0→1.5 を強く推奨(最重要)**: full PF **1.35→1.51** / OOS PF 1.39→**1.58** / **maxDD 1.70M→0.88M(ほぼ半減)** / net +4.83M→+5.58M / WFO全7fold>1.2(wfoMin1.20)。0.75/1.0/1.25/1.5は一貫した良域=崖スパイクでなく頑健(崖は1.75-2.0で1.09へ、2.5の1.57は崖隣接スパイク=不採用)。**maxDD半減→必要資本ほぼ半減(req_cap_99 310万→~160万)→同資本でlot約2倍可**=実マネー効率の最大改善。
- 組合せ(atr×ci)検証: **atr1.5×ci65(=atr変更のみ)が最良**(PF1.51・maxDD880k・全fold>1.2)。ci61.8を足すとnet増(+8.2M)だがPF1.33・DD増・過適合リスク→**ci据置でatrのみ変更が最適**。
- **NZDJPY atr1.5→1.0 で改善(full1.10→1.20/OOS1.15→1.27/wfoMed1.08→1.20)も wfoMin0.78<1.0=No-Go据置**。CHFJPY atr1.0→0.75で赤字→黒字(full0.95→1.07)も<1.2=No-Go据置。GBPJPYは全atrでnet深く負=救済不可。
- 結論: **AUDCAD atr_mult 1.0→1.5 が CI/atr両スイープ通じ単一で最大の改善(PF+15%・maxDD半減・OOS頑健)**。forward-test後に採用推奨。他3ペアはatrでもGo化せず。成果物: `grid_atr_optimize.py`/`grid_atr_optimize_result.csv`。
- **他ペア方針**: GBPJPY/CHFJPY/NZDJPY は実マネーGrid**不可(No-Go確定)**。demo継続は可だがスケール禁止。「GBPJPY最優先」方針は**完全撤回**。
- 成果物(全保持): `grid_data_truth_diagnose.py`(+_result/_matched.csv)/`grid_dukas_stress_wfo.py`(+events/wfo/wfo_summary/sensitivity/gap_dist.csv)/`grid_sizing_ruin.py`(+result.csv)/`grid_go_nogo.py`(+scorecard.csv)。エンジン`grid_floatstop_bt.py`にfs_events/b48_events返却を追加。

### ★重大: Grid v7 PFはDukascopy高品質データで再現しない（2026-06-08）
Grid実マネー化検証のため Dukascopy(無料/口座不要/深い履歴)で **1h 11年(2015-06〜2026-06, 各≈68,500本)** を取得(`optimizer/fetch_dukascopy_ohlc.py` 1h/4h/5m/D1汎用, 出力 `data/<pair>_1h_dukas.csv` でyfinance版温存)。確定v7をそのまま回した結果(`optimizer/grid_dukas_reconfirm.py`):

| ペア | yf確定PF | **Duk 2年同窓PF** | **Duk 11年PF** | 11yr net | worst単発(設定比) | 評価 |
|------|---:|---:|---:|---:|---:|---|
| GBPJPY | 1.96 | **1.21** | **0.81** | -11.5M | -2.75M(設定-1.5Mの1.83x) | ❌実マネー不可 |
| CHFJPY | 1.51 | **0.89**(赤字) | 0.95 | -1.3M | -1.53M | ❌均衡〜負 |
| NZDJPY | 2.36 | **1.20** | 1.10 | +2.84M | -1.19M | △限界的 |
| AUDCAD | 4.01 | **1.82** | **1.35** | +4.83M | -825k | ✅11年で唯一頑健 |

- **真因(推定確度高)**: yfinanceのFX時間足は**高値/安値(ヒゲ)を過小報告**→TPが綺麗に刺さりfloat-stop発動が過少=PF過大。Dukascopyは実tick由来の正OHLCで**実機MT5(Axiory/Exness)に近い→真値に近い**。確定PF(1.96等)はデータ品質アーティファクトだった疑い濃厚。
- **戦略判断の反転**: ①**「GBPJPY最優先」は誤り**(yf＋2024-25 benign窓の偶然。11年 PF0.81/net-11.5M)。②**AUDCADが実は最頑健**(11年PF1.35・大半の年黒)→優先順位逆転。③**float-stopは損を止めきれない**(worst単発が設定を最大1.83倍超過=ギャップ貫通)。OOS-benign懸念は実データで裏付け。④CHF unpeg(2015-01)は範囲外(2015-06開始)で最悪テール未捕捉→`--years 12`で再取得余地。
- **次アクション=プロンプトB(下記)で本格検証**: まず(0)yf↔Dukascopy乖離の真因をバー単位で確定しデータ真値を決める→(A)Dukascopy 11年でv7再最適化＋実テール局面ストレス→(B)テール→必要証拠金/安全lot/破産確率→(C)go/no-go。**実マネー投入は本検証完了まで全ペア保留**(特にGBPJPY)。
- 成果物保持: `fetch_dukascopy_ohlc.py`/`grid_dukas_reconfirm.py`/`grid_dukas_reconfirm_result.csv`/`data/*_1h_dukas.csv`(GBPJPY/CHFJPY/NZDJPY/AUDCAD/USDJPY)。

### Grid不感症ウィンドウ補完探索（案A CI逆ゲート / 案B bleedヘッジ）2026-06-08 ★結論
角度を変えた探索: 「Gridが効かない局面=不感症」に条件づけて稼ぐ補完スリーブを探す。Grid v7 4ペア(GBPJPY/CHFJPY/NZDJPY/AUDCAD)で `data/*_1h.csv` 2年・IS≤2025-06/OOS≥2025-07・next-bar fill。

**不感症ウィンドウ定義**(`optimizer/grid_insensitivity.py`, t-1のみ): idle=Grid建玉ゼロ&TPなし(完全休眠) / bleed=float-stop/B48発火 or 含み損が float_stop の30%超過 / flat=20日ローリングGrid損益の傾き≈0(非discriminating→union除外・診断用)。union=idle|bleed。
- **判明: Gridは大半の日が不感症**(idle|bleed: GBPJPY IS307/348・CHFJPY 333/348・NZDJPY 284・AUDCAD 218; OOS 175-231)。GridはCI>閾値のレンジ稀少バーストでのみ建て、純益はnormal(レンジ)日に集中。**不感症日のGrid IS損益はむしろ負**(GBPJPY -1.57M / CHFJPY -1.15M = bleed日のドラッグ)。→補完が稼ぐ余地は構造的にある。

| 案 | 設計 | 結果 | 判定 |
|----|------|------|------|
| **A CI逆ゲート・マルチペア trend** | 4ペアDonchian breakout+ATRトレイル, CI≤閾値(=トレンド)のみ稼働, next-bar | 全16構成 net_R負・PF0.54〜0.79・Sharpe全負(IS/OOS)。Grid相関≈0(0.05)だがエッジ無し。ブレンドはGrid劣化(IS PF1.85→1.50 / OOS5.20→2.32 @200k) | ❌**Close** |
| **B bleedヘッジ** | Grid建玉が含み損trig接近時に同トレンド方向へ小ロット建て梯子の損失相殺(grid+hedge同一ループ) | hedge_net 全ペア/全構成/IS&OOSで負(平均-77k〜-391k)。maxDD/worst単発は微減するがPF低下(GBPJPY1.23→1.16 / AUDCAD3.99→2.57) | ❌**Close** |
| C 金利差モメンタム | 外部FRED/VIX依存＋案Aと同根 | 未実施(A/B同根で結論不変・外部データ不要方針) | — 不採用 |

- **A真因**: 平均回帰ペアでtrend/breakoutレッグ自体に頑健エッジ無し(pullback案B/D/A単一GBPJPYのClose結論が**マルチペアでも不変**と確認)。CI逆ゲートで「いつ入るか(トレンド時のみ)」は制御できてもエッジは作れない。comp_normR=0=ゲートは機能(全PnLが不感症日に着地)するが負。
- **B真因**: 深いDD(価格が逆行伸長)でトレンド方向ヘッジ→直後に**平均回帰(=Gridが本来稼ぐ動き)でヘッジが踏まれる**。テールは機械的に圧縮できるが負EV。「DD圧縮を負EV保険で買う」典型。採用基準(DD縮小**かつ**Sharpe維持)を両立せず。
- **総括=「相関は狙い通り≈0だがエッジ無し」4例目**(配分A/レジームB/α三角/今回trend)。信号層・補完層・メタ層・不感症条件づけ層すべて新規採用ゼロ。Gridの稼ぎはレンジ稀少バーストに構造依存し、その隙間(トレンド)を埋める頑健エッジはこの平均回帰ペア群に存在しない。
- 成果物(全保持): `optimizer/grid_insensitivity.py`/`grid_insensitivity_complement.py`/`grid_bleed_hedge.py` + `grid_insensitivity_complement_result.csv`/`grid_insensitivity_complement_trades.csv`/`grid_bleed_hedge_result.csv`。
- **次アクション**: 不感症補完探索も終了。CLAUDE.md既存方針(Grid GBPJPY最優先・AUDCAD次点 + BB USDJPY実マネー蓄積)へ集約継続。

### ポートフォリオ・メタ層探索（配分A / レジームB）2026-06-07 ★結論
信号層が枯渇（BB/CORR/stat_arb三角/London/pullback/pre_event/session 全Close）したため、**既存の黒字エッジの上に乗せるメタ層**でrisk-adjusted return底上げを探索。素材=Grid v7 4ペア＋BB USDJPY。`data/*_1h.csv` 2年(729日)・IS≤2025-06/OOS≥2025-07・next-bar fill・全ウェイト/ゲートt-1のみ。エンジンはv7 PF(1.96/1.51/2.36/4.01)再現済み。

| 案 | 判定 | 根拠 |
|----|------|------|
| **A 配分層(vol-target/risk-parity)** | ⚠️**条件付きClose(弱パス)** | FULL/IS で+38%/+62% Sharpe・-26〜35% maxDD と機構は本物。だが**事前登録のOOS Sharpe+20%は未達**(OOSがGridにbenignすぎてbase OOS Sharpe=4.90→+20%=5.88に届かず)。IS最良(risk_parity_L20 IS3.55)はOOS3.39へ崩落=動的スキームの過適合も確認 |
| **B レジーム層(ADX/ERゲート)** | ❌**Close** | tail(worst単発)を消すにはforce-exitが必要だがnet retain20〜65%で基準割れ。noaddはnet改善(GBPJPY IS net2倍超)だが**worst単発不変**(-1.62M→-1.62M, float-stopは既存ポジで発動しゲート不可)＋2025春GBPJPY単一イベント依存＋OOS検出力ゼロ |
| C 相関レジーム | 保留 | A段階で**スリーブ間相関≈0**(最大0.27 GBPJPY×CHFJPY、他±0.08)判明→"崩れる相関"前提が薄く優先度低 |

- **最重要前提=相関≈0**: 「全部mean-reversionだからtailが同時に出る」仮説は外れ。各Gridは別ペア・別タイミング発火で分散が効く。**固定1ロットは非効率配分**: 最高Sharpe(2.93)・最低vol(41k)のAUDCADが最低Sharpe(1.06)・最高vol(122k)のGBPJPYと同ロット=GBPJPYにリスク偏重。
- **BB USDJPY**: 実マネー確定エッジ(PF1.42)だが2年5m無し(yfinance~3mo/MT5 0配信)→1h近似プロキシはPF0.84の負け(WR82%再現不能)。極小volで素朴な逆volだと最低volのBBに最大ウェイト=毒→配分層は正Sharpe4 Gridに限定。
- **唯一の低リスク示唆(A由来・動的不要)**: `vps/grid_monitor.py` の `LOT_PER_PAIR` を静的inverse-vol寄りに微調整(GBPJPYロット↓・AUDCAD/CHFJPY/NZDJPY↑)。動的リバランス層/新規monitorは過適合・運用複雑化に見合わず不要。OOS厳格基準は未通過のため実行は任意・少額前方検証推奨。
- 成果物(全保持): `optimizer/portfolio_meta_bt.py`/`portfolio_alloc.py`/`portfolio_regime.py` + `portfolio_meta_sleeves.csv`/`portfolio_alloc_result.csv`/`portfolio_regime_result.csv`。
- **次アクション**: メタ層探索も一旦終了。信号層・補完層・メタ層すべて新規採用ゼロ→ CLAUDE.md既存方針(Grid GBPJPY最優先・AUDCAD次点 + BB USDJPY実マネー蓄積)へ集約継続。

### 新規エッジ探索（案α / pre-event / session）3件棚卸し（2026-06-07）★結論
方針転換: Grid補完(案A/B)Close後、Gridと構造的に独立な領域で新規エッジを探索。3件をローカル2年データ＋現実的執行で再検証。

| 戦略 | 再検証条件 | 結果 | 判定 |
|------|-----------|------|------|
| **案α 非JPY三角stat_arb** | EURUSD/GBPUSD/EURGBP, OU平均回帰, IS/OOS, next-bar fill | 全81組合せ IS PF≤0.16/OOS≤0.30, 全Sharpe負 | ❌ **Close** |
| **pre_event vol_squeeze** | NFP/CPI実日付(news_events.csv)＋ローカル2年1h | 全8sweepセル PF≤0.72/Sharpe全負 | ❌ **Close** |
| **session_fakeout** | Dukascopy 2年5m(148k本)＋全16sweep | 全セル PF≤0.82/Sharpe全負(n最大629)。3moで見えたtp0.3正エッジは小標本の偶然 | ❌ **Close** |

- **案α真因**: 三角恒等式 EURGBP=EURUSD/GBPUSD はclose上ほぼ完全成立。乖離=非同期クォートノイズ(std1.3pip/半減期<1足)。当初「信号バー終値で約定」モデルがPF400+を出したが**バー内ルックアヘッド**。next-bar fillで全崩壊。独立性は完璧(Grid相関≈0)だが領域にエッジ無し→案A/B/αで「相関は狙い通りだがエッジ無し」3例目。スクリプト: `optimizer/stat_arb_nonjpy_bt.py`。
- **pre_event真因**: 近似カレンダーのズレ由来ではなく**本物の負エッジ**(実日付化でPF0.756→0.694と悪化)。NFP/CPI前ボラ縮小×BB逆張りにエッジ無し。FOMCはnews_events.csv未収録で実日付検証不可(旧FOMC-only PF1.57は近似日×n25×DD174%で未検証のまま放置)。`optimizer/pre_event_vol_squeeze_bt.py` に `--local`/`--real-cal` 追加済み。
- **session_fakeout知見(最終)**: 当初yfinance(58日/n27/PF0.33)はtp0.5の1点で見誤り→ローカル3mo(n≤72)では「tp0.3でfake_pips横断の正エッジ(PF1.15〜1.77)」に見えた。**だがDukascopy 2年5m(148k本)で再検証すると全16sweepセルが PF≤0.82・Sharpe全負(n最大629)。tp0.3の正エッジは小標本の順行窓アーティファクトと確定→Close**。「3moの正PFは2年で消える」典型例。
- **データ源教訓**: `data/*_1h.csv`=2年だが**`data/*_5m.csv`は元々~3moのみ**(update_data.pyがyfinance依存=5m遡及不可)。VPS MT5 demo(Axiory/Exness Trial)も**深い5mを配信せず copy_rates=0**(probe直近10本すら0)→ MT5依存は不可。**解決=Dukascopy(無料/口座不要/深い履歴)をローカル取得**: `optimizer/fetch_5m_dukascopy.py`(専用venv `.venv_dukas`)。`vps/export_5m_history.py`はMT5不可と判明したため非常用(成果物保持)。5m依存BTは今後Dukascopyで取得すること。
- **次アクション**: 案α/pre_event/session 3件すべてClose。新規エッジ探索は一旦終了し、CLAUDE.md既存方針(Grid GBPJPY最優先・AUDCAD次点 + BB USDJPY)へリソース集約。成果物BT/CSV/スクリプトは再検証用に全保持。

### Trend補完戦略（案B/D/A）全BT完了 → 打ち切り（2026-06-07）★結論
- **判定: Grid補完用 Trendレッグ（GBPJPY Donchian/ATRトレイル）は3案すべて不採用。アイデア打ち切り、Grid/BBへ集約。**
- 動機: GridはトレンドでDDを踏む。負相関のTrendをブレンドすればGrid単体よりPF改善/DD圧縮できるはず、を検証。
- スクリプト: `optimizer/pullback_trend_bt.py`(案B) / `optimizer/pullback_gated_trend_bt.py`(案D/A) / `optimizer/pullback_grid_complement.py`(補完指標)。IS 2024-04〜2025-06 / OOS 2025-07〜2026-05。
- 採用基準: IS[Grid DD上位5区間Trend損益>+5R & PF>1.1] / OOS[同>0R]。

| 案 | 設計 | Grid DD窓Trend損益(IS) | 単体PF | 結果 |
|----|------|----------------------:|-------:|------|
| B | 常時稼働 Donchian breakout | ≈+1.2R | <1.0 | ❌ DD窓"不在"（corr≈0）で補完不成立 |
| D | Grid含み損ゲート(残高比-2/-3/-5%) | **+9.6R** | <1.0(最高0.91) | ❌ 窓不在は解消も、単体PF<1.1でブレンドがGrid劣化 |
| A | ゲート深掘り(-10〜-25%=float-stop近傍) | +4〜6R(減少) | -25%でPF1.12 | ❌ OOS ddwin全0.00 / PF>1.1は開放n=1の過適合 |

- **3案共通の真因: GBPJPY trail-following のTrendレッグ自体に頑健エッジが無い。** ゲートで「いつ入るか」は最適化できてもエッジは作れない。
- 案A深掘りの知見: ①OOSのGrid DD窓は他ペア(CHF/NZD)主導で浅く、GBPJPY単体の深ゲートはそこで開かず→OOS補完=0.00。②深いDDでのトレンド順行ペイオフは過去2年で**2025春GBPJPYの単一イベント**のみ、OOS再現せず。③ブレンドPFはGrid単体(GBPJPY 1.87 / COMBINED 1.20)を全構成で劣化。相関は最良でも-0.28(目標<-0.3に未達)。
- **アクション: Trend補完は打ち切り。Grid(GBPJPY最優先・AUDCAD次点)とBB USDJPYにリソース集約**（CLAUDE.md既存方針と整合）。成果物BT/CSVはリポジトリ保持（再検証用）。

### Grid CHFJPY 実残高ベース評価（2026-06-02）★最優先タスクの結論
- **判定: 実マネー・エッジは未確認。demo口座の小サンプル × トレンド順行窓での額面利益にすぎず、スケール不可。**
- history.csv の額面 +187,768円（magic=20260032 / WR100% / n=8）の内訳:
  - 実ロット(1.0)は **5/28 21:03以降の約3.5日・実質5決済のみ**（それ以前は0.01の検証ロットで損益ほぼ0）
  - 期間中 CHFJPY は 201.9→204+ の上昇トレンド。buyが上昇を捕捉、sellは押し目の平均回帰で小利確 → **逆行トレンドでfloat-stopが発動した実績はゼロ（生存者バイアス）**
- WR=100%はグリッドの構造的必然（TPだけ計上、含み損は B48/float-stop まで保有）。実残高評価には**不利トレンドで損切りが発動する局面のデータが必須**だが未取得。
- **テールリスク（grid_monitor.py コメント記載値）**:
  - CHFJPY 1.0lot: FLOAT_STOP=**-1,500,000円/方向**、B48両建て最悪ケース **約-2,250,000円**
  - つまり実現+18.8万円に対し最悪-150〜225万円 → 実現益:テール ≈ 1:8〜1:12 の「ペニー拾い」プロファイル
- **結論アクション**: 実マネー移行は保留。最低でも(a)float-stop/B48が一度発動する不利局面を含むdemoデータ、(b)その損切り後も実現エッジが残るか、を確認するまでスケール禁止。NZDJPY/AUDCAD(lv7/1.0lot追加分)も同様に未検証。

### Grid float-stop込み2年BT 全5ペア（2026-06-02 / grid_floatstop_bt.py）★(a)(b)を過去データで実施
- ライブ設定(LOT_PER_PAIR / atr_mult / max_levels=7 / B48=48h / FLOAT_STOP_PER_PAIR)に忠実、各ペア_1h 2024-04〜2026-04(約12,330本)で検証。非JPYは quote_jpy 係数でJPY換算(NZDUSD≈155, AUDCAD CADJPY≈108)。検知=バー逆行extreme(保守的)。
- **(a)達成**: float-stopが全ペアで複数回発動(下表)= 不利トレンド局面を十分に内包。
- **(b)結論（PF=net・float-stop損込み）**:

| ペア | lot | PF | 2年net円 | TP | float-stop | worst単発 | maxDD | 判定 |
|------|----:|---:|---------:|---:|-----------:|----------:|------:|------|
| GBPJPY | 1.0 | **1.96** | +5,965,956 | 269 | 4回-6.20M | -1.62M | 3.40M | ✅生存(最良) |
| AUDCAD | 1.0 | **1.26** | +2,625,094 | 585 | 19回-10.27M | -0.60M | 1.08M | ✅生存(要CADJPY前提) |
| NZDUSD | 0.01 | 1.81 | +15,905 | 121 | 1回 | -0.02M | 0.02M | △microロット・額僅少 |
| NZDJPY | 1.0 | **0.96** | -428,291 | 562 | 18回-10.02M | **-0.75M** | 2.03M | ❌均衡〜負 |
| CHFJPY | 1.0 | **0.70** | -3,757,313 | 164 | 8回-12.46M | -1.62M | 5.72M | ❌非生存(最悪) |

- **PFはquote_jpy(FXレート想定)にほぼ不感**（TP/float-stop両方が同係数でスケールしgross比は不変）→ AUDCAD/NZDUSDの正エッジ判定は頑健。net円額のみレート想定で変動。
- NZDJPYは float_stop=-500k 設定だが worst単発-749k → **逆行extreme/ギャップで閾値を超過**。実機の単発損は閾値で完全には止まらない点に注意。
- B48は全ペアn=0（float-stopが先に発動）。
- 旧BT(CHFJPY IS PF=1.023/OOS 1.521)はfloat-stop未実装・lot0.02 → **float-stop現実でCHFJPY負に反転、過大評価だった**。
- **次アクション（2026-06-02完了）**: ①CHFJPY→v6/v7で再設計済み（ci65/atr1.0/lv3 BT PF=1.51 ✅反転）②NZDJPY→v7で最適化済み（ci61.8/atr1.5/lv7/fs-1.0M BT PF=2.36 ✅反転）③Grid実マネー候補は GBPJPY最優先・AUDCAD次点（DD吸収資金が前提）→ demo前方検証中。

### Grid パラメータ最適化（2026-06-02 / grid_param_sweep.py + grid_param_validate.py）★B48デッド対策
- **「B48 n=0」の真因**: max_levels=7 で最大深度の累積含み損が深く、48hを待たず float-stop(-1.5M) が先に発動 → B48デッド。b48_hours短縮(24/36h)はほぼ無効。
- **本丸の修正 = max_levels 7→3/5（+ ci_threshold 61.8→65）**: ラダーを浅くすると B48(マイルドな時間決済)が float-stop より先に効き(n_b48 が 0→8〜15)、**単発損とDDが激減・PFも改善**。負けペアが正に反転。
- IS/OOS 半々分割で両期間 net>0 & PF≥1.2 & 損失イベント実発生(過適合除外)を満たす頑健構成を選定。推奨（フル2年）:

| ペア | 変更(ci/atr/lv) | PF | net円 | n_tp | n_b48 | worst単発 | maxDD | vs LIVE |
|------|-----------------|---:|------:|-----:|------:|----------:|------:|---------|
| CHFJPY | 61.8→65 / 2.0→1.0 / 7→3 | **1.51** | +1,412,119 | 146 | 9 | -0.65M | 0.61M | -3.76M→+1.41M ✅反転 |
| NZDJPY | 61.8→65 / 1.0→1.5 / 7→5 | **1.65** | +1,915,527 | 190 | 1 | -0.58M | 0.66M | -0.43M→+1.92M ✅反転 |
| AUDCAD | 61.8→65 / 1.0 / 7→3 | **2.75** | +4,275,296 | 303 | 15 | -0.31M | 0.31M | +2.63M→+4.28M ✅大幅改善 |
| GBPJPY | — | 1.96 | +5,965,956 | 269 | 0 | -1.62M | 3.40M | LIVE維持が最良(PF最高) |

- **GBPJPY注意**: LIVE(lv7/atr1.5)が既にPF1.96で最良・IS/OOS両正。浅化(atr3.0/lv3)はDD 3.4M→1.74Mに圧縮できるがPFは1.26に低下 → **純益重視ならLIVE維持、DD抑制重視なら浅化**の選択（任意）。
- 検証注意: B48=48h据置(短縮効果なし)。worst単発はギャップで閾値超過しうる。AUDCAD net円はCADJPY想定でスケール(PFは不感)。
- **次アクション**: vps/grid_monitor.py の PAIR_CONFIG を CHFJPY/NZDJPY/AUDCAD で上記に変更 → demo再蓄積で前方検証。【v7で実装済み・下記参照】

### Grid float_stop結合最適化（2026-06-02 / grid_floatstop_sweep.py）★v7実装済み
- float_stop自体を最適化変数に追加。**知見: float_stopは深いラダーのテール保険としてのみ有効。浅いラダーでは緩め固定が正解**（きつくすると回復可能ポジを切りPF・DD悪化: CHFJPY lv3 fs-300k→PF0.98/DD980k vs fs-1.0M→PF1.51/DD608k）。
- **NZDJPY: float_stop -500k→-1.0M に緩めると lv7 が復活** → PF1.65→2.36 / net +1.92M→+4.55M（ci 65→61.8, lv 5→7）。テール -576k→-1.05M。
- **AUDCAD: lv3→5 / fs-500k→-750k** → PF2.75→4.01 / net +4.28M→+5.50M。テール -315k→-445k。
- CHFJPY/GBPJPY は v6据置（float_stop非発動/純益最良）。IS/OOS頑健性確認済み（NZDJPY IS2.19/OOS2.65, AUDCAD IS5.61/OOS2.92）。
- NZDJPY/AUDCAD は新float_stopに合わせ DD_DAY/DD_WEEK も緩和（ブレーカーが検証済み決済を先回りしないため）: NZDJPY DD_DAY-1.0M/DD_WEEK-2.0M, AUDCAD DD_DAY-750k。

### 取引実績集計（2026-06-03 / history.csv 4/24-6/2, 418決済）
- **実マネーで黒字エッジ確認は BB USDJPY 1本のみ**（PF1.42 / WR82.8% / n=64 / +11,960円）
- BB GBPJPY: PF0.95 / WR84.0% / n=25（σ=2.0後もまだPF<1.0、均衡圏）
- BB停止ペア(EURUSD/GBPUSD/USDCAD)の4月歴史的損失 -130k が総額を圧迫（現行非稼働）
- **SMA Squeeze: n=20 WR=0% -19,285円はv4.5以前（trailing有効時）のデータ。v4.5（trailing無効化・GBPJPY停止）適用後はサンプルリセット・n=0スタート。** v4.5でn=10以上蓄積後に再評価。
- 月次額面: 4月-67.5k / 5月+74.5k / 6月(2日時点)+71.5k だが、5月後半以降の黒字は**Grid CHFJPY(demo)が実体**。実マネー実体は概ね月±1万円台

### 現在の稼働状態（2026-06-03時点）
- **sma_squeeze**: v4.5 / axiory/exness（oanda停止中）
  有効ペア: USDJPY/EURUSD / 停止: GBPJPY（v4.5新規停止）・GBPUSD・EURJPY
- **bb_monitor**: v27 / 3ブローカー（Task Scheduler毎分実行）
  **【VPS未反映】git pull → bb_monitor再起動が必要**
- **trail_monitor**: v15 / axiory/exness・oanda（独立プロセス）
- **grid_monitor**: v7 / axiory/exness（NZDUSD停止 / GBPJPY 20260031 / CHFJPY 20260032 / NZDJPY 20260033 / AUDCAD 20260034）
  **【VPS未反映】v7 push → git pull → restart_grid.ps1 で全grid再起動が必要**
  v7確定: GBPJPY(61.8/1.5/7/-1.5M) CHFJPY(65/1.0/3/-1.5M) NZDJPY(61.8/1.5/7/**-1.0M**) AUDCAD(65/1.0/**5**/**-750k**)
- **news_monitor**: v1 / 未起動（news_monitor.bat 起動待ち）
- MT5端末起動順: OANDA→(60秒後)Axiory→Exness

### bb_monitor.py v27（2026-05-28完了）
- **GBPJPY bb_sigma 1.5→2.0**: BT全データ PF 1.019→1.275（N=268）達成
- BT実施: bb_analysis_bt.py（sigma sweep + SL×sigma グリッド）
- 実RR=0.12 の根本原因確定:
  - 実機: `rm.get_atr()` = H1足ATR14（ewm）
  - BT: `calc_atr(df_5m)` = 5m足ATR14（rolling mean）
  - H1/5m ATR比率: GBPJPY=3.73倍、USDJPY=3.91倍
  - → BT SL/TP は実機より約4倍小さい（BT sl=2.5 ≈ 実機 sl=10倍相当）
  - → 実稼働WR=82%は「H1 ATR基準の大きなSLが早期にストップされにくい」ため

### Phase1判定進捗（BB 全期間, 2026-06-08更新, magic=20250001）
| ペア   |    PF | 勝率  | n  | 総合  |
|--------|------:|------:|---:|-------|
| USDJPY | 1.479 | 83.3% | 66 | ⚠️ PF合格・n不足のみ（n=100まであと34件≈3週）、σ=2.0維持 |
| GBPJPY | 0.950 | 84.0% | 25 | ❌（PF未達/均衡圏）σ=2.0後も<1.0、PF>1.2転換 or 停止を7月判断 |
| EURJPY | 0.254 | 66.7% | **9** | ❌ **バグn=71除外・n=9からリセット（2026-06-08）**。正常サンプル蓄積中。バグ事件記録は上記参照 |
| EURUSD | — | — | — | enabled=False（BT PF<0.7/v20停止） |
| GBPUSD | — | — | — | enabled=False（実稼働PF=0.294/v20停止） |

### USDJPY Phase1確定への分析結果（2026-05-28）
- BT全データ PF=1.159（5m ATR基準）→ H1 ATR補正後は実稼働 PF=1.355 が正
- bb_sigma=2.0の妥当性: σ=1.5(PF=0.835) vs σ=2.0(PF=1.161) → σ=2.0が優位
- T_max=8h+exp Decay: OOS PF=1.137→1.211 (+6.5%)、v26実装根拠確認済み
- → n=100到達まで蓄積継続（現在n=66、目安あと3週間）

### 翌日確認事項
- **【最優先】VPS**: `git pull origin main` → bb_monitor.bat 再起動（v27反映）
- **【要対応】VPS**: `news_monitor.bat` 起動（axiory/exness）
- bb_monitor v27 動作確認: GBPJPY の `シグナル確定` ログで `BB_σ=2.0` を確認
- Phase1 USDJPY: n=100超えたら再判定（目安あと3週間）
- **EURJPY バグ後の正常稼働確認**: v29クールダウン修正が効いているか（15分以内の再エントリーが抑制されているか）ログ確認
- backtest.py BT精度向上（任意）: simulate_with_stage2をH1足ATRに切り替え
- **BB戦略 10年バックテスト**: ローカルClaudeCodeに依頼中（Dukascopyデータ取得 + 全ペアIS/OOS評価）

## 直近タスク
- [x] BB戦略 実稼働vsET乖離分析（2026-05-28: H1/5m ATR比率定量化）
- [x] GBPJPY bb_sigma最適化BT（2026-05-28: sigma=2.0でPF>1.2達成）
- [x] USDJPY Phase1補強分析（2026-05-28: σ=2.0維持・T_max有効性再確認）
- [x] bb_monitor v27: GBPJPY sigma 1.5→2.0・push済み（2026-05-28完了）
- [x] strategy_spec.md / strategy_spec.html 更新（2026-05-28完了）
- [ ] **VPS**: `git pull origin main` → bb_monitor.bat 再起動（v27反映）
- [ ] **VPS**: `news_monitor.bat` 起動（axiory/exness）
- [ ] Phase1 USDJPY: n=100超えたら再判定（あと36件≈3〜4週間）
- [ ] backtest.py BT精度向上: simulate_with_stage2をH1足ATRに切り替え（任意）
- [x] **Grid CHFJPY 実残高ベース評価（2026-06-02完了）**: demo小サンプル・トレンド順行窓・テール-1.5〜2.25M円 → 実マネー移行保留
- [x] **Grid float-stop込み2年BT 全5ペア（2026-06-02完了 / grid_floatstop_bt.py）**: GBPJPY PF1.96✅ / AUDCAD PF1.26✅ / NZDUSD PF1.81(micro) / NZDJPY PF0.96❌ / CHFJPY PF0.70❌
- [x] **Grid CHFJPY**: v6/v7で再設計完了（ci65/atr1.0/lv3/fs-1.5M → BT PF=1.51 ✅反転）demo前方検証中
- [x] **Grid NZDJPY**: v7で最適化完了（ci61.8/atr1.5/lv7/fs-1.0M → BT PF=2.36 ✅反転）demo前方検証中
- [ ] **Grid 実マネー候補選定**: GBPJPY最優先・AUDCAD次点。DD(3.4M/1.1M)・単発損(-1.62M/-0.60M)を吸収できる資金計画を策定
- [x] **Grid パラメータ最適化（2026-06-02完了 / grid_param_sweep.py + grid_param_validate.py）**: 真因=lv7でfloat-stop先行→B48デッド。lv7→3/5+ci65でCHFJPY/NZDJPY反転・AUDCAD改善（IS/OOS頑健確認）
- [x] **vps/grid_monitor.py v6 実装（2026-06-02完了）**: CHFJPY(ci65/atr1.0/lv3)・NZDJPY(ci65/atr1.5/lv5)・AUDCAD(ci65/atr1.0/lv3)。per-pair ci_threshold追加(CI_TH)。strategy_spec.md/html同時更新済み
- [x] **vps/grid_monitor.py v7 実装（2026-06-02完了）**: float_stop結合最適化。NZDJPY(61.8/1.5/7/-1.0M)・AUDCAD(65/1.0/5/-750k)更新+DD緩和。CHFJPY/GBPJPY据置。spec md/html・restart_grid.ps1同時更新
- [x] **VPS**: `git pull origin main` → restart_grid.ps1 で全grid再起動（v7反映）→ demo前方検証（2026-06-03完了）
- [ ] **GBPJPY浅化(任意)**: DD抑制重視なら atr3.0/lv3 化を検討（PFは1.96→1.26に低下）
- [ ] **SMA Squeeze 存続判定**: v4.5（trailing無効・GBPJPY停止）で新サンプルn=10到達まで蓄積、正転しなければ全停止しGrid/BBへリソース集約
- [ ] **BB GBPJPY**: 7月までにPF>1.2転換なければ停止、Phase1をUSDJPY単独合格で締める判断
- [x] **EURJPY BBバグポジション決済・nリセット（2026-06-08完了）**: バグ71件を除外、正常n=9からPhase1再蓄積。バグ事件・v29修正をCLAUDE.md/strategy_specに記録済み
- [ ] **BB戦略 10年バックテスト**: ローカルClaudeCodeに依頼。Dukascopyで10年5mデータ取得→現行v29パラメータ（GBPJPY/USDJPY/EURJPY）でIS/OOS評価。結果をもとにPhase1判定基準・パラメータの頑健性検証

## 作業スタイル
- 作業時間: 夜まとめて1〜2時間
- Chat: タスク設計・判断のみ（10〜15分）
- Code: 実装・実行・push（残り全て）
- Codeセッション開始前に必ずタスクリストを用意する

## 夜の終了チェックリスト（2026-05-28）
- [x] 変更ファイルをcommit/push済み
- [x] CLAUDE.mdのTop of mindを更新済み
- [x] 翌日Chatで確認すべき事項をメモ済み
- [x] bb_monitor v27: GBPJPY sigma 1.5→2.0・push済み
- [ ] VPS: git pull origin main → bb_monitor.bat 再起動（翌日手動対応）
- [ ] VPS: news_monitor.bat 起動（翌日手動対応）

## ロードマップ

### Phase1（現在）: 実稼働データ蓄積・完了判定
- 判定基準: PF>1.2 / 勝率>50% / DD<15%
- 対象ペア: GBPJPY/USDJPY/EURUSD/GBPUSD
- 完了条件: 全ペアで判定基準クリア
- 完了後タスク: USDCAD再評価BT実施

### Phase2: 戦略改善・追加
- BB戦略RR改善（Stage2 distance微調整継続）
- 200MA Pullback本格導入（USDJPY Pinbar候補）
- SMC_GBPAUD 実稼働評価
- stat_arb 評価・調整

### Phase3: スケールアップ
- 目標: 月利30万円達成
- ロット拡大・ペア追加
