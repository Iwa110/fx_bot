"""
bb_monitor.py  - BB逆張り戦略（5分毎実行）v22
v14:
  - ALLOWED_HOURS_UTC辞書を追加（ペア別UTC時間帯フィルター）
  - main()ループに時間帯チェックを追加（空リスト=停止、None=制限なし）
  - USDCADは空リスト（enabled=Falseに加え時間帯でも停止）
v15 GBPJPY tp_dist上書き削除（RR改善・rm.calc_tp_sl設計値に統一）
v16
  - BB_PAIRS各ペアにsl_atr_mult追加
  - calc_bb_signal内のTP/SL計算にsl_atr_multを渡す
v17 sl_atr_mult updated
v18 マルチブローカー対応: broker_utils / argparse --broker 追加
v19 EURUSD/GBPUSD エントリー条件強化 (2026-05-12)
  - ENTRY_FILTER: EURUSD/GBPUSD に use_htf4h=True 追加（4h EMA20フィルター）
  - EURUSD: sl_atr_mult 3.0→1.2, bb_width_th=0.0020 追加（低ボラ除外）
  - GBPUSD: sl_atr_mult 2.0→1.2
  - calc_bb_signal: bb_width_th ペア別設定対応
  BT根拠 (1h足, 2024-04-24〜2026-04-24):
    EURUSD htf4h_and_bb_width(bw=0.002) sl=1.2 rr=1.5 → PF=1.649 WR=50.6% N=83
    GBPUSD htf4h_only sl=1.2 rr=1.5 → PF=3.440 WR=69.1% N=97
v20 EURUSD/GBPUSD停止、GBPJPY/USDJPY F1フィルター削除 (2026-05-14)
  - EURUSD: enabled=False（BT PF<0.7、BB戦略との相性不良により停止）
  - GBPUSD: enabled=False（実稼働PF=0.397、BT最高0.854で目標未達のため停止）
  - GBPJPY: filter_type None化（htf4h後はF1追加効果ゼロのためシンプル化）
  - USDJPY: filter_type None化（htf4h後はF1追加効果ゼロのためシンプル化）
  - main()ループにenabled=Falseチェック追加
  BT根拠 (5m足, pair_grid_results.csv, 2026-05-14):
    GBPJPY htf4h_only sl=3.5 s2d=0.3 → PF=1.326 WR=51.7% N=151
    USDJPY f1_p3     sl=3.0 s2d=0.3 → PF=1.331 WR=49.4% N=83
    EURUSD (全組合せ最高PF=0.681, N=132) → 停止
    GBPUSD (全組合せ最高PF=0.854, N=153) → 停止
v21 GBPJPY/USDJPY Stage2廃止→固定TP、USDJPY htf4h_rsiフィルター追加 (2026-05-14)
  - GBPJPY: fixed_tp_rr=1.5 追加（TP=SL×1.5）、trail_monitor Stage2無効化
  - USDJPY: fixed_tp_rr=1.5 追加（TP=SL×1.5）、trail_monitor Stage2無効化
  - USDJPY: ENTRY_FILTER use_htf4h_rsi=True（4h EMA20方向一致+RSI<55/RSI>45）
  - get_htf4h_rsi_signal() 新規追加
  - GBPJPY: ENTRY_FILTER use_htf4h_rsi_bw=True（4h EMA20+RSI<60/RSI>55 + 5m BBwidth rolling）
  - get_htf4h_rsi_bw_signal() 新規追加
  BT根拠 (5m足, gbpjpy_filter_bt.py + gbpjpy_split_validation.py, 全データ2026-05-14):
    RSI(buy<60,sell>55)+BBwidth(ratio=1.2,lb=20) → PF=1.861 WR=59.8% N=92 MaxDD=186.8pips
    期間分割検証(A/B/C): PF=1.315/1.612/3.539 全期間>1.0 → STABLE
  - USDJPY: htf4h_rsi_bw追加（4h EMA20+RSI<55/RSI>45 + 5m BBwidth rolling）(2026-05-14)
    bw_ratio=0.8, bw_lookback=30（GBPJPYとは別パラメータ）
    RSI閾値は変更なし（buy<55, sell>45のまま）
  BT根拠 (5m足, usdjpy_filter_bt.py + usdjpy_sell_grid.py, 全データ2026-05-14):
    BBwidth(ratio=0.8,lb=30)+RSI(buy<55,sell>45) → PF=1.300 WR=49.7% N=157 MaxDD=301.9pips [CONDITIONAL]
    期間分割: STABLE予定（VPS実稼働でデータ蓄積後に確認）
v22 EURJPY F1andF2廃止→htf4h_only + 固定TP (2026-05-14)
  - EURJPY: filter_type None化（F1andF2廃止）
  - EURJPY: fixed_tp_rr=1.5 追加（TP=SL×1.5）、trail_monitor Stage2無効化
  - EURJPY: ENTRY_FILTER use_htf4h=True（4h EMA20方向のみ）
  BT根拠 (1h足, eurjpy_filter_bt.py + eurjpy_split_validation.py, 2026-05-14):
    ベースライン(htf4h_only) → PF=1.645 WR=50.0% N=30（全データ）
    期間分割検証(A/B/C): PF=2.903/1.078/1.543 全期間>1.0 → STABLE
    フィルター追加(rsi+bw)はPeriod_C PF=0.610で過適合 → htf4h_onlyを採用
"""

