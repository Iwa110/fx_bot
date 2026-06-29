# FX日次分析プロンプト（実口座対応版）

**使い方**: このファイルをそのままClaudeに貼り付けて実行する。自動ルーティン（毎朝 scheduled）も本手順を実行する。
戦略設定が変わった場合は「## 分析コンテキスト」を更新する（source of truth は `CLAUDE.md` Top of mind）。

**前提**: 2026-06-24 に確定Grid 4本（AUDCAD/CADCHF/AUDNZD/EURGBP）が国内OANDAで実口座 go-live。
demo（axiory/exness）は並行フォワードテスト継続。`history.csv` は `broker` 列で live/demo を分離する
（`oanda_live`=実口座 / それ以外=demo。旧行は `legacy_demo`）。

---

## STEP 1: 取引実績データ取得（live / demo 分離）

```bash
python3 - <<'EOF'
import pandas as pd
from datetime import datetime, timedelta, timezone

JST = timezone(timedelta(hours=9))
df = pd.read_csv('optimizer/history.csv')
df['close_time'] = pd.to_datetime(df['close_time'])
df['date_jst'] = df['close_time'].dt.tz_localize('UTC').dt.tz_convert(JST).dt.date

# broker列が無い旧CSVでも動くようフォールバック
if 'broker' not in df.columns:
    df['broker'] = 'legacy_demo'
df['broker'] = df['broker'].fillna('legacy_demo')
df['account'] = df['broker'].apply(lambda b: 'LIVE' if b == 'oanda_live' else 'DEMO')

MAGIC = {
    20250001:'BB', 20260001:'stat_arb', 20260010:'SMA_SQ',
    20260030:'GRID_NZDUSD', 20260031:'GRID_GBPJPY', 20260032:'GRID_CHFJPY',
    20260033:'GRID_NZDJPY', 20260034:'GRID_AUDCAD', 20260035:'GRID_EURGBP',
    20260036:'GRID_AUDNZD', 20260037:'GRID_USDJPY', 20260038:'GRID_CADCHF',
}
LIVE_GRID = {'GRID_AUDCAD','GRID_CADCHF','GRID_AUDNZD','GRID_EURGBP'}  # 確定4本

def pf(g):
    w = g[g.profit>0].profit.sum(); l = abs(g[g.profit<0].profit.sum())
    return round(w/l, 3) if l>0 else float('inf')
def wr(g):
    return round(len(g[g.profit>0])/max(len(g),1)*100, 1)

today = datetime.now(JST).date()
last7 = today - timedelta(days=6)
last30 = today - timedelta(days=29)
df['strategy'] = df['magic'].map(MAGIC).fillna(df['magic'].astype(str))

for acct in ['LIVE','DEMO']:
    a = df[df['account']==acct]
    if a.empty:
        print(f'\n########## {acct} ########## (データなし)'); continue
    print(f'\n########## {acct} 口座 ##########')
    for label, sub in [('本日', a[a.date_jst==today]),
                       ('直近7日', a[a.date_jst>=last7]),
                       ('直近30日', a[a.date_jst>=last30])]:
        if sub.empty:
            print(f'[{label}] 約定なし'); continue
        print(f'[{label}] 総損益={sub.profit.sum():+,.0f}円 n={len(sub)} '
              f'PF={pf(sub):.3f} WR={wr(sub)}%')
        for s, g in sub.groupby('strategy'):
            print(f'   {s}: {g.profit.sum():+,.0f}円 PF={pf(g):.3f} WR={wr(g)}% n={len(g)}'
                  f"{'  ★確定Grid' if s in LIVE_GRID else ''}")

print('\n=== 確定Grid 4本 累計（全期間・口座別）===')
grid = df[df.strategy.isin(LIVE_GRID)]
for acct in ['LIVE','DEMO']:
    a = grid[grid.account==acct]
    if a.empty:
        print(f'[{acct}] 約定なし'); continue
    print(f'[{acct}]')
    for s, g in a.groupby('strategy'):
        n_tp = len(g[g.profit>0]); n_fs = len(g[g.profit<0])
        print(f'   {s}: net={g.profit.sum():+,.0f}円 PF={pf(g):.3f} WR={wr(g)}% '
              f'n={len(g)} (勝{n_tp}/負{n_fs})  期間{g.date_jst.min()}〜{g.date_jst.max()}')

print(f'\nhistory.csv 総件数 {len(df)} / live {len(df[df.account=="LIVE"])} / demo {len(df[df.account=="DEMO"])}')
EOF
```

## STEP 2: Grid 未エントリー診断 + 次機会予測

```bash
python3 optimizer/grid_gate_review.py
```

