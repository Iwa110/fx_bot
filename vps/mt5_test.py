import MetaTrader5 as mt5

print("MT5接続テスト開始...")

if not mt5.initialize():
    print(f"接続失敗: {mt5.last_error()}")
    quit()

info = mt5.account_info()
print(f"接続成功！")
print(f"口座番号: {info.login}")
print(f"残高: {info.balance} {info.currency}")
print(f"業者: {info.company}")
print(f"サーバー: {info.server}")

mt5.shutdown()