import sys, os, ssl, json, argparse
from datetime import datetime, timedelta, timezone
import MetaTrader5 as mt5
import pandas as pd
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import risk_manager as rm
from broker_utils import connect_mt5, disconnect_mt5, build_symbol_map, is_live_broker

# ══════════════════════════════════════════
# ブローカー設定
# ══════════════════════════════════════════
BROKER_KEY = 'oanda'

# ベースシンボル → MT5シンボル名（main()内で populate）
_SYMBOL_MAP: dict[str, str] = {}

def _rsym(base: str) -> str:
    """ベースシンボルをブローカー固有のMT5シンボル名に変換する"""
    return _SYMBOL_MAP.get(base, base)

# ══════════════════════════════════════════
# 定数・設定
# ══════════════════════════════════════════
MAX_JPY_LOT      = 0.4
MAX_TOTAL_POS    = 13
COOLDOWN_MINUTES = 15

# 時間帯フィルター（UTC）空リスト=全停止、None=制限なし
ALLOWED_HOURS_UTC = {
    'USDCAD': [],
    'GBPJPY': None,  # time_filter_bt結果: [9,17]は逆効果（PF 0.936→0.872, N=563→31）
    'EURJPY': [9, 17],
    # 'USDJPY': [21, 22, 5],  # htf4h_onlyに統一のため無効化
    # [FIX: Phase1データ蓄積再開のため制限解除。PF改善確認後に再停止を検討]
    'EURUSD': [],  # v20: BT PF<0.7, BB戦略との相性不良により停止
    'GBPUSD': [],  # v20: 実稼働PF=0.397, BT最高0.854で目標未達のため停止
}
ENTRY_FILTER = {
    'GBPJPY': {'use_htf4h_rsi_bw': True},  # v21 GBPJPY filter added: 4h EMA20+RSI<60/RSI>55 + 5m BBwidth
    'USDJPY': {'use_htf4h_rsi_bw': True},  # v21 USDJPY BBwidth filter added: 4h EMA20+RSI<55/RSI>45 + 5m BBwidth(ratio=0.8,lb=30)
    'EURJPY': {'use_htf4h': True},          # v22 EURJPY: htf4h_only（4h EMA20方向のみ）
    # EURUSD/GBPUSD は v20 で停止のためエントリーフィルター不要
}
BB_PAIRS = {
    'USDCAD': {
        'enabled': False,
        'is_jpy': False,
        'max_pos': 1,
        'sigma': None,
        'filter_type': None,
        'rsi_buy_max': 45,
        'rsi_sell_min': 55,
        'sl_atr_mult': 1.5,  # 停止中・変更なし
    },
    'GBPJPY': {
        'is_jpy': True, 'max_pos': 1, 'sigma': None,
        'filter_type': None,  # v20: F1フィルター削除（htf4h後は追加効果ゼロのためシンプル化）
        'sl_atr_mult': 3.0,  # BT採用値
        'fixed_tp_rr': 1.5,  # v21: Stage2廃止→固定TP(SL×1.5)
        'bw_ratio':    1.2,  # v21 GBPJPY filter added: BBwidth > 20bar_avg × 1.2
        'bw_lookback': 20,   # v21 GBPJPY filter added
    },
    'EURJPY': {
        'is_jpy': True, 'max_pos': 1, 'sigma': None,
        'filter_type': None,   # v22: F1andF2廃止（htf4h_only + 固定TPに移行）
        'sl_atr_mult': 2.5,    # BT採用値（変更なし）
        'fixed_tp_rr': 1.5,    # v22: Stage2廃止→固定TP(SL×1.5)
    },
    'USDJPY': {
        'is_jpy': True, 'max_pos': 1, 'sigma': 2.0,
        'filter_type': None,  # v20: F1フィルター削除（htf4h後は追加効果ゼロのためシンプル化）
        'sl_atr_mult': 3.0,  # BT採用値
        'fixed_tp_rr': 1.5,  # v21: Stage2廃止→固定TP(SL×1.5)
        'bw_ratio':    0.8,  # v21 USDJPY BBwidth filter added: BBwidth > 30bar_avg × 0.8
        'bw_lookback': 30,   # v21 USDJPY BBwidth filter added (GBPJPYはratio=1.2,lb=20で別管理)
    },
    'EURUSD': {
        'enabled': False,        # BT PF<0.7, BB戦略との相性不良により停止 (v20)
        'is_jpy': False, 'max_pos': 1, 'sigma': None,
        'filter_type': None,
        'sl_atr_mult': 1.2,      # v19 BT採用値 (旧3.0)
        'bb_width_th': 0.0020,   # v19 低ボラ除外（BB幅<0.0020 pipsスキップ）
    },
    'GBPUSD': {
        'enabled': False,        # 実稼働PF=0.397, BT最高0.854で目標未達のため停止 (v20)
        'is_jpy': False, 'max_pos': 1, 'sigma': None,
        'filter_type': None,
        'sl_atr_mult': 1.2,      # v19 BT採用値 (旧2.0)
    },
}
BB_PARAMS = {
    'period':     20,
    'sigma':      1.5,
    'rr':         1.0,
    'exit_sigma': 1.0,
}

