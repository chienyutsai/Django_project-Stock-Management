# main/services/snapshots.py
from typing import Optional
from django.utils import timezone
from main.models import StockSnapshot
from .metrics_tw import fetch_inputs_finmind_twse, compute_metrics
from main.views import get_current_price_now  # ← 用你現有抓價格的方法；名稱依你的專案調整

def build_and_save_snapshot(code: str, name: Optional[str] = None) -> Optional[StockSnapshot]:
    """
    抓 price / shares / PE / 殖利率 → 計算市值 → 存一筆快照。
    """
    try:
        price = get_current_price_now(code)  # 你目前在 views 用的那個
        inputs = fetch_inputs_finmind_twse(code, price=price)   # 你已經做好的
        metrics = compute_metrics(inputs)                       # 你已經做好的

        if price is None or inputs.shares_outstanding is None:
            # 還是抓不到就先略過
            return None

        snap = StockSnapshot.objects.create(
            code=code,
            name=name or "",
            price=price,
            shares=inputs.shares_outstanding,
            market_cap=metrics.market_cap or 0,
            pe=metrics.pe or 0,
            dividend_yield=metrics.dividend_yield or 0,
            created_at=timezone.now(),
        )
        return snap
    except Exception as e:
        print("[snapshot] error:", code, repr(e))
        return None
