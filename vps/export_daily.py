"""
export_daily.py - MT5 当日取引履歴をCSVエクスポート
"""

import sys, os
from datetime import datetime, timezone, timedelta
import MetaTrader5 as mt5
import pandas as pd

ENV_FILE  = r'C:\Users\Administrator\fx_bot\vps\.env'
DATA_DIR  = r'C:\Users\Administrator\fx_bot\data'
JST       = timezone(timedelta(hours=9))
UTC       = timezone.utc

DEAL_TYPE = {
    mt5.DEAL_TYPE_BUY:  'buy',
    mt5.DEAL_TYPE_SELL: 'sell',
}


def load_env():
    env = {}
    try:
        with open(ENV_FILE, encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if '=' in line and not line.startswith('#'):
                    k, v = line.split('=', 1)
                    env[k.strip()] = v.strip()
    except Exception as e:
        print('ENV読込失敗:', e)
    return env


def main():
    env = load_env()

    if not mt5.initialize(
        login=int(env.get('OANDA_LOGIN', 0)),
        password=env.get('OANDA_PASSWORD', ''),
        server=env.get('OANDA_SERVER', '')
    ):
        print('MT5初期化失敗:', mt5.last_error())
        return

    try:
        # 今日のJST 00:00〜23:59:59 をUTCへ変換
        now_jst   = datetime.now(JST)
        today_jst = now_jst.replace(hour=0, minute=0, second=0, microsecond=0)
        end_jst   = today_jst.replace(hour=23, minute=59, second=59)
        date_from = today_jst.astimezone(UTC)
        date_to   = end_jst.astimezone(UTC)

        date_str = today_jst.strftime('%Y%m%d')
        out_path = os.path.join(DATA_DIR, f'daily_{date_str}.csv')

        print(f'取得期間(UTC): {date_from} 〜 {date_to}')

        deals = mt5.history_deals_get(date_from, date_to)
        if deals is None or len(deals) == 0:
            print('本日の取引なし')
            mt5.shutdown()
            return

        rows = []
        for d in deals:
            # IN/OUT どちらも含める（balance transferなどは除外）
            if d.type not in DEAL_TYPE:
                continue
            time_jst = datetime.fromtimestamp(d.time, tz=UTC).astimezone(JST)
            rows.append({
                'time':       time_jst.strftime('%Y-%m-%d %H:%M:%S'),
                'symbol':     d.symbol,
                'type':       DEAL_TYPE[d.type],
                'volume':     d.volume,
                'price':      d.price,
                'profit':     d.profit,
                'commission': d.commission,
                'swap':       d.swap,
                'comment':    d.comment,
                'magic':      d.magic,
            })

        df = pd.DataFrame(rows, columns=[
            'time', 'symbol', 'type', 'volume', 'price',
            'profit', 'commission', 'swap', 'comment', 'magic'
        ])

        os.makedirs(DATA_DIR, exist_ok=True)
        df.to_csv(out_path, index=False, encoding='utf-8-sig')
        print(f'エクスポート完了: {out_path}  ({len(df)}件)')

    except Exception as e:
        print('エラー:', e)
    finally:
        mt5.shutdown()


if __name__ == '__main__':
    main()