HTF_PARAMS = {
    'period':      20,
    'sigma':       1.5,
    'range_sigma': 1.0,
    'bars':        50,
}

RSI_PARAMS = {
    'period':   14,
    'sell_min': 60,
    'buy_max':  40,
}

LOG_FILE         = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'bb_log.txt')
ENV_FILE         = os.path.join(os.path.dirname(os.path.abspath(__file__)), '.env')
DAILY_LOSS_LIMIT = -50000

# ══════════════════════════════════════════
# ユーティリティ
# ══════════════════════════════════════════
def load_env():
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

def log(msg, filepath=None):
    if filepath is None:
        filepath = LOG_FILE
    ts   = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    line = '[' + ts + '] ' + msg
    print(line)
    try:
        with open(filepath, 'a', encoding='utf-8') as f:
            f.write(line + '\n')
    except Exception:
        pass

def send_discord(msg, webhook):
    if not webhook:
        return
    try:
        import urllib.request, json as _json
        data = _json.dumps({'content': msg}).encode('utf-8')
        req  = urllib.request.Request(
            webhook, data=data,
            headers={'Content-Type': 'application/json', 'User-Agent': 'Mozilla/5.0'}
        )
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode    = ssl.CERT_NONE
        urllib.request.urlopen(req, timeout=10, context=ctx)
    except Exception as e:
        log('Discord送信エラー: ' + str(e))

# ══════════════════════════════════════════
# MT5 ポジション管理
# ══════════════════════════════════════════
def count_total():
    pos = mt5.positions_get()
    return len(pos) if pos else 0

def count_by_strategy(strategy):
    pos = mt5.positions_get()
    if not pos:
        return 0
    return sum(1 for p in pos if p.comment == strategy)

def count_jpy_lots():
    pos = mt5.positions_get()
    if not pos:
        return 0.0
    total = 0.0
    for p in pos:
        sym = p.symbol
        if sym.endswith('JPY') or (len(sym) > 3 and sym[3:6] == 'JPY'):
            total += p.volume
    return total

def is_dup(symbol, strategy, logf):
    pos = mt5.positions_get(symbol=symbol)
    if not pos:
        return False
    for p in pos:
        if p.comment == strategy:
            return True
    return False

def is_in_cooldown(symbol):
    now_utc  = datetime.now(timezone.utc)
    from_utc = now_utc - timedelta(minutes=COOLDOWN_MINUTES)
    deals = mt5.history_deals_get(from_utc, now_utc)
    if not deals:
        return False
    for d in deals:
        if d.symbol != symbol:
            continue
        if d.magic != 20250001:
            continue
        if d.entry != mt5.DEAL_ENTRY_OUT:
            continue
        if d.reason == mt5.DEAL_REASON_SL:
            deal_time = datetime.fromtimestamp(d.time, tz=timezone.utc)
            elapsed   = (now_utc - deal_time).total_seconds() / 60
            if elapsed <= COOLDOWN_MINUTES:
                log(symbol + ': クールダウン中 SL後' + f'{elapsed:.1f}' + '分')
                return True
    return False

def check_closed(logf, webhook):
    hist = mt5.history_deals_get(
        datetime.now() - timedelta(hours=1),
        datetime.now()
    )
    if not hist:
        return
    for deal in hist:
        if deal.entry == mt5.DEAL_ENTRY_OUT and 'BB_' in deal.comment:
            pnl  = round(deal.profit)
            sign = '+' if pnl >= 0 else ''
            log('決済: ' + deal.symbol + ' PnL=' + sign + str(pnl) + '円 ' + deal.comment, logf)

def check_daily_loss(logf, webhook):
    today_start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    deals       = mt5.history_deals_get(today_start, datetime.now())
    if not deals:
        return True
    daily_pnl = sum(d.profit for d in deals if 'BB_' in d.comment)
    if daily_pnl < DAILY_LOSS_LIMIT:
        msg = '【BB警告】日次損失上限到達: ' + str(round(daily_pnl)) + '円 → 本日の取引停止'
        log(msg, logf)
        send_discord(msg, webhook)
        return False
    return True

# ══════════════════════════════════════════
# インジケーター計算
# ══════════════════════════════════════════
def calc_rsi(rates_df, period=14):
    close = rates_df['close']
    delta = close.diff()
    gain  = delta.clip(lower=0)
    loss  = (-delta).clip(lower=0)
    avg_g = gain.ewm(com=period - 1, min_periods=period).mean()
    avg_l = loss.ewm(com=period - 1, min_periods=period).mean()
    rs    = avg_g / avg_l.replace(0, np.nan)
    rsi   = 100 - (100 / (1 + rs))
    return float(rsi.iloc[-1]) if not rsi.empty else 50.0

def calc_bb(rates_df, period, sigma):
    close   = rates_df['close']
    ma      = close.rolling(period).mean()
    std     = close.rolling(period).std()
    upper   = ma + sigma * std
    lower   = ma - sigma * std
    idx     = -2
    ma_v    = float(ma.iloc[idx])
    std_v   = float(std.iloc[idx])
    upper_v = float(upper.iloc[idx])
    lower_v = float(lower.iloc[idx])
    close_v = float(close.iloc[idx])
    sigma_pos = (close_v - ma_v) / std_v if std_v > 0 else 0.0
    return {
        'ma':        ma_v,
        'upper':     upper_v,
        'lower':     lower_v,
        'close':     close_v,
        'sigma_pos': sigma_pos,
        'std':       std_v,
    }

