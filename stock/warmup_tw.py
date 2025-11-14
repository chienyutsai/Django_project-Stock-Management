# warmup_tw.py

import os
import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "stock.settings")  # 如果你的專案名稱不是 stock 要改這裡
django.setup()
from main.services.metrics_tw import fetch_inputs_finmind_twse, compute_metrics
from main.models import StockSnapshot
from decouple import config


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


if __name__ == "__main__":
    warmup_tw_stocks()
