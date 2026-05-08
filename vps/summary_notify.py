"""
サマリー通知スクリプト（7/12/16/21時実行）
- 現在の口座状況・ポジション・損益をDiscordに送信
- 決済済みポジションの通知も含む
"""
import MetaTrader5 as mt5
import json, os, ssl, urllib.request
from datetime import datetime, date

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ENV_PATH = os.path.join(BASE_DIR, '.env')
LOG_PATH = os.path.join(BASE_DIR, 'trade_log.json')

STRATEGY_CONFIG = {
    'TRI':     {'max_pos': 1},
    'MOM_JPY': {'max_pos': 1},
    'MOM_GBJ': {'max_pos': 1},
    'CORR':    {'max_pos': 1},
    'STR':     {'max_pos': 1},
}
BB_PAIRS_ACTIVE = ['GBPJPY', 'EURJPY', 'USDJPY', 'EURUSD', 'GBPUSD']  # USDCAD=停止
STAT_ARB_PAIRS  = [('GBPJPY', 'USDJPY'), ('EURUSD', 'GBPUSD')]
MAX_TOTAL  = 13
MAGIC_BB   = 20250001
MAGIC_STAT = 20260001
MAGIC_SMC  = 20260002


def load_env():
    config = {}
    if os.path.exists(ENV_PATH):
        with open(ENV_PATH, encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if '=' in line and not line.startswith('#'):
                    k, v = line.split('=', 1)
                    config[k.strip()] = v.strip()
    return config


def send_discord(message, webhook):
    if not webhook:
        return
    data = json.dumps({'content': message}).encode('utf-8')
    req  = urllib.request.Request(webhook, data=data,
           headers={'Content-Type': 'application/json', 'User-Agent': 'Mozilla/5.0'})
    try:
        urllib.request.urlopen(req, context=ssl._create_unverified_context())
        print('Discord通知完了')
    except Exception as e:
        print(f'Discord送信エラー: {e}')


def load_log():
    if os.path.exists(LOG_PATH):
        with open(LOG_PATH, encoding='utf-8') as f:
            return json.load(f)
    return {'date': str(date.today()), 'initial_balance': 0,
            'orders': [], 'closed': [], 'daily_loss_stopped': False}


def save_log(log):
    with open(LOG_PATH, 'w', encoding='utf-8') as f:
        json.dump(log, f, ensure_ascii=False, indent=2)


def get_positions():
    p = mt5.positions_get()
    return list(p) if p else []


def count_by(strategy):
    return sum(1 for p in get_positions() if strategy in p.comment)


def check_closed(log, webhook):
    if not log['orders']:
        return
    current      = {p.ticket for p in get_positions()}
    newly_closed = [o for o in log['orders'] if o['ticket'] not in current]
    if not newly_closed:
        return
    from_date = datetime(date.today().year, date.today().month, date.today().day)
    deals     = mt5.history_deals_get(from_date, datetime.now())
    deal_map  = {d.order: d for d in deals} if deals else {}
    now       = datetime.now().strftime('%Y-%m-%d %H:%M')
    for order in newly_closed:
        deal   = deal_map.get(order['ticket'])
        profit = deal.profit if deal else 0
        emoji  = '✅' if profit >= 0 else '❌'
        reason = '利確' if profit >= 0 else '損切'
        send_discord(
            f"【FX Bot】{now}\n{emoji} **{reason}確定**\n"
            f"通貨ペア: {order['symbol']}\n方向: {order['direction']}\n"
            f"損益: {'+' if profit >= 0 else ''}{profit:,.0f}円\n戦略: {order['strategy']}",
            webhook
        )
        log['closed'].append({**order, 'profit': profit, 'reason': reason})
    closed_tickets = {o['ticket'] for o in newly_closed}
    log['orders']  = [o for o in log['orders'] if o['ticket'] not in closed_tickets]
    save_log(log)


def build_bb_summary(positions, closed):
    bb_pos = [p for p in positions if p.magic == MAGIC_BB]
    lines  = []
    for p in bb_pos:
        dir_jp = '買い' if p.type == 0 else '売り'
        sign   = '+' if p.profit >= 0 else ''
        lines.append(f'  {p.symbol} {dir_jp} {p.volume}lot 損益:{sign}{p.profit:,.0f}円')

    bb_closed     = [c for c in closed if 'BB_' in c.get('strategy', '')]
    bb_closed_pnl = sum(c.get('profit', 0) for c in bb_closed)

    text = f'\n  保有: {len(bb_pos)}件'
    if lines:
        text += '\n' + '\n'.join(lines)
    if bb_closed:
        sign = '+' if bb_closed_pnl >= 0 else ''
        text += f'\n  本日決済: {len(bb_closed)}件 合計{sign}{bb_closed_pnl:,.0f}円'
    return len(bb_pos), text


def build_stat_arb_summary(positions, closed):
    stat_pos = [p for p in positions if p.magic == MAGIC_STAT]

    pair_groups = {f'{a}/{b}': {'a': None, 'b': None} for a, b in STAT_ARB_PAIRS}
    orphans = []
    for p in stat_pos:
        matched = False
        for sym_a, sym_b in STAT_ARB_PAIRS:
            key = f'{sym_a}/{sym_b}'
            if p.symbol == sym_a:
                pair_groups[key]['a'] = p
                matched = True
                break
            elif p.symbol == sym_b:
                pair_groups[key]['b'] = p
                matched = True
                break
        if not matched:
            orphans.append(p)

    lines        = []
    trade_count  = 0
    total_profit = 0.0

    for key, grp in pair_groups.items():
        pa, pb = grp['a'], grp['b']
        if pa is None and pb is None:
            continue
        if pa is not None and pb is not None:
            trade_count += 1
            profit = pa.profit + pb.profit
            total_profit += profit
            dir_a = '買' if pa.type == 0 else '売'
            dir_b = '買' if pb.type == 0 else '売'
            sign  = '+' if profit >= 0 else ''
            lines.append(
                f'  [{key}] {pa.symbol}{dir_a}{pa.volume}L / '
                f'{pb.symbol}{dir_b}{pb.volume:.2f}L 損益:{sign}{profit:,.0f}円'
            )
        else:
            p      = pa if pa is not None else pb
            total_profit += p.profit
            dir_jp = '買' if p.type == 0 else '売'
            lines.append(f'  [{key}] ⚠️片足 {p.symbol} {dir_jp} {p.volume}L 損益:{p.profit:,.0f}円')

    for p in orphans:
        total_profit += p.profit
        dir_jp = '買' if p.type == 0 else '売'
        lines.append(f'  [不明ペア] {p.symbol} {dir_jp} {p.volume}L 損益:{p.profit:,.0f}円')

    stat_closed     = [c for c in closed if 'stat_arb' in c.get('strategy', '')]
    stat_closed_pnl = sum(c.get('profit', 0) for c in stat_closed)

    text = f'\n  保有: {trade_count}ペア\n'
    text += '\n'.join(lines) if lines else '  なし'
    if stat_closed:
        sign = '+' if stat_closed_pnl >= 0 else ''
        text += f'\n  本日決済: {len(stat_closed) // 2}ペア 合計{sign}{stat_closed_pnl:,.0f}円'
    return trade_count, text


def build_smc_summary(positions, closed):
    smc_pos = [p for p in positions if p.magic == MAGIC_SMC]
    lines   = []
    for p in smc_pos:
        dir_jp = '買い' if p.type == 0 else '売り'
        sign   = '+' if p.profit >= 0 else ''
        lines.append(f'  {p.symbol} {dir_jp} {p.volume}lot 損益:{sign}{p.profit:,.0f}円')

    smc_closed     = [c for c in closed if 'SMC' in c.get('strategy', '')]
    smc_closed_pnl = sum(c.get('profit', 0) for c in smc_closed)

    text = f'\n  保有: {len(smc_pos)}件'
    if lines:
        text += '\n' + '\n'.join(lines)
    if smc_closed:
        sign = '+' if smc_closed_pnl >= 0 else ''
        text += f'\n  本日決済: {len(smc_closed)}件 合計{sign}{smc_closed_pnl:,.0f}円'
    return len(smc_pos), text


def build_daily_strategy_lines(positions):
    lines = []
    for s, cfg in STRATEGY_CONFIG.items():
        matched = [p for p in positions if s in p.comment]
        if matched:
            p      = matched[0]
            dir_jp = '買い' if p.type == 0 else '売り'
            sign   = '+' if p.profit >= 0 else ''
            lines.append(
                f'  {s}: 1/{cfg["max_pos"]} {p.symbol} {dir_jp} '
                f'{p.volume}lot 損益:{sign}{p.profit:,.0f}円'
            )
        else:
            lines.append(f'  {s}: 0/{cfg["max_pos"]}')
    return '\n'.join(lines)


import heartbeat_check as hb


def main():
    config  = load_env()
    webhook = config.get('DISCORD_WEBHOOK', '')

    if not mt5.initialize():
        print('MT5接続失敗')
        return

    log       = load_log()
    now       = datetime.now().strftime('%Y-%m-%d %H:%M')
    info      = mt5.account_info()
    initial   = log['initial_balance']
    pnl       = info.equity - initial
    positions = get_positions()

    check_closed(log, webhook)

    closed       = log.get('closed', [])
    total_closed = sum(c.get('profit', 0) for c in closed)
    pnl_sign     = '+' if pnl >= 0 else ''
    closed_text  = ''
    if closed:
        cl_sign     = '+' if total_closed >= 0 else ''
        closed_text = (f'\n本日決済: {len(closed)}回 '
                       f'合計{cl_sign}{total_closed:,.0f}円')

    bb_count,   bb_text   = build_bb_summary(positions, closed)
    stat_count, stat_text = build_stat_arb_summary(positions, closed)
    smc_count,  smc_text  = build_smc_summary(positions, closed)
    daily_lines           = build_daily_strategy_lines(positions)

    total_pos    = len(positions)
    stopped_text = '⛔ 停止中' if log['daily_loss_stopped'] else '✅ 稼働中'

    send_discord(
        f'【FX Bot】{now} 日次サマリー\n\n'
        f'**■ 口座状況**\n残高: {info.balance:,.0f}円\n資産: {info.equity:,.0f}円\n'
        f'本日損益: {pnl_sign}{pnl:,.0f}円{closed_text}\n\n'
        f'**■ BB逆張り（{bb_count}/{len(BB_PAIRS_ACTIVE)}）**{bb_text}\n\n'
        f'**■ Stat Arb（{stat_count}/{len(STAT_ARB_PAIRS)}ペア）**{stat_text}\n\n'
        f'**■ SMC_GBPAUD（{smc_count}件）**{smc_text}\n\n'
        f'**■ Daily戦略**\n{daily_lines}\n\n'
        f'ポジション合計: {total_pos}/{MAX_TOTAL} | 取引状態: {stopped_text}',
        webhook
    )

    hb.check_heartbeats(webhook)
    mt5.shutdown()


if __name__ == '__main__':
    main()