# ══════════════════════════════════════════
# 上位足フィルター（1時間足）
# ══════════════════════════════════════════
def htf_range_filter(symbol, range_sigma_override=None):
    bars = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_H1, 0, HTF_PARAMS['bars'])
    if bars is None or len(bars) < HTF_PARAMS['period'] + 5:
        return True, 0.0, '1hデータ不足（スキップ判定せず通過）'

    df          = pd.DataFrame(bars)
    bb          = calc_bb(df, HTF_PARAMS['period'], HTF_PARAMS['sigma'])
    sigma_pos   = bb['sigma_pos']
    range_limit = range_sigma_override if range_sigma_override is not None \
                  else HTF_PARAMS['range_sigma']

    if abs(sigma_pos) > range_limit:
        direction = '上方トレンド' if sigma_pos > 0 else '下方トレンド'
        reason    = '1h足 ' + direction + '（σ=' + f'{sigma_pos:+.2f}' + '）'
        return False, sigma_pos, reason

    return True, sigma_pos, 'レンジ判定OK（1hσ=' + f'{sigma_pos:+.2f}' + '）'

def get_htf4h_signal(symbol):
    """4h足EMA20フィルター。+1=Buy許可 / -1=Sell許可"""
    rates = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_H4, 0, 25)
    if rates is None or len(rates) < 21:
        return None
    closes = pd.Series([r['close'] for r in rates])
    ema20 = closes.ewm(span=20, adjust=False).mean()
    return 1 if closes.iloc[-1] > ema20.iloc[-1] else -1

def get_htf4h_rsi_signal(symbol):  # v21
    """4h足EMA20方向一致+RSI14フィルター。+1=Buy許可 / -1=Sell許可 / 0=条件不成立"""
    rates = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_H4, 0, 30)
    if rates is None or len(rates) < 25:
        return None
    df = pd.DataFrame(rates)
    closes = df['close']
    ema20 = closes.ewm(span=20, adjust=False).mean()
    rsi_val = calc_rsi(df, 14)
    last_close = float(closes.iloc[-1])
    last_ema = float(ema20.iloc[-1])
    if last_close > last_ema and rsi_val < 55:   # buy許可: EMA20上方+RSI<55
        return 1
    if last_close < last_ema and rsi_val > 45:   # sell許可: EMA20下方+RSI>45
        return -1
    return 0

def get_htf4h_rsi_bw_signal(symbol):  # v21 GBPJPY filter added
    """GBPJPY用 4h足EMA20方向一致+RSI14フィルター。
    buy許可: EMA20上方 + RSI<60 / sell許可: EMA20下方 + RSI>55
    +1=Buy許可 / -1=Sell許可 / 0=条件不成立"""
    rates = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_H4, 0, 30)
    if rates is None or len(rates) < 25:
        return None
    df = pd.DataFrame(rates)
    closes = df['close']
    ema20 = closes.ewm(span=20, adjust=False).mean()
    rsi_val = calc_rsi(df, 14)
    last_close = float(closes.iloc[-1])
    last_ema = float(ema20.iloc[-1])
    if last_close > last_ema and rsi_val < 60:   # buy許可: EMA20上方+RSI<60
        return 1
    if last_close < last_ema and rsi_val > 55:   # sell許可: EMA20下方+RSI>55
        return -1
    return 0

# ══════════════════════════════════════════
# RSIフィルター
# ══════════════════════════════════════════
def rsi_filter(rates_df, direction, buy_max=None, sell_min=None):
    rsi_val  = calc_rsi(rates_df, RSI_PARAMS['period'])
    buy_max  = buy_max  if buy_max  is not None else RSI_PARAMS['buy_max']
    sell_min = sell_min if sell_min is not None else RSI_PARAMS['sell_min']
    if direction == 'sell':
        if rsi_val < sell_min:
            reason = 'RSI未達（' + f'{rsi_val:.1f}' + ' < ' + str(sell_min) + '、買われすぎ未確認）'
            return False, rsi_val, reason
    else:
        if rsi_val > buy_max:
            reason = 'RSI未達（' + f'{rsi_val:.1f}' + ' > ' + str(buy_max) + '、売られすぎ未確認）'
            return False, rsi_val, reason
    return True, rsi_val, 'RSI OK（' + f'{rsi_val:.1f}' + '）'

# ══════════════════════════════════════════
# 追加フィルター関数
# ══════════════════════════════════════════
def f1_momentum_filter(df_5m, direction, param):
    """
    F1_Momentum: 直近param本の終値モメンタム方向確認
    BUY  → 直近param本が下降トレンド（価格が下落中）
    SELL → 直近param本が上昇トレンド（価格が上昇中）
    """
    close = df_5m['close']
    if len(close) < param + 2:
        return True, 'F1: データ不足（通過）'

    recent_close = float(close.iloc[-2])
    past_close   = float(close.iloc[-2 - param])
    diff         = recent_close - past_close

    info_str = f'F1(param={param}) diff={diff:+.5f}'

    if direction == 'buy':
        if diff >= 0:
            return False, info_str + ' → 下落モメンタム未確認（BUYスキップ）'
    else:
        if diff <= 0:
            return False, info_str + ' → 上昇モメンタム未確認（SELLスキップ）'

    return True, info_str + ' OK'


