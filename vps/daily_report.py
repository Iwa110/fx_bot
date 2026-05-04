"""
daily_report.py - FX日次レポート生成スクリプト v1
Task Schedulerで毎朝7時JST実行を想定。

出力:
  - logs/daily_report_YYYYMMDD.txt
  - Discord通知（.envのDISCORD_WEBHOOK）
"""

import sys, os, ssl, json, urllib.request
from datetime import datetime, timedelta, timezone

try:
    import MetaTrader5 as mt5
except ImportError:
    print('[ERROR] MetaTrader5パッケージが見つかりません: pip install MetaTrader5')
    sys.exit(1)

# ══════════════════════════════════════════
# 定数・設定
# ══════════════════════════════════════════
BASE_DIR = r'C:\Users\Administrator\fx_bot'
LOG_DIR  = os.path.join(BASE_DIR, 'logs')
ENV_FILE = os.path.join(BASE_DIR, 'vps', '.env')

JST = timezone(timedelta(hours=9))

# magic番号 → 戦略名
MAGIC_MAP = {
    20250001: 'BB',
    20250002: 'SMC_GBPAUD',
    20260001: 'stat_arb',
}

# JPYペアかどうか（損益表示の単位判定用）
JPY_PAIRS = {'GBPJPY', 'USDJPY', 'EURJPY', 'AUDJPY', 'CADJPY', 'NZDJPY', 'CHFJPY'}


# ══════════════════════════════════════════
# ユーティリティ
# ══════════════════════════════════════════
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
        return
    try:
        data = json.dumps({'content': msg}).encode('utf-8')
        req  = urllib.request.Request(
            webhook, data=data,
            headers={'Content-Type': 'application/json', 'User-Agent': 'Mozilla/5.0'}
        )
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode    = ssl.CERT_NONE
        urllib.request.urlopen(req, timeout=10, context=ctx)
    except Exception as e:
        print(f'[WARN] Discord送信エラー: {e}')


def profit_currency(symbol: str) -> str:
    return '円' if symbol in JPY_PAIRS else 'USD'


# ══════════════════════════════════════════
# MT5データ取得
# ══════════════════════════════════════════
def get_yesterday_range_utc() -> tuple[datetime, datetime]:
    """前日JST 00:00 〜 23:59:59 をUTCに変換して返す"""
    now_jst   = datetime.now(tz=JST)
    yesterday = now_jst.date() - timedelta(days=1)
    from_jst  = datetime(yesterday.year, yesterday.month, yesterday.day, 0, 0, 0, tzinfo=JST)
    to_jst    = datetime(yesterday.year, yesterday.month, yesterday.day, 23, 59, 59, tzinfo=JST)
    return from_jst.astimezone(timezone.utc), to_jst.astimezone(timezone.utc)


def fetch_closed_deals(from_dt: datetime, to_dt: datetime) -> list[dict]:
    """
    history_deals_get()でクローズ済みdealを取得し、
    エントリーdealと突合して行データを返す。
    """
    deals = mt5.history_deals_get(from_dt, to_dt)
    if deals is None:
        raise RuntimeError(f'history_deals_get失敗: {mt5.last_error()}')

    # エントリーdeal（IN）をposition_idでインデックス化
    entry_map: dict[int, object] = {}
    for d in deals:
        if d.entry == mt5.DEAL_ENTRY_IN:
            entry_map[d.position_id] = d

    rows = []
    for d in deals:
        if d.entry != mt5.DEAL_ENTRY_OUT:
            continue
        if d.magic not in MAGIC_MAP:
            continue

        entry_d   = entry_map.get(d.position_id)
        open_price = entry_d.price if entry_d else d.price
        is_buy     = (entry_d.type == mt5.DEAL_TYPE_BUY) if entry_d else (d.type == mt5.DEAL_TYPE_SELL)

        rows.append({
            'ticket':      d.position_id,
            'symbol':      d.symbol,
            'type':        'BUY' if is_buy else 'SELL',
            'lots':        float(d.volume),
            'open_price':  float(open_price),
            'close_price': float(d.price),
            'profit':      float(d.profit),
            'magic':       d.magic,
            'strategy':    MAGIC_MAP[d.magic],
            'close_time':  datetime.fromtimestamp(d.time, tz=JST).strftime('%H:%M'),
        })

    return rows


