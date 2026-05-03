"""
メール監視スクリプト
- 5分毎に実行（タスクスケジューラで設定）
- 件名「FX RESET」のメールを検知して損失上限をリセット
- 件名「FX STOP」のメールを検知して全ポジションを決済
- 件名「FX STATUS」のメールを検知して現在状況をDiscordに通知
"""
import imaplib, email, json, os, ssl, urllib.request
from datetime import datetime

# ── パス設定 ──────────────────────────────────
BASE_DIR    = os.path.dirname(os.path.abspath(__file__))
ENV_PATH    = os.path.join(BASE_DIR, '.env')
LOG_PATH    = os.path.join(BASE_DIR, 'trade_log.json')
DONE_PATH   = os.path.join(BASE_DIR, 'processed_mails.json')

# ── コマンド定義 ──────────────────────────────
COMMANDS = {
    'FX RESET':  '損失上限をリセット',
    'FX STOP':   '全ポジションを緊急決済',
    'FX STATUS': '現在状況をDiscordに送信',
}

# ── .env読み込み ──────────────────────────────
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

# ── 処理済みメールID管理 ──────────────────────
def load_done():
    if os.path.exists(DONE_PATH):
        with open(DONE_PATH, encoding='utf-8') as f:
            return set(json.load(f))
    return set()

def save_done(done: set):
    with open(DONE_PATH, 'w', encoding='utf-8') as f:
        json.dump(list(done), f)

# ── Discord通知 ───────────────────────────────
def send_discord(message: str, webhook: str):
    if not webhook:
        return
    data = json.dumps({'content': message}).encode('utf-8')
    req = urllib.request.Request(
        webhook, data=data,
        headers={'Content-Type': 'application/json', 'User-Agent': 'Mozilla/5.0'}
    )
    try:
        ctx = ssl._create_unverified_context()
        urllib.request.urlopen(req, context=ctx)
    except Exception as e:
        print(f"Discord送信エラー: {e}")

# ── ログ操作 ──────────────────────────────────
def load_log():
    if os.path.exists(LOG_PATH):
        with open(LOG_PATH, encoding='utf-8') as f:
            return json.load(f)
    return {}

def save_log(log: dict):
    with open(LOG_PATH, 'w', encoding='utf-8') as f:
        json.dump(log, f, ensure_ascii=False, indent=2)