def f2_divergence_filter(symbol, direction, div_pips, is_jpy):
    """
    F2_Divergence: 合成レートとの乖離確認
    JPY系ペア: USDJPY/EURJPY/AUDJPYの相互乖離を確認
    """
    pip_unit = 0.01 if is_jpy else 0.0001

    def get_mid(sym):
        tick = mt5.symbol_info_tick(_rsym(sym))
        if tick is None:
            return None
        return (tick.bid + tick.ask) / 2.0

    try:
        if symbol == _rsym('EURJPY'):
            eurusd = get_mid('EURUSD')
            usdjpy = get_mid('USDJPY')
            if eurusd is None or usdjpy is None:
                return True, 'F2: 構成ペア価格取得失敗（通過）'
            synthetic = eurusd * usdjpy
            actual    = get_mid('EURJPY')
        elif symbol == _rsym('GBPJPY'):
            gbpusd = get_mid('GBPUSD')
            usdjpy = get_mid('USDJPY')
            if gbpusd is None or usdjpy is None:
                return True, 'F2: 構成ペア価格取得失敗（通過）'
            synthetic = gbpusd * usdjpy
            actual    = get_mid('GBPJPY')
        elif symbol == _rsym('AUDJPY'):
            audusd = get_mid('AUDUSD')
            usdjpy = get_mid('USDJPY')
            if audusd is None or usdjpy is None:
                return True, 'F2: 構成ペア価格取得失敗（通過）'
            synthetic = audusd * usdjpy
            actual    = get_mid('AUDJPY')
        else:
            return True, 'F2: 非対応ペア（通過）'

        if actual is None:
            return True, 'F2: 実レート取得失敗（通過）'

        diff_pips = abs(actual - synthetic) / pip_unit
        info_str  = f'F2(div_pips={div_pips}) actual={actual:.4f} synthetic={synthetic:.4f} diff={diff_pips:.1f}pips'

        if diff_pips < div_pips:
            return False, info_str + ' → 乖離不足（スキップ）'

        return True, info_str + ' OK'

    except Exception as e:
        return True, 'F2: 計算エラー ' + str(e) + '（通過）'


def f3_bbstack_filter(symbol, direction, sigma_threshold, is_jpy):
    """
    F3_BBStack: 構成ペアのBB位置確認
    AUDJPYの場合: AUDUSDとUSDJPYが同方向のBB位置にあるか確認
    """
    def get_bb_sigma(sym):
        bars = mt5.copy_rates_from_pos(_rsym(sym), mt5.TIMEFRAME_M5, 0, 60)
        if bars is None or len(bars) < BB_PARAMS['period'] + 5:
            return None
        df  = pd.DataFrame(bars)
        bb  = calc_bb(df, BB_PARAMS['period'], BB_PARAMS['sigma'])
        return bb['sigma_pos']

    try:
        if symbol == _rsym('AUDJPY'):
            audusd_sigma = get_bb_sigma('AUDUSD')
            usdjpy_sigma = get_bb_sigma('USDJPY')
            if audusd_sigma is None or usdjpy_sigma is None:
                return True, 'F3: 構成ペアデータ不足（通過）'

            info_str = (f'F3(σ_thr={sigma_threshold}) '
                        f'AUDUSD_σ={audusd_sigma:+.2f} USDJPY_σ={usdjpy_sigma:+.2f}')

            if direction == 'buy':
                if audusd_sigma > -sigma_threshold or usdjpy_sigma > -sigma_threshold:
                    return False, info_str + ' → BUYスタック未確認（スキップ）'
            else:
                if audusd_sigma < sigma_threshold or usdjpy_sigma < sigma_threshold:
                    return False, info_str + ' → SELLスタック未確認（スキップ）'

            return True, info_str + ' OK'
        else:
            return True, 'F3: 非対応ペア（通過）'

    except Exception as e:
        return True, 'F3: 計算エラー ' + str(e) + '（通過）'