def fetch_open_positions() -> list[dict]:
    """現在のオープンポジション一覧を返す"""
    positions = mt5.positions_get()
    if not positions:
        return []

    result = []
    for p in positions:
        strategy = MAGIC_MAP.get(p.magic, f'magic={p.magic}')
        is_buy   = (p.type == mt5.POSITION_TYPE_BUY)
        result.append({
            'ticket':   p.ticket,
            'symbol':   p.symbol,
            'type':     'BUY' if is_buy else 'SELL',
            'lots':     float(p.volume),
            'open':     float(p.price_open),
            'current':  float(p.price_current),
            'profit':   float(p.profit),
            'strategy': strategy,
        })
    return result


# ══════════════════════════════════════════
# メトリクス計算（evaluate.pyと統一ロジック）
# ══════════════════════════════════════════
def calc_pair_metrics(trades: list[dict]) -> dict:
    """ペア別メトリクスを計算する"""
    if not trades:
        return {'n': 0, 'wins': 0, 'losses': 0, 'pf': 0.0, 'avg_profit': 0.0, 'total': 0.0}

    profits      = [t['profit'] for t in trades]
    wins_list    = [p for p in profits if p > 0]
    losses_list  = [p for p in profits if p < 0]

    gross_profit = sum(wins_list)
    gross_loss   = abs(sum(losses_list)) if losses_list else 0.0
    pf           = (gross_profit / gross_loss) if gross_loss > 0 else 0.0
    total        = sum(profits)
    avg_profit   = total / len(profits)

    return {
        'n':          len(profits),
        'wins':       len(wins_list),
        'losses':     len(losses_list),
        'pf':         round(pf, 3),
        'avg_profit': round(avg_profit, 2),
        'total':      round(total, 2),
    }


# ══════════════════════════════════════════
# レポート生成
# ══════════════════════════════════════════
def build_report(target_date: datetime.date, trades: list[dict], open_positions: list[dict]) -> str:
    lines = []

    lines.append('=' * 55)
    lines.append(f'  FX日次レポート  {target_date.strftime("%Y-%m-%d")} (JST)')
    lines.append('=' * 55)
    lines.append('')

    # ── 前日クローズ取引 ──────────────────
    lines.append('【前日クローズ取引】')
    if not trades:
        lines.append('  取引なし')
    else:
        # 戦略 > ペア の順でグループ化
        groups: dict[str, dict[str, list]] = {}
        for t in trades:
            strat = t['strategy']
            sym   = t['symbol']
            groups.setdefault(strat, {}).setdefault(sym, []).append(t)

        for strat in sorted(groups.keys()):
            lines.append(f'\n  [{strat}]')
            strat_trades = [t for sym_trades in groups[strat].values() for t in sym_trades]
            strat_m      = calc_pair_metrics(strat_trades)

            for sym in sorted(groups[strat].keys()):
                sym_trades = groups[strat][sym]
                m          = calc_pair_metrics(sym_trades)
                cur        = profit_currency(sym)
                pf_str     = f'{m["pf"]:.3f}' if m['losses'] > 0 else 'N/A '
                lines.append(
                    f'    {sym:<10} n={m["n"]:2d}'
                    f'  勝={m["wins"]:2d} 負={m["losses"]:2d}'
                    f'  PF={pf_str}'
                    f'  avg={m["avg_profit"]:+8.2f}{cur}'
                    f'  合計={m["total"]:+9.2f}{cur}'
                )

                # 個別取引明細
                for t in sorted(sym_trades, key=lambda x: x['close_time']):
                    sign = '+' if t['profit'] >= 0 else ''
                    lines.append(
                        f'      {t["close_time"]}  {t["type"]:<4}  lots={t["lots"]:.2f}'
                        f'  {t["open_price"]:.5f}->{t["close_price"]:.5f}'
                        f'  {sign}{t["profit"]:.2f}{cur}'
                    )

            # 戦略合計
            cur_total = '円' if all(sym in JPY_PAIRS for sym in groups[strat].keys()) else ''
            pf_str    = f'{strat_m["pf"]:.3f}' if strat_m['losses'] > 0 else 'N/A'
            lines.append(
                f'    ---合計--- n={strat_m["n"]:2d}'
                f'  勝={strat_m["wins"]:2d} 負={strat_m["losses"]:2d}'
                f'  PF={pf_str}'
                f'  合計={strat_m["total"]:+.2f}{cur_total}'
            )

    lines.append('')

    # ── 全体サマリー ──────────────────────
    lines.append('【全体サマリー】')
    if trades:
        all_m     = calc_pair_metrics(trades)
        pf_str    = f'{all_m["pf"]:.3f}' if all_m['losses'] > 0 else 'N/A'
        lines.append(f'  取引数  : {all_m["n"]}件 (勝:{all_m["wins"]} 負:{all_m["losses"]})')
        lines.append(f'  PF      : {pf_str}')
        lines.append(f'  合計損益: {all_m["total"]:+.2f}')
    else:
        lines.append('  取引なし（前日はクローズなし）')

    lines.append('')

    # ── オープンポジション ─────────────────
    lines.append('【現在のオープンポジション】')
    if not open_positions:
        lines.append('  なし')
    else:
        lines.append(f'  {len(open_positions)}件')
        for p in sorted(open_positions, key=lambda x: x['symbol']):
            cur   = profit_currency(p['symbol'])
            sign  = '+' if p['profit'] >= 0 else ''
            lines.append(
                f'  [{p["strategy"]}]  {p["symbol"]:<10} {p["type"]:<4}'
                f'  lots={p["lots"]:.2f}'
                f'  open={p["open"]:.5f}  now={p["current"]:.5f}'
                f'  含損益={sign}{p["profit"]:.2f}{cur}'
            )

    lines.append('')
    lines.append(f'  生成時刻: {datetime.now(tz=JST).strftime("%Y-%m-%d %H:%M:%S")} JST')
    lines.append('=' * 55)

    return '\n'.join(lines)


