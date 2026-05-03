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
    'TRI':    {'max_pos':1},
    'MOM_JPY':{'max_pos':1},
    'MOM_GBJ':{'max_pos':1},
    'CORR':   {'max_pos':1},
    'STR':    {'max_pos':1},
}
BB_PAIRS = ['USDCAD','GBPJPY','EURJPY','USDJPY','AUDJPY','EURUSD','GBPUSD']
STAT_ARB_PAIRS = [('GBPJPY','USDJPY'), ('EURUSD','GBPUSD')]
MAX_TOTAL = 13
MAGIC_BB   = 20250001
MAGIC_STAT = 20260001

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
    if not webhook: return
    data = json.dumps({'content': message}).encode('utf-8')
    req  = urllib.request.Request(webhook, data=data,
           headers={'Content-Type':'application/json','User-Agent':'Mozilla/5.0'})
    try:
        urllib.request.urlopen(req, context=ssl._create_unverified_context())
        print("Discord通知完了")
    except Exception as e:
        print(f"Discord送信エラー: {e}")

def load_log():
    if os.path.exists(LOG_PATH):
        with open(LOG_PATH, encoding='utf-8') as f:
            return json.load(f)
    return {'date':str(date.today()),'initial_balance':0,
            'orders':[],'closed':[],'daily_loss_stopped':False}

def save_log(log):
    with open(LOG_PATH, 'w', encoding='utf-8') as f:
        json.dump(log, f, ensure_ascii=False, indent=2)

def get_positions():
    p = mt5.positions_get()
    return list(p) if p else []

def count_by(strategy):
    return sum(1 for p in get_positions() if strategy in p.comment)

def check_closed(log, webhook):
    if not log['orders']: return
    current      = {p.ticket for p in get_positions()}
    newly_closed = [o for o in log['orders'] if o['ticket'] not in current]
    if not newly_closed: return
    from_date = datetime(date.today().year, date.today().month, date.today().day)
    deals     = mt5.history_deals_get(from_date, datetime.now())
    deal_map  = {d.order:d for d in deals} if deals else {}
    now       = datetime.now().strftime('%Y-%m-%d %H:%M')
    for order in newly_closed:
        deal   = deal_map.get(order['ticket'])
        profit = deal.profit if deal else 0
        emoji  = '✅' if profit>=0 else '❌'
        reason = '利確' if profit>=0 else '損切'
        send_discord(
            f"【FX Bot】{now}\n{emoji} **{reason}確定**\n"
            f"通貨ペア: {order['symbol']}\n方向: {order['direction']}\n"
            f"損益: {'+' if profit>=0 else ''}{profit:,.0f}円\n戦略: {order['strategy']}",
            webhook
        )
        log['closed'].append({**order,'profit':profit,'reason':reason})
    closed_tickets = {o['ticket'] for o in newly_closed}
    log['orders']  = [o for o in log['orders'] if o['ticket'] not in closed_tickets]
    save_log(log)

def build_stat_arb_summary(positions):
    """
    magic=20260001のポジションをペアトレードとして集計。
    commentに 'STAT_pair_A' / 'STAT_pair_B' が入る想定。
    ペアが揃っていない場合も個別表示。
    """
    stat_pos = [p for p in positions if p.magic == MAGIC_STAT]
    if not stat_pos:
        return 0, 0.0, '  なし'

    # ペアごとにグループ化（symbol_a/symbol_bで分類）
    pair_groups = {}
    for sym_a, sym_b in STAT_ARB_PAIRS:
        key = f'{sym_a}/{sym_b}'
        pair_groups[key] = {'a': None, 'b': None}

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

    lines = []
    trade_count = 0
    total_profit = 0.0

    for key, grp in pair_groups.items():
        pa, pb = grp['a'], grp['b']
        if pa is None and pb is None:
            continue
        if pa is not None and pb is not None:
            # 正常ペア
            trade_count += 1
            profit = pa.profit + pb.profit
            total_profit += profit
            dir_a = '買' if pa.type == 0 else '売'
            dir_b = '買' if pb.type == 0 else '売'
            sign = '+' if profit >= 0 else ''
            lines.append(
                f'  [{key}] {pa.symbol}{dir_a}{pa.volume}L / '
                f'{pb.symbol}{dir_b}{pb.volume:.2f}L '
                f'損益:{sign}{profit:,.0f}円'
            )
        else:
            # 片方のみ（異常系）
            p = pa if pa is not None else pb
            total_profit += p.profit
            dir_jp = '買' if p.type == 0 else '売'
            lines.append(f'  [{key}] ⚠️片足 {p.symbol} {dir_jp} {p.volume}L 損益:{p.profit:,.0f}円')

    for p in orphans:
        total_profit += p.profit
        dir_jp = '買' if p.type == 0 else '売'
        lines.append(f'  [不明ペア] {p.symbol} {dir_jp} {p.volume}L 損益:{p.profit:,.0f}円')

    return trade_count, total_profit, '\n'.join(lines) if lines else '  なし'