def apply_pair_filter(symbol, cfg, df_5m, direction):
    """
    ペア別フィルターを適用する。
    filter_typeに応じてF1/F2/F3をAND/OR結合。
    """
    ft = cfg.get('filter_type')
    if ft is None:
        return True, 'ペア別フィルターなし'

    is_jpy = cfg.get('is_jpy', False)

    if ft == 'F1':
        param = cfg.get('f1_param', 5)
        ok, reason = f1_momentum_filter(df_5m, direction, param)
        return ok, 'F1: ' + reason

    elif ft == 'F2':
        div_pips = cfg.get('f2_param', 5.0)
        ok, reason = f2_divergence_filter(symbol, direction, div_pips, is_jpy)
        return ok, reason

    elif ft == 'F3':
        f3_sig = cfg.get('f3_sigma', 0.5)
        ok, reason = f3_bbstack_filter(symbol, direction, f3_sig, is_jpy)
        return ok, reason

    elif ft == 'F1andF2':
        param    = cfg.get('f1_param', 5)
        div_pips = cfg.get('f2_param', 10.0)
        f1_ok, f1_r = f1_momentum_filter(df_5m, direction, param)
        if not f1_ok:
            return False, 'F1andF2(F1失敗): ' + f1_r
        f2_ok, f2_r = f2_divergence_filter(symbol, direction, div_pips, is_jpy)
        if not f2_ok:
            return False, 'F1andF2(F2失敗): ' + f2_r
        return True, 'F1andF2: ' + f1_r + ' / ' + f2_r

    elif ft == 'F2andF1':
        param    = cfg.get('f1_param', 3)
        div_pips = cfg.get('f2_param', 10.0)
        f2_ok, f2_r = f2_divergence_filter(symbol, direction, div_pips, is_jpy)
        if not f2_ok:
            return False, 'F2andF1(F2失敗): ' + f2_r
        f1_ok, f1_r = f1_momentum_filter(df_5m, direction, param)
        if not f1_ok:
            return False, 'F2andF1(F1失敗): ' + f1_r
        return True, 'F2andF1: ' + f2_r + ' / ' + f1_r

    elif ft == 'F2orF1':
        param    = cfg.get('f1_param', 3)
        div_pips = cfg.get('f2_param', 5.0)
        f2_ok, f2_r = f2_divergence_filter(symbol, direction, div_pips, is_jpy)
        if f2_ok:
            return True, 'F2orF1(F2通過): ' + f2_r
        f1_ok, f1_r = f1_momentum_filter(df_5m, direction, param)
        if f1_ok:
            return True, 'F2orF1(F1通過): ' + f1_r
        return False, 'F2orF1(両方失敗): ' + f2_r + ' / ' + f1_r

    elif ft == 'F3orF2':
        div_pips = cfg.get('f2_param', 4.0)
        f3_sig   = cfg.get('f3_sigma', 0.5)
        f3_ok, f3_r = f3_bbstack_filter(symbol, direction, f3_sig, is_jpy)
        if f3_ok and 'OK' in f3_r:
            return True, 'F3orF2(F3通過): ' + f3_r
        f2_ok, f2_r = f2_divergence_filter(symbol, direction, div_pips, is_jpy)
        if f2_ok and 'OK' in f2_r:
            return True, 'F3orF2(F2通過): ' + f2_r
        return False, 'F3orF2(両方失敗): ' + f3_r + ' / ' + f2_r

    return True, '未定義filter_type: ' + str(ft) + '（通過）'

# ══════════════════════════════════════════
# BBシグナル計算（メイン）
# ══════════════════════════════════════════
def calc_bb_signal(symbol, cfg):
    """
    symbol はブローカー固有名（_rsym適用済み）で受け取る。
    フィルター適用順:
      1. 1時間足レンジフィルター
      2. 5分足BBタッチ確認
      3. RSIフィルター
      4. ペア別フィルター（F1/F2/F3）
    """
    is_jpy = cfg.get('is_jpy', False)
    sigma  = cfg.get('sigma')

    is_range, htf_sigma, htf_reason = htf_range_filter(symbol, range_sigma_override=cfg.get('htf_range_sigma'))
    if not is_range:
        log(symbol + ': HTFスキップ → ' + htf_reason)
        return None

    bars_5m = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_M5, 0, 60)
    if bars_5m is None or len(bars_5m) < BB_PARAMS['period'] + 5:
        return None

    df_5m             = pd.DataFrame(bars_5m)
    effective_sigma   = sigma if sigma is not None else BB_PARAMS['sigma']
    bb                = calc_bb(df_5m, BB_PARAMS['period'], effective_sigma)
    close             = bb['close']
    upper             = bb['upper']
    lower             = bb['lower']
    sigma_pos         = bb['sigma_pos']

    if close >= upper:
        direction = 'sell'
    elif close <= lower:
        direction = 'buy'
    else:
        log(symbol + ': BBバンド未到達（σ=' + f'{sigma_pos:+.2f}' + '） ' + htf_reason)
        return None

    # BBバンド幅フィルター（低ボラ除外）
    bb_width_th = cfg.get('bb_width_th')
    if bb_width_th is not None:
        bb_width = upper - lower
        if bb_width < bb_width_th:
            log(symbol + ': BB幅不足スキップ（width=' + f'{bb_width:.5f}' + ' < ' + str(bb_width_th) + '）')
            return None

    # v21 GBPJPY filter added: BBwidth rolling mean フィルター
    # エントリー足BB幅 > 直近bw_lookback本のBB幅移動平均 × bw_ratio
    bw_ratio    = cfg.get('bw_ratio')
    bw_lookback = cfg.get('bw_lookback')
    if bw_ratio is not None and bw_lookback is not None:
        std_series = df_5m['close'].rolling(BB_PARAMS['period']).std()
        bw_series  = 2.0 * effective_sigma * std_series
        mean_bw_v  = float(bw_series.rolling(bw_lookback).mean().iloc[-2])
        cur_bw     = upper - lower
        if not np.isnan(mean_bw_v) and cur_bw < mean_bw_v * bw_ratio:
            log(symbol + ': BBwidth不足スキップ（cur=' + f'{cur_bw:.5f}' +
                ' < avg*' + str(bw_ratio) + '=' + f'{mean_bw_v * bw_ratio:.5f}' + '）')
            return None

    rsi_ok, rsi_val, rsi_reason = rsi_filter(
        df_5m, direction,
        buy_max  = cfg.get('rsi_buy_max'),
        sell_min = cfg.get('rsi_sell_min'),
    )

    filter_ok, filter_reason = apply_pair_filter(symbol, cfg, df_5m, direction)
    if not filter_ok:
        log(symbol + ': フィルタースキップ → ' + filter_reason +
            ' dir=' + direction +
            ' σ=' + f'{sigma_pos:+.2f}' +
            ' RSI=' + f'{rsi_val:.1f}')
        return None

    try:
        atr = rm.get_atr(symbol, 'BB')
        tp_dist, sl_dist = rm.calc_tp_sl(atr, 'BB', is_jpy=is_jpy)
        sl_mult_pair = cfg.get('sl_atr_mult', 2.0)
        if sl_mult_pair != 2.0:
            floor = rm.ATR_FLOOR_JPY if is_jpy else rm.ATR_FLOOR_NONJPY
            sl_dist = max(atr, floor) * sl_mult_pair
        fixed_tp_rr = cfg.get('fixed_tp_rr')
        if fixed_tp_rr is not None:
            tp_dist = sl_dist * fixed_tp_rr  # v21: Stage2廃止→固定TP(SL×fixed_tp_rr)
    except Exception as e:
        log(symbol + ': TP/SL計算エラー: ' + str(e))
        return None

    tick = mt5.symbol_info_tick(symbol)
    if tick is None:
        return None

    if direction == 'sell':
        entry = tick.bid
        tp    = entry - tp_dist
        sl    = entry + sl_dist
    else:
        entry = tick.ask
        tp    = entry + tp_dist
        sl    = entry - sl_dist

    log(symbol + ': シグナル確定 dir=' + direction +
        ' σ=' + f'{sigma_pos:+.2f}' +
        ' BB_σ=' + str(effective_sigma) +
        ' RSI=' + f'{rsi_val:.1f}' +
        ' ' + htf_reason +
        ' Filter:' + filter_reason)

    return {
        'direction':     direction,
        'entry':         entry,
        'tp':            tp,
        'sl':            sl,
        'sigma_pos':     sigma_pos,
        'rsi':           rsi_val,
        'htf_sigma':     htf_sigma,
        'filter_reason': filter_reason,
    }

