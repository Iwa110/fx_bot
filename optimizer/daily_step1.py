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
    20260050:'MR_AC',
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

print('\n=== 確定Grid 4本 累計(全期間・口座別) ===')
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
