"""
daily_analysis.py  v1 - 当日取引分析 + 改善提案 -> Discord通知

history.csv (sync_history.pyが更新) を読み込んで分析する。
MT5接続不要。sync_history.py 実行後に呼ぶこと（22:05 JST想定）。

Usage:
    python daily_analysis.py [--date YYYY-MM-DD] [--no-discord]
"""

import sys, os, argparse, json, ssl, urllib.request
from datetime import datetime, timedelta, timezone, date
from pathlib import Path

import pandas as pd

import platform
if platform.system() == 'Windows':
    BASE_DIR = Path(r'C:\Users\Administrator\fx_bot')
else:
    BASE_DIR = Path(__file__).resolve().parent.parent  # ローカル開発用

HISTORY_CSV = BASE_DIR / 'optimizer' / 'history.csv'
ENV_FILE    = BASE_DIR / 'vps' / '.env'
JST         = timezone(timedelta(hours=9))

MAGIC_MAP = {
    20250001: 'BB',
    20260001: 'stat_arb',
    20260010: 'SMA_SQ',
    20240101: 'MOM_JPY',
    20240102: 'MOM_GBJ',
    20240104: 'STR',
    20240107: 'MOM_GBU',
}

# アクティブ戦略 × ペアの組み合わせ（ゼロ取引チェック用）
ACTIVE_PAIRS = {
    'BB':      ['GBPJPY', 'USDJPY', 'EURJPY'],
    'SMA_SQ':  ['USDJPY', 'GBPJPY', 'EURUSD', 'GBPUSD', 'EURJPY'],
}

# 改善提案ルールの閾値
PF_WARN    = 1.0   # これ以下で警告
PF_OK      = 1.2   # Phase1合格基準
RR_WARN    = 0.5   # 実RRこれ以下でTP/SL見直し推奨
DD_STREAK  = 4     # 連続損失このレコード数以上で警告
N_MIN      = 10    # 判定に必要な最低トレード数


# ── ユーティリティ ─────────────────────────────────────────────────────