# ══════════════════════════════════════════
# 発注
# ══════════════════════════════════════════
def place_order(symbol, base_sym, sig, logf, webhook):
    """
    symbol    : ブローカー固有名（MT5発注用）
    base_sym  : ベース名（ストラテジー名・ログ用）
    """
    direction  = sig['direction']
    order_type = mt5.ORDER_TYPE_SELL if direction == 'sell' else mt5.ORDER_TYPE_BUY

    info = mt5.symbol_info(symbol)
    if info is None:
        log('symbol_info取得失敗: ' + symbol, logf)
        return False

    balance  = rm.get_balance()
    sl_dist  = abs(sig['sl'] - sig['entry'])
    lot      = rm.calc_lot(balance, sl_dist, symbol)
    strategy = 'BB_' + base_sym

    if is_live_broker(BROKER_KEY):
        log('*** ライブ口座発注 *** ' + symbol + ' ' + direction.upper() +
            ' lot=' + str(lot) + ' broker=' + BROKER_KEY, logf)

    request = {
        'action':       mt5.TRADE_ACTION_DEAL,
        'symbol':       symbol,
        'volume':       lot,
        'type':         order_type,
        'price':        sig['entry'],
        'tp':           round(sig['tp'], info.digits),
        'sl':           round(sig['sl'], info.digits),
        'deviation':    10,
        'magic':        20250001,
        'comment':      strategy,
        'type_time':    mt5.ORDER_TIME_GTC,
        'type_filling': mt5.ORDER_FILLING_IOC,
    }

    result = mt5.order_send(request)
    if result is None or result.retcode != mt5.TRADE_RETCODE_DONE:
        code = result.retcode if result else 'None'
        log('発注失敗: ' + symbol + ' code=' + str(code), logf)
        return False

    msg = ('BB発注: ' + symbol + ' ' + direction.upper() +
           ' lot=' + str(lot) +
           ' σ=' + f'{sig["sigma_pos"]:+.2f}' +
           ' RSI=' + f'{sig["rsi"]:.1f}' +
           ' 1hσ=' + f'{sig["htf_sigma"]:+.2f}' +
           ' Filter:' + sig['filter_reason'])
    log(msg, logf)
    return True

