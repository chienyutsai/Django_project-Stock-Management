# warmup_tw.py

import os
import django
from django.conf import settings
from FinMind.data import DataLoader
import datetime as dt
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "stock.settings")  # 如果你的專案名稱不是 stock 要改這裡
django.setup()
from main.services.metrics_tw import fetch_inputs_finmind_twse, compute_metrics
from main.models import StockSnapshot
from decouple import config

STOCKS = [
        "2330", "2317", "2454", "2303",
        "1301", "1303", "2882", "2881",
        "5871", "1101", "2603", "2609",
        "2615", "3711", "2308", "3008",
        "3034", "4904", "2412",
    ]
'''
def warmup_tw_stocks():
    token = config("FINMIND_TOKEN")

    STOCKS = [
        "2330", "2317", "2454", "2303",
        "1301", "1303", "2882", "2881",
        "5871", "1101", "2603", "2609",
        "2615", "3711", "2308", "3008",
        "3034", "4904", "2412",
    ]

    for code in STOCKS:
        print(f"[WARMUP] refreshing {code}...")
        try:
            inputs = fetch_inputs_finmind_twse(code, token=token)
            metrics = compute_metrics(inputs)

            StockSnapshot.save_snapshot(
                code=code,
                name=getattr(inputs, "stock_name", None),
                price=inputs.price,
                inputs=inputs,
                metrics=metrics,
            )

        except Exception as e:
            print(f"[WARMUP] Failed to update {code}: {e}")

    print("Warmup 完成")
'''

def run():
    api = DataLoader()
    api.login_by_token(settings.FINMIND_TOKEN)

    today = dt.date.today().strftime("%Y-%m-%d")
    base_start = "2025-01-01"

    for code in STOCKS:
        print(f"[WARMUP] refreshing {code}...")
        try:
            # 1) 用 FinMind 抓日線 & 價格
            df = api.taiwan_stock_daily(
                stock_id=code,
                start_date=base_start,
                end_date=today,
            )
            if df is None or df.empty:
                print(f"[WARMUP] {code} 沒有日線資料，跳過")
                continue

            price = float(df["close"].iloc[-1])

            # 2) 把 price 傳給 fetch_inputs_finmind_twse
            inputs = fetch_inputs_finmind_twse(code, price=price, token=settings.FINMIND_TOKEN)
            if inputs is None:
                print(f"[WARMUP] {code} 沒拿到 inputs，跳過")
                continue

            metrics = compute_metrics(inputs)

            # 3) 取得股票名稱（FinMind 的 taiwan_stock_info）
            info = api.taiwan_stock_info()
            name_arr = info.loc[info["stock_id"] == code, "stock_name"].values
            stock_name = name_arr[0] if len(name_arr) else code

            # 4) 寫入 snapshot（你原本的 save_snapshot 已經包好了）
            StockSnapshot.save_snapshot(code, stock_name, price, inputs, metrics)
        except Exception as e:
            print(f"[WARMUP] Failed to update {code}: {e}")

    print("Warmup 完成")

'''
if __name__ == "__main__":
    warmup_tw_stocks()
'''