from django.core.management.base import BaseCommand
import requests, io, csv, time
from main.services.snapshots import build_and_save_snapshot

def iter_all_twse_codes():
    # 用 TWSE OpenData 的 CSV 取全上市公司代碼；也可再加 OTC 資料來源
    url = "https://openapi.twse.com.tw/v1/opendata/t187ap03_L"
    r = requests.get(url, timeout=20)
    r.raise_for_status()
    text = r.text
    f = io.StringIO(text)
    reader = csv.DictReader(f)
    for row in reader:
        code = str(row.get("公司代號", "")).strip()
        name = str(row.get("公司名稱", "")).strip()
        if code.isdigit():
            yield code, name

class Command(BaseCommand):
    help = "預熱全市場快照（第一次上線或要補資料時使用）"

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=0, help="只跑前 N 檔（測試用）")
        parser.add_argument("--sleep", type=float, default=0.4, help="每檔間隔秒數，避免被限流")

    def handle(self, *args, **opts):
        limit = opts["limit"]
        sleep = opts["sleep"]
        count = 0

        for code, name in iter_all_twse_codes():
            if limit and count >= limit:
                break
            self.stdout.write(f"[warmup] {code} {name}")
            build_and_save_snapshot(code, name)
            count += 1
            time.sleep(sleep)

        self.stdout.write(self.style.SUCCESS(f"Done. total={count}"))
