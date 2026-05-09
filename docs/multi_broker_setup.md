# マルチブローカー対応 システム変更まとめ

新規ブローカーのデモ口座追加作業のためのリファレンス。

---

## 背景・目的

FX Botシステム（VPS: Windows Server 2022、MT5 + Python）を複数ブローカー対応に拡張した。
現在はOANDAデモ口座での動作確認済み。次のステップで別ブローカー（Axiory/Exness等）のデモ口座を追加する。

---

## 追加・変更ファイル一覧

### 新規追加
- `vps/broker_config.py` — ブローカー設定の一元管理
- `vps/broker_utils.py` — MT5接続・シンボル解決ユーティリティ
- `vps/check_broker_connection.py` — 接続診断スクリプト

### 変更済み
- `vps/bb_monitor.py` — `BROKER_KEY`定数 + `--broker`引数 + `_rsym()`シンボル解決
- `vps/trail_monitor.py` — 同上
- `vps/daily_trade.py` — 同上
- `vps/stat_arb_monitor.py` — 同上

---

## broker_config.py の構造

```python
BROKERS: dict[str, dict[str, Any]] = {
    'oanda': {
        'path':          r'C:\Program Files\OANDA MetaTrader 5\terminal64.exe',
        'server':        ...,  # .envから読込
        'login':         ...,  # .envから読込
        'password':      ...,  # .envから読込
        'symbol_suffix': '.cl',
        'is_live':       True,
        'enabled':       True,
    },
    'oanda_demo': {
        'path':          '',       # 起動済みMT5にアタッチするため空
        'attach':        True,     # mt5.initialize()を引数なしで呼ぶ（IPC timeout回避）
        'symbol_suffix': '.cl',
        'is_live':       False,
        'enabled':       True,
    },
    'axiory': {
        'path':          r'C:\Program Files\Axiory MetaTrader 5\terminal64.exe',
        'symbol_suffix': '',       # サフィックスなし
        'is_live':       False,
        'enabled':       True,     # 開設後に認証情報を.envに設定する
    },
    'exness': {
        'path':          r'C:\Program Files\Exness MetaTrader 5\terminal64.exe',
        'symbol_suffix': 'm',
        'is_live':       False,
        'enabled':       True,
    },
}
```

ログイン情報は `vps/.env` から読込（gitignore済み）：

```
OANDA_LOGIN=
OANDA_PASSWORD=
OANDA_SERVER=OANDA Division1-MT5 1

OANDA_DEMO_LOGIN=400094493
OANDA_DEMO_PASSWORD=
OANDA_DEMO_SERVER=OANDA Division1-MT5 2

AXIORY_LOGIN=
AXIORY_PASSWORD=
AXIORY_SERVER=

EXNESS_LOGIN=
EXNESS_PASSWORD=
EXNESS_SERVER=
```

---

## 新規ブローカー追加の手順

### 1. MT5ターミナルをインストール・起動

ブローカーのサイトからMT5をダウンロードし、**既存とは別フォルダ**にインストール（例: `C:\Program Files\Axiory MetaTrader 5\`）。デモ口座を開設してログイン済み状態にする。

### 2. MT5情報を確認（`--discover`）

```
cd C:\Users\Administrator\fx_bot\vps
python check_broker_connection.py --discover
```

出力例：
```
login   : 12345678     ← .env の AXIORY_LOGIN に設定
server  : Axiory-Demo  ← .env の AXIORY_SERVER に設定
path    : C:\Program Files\Axiory MetaTrader 5\terminal64.exe
```

### 3. .env に認証情報を追記

```
AXIORY_LOGIN=12345678
AXIORY_PASSWORD=（パスワード）
AXIORY_SERVER=Axiory-Demo
```

### 4. broker_config.py の設定を確認・修正

- `path`: `--discover` で表示された `terminal.path` の値
- `symbol_suffix`: ブローカーによって異なる（Axiory=なし、Exness=`m`）
- `attach`: OANDAデモのように同一実行ファイルを複数口座で使う場合のみ `True`。**別ブローカーは別exe → `attach`不要**

### 5. 接続・シンボル確認

```
python check_broker_connection.py --broker axiory
```

期待結果：全8ペアが `OK -> GBPJPY` のように解決される

### 6. 戦略スクリプトで動作確認

```
python daily_trade.py --broker axiory
python bb_monitor.py --broker axiory
```

---

## attach=True フラグについて（重要）

| 状況 | 設定 | 理由 |
|------|------|------|
| OANDAデモ（OANDAライブと同じexe） | `attach=True`, `path=''` | 同一exeを2つ起動できない。起動済みMT5に引数なし`mt5.initialize()`でアタッチ |
| 別ブローカー（別exe） | `attach`不要, `path=実行ファイルパス` | 別exeなので独立して起動可能 |

> **OANDAライブ運用開始時の注意**: ライブとデモを同時稼働する場合はポータブルモード（`/portable /datadir:...`）で2つのMT5インスタンスを起動する必要がある（別途設計が必要）。

---

## シンボル解決の仕組み

`resolve_symbol(base, broker_key)` が3段階でシンボルを解決：

1. `base + suffix`（例: `GBPJPY.cl`）で直接確認
2. サフィックスなし `base`（例: `GBPJPY`）で確認
3. 全シンボルから前方一致検索

`symbol_suffix` の設定が不確かでも動作する。

---

## GitHub

- Repo: https://github.com/Iwa110/fx_bot (Public)
- VPS更新: `git pull` するだけで反映
