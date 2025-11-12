from django.core.management.base import BaseCommand
from django.utils import timezone
from main.models import StockSnapshot, Stock
from main.services.snapshots import build_and_save_snapshot

class Command(BaseCommand):
    help = "每日更新：對你關心的一批股票（或資料庫現有代碼）重新抓一筆快照"

    def add_arguments(self, parser):
        parser.add_argument("--all", action="store_true",
                            help="全市場更新（較慢）；不加時僅更新資料庫已存在的代碼或你的清單")

    def handle(self, *args, **opts):
        # 你可以決定更新集合的來源：
        # 1) 你的自選清單/資料庫已有代碼
        # 2) 全市場（呼叫 warmup 的取代碼方法）
        from .warmup_universe import iter_all_twse_codes

        codes = []
        if opts["all"]:
            codes = list(iter_all_twse_codes())
        else:
            # 這裡示範：拿你快照表裡已有的代碼去更新
            codes = [(c, "") for c in StockSnapshot.objects.values_list("code", flat=True).distinct()]

        for code, name in codes:
            self.stdout.write(f"[refresh] {code}")
            build_and_save_snapshot(code, name)
        self.stdout.write(self.style.SUCCESS("refresh done"))
