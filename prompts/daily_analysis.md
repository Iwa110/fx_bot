# FX日次分析プロンプト

**使い方**: このファイルをそのままClaudeに貼り付けて実行する。
戦略設定が変わった場合は「## 分析コンテキスト」セクションを更新する。

---

## データ取得

```bash
python3 - <<'EOF'
import pandas as pd
from datetime import datetime, timedelta, timezone

JST = timezone(timedelta(hours=9))
df = pd.read_csv('optimizer/history.csv')
df['close_time'] = pd.to_datetime(df['close_time'])
df['date_jst'] = df['close_time'].dt.tz_localize('UTC').dt.tz_convert(JST).dt.date

today = datetime.now(JST).date()
last7_start = today - timedelta(days=6)

MAGIC = {20250001:'BB', 20260001:'stat_arb', 20260010:'SMA_SQ',
         20240101:'MOM_JPY', 20240102:'MOM_GBJ', 20240104:'STR', 20240107:'MOM_GBU'}

def pf(g):
    w = g[g.profit>0].profit.sum()
    l = abs(g[g.profit<0].profit.sum())
    return round(w/l, 3) if l>0 else float('inf')

today_df = df[df['date_jst']==today]
last7_df = df[df['date_jst']>=last7_start]

print('=== 本日', today, '===')
print(f'総損益: {today_df.profit.sum():+,.0f}円  n={len(today_df)}  PF={pf(today_df):.3f}  WR={len(today_df[today_df.profit>0])/max(len(today_df),1)*100:.1f}%')
for sym, g in today_df[~today_df.symbol.str.endswith("m")].groupby('symbol'):
    print(f'  {sym}: {g.profit.sum():+,.0f}円 n={len(g)}')
strats = today_df.copy(); strats['strategy'] = strats['magic'].map(MAGIC).fillna(strats['magic'].astype(str))
for s, g in strats.groupby('strategy'):
    print(f'  [{s}] PF={pf(g):.3f} WR={len(g[g.profit>0])/max(len(g),1)*100:.1f}% n={len(g)}')

print()
print('=== 直近7日 ===')
print(f'総損益: {last7_df.profit.sum():+,.0f}円  n={len(last7_df)}  PF={pf(last7_df):.3f}  WR={len(last7_df[last7_df.profit>0])/max(len(last7_df),1)*100:.1f}%')
for sym, g in last7_df[~last7_df.symbol.str.endswith("m")].groupby('symbol'):
    print(f'  {sym}: {g.profit.sum():+,.0f}円 PF={pf(g):.3f} n={len(g)}')

print()
print('=== 日次推移（直近7日）===')
for d, g in last7_df.groupby('date_jst'):
    print(f'  {d}: {g.profit.sum():+,.0f}円')

print()
print(f'history.csv 総件数: {len(df)}件  期間: {df.date_jst.min()} 〜 {df.date_jst.max()}')

print()
print('=== 戦略×ペア別（直近7日）===')
last7_df2 = last7_df.copy()
last7_df2['strategy'] = last7_df2['magic'].map(MAGIC).fillna(last7_df2['magic'].astype(str))
for s, g in last7_df2.groupby('strategy'):
    print(f'[{s}] 総損益={g.profit.sum():+,.0f}円 PF={pf(g):.3f} WR={len(g[g.profit>0])/max(len(g),1)*100:.1f}% n={len(g)}')
    for sym, sg in g[~g.symbol.str.endswith("m")].groupby('symbol'):
        print(f'  {sym}: {sg.profit.sum():+,.0f}円 PF={pf(sg):.3f} n={len(sg)}')

print()
print('=== Phase1判定進捗 (BB戦略 magic=20250001, 全期間) ===')
bb = df[df.magic==20250001]
for sym, g in bb[~bb.symbol.str.endswith("m")].groupby('symbol'):
    p = pf(g)
    wr = len(g[g.profit>0])/max(len(g),1)*100
    print(f'  {sym}: PF={p:.3f} WR={wr:.1f}% n={len(g)}  判定:{"OK" if p>1.2 and wr>50 else "NG"}')
EOF
```

---

## 分析コンテキスト（戦略変更時に更新）

