from django.core.management.base import BaseCommand
from django.conf import settings
from main.models import Watchlist, StockSnapshot
from main.views import get_current_price_now
from main.services.metrics_tw import fetch_inputs_finmind_twse, compute_metrics
import datetime as dt

class Command(BaseCommand):
    help = "Refresh snapshots for all TW codes in users' watchlists."

    def handle(self, *args, **kwargs):
        codes = (Watchlist.objects
                 .exclude(stock_code__isnull=True)
                 .exclude(stock_code__exact="")
                 .values_list("stock_code", flat=True)
                 .distinct())
        for code in codes:
            if not str(code).isdigit():
                continue
            self.stdout.write(f"Refresh {code} ...")
            try:
                price = get_current_price_now(code)
                inputs = fetch_inputs_finmind_twse(code, price=price, token=settings.FINMIND_TOKEN)
                metrics = compute_metrics(inputs)
                StockSnapshot.save_snapshot(code, inputs.stock_name, price, inputs, metrics)
            except Exception as e:
                self.stderr.write(f"[{code}] refresh failed: {e}")



def get_latest_twse_data(api, stock_id: str, max_back_days=7):
    """抓取 TWSE 最新一筆有效資料（往前找最多 max_back_days 天）"""
    today = dt.date.today()

    for i in range(max_back_days):
        check_date = today - dt.timedelta(days=i)
        date_str = check_date.strftime("%Y-%m-%d")

        df = api.taiwan_stock_daily(
            stock_id=stock_id,
            start_date=date_str,
            end_date=date_str
        )

        if df is not None and not df.empty:
            return df  # 找到有效資料 → 回傳

    # 都沒有資料
    return None