def build_discord_summary(target_date: datetime.date, trades: list[dict], open_positions: list[dict]) -> str:
    """Discord向けの簡潔なサマリーテキストを生成する"""
    lines = []
    lines.append(f'**FX日次レポート {target_date.strftime("%Y-%m-%d")}**')

    if not trades:
        lines.append('前日取引: なし')
    else:
        all_m  = calc_pair_metrics(trades)
        pf_str = f'{all_m["pf"]:.3f}' if all_m['losses'] > 0 else 'N/A'
        emoji  = ':white_check_mark:' if all_m['total'] >= 0 else ':x:'
        lines.append(
            f'{emoji} n={all_m["n"]}  勝={all_m["wins"]} 負={all_m["losses"]}'
            f'  PF={pf_str}  合計={all_m["total"]:+.2f}'
        )

        # ペア別1行サマリー
        pair_groups: dict[str, list] = {}
        for t in trades:
            pair_groups.setdefault(t['symbol'], []).append(t)
        for sym in sorted(pair_groups.keys()):
            m   = calc_pair_metrics(pair_groups[sym])
            cur = profit_currency(sym)
            lines.append(f'  {sym}: {m["wins"]}勝{m["losses"]}敗 {m["total"]:+.2f}{cur}')

    lines.append(f'OP: {len(open_positions)}件')
    return '\n'.join(lines)


# ══════════════════════════════════════════
# エントリーポイント
# ══════════════════════════════════════════
def main():
    env     = load_env()
    webhook = env.get('DISCORD_WEBHOOK', '')

    print('[INFO] MT5初期化...')
    if not mt5.initialize():
        msg = f'[ERROR] MT5初期化失敗: {mt5.last_error()}'
        print(msg)
        send_discord(f':warning: daily_report: MT5初期化失敗 `{mt5.last_error()}`', webhook)
        sys.exit(1)

    try:
        from_utc, to_utc  = get_yesterday_range_utc()
        target_date       = (datetime.now(tz=JST) - timedelta(days=1)).date()

        print(f'[INFO] 取得期間(UTC): {from_utc} ~ {to_utc}')

        trades         = fetch_closed_deals(from_utc, to_utc)
        open_positions = fetch_open_positions()

        print(f'[INFO] クローズ取引: {len(trades)}件  オープン: {len(open_positions)}件')

        report_text = build_report(target_date, trades, open_positions)

        # ── ファイル保存 ─────────────────────
        os.makedirs(LOG_DIR, exist_ok=True)
        report_path = os.path.join(LOG_DIR, f'daily_report_{target_date.strftime("%Y%m%d")}.txt')
        with open(report_path, 'w', encoding='utf-8') as f:
            f.write(report_text)
        print(f'[INFO] レポート保存: {report_path}')
        print()
        print(report_text)

        # ── Discord通知 ──────────────────────
        discord_msg = build_discord_summary(target_date, trades, open_positions)
        send_discord(discord_msg, webhook)
        if webhook:
            print('[INFO] Discord通知送信完了')

    except Exception as e:
        import traceback
        err_msg = f'[ERROR] daily_report例外: {e}'
        print(err_msg)
        traceback.print_exc()
        send_discord(f':rotating_light: daily_report エラー\n```{e}```', webhook)
    finally:
        mt5.shutdown()


if __name__ == '__main__':
    main()
