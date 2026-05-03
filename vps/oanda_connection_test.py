# oanda_connection_test.py
import MetaTrader5 as mt5

LOGIN    = 400094493          # ← OANDAのログインIDに変更
PASSWORD = "mukxAk-2xabc"   # ← パスワードに変更
SERVER   = "OANDA-Japan MT5 Demo"  # ← 実際のサーバー名に変更

if not mt5.initialize(login=LOGIN, password=PASSWORD, server=SERVER):
    print("初期化失敗:", mt5.last_error())
    quit()

acc = mt5.account_info()
print("接続成功!")
print(f"  会社: {acc.company}")
print(f"  サーバー: {acc.server}")
print(f"  残高: {acc.balance} {acc.currency}")
print(f"  レバレッジ: 1:{acc.leverage}")

# 通貨ペアのシンボル名確認（TitanFXと違う可能性あり）
print("\n--- 利用可能シンボル（JPY関連） ---")
symbols = mt5.symbols_get()
jpy_symbols = [s.name for s in symbols if 'JPY' in s.name]
print(jpy_symbols[:15])

mt5.shutdown()