# ══════════════════════════════════════════
# メイン
# ══════════════════════════════════════════
def main():
    global BROKER_KEY

    parser = argparse.ArgumentParser(description='BB逆張り戦略モニター v21')
    parser.add_argument('--broker', default=BROKER_KEY,
                        choices=['oanda', 'oanda_demo', 'axiory', 'exness'],
                        help='使用するブローカーキー')
    args = parser.parse_args()
    BROKER_KEY = args.broker

    global LOG_FILE
    LOG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'bb_log_' + BROKER_KEY + '.txt')

    env     = load_env()
    webhook = env.get('DISCORD_WEBHOOK', '')
    logf    = LOG_FILE

    if not connect_mt5(BROKER_KEY):
        log('MT5初期化失敗 broker=' + BROKER_KEY, logf)
        return

    try:
        account = mt5.account_info()
        if account is None:
            log('MT5口座情報取得失敗', logf)
            disconnect_mt5()
            return
    except Exception as e:
        log('MT5接続エラー: ' + str(e), logf)
        disconnect_mt5()
        return

    # シンボルマップを構築
    all_bases = list(BB_PAIRS.keys()) + [
        'EURUSD', 'USDJPY', 'GBPUSD', 'AUDUSD', 'AUDJPY', 'EURJPY', 'GBPJPY',
    ]
    _SYMBOL_MAP.update(build_symbol_map(list(dict.fromkeys(all_bases)), BROKER_KEY))

    check_closed(logf, webhook)

    if not check_daily_loss(logf, webhook):
        disconnect_mt5()
        return

    executed = 0
    skipped  = 0

    for base_sym, cfg in BB_PAIRS.items():
        if not cfg.get('enabled', True):  # v20: enabled=False のペアはスキップ
            continue
        strategy = 'BB_' + base_sym
        symbol   = _rsym(base_sym)

        if count_by_strategy(strategy) >= cfg['max_pos']:
            skipped += 1
            continue
        if is_dup(symbol, strategy, logf):
            skipped += 1
            continue
        if count_total() >= MAX_TOTAL_POS:
            break
        if cfg['is_jpy'] and count_jpy_lots() >= MAX_JPY_LOT:
            log('JPYロット上限: ' + base_sym + ' スキップ')
            skipped += 1
            continue
        if is_in_cooldown(symbol):
            skipped += 1
            continue

        allowed = ALLOWED_HOURS_UTC.get(base_sym)  # None=制限なし
        if allowed is not None:
            now_hour_utc = datetime.now(timezone.utc).hour
            if now_hour_utc not in allowed:
                log(base_sym + ': 時間帯外スキップ UTC=' + str(now_hour_utc) + 'h')
                skipped += 1
                continue

        sig = calc_bb_signal(symbol, cfg)
        if not sig:
            skipped += 1
            continue

        if ENTRY_FILTER.get(base_sym, {}).get('use_htf4h'):
            htf4h_sig = get_htf4h_signal(symbol)
            if htf4h_sig is None:
                log(base_sym + ': HTF4h取得失敗 スキップ', logf)
                skipped += 1
                continue
            if sig['direction'] == 'buy' and htf4h_sig != 1:
                log(base_sym + ': HTF4h BUY不可（EMA20下方） スキップ', logf)
                skipped += 1
                continue
            if sig['direction'] == 'sell' and htf4h_sig != -1:
                log(base_sym + ': HTF4h SELL不可（EMA20上方） スキップ', logf)
                skipped += 1
                continue

        if ENTRY_FILTER.get(base_sym, {}).get('use_htf4h_rsi'):  # v21
            htf4h_sig = get_htf4h_rsi_signal(symbol)
            if htf4h_sig is None:
                log(base_sym + ': HTF4h RSI取得失敗 スキップ', logf)
                skipped += 1
                continue
            if sig['direction'] == 'buy' and htf4h_sig != 1:
                log(base_sym + ': HTF4h RSI BUY不可（EMA20下方 or RSI>=55） スキップ', logf)
                skipped += 1
                continue
            if sig['direction'] == 'sell' and htf4h_sig != -1:
                log(base_sym + ': HTF4h RSI SELL不可（EMA20上方 or RSI<=45） スキップ', logf)
                skipped += 1
                continue

        if ENTRY_FILTER.get(base_sym, {}).get('use_htf4h_rsi_bw'):  # v21 GBPJPY filter added
            # USDJPY: RSI閾値は変更なし(buy<55,sell>45)のため get_htf4h_rsi_signal() を使用
            # GBPJPY: RSI<60/RSI>55 のため get_htf4h_rsi_bw_signal() を使用
            if base_sym == 'USDJPY':  # v21 USDJPY BBwidth filter added
                htf4h_sig = get_htf4h_rsi_signal(symbol)
                rsi_buy_th_label  = '55'
                rsi_sell_th_label = '45'
            else:
                htf4h_sig = get_htf4h_rsi_bw_signal(symbol)
                rsi_buy_th_label  = '60'
                rsi_sell_th_label = '55'
            if htf4h_sig is None:
                log(base_sym + ': HTF4h RSI BW取得失敗 スキップ', logf)
                skipped += 1
                continue
            if sig['direction'] == 'buy' and htf4h_sig != 1:
                log(base_sym + ': HTF4h RSI BUY不可（EMA20下方 or RSI>=' + rsi_buy_th_label + '） スキップ', logf)
                skipped += 1
                continue
            if sig['direction'] == 'sell' and htf4h_sig != -1:
                log(base_sym + ': HTF4h RSI SELL不可（EMA20上方 or RSI<=' + rsi_sell_th_label + '） スキップ', logf)
                skipped += 1
                continue

        if place_order(symbol, base_sym, sig, logf, webhook):
            executed += 1

    now = datetime.now().strftime('%H:%M')
    log('[' + now + '] BB v21完了: 発注' + str(executed) + '件 ' +
        'スキップ' + str(skipped) + '件 ' +
        'ポジション' + str(count_total()) + '/' + str(MAX_TOTAL_POS) +
        ' broker=' + BROKER_KEY)


    try:
        import heartbeat_check as hb_mod
        hb_mod.record_heartbeat('bb_monitor')
    except Exception as e:
        log('heartbeat記録エラー: ' + str(e))
    disconnect_mt5()

if __name__ == '__main__':
    main()