def load_env() -> dict:
    env = {}
    try:
        with open(ENV_FILE, encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if '=' in line and not line.startswith('#'):
                    k, v = line.split('=', 1)
                    env[k.strip()] = v.strip()
    except Exception:
        pass
    return env


def send_discord(msg: str, webhook: str):
    if not webhook:
        print('[INFO] DISCORD_WEBHOOK未設定: Discord通知スキップ')
        return
    try:
        # Discord 2000文字制限: 超える場合は分割送信
        for chunk in _split_message(msg, 1900):
            data = json.dumps({'content': chunk}).encode('utf-8')
            req  = urllib.request.Request(
                webhook, data=data,
                headers={'Content-Type': 'application/json',
                         'User-Agent': 'Mozilla/5.0'}
            )
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode    = ssl.CERT_NONE
            urllib.request.urlopen(req, timeout=10, context=ctx)
    except Exception as e:
        print(f'[WARN] Discord送信エラー: {e}')


def _split_message(msg: str, limit: int) -> list:
    if len(msg) <= limit:
        return [msg]
    lines  = msg.split('\n')
    chunks = []
    buf    = ''
    for line in lines:
        if len(buf) + len(line) + 1 > limit:
            chunks.append(buf)
            buf = line + '\n'
        else:
            buf += line + '\n'
    if buf:
        chunks.append(buf)
    return chunks


def _pf(wins: pd.Series, losses: pd.Series) -> float:
    if len(losses) == 0 or losses.sum() == 0:
        return float('inf') if len(wins) > 0 else 0.0
    return round(wins.sum() / abs(losses.sum()), 3)


def _streak(series: pd.Series) -> int:
    """末尾から連続損失件数を返す"""
    count = 0
    for v in reversed(series.tolist()):
        if v < 0:
            count += 1
        else:
            break
    return count


# ── 分析ロジック ──────────────────────────────────────────────────────

def analyze(df: pd.DataFrame, target: date) -> dict:
    """分析結果を辞書で返す"""
    df = df.copy()
    df['close_time'] = pd.to_datetime(df['close_time'])
    df['date_jst']   = (df['close_time']
                        .dt.tz_localize('UTC')
                        .dt.tz_convert(JST)
                        .dt.date)
    df['strategy']   = df['magic'].map(MAGIC_MAP).fillna(df['magic'].astype(str))

    # 今日・直近7日・先週7日
    today_df  = df[df['date_jst'] == target]
    last7_df  = df[df['date_jst'] >= target - timedelta(days=6)]
    prev7_df  = df[(df['date_jst'] >= target - timedelta(days=13)) &
                   (df['date_jst'] < target - timedelta(days=6))]

    def metrics(g):
        w = g[g.profit > 0]['profit']
        l = g[g.profit < 0]['profit']
        avg_w = w.mean() if len(w) > 0 else 0.0
        avg_l = l.mean() if len(l) > 0 else 0.0
        rr    = abs(avg_w / avg_l) if avg_l != 0 else float('inf')
        return {
            'n':      len(g),
            'pf':     _pf(w, l),
            'wr':     len(w) / len(g) * 100 if len(g) > 0 else 0.0,
            'profit': g['profit'].sum(),
            'rr':     round(rr, 2),
            'streak': _streak(g.sort_values('close_time')['profit']),
        }

    return {
        'target':   target,
        'today':    metrics(today_df),
        'last7':    metrics(last7_df),
        'prev7':    metrics(prev7_df),
        'today_df': today_df,
        'last7_df': last7_df,
        'by_pair_last7': {
            sym: metrics(g)
            for sym, g in last7_df.groupby('symbol')
            if not sym.endswith('m')   # exness mサフィックスは除外
        },
        'by_strategy_last7': {
            strat: metrics(g)
            for strat, g in last7_df.groupby('strategy')
        },
    }


def suggest(result: dict) -> list[str]:
    """改善提案リストを生成"""
    tips = []
    last7   = result['last7']
    prev7   = result['prev7']
    by_pair = result['by_pair_last7']
    by_strat= result['by_strategy_last7']

    # 1. 全体PF
    if last7['n'] >= N_MIN:
        if last7['pf'] < PF_WARN:
            tips.append(f'⚠️ 直近7日 PF={last7["pf"]:.3f} < {PF_WARN}：戦略全体が損益マイナス圏。エントリー条件の見直しを検討。')
        elif last7['pf'] < PF_OK:
            tips.append(f'📊 直近7日 PF={last7["pf"]:.3f}（Phase1基準 {PF_OK} 未達）：データ蓄積継続。')

    # 2. 前週比悪化
    if prev7['n'] >= N_MIN and last7['n'] >= N_MIN:
        delta = last7['pf'] - prev7['pf']
        if delta < -0.3:
            tips.append(f'📉 PF前週比 {prev7["pf"]:.3f} → {last7["pf"]:.3f}（{delta:+.3f}）：悪化トレンド。直近の市場環境変化を確認。')

    # 3. ペア別
    for sym, m in sorted(by_pair.items()):
        if m['n'] < N_MIN:
            continue
        if m['pf'] < PF_WARN:
            tips.append(f'⚠️ {sym} PF={m["pf"]:.3f}（直近7日 n={m["n"]}）：停止 or フィルター強化を検討。')
        if m['rr'] < RR_WARN and m['n'] >= N_MIN:
            tips.append(f'🔧 {sym} 実RR={m["rr"]:.2f}（<{RR_WARN}）：TP距離が小さい or SLが大きい可能性。fixed_tp_rrの見直しを検討。')
        if m['streak'] >= DD_STREAK:
            tips.append(f'⛔ {sym} 直近{m["streak"]}連続損失：一時停止またはロット縮小を検討。')

    # 4. 戦略別ゼロ取引チェック
    last7_syms = set(result['last7_df']['symbol'].str.replace('m$', '', regex=True).unique())
    for strat, pairs in ACTIVE_PAIRS.items():
        fired = any(p in last7_syms for p in pairs)
        if not fired:
            tips.append(f'❓ {strat}戦略: 直近7日取引ゼロ。エントリー条件が厳しすぎるか、プロセスが停止している可能性。')

    # 5. ゼロ提案時
    if not tips:
        tips.append('✅ 特記事項なし。引き続きデータ蓄積。')

    return tips


# ── メッセージ生成 ────────────────────────────────────────────────────

def build_message(result: dict, tips: list[str]) -> str:
    t      = result['target']
    today  = result['today']
    last7  = result['last7']
    lines  = []

    lines.append(f'📈 **FX日次レポート {t}**')
    lines.append('')

    # 当日
    sign = '+' if today['profit'] >= 0 else ''
    lines.append(f'**【本日】** n={today["n"]}  '
                 f'損益={sign}{today["profit"]:,.0f}円  '
                 f'PF={today["pf"]:.3f}  WR={today["wr"]:.1f}%')

    # 当日ペア別
    if not result['today_df'].empty:
        pair_lines = []
        for sym, g in result['today_df'].groupby('symbol'):
            if sym.endswith('m'):
                continue
            p = g['profit'].sum()
            s = '+' if p >= 0 else ''
            pair_lines.append(f'  {sym}: {s}{p:,.0f}円（n={len(g)}）')
        if pair_lines:
            lines.extend(pair_lines)

    lines.append('')

    # 直近7日
    sign7 = '+' if last7['profit'] >= 0 else ''
    lines.append(f'**【直近7日】** n={last7["n"]}  '
                 f'損益={sign7}{last7["profit"]:,.0f}円  '
                 f'PF={last7["pf"]:.3f}  WR={last7["wr"]:.1f}%')

    # 直近7日ペア別
    for sym, m in sorted(result['by_pair_last7'].items()):
        if m['n'] == 0:
            continue
        s = '+' if m['profit'] >= 0 else ''
        pf_icon = '🟢' if m['pf'] >= PF_OK else ('🟡' if m['pf'] >= PF_WARN else '🔴')
        lines.append(f'  {pf_icon} {sym}: {s}{m["profit"]:,.0f}円  '
                     f'PF={m["pf"]:.3f}  WR={m["wr"]:.1f}%  n={m["n"]}')

    lines.append('')
    lines.append('**【改善提案】**')
    for tip in tips:
        lines.append(tip)

    return '\n'.join(lines)


# ── main ─────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser(description='当日取引分析 + Discord通知')
    ap.add_argument('--date',       help='対象日 YYYY-MM-DD（省略時は当日JST）')
    ap.add_argument('--no-discord', action='store_true', help='Discord通知スキップ')
    args = ap.parse_args()

    target = (datetime.strptime(args.date, '%Y-%m-%d').date()
              if args.date
              else datetime.now(JST).date())

    if not HISTORY_CSV.exists():
        print(f'[ERROR] {HISTORY_CSV} が見つかりません。sync_history.py を先に実行してください。')
        sys.exit(1)

    df = pd.read_csv(HISTORY_CSV, dtype={'magic': int})

    now_jst = datetime.now(JST).strftime('%Y-%m-%d %H:%M:%S')
    print(f'=== daily_analysis.py  {now_jst} JST  target={target} ===')

    result = analyze(df, target)
    tips   = suggest(result)
    msg    = build_message(result, tips)

    print(msg)

    if not args.no_discord:
        env     = load_env()
        webhook = env.get('DISCORD_WEBHOOK', '')
        send_discord(msg, webhook)
        if webhook:
            print('[OK] Discord通知送信')

    print('=== 完了 ===')


if __name__ == '__main__':
    main()