import heartbeat_check as hb

def main():
    config  = load_env()
    webhook = config.get('DISCORD_WEBHOOK','')

    if not mt5.initialize():
        print("MT5接続失敗"); return

    log       = load_log()
    now       = datetime.now().strftime('%Y-%m-%d %H:%M')
    info      = mt5.account_info()
    initial   = log['initial_balance']
    pnl       = info.equity - initial
    positions = get_positions()

    check_closed(log, webhook)

    pos_text = ''
    for p in positions:
        strategy = p.comment.replace('FXBot_','')
        dir_jp   = '買い' if p.type==0 else '売り'
        pos_text += (
            f"\n  [{strategy}] {p.symbol} {dir_jp} {p.volume}lot "
            f"損益:{'+' if p.profit>=0 else ''}{p.profit:,.0f}円"
        )

    total_closed = sum(c.get('profit',0) for c in log['closed'])
    closed_text  = (f"\n本日決済: {len(log['closed'])}回 "
                    f"合計{'+' if total_closed>=0 else ''}{total_closed:,.0f}円"
                    if log['closed'] else '')

    strategy_lines = '\n'.join(
        f"  {s}: {count_by(s)}/{cfg['max_pos']}"
        for s, cfg in STRATEGY_CONFIG.items()
    )

    # BB戦略集計
    bb_pos_text = ''
    bb_count = 0
    for p in positions:
        if p.magic == MAGIC_BB:
            dir_jp = '買い' if p.type==0 else '売り'
            bb_pos_text += (
                f"\n  {p.symbol} {dir_jp} {p.volume}lot "
                f"損益:{'+' if p.profit>=0 else ''}{p.profit:,.0f}円"
            )
            bb_count += 1

    bb_closed_today = [c for c in log.get('closed',[]) if 'BB_' in c.get('strategy','')]
    bb_closed_pnl   = sum(c.get('profit',0) for c in bb_closed_today)
    bb_summary      = f"\n  保有: {bb_count}件{bb_pos_text}"
    if bb_closed_today:
        bb_summary += (
            f"\n  本日決済: {len(bb_closed_today)}件 "
            f"合計{'+' if bb_closed_pnl>=0 else ''}{bb_closed_pnl:,.0f}円"
        )

    # stat_arb集計
    stat_trade_count, stat_profit, stat_detail = build_stat_arb_summary(positions)
    stat_closed_today = [c for c in log.get('closed',[]) if 'stat_arb' in c.get('strategy','')]
    stat_closed_pnl   = sum(c.get('profit',0) for c in stat_closed_today)
    stat_summary      = f"\n  保有: {stat_trade_count}ペア\n{stat_detail}"
    if stat_closed_today:
        stat_summary += (
            f"\n  本日決済: {len(stat_closed_today)//2}ペア "
            f"合計{'+' if stat_closed_pnl>=0 else ''}{stat_closed_pnl:,.0f}円"
        )

    send_discord(
        f"【FX Bot】{now} 日次サマリー\n\n"
        f"**■ 口座状況**\n残高: {info.balance:,.0f}円\n資産: {info.equity:,.0f}円\n"
        f"本日損益: {'+' if pnl>=0 else ''}{pnl:,.0f}円{closed_text}\n\n"
        f"**■ 保有ポジション（{len(positions)}/{MAX_TOTAL}）**"
        f"{pos_text if pos_text else chr(10)+'  なし'}\n\n"
        f"**■ 戦略別ポジション**\n{strategy_lines}\n\n"
        f"**■ BB逆張り（{bb_count}/{len(BB_PAIRS)}）**{bb_summary}\n\n"
        f"**■ Stat Arb ペアトレード（{stat_trade_count}/{len(STAT_ARB_PAIRS)}）**{stat_summary}\n\n"
        f"取引状態: {'⛔ 停止中' if log['daily_loss_stopped'] else '✅ 稼働中'}",
        webhook
    )

    hb.check_heartbeats(webhook)
    mt5.shutdown()

if __name__ == '__main__':
    main()