**最終更新: 2026-05-16**

### 稼働中の戦略
| 戦略 | magic | 対象ペア | バージョン | 状態 |
|------|-------|---------|-----------|------|
| BB | 20250001 | GBPJPY / USDJPY / EURJPY | v22 | ✅ 稼働中 |
| BB | 20250001 | EURUSD / GBPUSD | v20 | ❌ 停止（enabled=False） |
| SMA_SQ | 20260010 | USDJPY/GBPJPY/EURUSD/GBPUSD/EURJPY | v3 | ✅ 稼働中 |
| stat_arb | 20260001 | GBPJPY/USDJPY・EURUSD/GBPUSD ペア | - | ✅ 稼働中 |
| MOM_JPY/MOM_GBJ/MOM_GBU/STR | 各magic | 各ペア | - | ✅ trail_monitor管理 |

### BB戦略 現在の設定（v21/v22）
- GBPJPY: `htf4h_rsi_bw=True`（RSI<60/RSI>55）+ `fixed_tp_rr=1.5`、Stage2廃止
- USDJPY: `htf4h_rsi_bw=True`（RSI<55/RSI>45）+ `fixed_tp_rr=1.5`、Stage2廃止
- EURJPY: `htf4h=True`（4h EMA20のみ）+ `fixed_tp_rr=1.5`、Stage2廃止
- EURUSD/GBPUSD: `enabled=False`（BT PF未達のため停止）

### SMA_SQ v3 現在の設定（2026-05-16更新）
- 全5ペア稼働: USDJPY / GBPJPY / EURUSD / GBPUSD / EURJPY
- A-1 SMA_long slope reversal exit（slope_exit=3）: 傾き反転で強制決済
- B-1 breakeven move（be_r=0.5）: profit≥0.5×SLでSLを建値移動
- **v3追加**: 日足SMAスロープフィルター（1h/4h方向と日足方向が不一致→スキップ）
  - USDJPY: daily_sma=20, daily_sp=3 / GBPJPY: daily_sma=20, daily_sp=3
  - EURUSD: daily_sma=50, daily_sp=3 / GBPUSD: daily_sma=20, daily_sp=5
  - EURJPY: daily_sma=20, daily_sp=5
- **v3変更**: COOLDOWN_MIN=180分（60分→180分）
- BT PF（日足フィルター後）: USDJPY=1.928 / GBPJPY=1.522 / EURUSD=2.831 / GBPUSD=1.372 / EURJPY=3.748

### Phase1判定基準
- PF > 1.2 / WR > 50% / DD < 15%
- 対象: BB戦略 magic=20250001、GBPJPY/USDJPY/EURUSD/GBPUSD

---

## 分析タスク

上記データを基に以下を分析してください：

1. **本日の評価**: 損益・PF・WRを総合評価。勝ちトレードと負けトレードのパターン。

2. **直近7日のトレンド**: 日次推移から改善・悪化を判断。ペア別の強弱。

3. **戦略別評価**:
   - BB戦略（GBPJPY/USDJPY/EURJPY）: htf4h_rsi_bwフィルター+固定TP(SL×1.5)の効果。
   - SMA_SQ v3（全5ペア）: 日足フィルター・COOLDOWN=180追加済み。ログに「daily_slope=DN/UP vs 1h」が出ているか確認。GBPUSDはデータ蓄積目的で再開（実績PF要監視）。
   - stat_arb: ペアトレードとしての機能評価。

4. **改善提案**: PF<0.5のペアや連続損失パターンを特定し、具体的な改善アクションを提案。停止基準（PF<0.5 かつ n≥10）に該当するペアは即時対応を推奨。

5. **Phase1判定進捗**: 各ペアの現状（PF/WR/n）と合格見込みを評価。100件超えたペアの再判定実施。

---

## 通知

分析完了後、PushNotification ツールで以下の形式でiPhoneに通知してください（200字以内）：

`FX {MM-DD} 本日{損益:+,}円 PF{PF値} / 直近7日{損益:+,}円 / {最重要改善点1行}`

例: `FX 05-15 本日-13,100円 PF0.00 / 直近7日-55,187円 / GBPUSD BB停止中・GBPJPY PF0.20要注意`