# ── コマンド実行 ──────────────────────────────
def execute_command(command: str, config: dict):
    webhook = config.get('DISCORD_WEBHOOK', '')
    now = datetime.now().strftime('%Y-%m-%d %H:%M')
    log = load_log()

    # FX RESET: 損失上限リセット
    if command == 'FX RESET':
        if log:
            initial = log.get('initial_balance', 0)
            log['daily_loss_stopped'] = False
            # 初期残高を現在の残高でリセット
            try:
                import MetaTrader5 as mt5
                if mt5.initialize():
                    info = mt5.account_info()
                    log['initial_balance'] = info.balance
                    mt5.shutdown()
            except Exception as e:
                print(f"MT5残高取得失敗: {e}")
            save_log(log)

        msg = f"""【FX Bot】{now}
🔄 **損失上限をリセットしました**

メールコマンドにより実行
本日の取引を再開します。
初期残高を現在の残高で更新しました。"""
        send_discord(msg, webhook)
        print("FX RESET 実行完了")

    # FX STOP: 全ポジション緊急決済
    elif command == 'FX STOP':
        closed = 0
        try:
            import MetaTrader5 as mt5
            if mt5.initialize():
                positions = mt5.positions_get()
                if positions:
                    for pos in positions:
                        symbol    = pos.symbol
                        volume    = pos.volume
                        pos_type  = pos.type
                        ticket    = pos.ticket
                        price     = mt5.symbol_info_tick(symbol).bid if pos_type == 0 else mt5.symbol_info_tick(symbol).ask
                        close_type = mt5.ORDER_TYPE_SELL if pos_type == 0 else mt5.ORDER_TYPE_BUY

                        request = {
                            'action':       mt5.TRADE_ACTION_DEAL,
                            'symbol':       symbol,
                            'volume':       volume,
                            'type':         close_type,
                            'position':     ticket,
                            'price':        price,
                            'deviation':    20,
                            'magic':        20240101,
                            'comment':      'FXBot_STOP',
                            'type_time':    mt5.ORDER_TIME_GTC,
                            'type_filling': mt5.ORDER_FILLING_FOK,
                        }
                        result = mt5.order_send(request)
                        if result.retcode == mt5.TRADE_RETCODE_DONE:
                            closed += 1
                mt5.shutdown()
        except Exception as e:
            print(f"MT5決済エラー: {e}")

        # 取引停止フラグも立てる
        if log:
            log['daily_loss_stopped'] = True
            save_log(log)

        msg = f"""【FX Bot】{now}
⛔ **緊急停止・全ポジション決済**

メールコマンドにより実行
決済ポジション数: {closed}件
本日の新規取引も停止しました。

再開するには「FX RESET」を送信してください。"""
        send_discord(msg, webhook)
        print(f"FX STOP 実行完了 ({closed}件決済)")

    # FX STATUS: 現在状況通知
    elif command == 'FX STATUS':
        try:
            import MetaTrader5 as mt5
            if mt5.initialize():
                info      = mt5.account_info()
                positions = mt5.positions_get()
                pos_count = len(positions) if positions else 0
                pos_text  = ''
                if positions:
                    for p in positions:
                        dir_jp    = '買い' if p.type == 0 else '売り'
                        pos_text += f"\n  {p.symbol} {dir_jp} {p.volume}lot 損益:{'+' if p.profit>=0 else ''}{p.profit:,.0f}円"
                mt5.shutdown()

                stopped = log.get('daily_loss_stopped', False)
                initial = log.get('initial_balance', info.balance)
                pnl     = info.equity - initial

                msg = f"""【FX Bot】{now}
📊 **現在状況（メールリクエスト）**

残高: {info.balance:,.0f}円
資産: {info.equity:,.0f}円
本日損益: {'+' if pnl>=0 else ''}{pnl:,.0f}円

保有ポジション ({pos_count}件){pos_text if pos_text else chr(10)+'  なし'}

取引状態: {'⛔ 停止中' if stopped else '✅ 稼働中'}"""
                send_discord(msg, webhook)
                print("FX STATUS 送信完了")
        except Exception as e:
            print(f"MT5接続エラー: {e}")
            send_discord(f"【FX Bot】{now}\n⚠️ STATUS取得エラー: {e}", webhook)

# ── メール監視 ────────────────────────────────
def check_mail(config: dict):
    gmail_address  = config.get('GMAIL_ADDRESS', '')
    app_password   = config.get('GMAIL_APP_PASSWORD', '')

    if not gmail_address or not app_password:
        print("Gmail設定が見つかりません")
        return

    done = load_done()

    try:
        ctx = ssl._create_unverified_context()
        mail = imaplib.IMAP4_SSL('imap.gmail.com', 993, ssl_context=ctx)
        mail.login(gmail_address, app_password)
        mail.select('inbox')

        # 未読メールを検索
        _, data = mail.search(None, 'UNSEEN')
        mail_ids = data[0].split()

        for mail_id in mail_ids:
            mail_id_str = mail_id.decode()
            if mail_id_str in done:
                continue

            _, msg_data = mail.fetch(mail_id, '(RFC822)')
            raw = msg_data[0][1]
            msg = email.message_from_bytes(raw)

            # 件名デコード
            subject_raw = msg.get('Subject', '')
            subject = email.header.decode_header(subject_raw)
            subject_str = ''
            for part, enc in subject:
                if isinstance(part, bytes):
                    subject_str += part.decode(enc or 'utf-8')
                else:
                    subject_str += part
            subject_str = subject_str.strip().upper()

            print(f"メール受信: 件名='{subject_str}'")

            # コマンド判定
            for cmd in COMMANDS:
                if cmd in subject_str:
                    print(f"コマンド検知: {cmd}")
                    execute_command(cmd, config)
                    break

            done.add(mail_id_str)

        mail.logout()
        save_done(done)

    except Exception as e:
        print(f"メール監視エラー: {e}")

# ── メイン ────────────────────────────────────
def main():
    print(f"[{datetime.now().strftime('%H:%M:%S')}] メール監視開始...")
    try:
        import heartbeat_check as hb
        hb.record_heartbeat('mail_monitor')
    except: pass
    config = load_env()
    check_mail(config)
    print(f"[{datetime.now().strftime('%H:%M:%S')}] 完了")

if __name__ == '__main__':
    main()
