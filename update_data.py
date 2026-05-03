# update_data.py - FXデータ差分更新スクリプト
# 対象: 14ペア, 1h/5m足
# 毎日0時にタスクスケジューラから実行

import yfinance as yf
import pandas as pd
import os
import logging
from datetime import datetime, timedelta

# ===================== 設定 =====================
DATA_DIR = r'C:\Users\Administrator\fx_bot\data'

PAIRS = [
    'USDJPY=X', 'EURJPY=X', 'GBPJPY=X', 'AUDJPY=X',
    'EURUSD=X', 'GBPUSD=X', 'AUDUSD=X', 'USDCAD=X',
    'GBPAUD=X', 'EURGBP=X', 'NZDJPY=X', 'CHFJPY=X',
    'EURCAD=X', 'GBPCAD=X',
]

TIMEFRAMES = {
    '1h': {'interval': '1h', 'period': '60d'},
    '5m': {'interval': '5m', 'period': '60d'},
}

LOG_FILE = os.path.join(DATA_DIR, 'update_log.txt')

# ===================== ログ設定 =====================
os.makedirs(DATA_DIR, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler(LOG_FILE, encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)


def get_save_path(pair: str, tf: str) -> str:
    """保存パスを返す（例: USDJPY_1h.csv）"""
    symbol = pair.replace('=X', '')
    return os.path.join(DATA_DIR, f'{symbol}_{tf}.csv')


def load_existing(path: str) -> pd.DataFrame | None:
    """既存CSVを読み込む。なければNone"""
    if not os.path.exists(path):
        return None
    try:
        df = pd.read_csv(path, index_col=0, parse_dates=True)
        df.index = pd.to_datetime(df.index, utc=True)
        return df
    except Exception as e:
        logger.warning(f'既存ファイル読み込みエラー {path}: {e}')
        return None


def fetch_new_data(pair: str, interval: str, period: str) -> pd.DataFrame | None:
    """yfinanceで最新データ取得"""
    try:
        ticker = yf.Ticker(pair)
        df = ticker.history(period=period, interval=interval, auto_adjust=True)
        if df.empty:
            logger.warning(f'データ空: {pair} {interval}')
            return None
        df.index = pd.to_datetime(df.index, utc=True)
        # 不要カラム除去
        df = df[['Open', 'High', 'Low', 'Close', 'Volume']]
        return df
    except Exception as e:
        logger.error(f'取得エラー {pair} {interval}: {e}')
        return None


def merge_and_save(existing: pd.DataFrame | None, new_df: pd.DataFrame, path: str) -> int:
    """
    差分マージして保存。
    戻り値: 追加行数
    """
    if existing is None:
        merged = new_df
        added = len(merged)
    else:
        # 既存にない新しい行だけ追加
        new_rows = new_df[~new_df.index.isin(existing.index)]
        if new_rows.empty:
            return 0
        merged = pd.concat([existing, new_rows]).sort_index()
        added = len(new_rows)

    merged.to_csv(path)
    return added


def update_pair(pair: str) -> dict:
    """1ペアの全TF更新。結果サマリを返す"""
    results = {}
    for tf, cfg in TIMEFRAMES.items():
        path = get_save_path(pair, tf)
        existing = load_existing(path)

        new_df = fetch_new_data(pair, cfg['interval'], cfg['period'])
        if new_df is None:
            results[tf] = 'SKIP'
            continue

        added = merge_and_save(existing, new_df, path)
        results[tf] = f'+{added}行'
        logger.info(f'{pair} {tf}: {added}行追加 -> {path}')

    return results


def main():
    start = datetime.now()
    logger.info('=' * 50)
    logger.info(f'update_data.py 開始: {start.strftime("%Y-%m-%d %H:%M:%S")}')
    logger.info('=' * 50)

    success, skip, error = 0, 0, 0

    for pair in PAIRS:
        logger.info(f'--- {pair} 更新中 ---')
        try:
            results = update_pair(pair)
            for tf, status in results.items():
                if status == 'SKIP':
                    skip += 1
                else:
                    success += 1
        except Exception as e:
            logger.error(f'{pair} 予期しないエラー: {e}')
            error += 1

    elapsed = (datetime.now() - start).seconds
    logger.info('=' * 50)
    logger.info(f'完了: 成功={success} スキップ={skip} エラー={error} 経過={elapsed}秒')
    logger.info('=' * 50)


if __name__ == '__main__':
    main()