- 確定4本それぞれの「今日なぜ建たないか（CI未達 / 上昇レジームでshort停止 / mom過大 / 建玉あり）」と
  「次にレンジ（エントリー可能）になる時期の目安（CIトレンド外挿 ＋ 過去base-rate）」を出力。
- データソースは `optimizer/grid_gate_log.csv`（VPS実ゲート値・go-live後から蓄積）優先、無ければ
  `data/{PAIR}_1h_dukas.csv` で近似（`@ 日付` が古い場合は近似値である点に留意）。

---

## 分析コンテキスト（戦略変更時に更新 / source of truth = CLAUDE.md）

**最終更新: 2026-06-29**

### 稼働中の戦略と口座
| 戦略 | magic | 対象 | 口座 | 状態 |
|------|-------|------|------|------|
| **Grid（確定4本）** | AUDCAD 20260034 / CADCHF 20260038 / AUDNZD 20260036 / EURGBP 20260035 | 相関クロス | **LIVE(OANDA)+demo** | ✅ go-live 2026-06-24。実投入は forward-test 完了が前提 |
| Grid（carry/No-Go） | NZDJPY 20260033 / USDJPY 20260037 / 他 20260030-32 | JPY等 | demo のみ | demo継続・**実口座スケール禁止** |
| BB | 20250001 | USDJPY 他 | demo | USDJPY micro 蓄積のみ（10年BTで頑健エッジ無し）|
| SMA_SQ | 20260010 | - | demo | 10年BTでエッジ無し・縮小/停止寄り |
| stat_arb | 20260001 | ペア | demo | 参考 |

### 実口座（LIVE）運用ルール（`optimizer/grid_forward_test_plan.md`）
- ロット: `LIVE_LOT_PER_PAIR`（S0: AUDCAD 0.15 / CADCHF 0.05 / AUDNZD 0.08 / EURGBP 0.05、25倍レバ・証拠金律速）。
- 昇格: 3ヶ月 ∧ TP≥30 ∧ FS最低1回発火 ∧ 実現PF>1.2。
- 撤退/監視（決定論的）: 執行整合性 / FS単発スリッページ≤設定×1.3 / req_cap_99 ハードストップ。
- 必要資本（暦月basis・等req_cap分散バスケット）: 月利30万=2.80M。実口座は25倍で満玉不可=本フェーズは税優位の検証フェーズ。

### Grid ゲート（確定4本）
- エントリー条件: Choppiness Index(D1,14) > 65（レンジ要求）＋ regime_short(SMA1200, CADCHF/AUDCAD/AUDNZD は上昇局面でshort停止)＋ momentum gate。
- **大半の日はCI未達でアイドル＝設計通り**（`project_grid_audcad_idle_observation_20260621`）。

---

## 分析タスク

上記 STEP1/STEP2 の出力を基に、以下を**簡潔に**評価する。

1. **A. 実口座（LIVE）評価**:
   - 当日/直近7日/30日の実損益・PF・WR、確定Grid 4本のペア別実績。
   - forward-test 進捗（TP件数 / FS発火有無 / 実現PF）と昇格・撤退ルールへの抵触有無。
   - 異常検知: FSスリッページが設定×1.3超 / 想定外magic・symbol / 執行不整合があれば最優先で指摘。

2. **B. demo（フォワードテスト）評価**:
   - 確定4本の demo 実績（昇格条件 3ヶ月∧TP≥30∧FS発火∧PF>1.2 の進捗）。live との乖離。
   - carry/No-Go（NZDJPY/USDJPY 等）は参考。スケール禁止の再確認。

3. **C. Grid 未エントリー診断（STEP2）**:
   - 4本それぞれ「今日建たない理由」を1行で。アイドルは異常ではない（設計通り）点を明示。
   - 次機会予測: CIトレンド＋base-rate から「いつ頃レンジ入りしそうか」をレンジで提示（断定しない）。
   - **損失/大DDの発生時期は予測不能**（`project_grid_episode_prediction`）。予測対象は「エントリー可能局面の到来」のみ。

4. **改善提案**:
   - 実口座のリスク逸脱（DD/維持率/スリッページ）があれば具体的アクション。無ければ「現状維持・監視継続」と明記。
   - demo で昇格条件に近いペアがあれば次ロット段階の検討。BT探索は墓場確定済（CLAUDE.md）＝新戦略提案は原則しない。

---

## 通知

分析完了後、PushNotification ツールで iPhone に通知（200字以内）:

`FX {MM-DD} 実{損益:+,}円(n) / demo{損益:+,}円 / Grid:{未エントリー診断1行 or 建った本数} / {最重要点1行}`

例: `FX 06-29 実±0円(0) / demo+1,200円 / Grid:4本ともCI未達アイドル(CADCHF上昇中~3wで発火可能性) / 実口座DD/逸脱なし・監視継続`
