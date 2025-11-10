import os
import datetime as dt
from dataclasses import dataclass
from typing import Optional, Dict, Any, List
import requests
import time, json, pathlib

DEBUG_SHARES = False
def _dbg(*a):
    if DEBUG_SHARES:
        _dbg(*a)

FINMIND_TOKEN = os.getenv("FINMIND_TOKEN", "")
# Twelve Data 這份用不到；保留以後要擴充即時價或其他欄位時可用
TWELVEDATA_API_KEY = os.getenv("TWELVEDATA_API_KEY", "")

# ---------- 基本工具 ----------

@dataclass
class Inputs:
    price: Optional[float] = None                 # 現價
    shares_outstanding: Optional[int] = None      # 在外股數（股）
    eps_ttm: Optional[float] = None               # 近四季 EPS
    cash_dividend_ttm: Optional[float] = None     # 最近一年現金股利/股

def _to_float(x):
    try:
        if x in (None, "", "-", "--"):
            return None
        return float(str(x).replace(",", ""))
    except Exception:
        return None

def _to_int(x):
    try:
        if x in (None, "", "-", "--"):
            return None
        return int(float(str(x).replace(",", "")))
    except Exception:
        return None

def _safe_div(a, b):
    if a is None or b in (None, 0):
        return None
    return a / b

def _fmt_money(n: Optional[float]) -> str:
    if n is None:
        return "--"
    if n >= 1_0000_0000_0000:
        return f"{n / 1_0000_0000_0000:.2f} 兆"
    if n >= 1_0000_0000:
        return f"{n / 1_0000_0000:.2f} 億"
    return f"{n:,.0f} 元"


# --- 1) TWSE rwd 公司基本資料（優先；需要一些 Ajax 標頭，否則常被擋） ---
def _twse_rwd_company_basic_shares(stock_code: str) -> Optional[int]:
    import csv, io
    try:
        url = "https://www.twse.com.tw/rwd/zh/company/basic"
        headers = {
            "User-Agent": "Mozilla/5.0",
            "Accept": "application/json, text/plain, */*",
            "Referer": "https://www.twse.com.tw/",
            "X-Requested-With": "XMLHttpRequest",
            "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.8",
        }
        r = requests.get(
            url,
            params={"stockNo": stock_code, "response": "json"},
            headers=headers,
            timeout=12,
        )
        if not r.ok:
            _dbg("[TWSE rwd] status:", r.status_code)
            return None

        j = r.json()  # 這支成功時一定是 JSON
        rows = j.get("data") or []
        fields = j.get("fields") or []
        if not rows or not fields:
            return None

        row = rows[0]
        m = {fields[i]: row[i] for i in range(min(len(fields), len(row)))}

        # 先取已發行股數
        for k in ("已發行普通股數或TDR原發行股數(股)", "普通股已發行股數(股)", "已發行普通股數(股)"):
            if k in m and str(m[k]).strip():
                try:
                    return int(str(m[k]).replace(",", "").strip())
                except Exception:
                    pass

        # 沒有就用資本額(千元)換：×100（面額 10 元）
        for k in ("資本額(千元)", "實收資本額(千元)", "實收資本額-普通股(千元)"):
            if k in m and str(m[k]).strip():
                try:
                    cap_thousand = float(str(m[k]).replace(",", "").strip())
                    return int(round(cap_thousand * 100))
                except Exception:
                    pass
        return None
    except Exception as e:
        _dbg("[TWSE rwd] error:", repr(e))
        return None
    

# --- 2) TWSE OpenAPI（備援；可能回 CSV 或 JSON，兩者都支援） ---
def _twse_openapi_basic_shares(stock_code: str) -> Optional[int]:
    try:
        url = "https://openapi.twse.com.tw/v1/company/basic_info"
        headers = {
            "User-Agent": "Mozilla/5.0",
            "Accept": "*/*",
            "Referer": "https://openapi.twse.com.tw/",
            "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.8",
        }
        r = requests.get(url, headers=headers, timeout=12)
        if not r.ok:
            _dbg("[TWSE openapi] bad status:", r.status_code)
            return None

        ct = (r.headers.get("Content-Type") or "").lower()
        txt = r.text.strip()

        # a) JSON 陣列
        if "application/json" in ct or (txt.startswith("[") and txt.endswith("]")):
            data = r.json()
            for row in data:
                if str(row.get("公司代號", "")).strip() == str(stock_code):
                    # 先取已發行股數
                    for k in row.keys():
                        if "已發行普通股數" in k:
                            val = str(row.get(k, "")).replace(",", "").strip()
                            try:
                                return int(float(val))
                            except Exception:
                                pass
                    # 沒有就用資本額(千元) → ×100
                    for k in ("資本額(千元)", "實收資本額(千元)"):
                        if k in row and str(row[k]).strip():
                            try:
                                return int(round(float(str(row[k]).replace(",", "")) * 100))
                            except Exception:
                                pass
            return None

        # b) 不是 JSON，當作 CSV 解析
        import csv, io
        f = io.StringIO(txt)
        reader = csv.DictReader(f)
        for row in reader:
            if str(row.get("公司代號", "")).strip() == str(stock_code):
                for k in row.keys():
                    if "已發行普通股數" in k:
                        val = str(row.get(k, "")).replace(",", "").strip()
                        try:
                            return int(float(val))
                        except Exception:
                            pass
                for k in ("資本額(千元)", "實收資本額(千元)"):
                    if k in row and str(row[k]).strip():
                        try:
                            return int(round(float(str(row[k]).replace(",", "")) * 100))
                        except Exception:
                            pass
        return None
    except Exception as e:
        _dbg("[TWSE openapi] error:", repr(e))
        return None

    

# --- FinMind: 備援（v4 data） ------------------------------------
def _finmind_total_shares(stock_code: str, token: str) -> Optional[int]:
    """優先用 TaiwanStockTotalShares 的 TotalShares（千股 → ×1000）"""
    try:
        url = "https://api.finmindtrade.com/api/v4/data"
        r = requests.get(url, params={
            "dataset": "TaiwanStockTotalShares",
            "data_id": stock_code,
            "token": token
        }, timeout=12)
        # 觀察 422 的內容，方便除錯
        if not r.ok:
            _dbg("[FINMIND] TotalShares resp:", r.status_code, r.text[:200])
            return None
        data = (r.json() or {}).get("data") or []
        if not data:
            return None
        row = data[-1]
        if "TotalShares" in row and row["TotalShares"] not in (None, "", "-", "--"):
            return int(round(float(row["TotalShares"]) * 1000))
    except Exception as e:
        _dbg("[FINMIND] TotalShares err:", repr(e))
    return None

def _finmind_capital_to_shares(stock_code: str, token: str) -> Optional[int]:
    """用 TaiwanStockShareCapital 的 ShareCapital（千元）→ ×100 轉股數"""
    try:
        url = "https://api.finmindtrade.com/api/v4/data"
        r = requests.get(url, params={
            "dataset": "TaiwanStockShareCapital",
            "data_id": stock_code,
            "token": token
        }, timeout=12)
        if not r.ok:
            _dbg("[FINMIND] ShareCapital resp:", r.status_code, r.text[:200])
            return None
        data = (r.json() or {}).get("data") or []
        if not data:
            return None
        row = data[-1]
        if row.get("ShareCapital") not in (None, "", "-", "--"):
            return int(round(float(row["ShareCapital"]) * 100))
    except Exception as e:
        _dbg("[FINMIND] ShareCapital err:", repr(e))
    return None

def _twse_market_cap_to_shares_from_mi_index(stock_code: str, price: Optional[float]) -> Optional[int]:
    """
    從 TWSE MI_INDEX 每日報表直接抓「市值(百萬元)」，再用 price 反推 shares。
    回傳：shares_outstanding（股）。失敗回 None。
    """
    if not price or price <= 0:
        return None

    url = "https://www.twse.com.tw/exchangeReport/MI_INDEX"
    headers = {
        "User-Agent": "Mozilla/5.0",
        "Accept": "application/json,text/plain,*/*",
        "Referer": "https://www.twse.com.tw/",
        "X-Requested-With": "XMLHttpRequest",
        "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.8",
    }

    # 向前回溯最近 10 個日曆日，找到最近一個有資料的交易日
    for i in range(0, 10):
        d = dt.date.today() - dt.timedelta(days=i)
        date_str = d.strftime("%Y%m%d")
        try:
            r = requests.get(
                url,
                params={"response": "json", "date": date_str, "type": "ALLBUT0999"},
                headers=headers,
                timeout=12,
            )
            if not r.ok:
                continue
            j = r.json()
            # MI_INDEX 會有 data1~dataN、fields1~fieldsN，找出含有「市值(百萬元)」欄位的表
            for k, rows in j.items():
                if not k.startswith("data") or not isinstance(rows, list):
                    continue
                idx = k[4:]  # '1'..'N'
                fields = j.get(f"fields{idx}") or []
                # 找欄位位置
                code_col = None
                mcap_col = None
                for ci, name in enumerate(fields):
                    if "代號" in name or "股票代號" in name or "證券代號" in name:
                        code_col = ci
                    if "市值" in name:  # 通常是「市值(百萬元)」
                        mcap_col = ci
                if code_col is None or mcap_col is None:
                    continue

                for row in rows:
                    if len(row) <= max(code_col, mcap_col):
                        continue
                    if str(row[code_col]).strip() == str(stock_code):
                        val = str(row[mcap_col]).replace(",", "").strip()
                        if not val or val in ("--", "-"):
                            return None
                        try:
                            mcap_million = float(val)  # 單位：百萬元
                            mcap = mcap_million * 1_000_000.0
                            shares = int(round(mcap / float(price)))
                            if shares > 0:
                                _dbg(f"[TWSE MI_INDEX] got mcap(M)={mcap_million} -> shares={shares}")
                                return shares
                        except Exception:
                            return None
        except Exception as e:
            _dbg("[TWSE MI_INDEX] error:", repr(e))
            continue
    return None

# 快取

CACHE_FILE = pathlib.Path("/tmp/mops_basic_cache.json")
CACHE_TTL  = 60 * 60 * 12   # 快取 12 小時

def _read_cache():
    try:
        if CACHE_FILE.exists() and time.time() - CACHE_FILE.stat().st_mtime < CACHE_TTL:
            return json.loads(CACHE_FILE.read_text(encoding="utf-8"))
    except Exception:
        pass
    return None

def _write_cache(obj):
    try:
        CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        CACHE_FILE.write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass



# --- MOPS 開放資料：公司基本資料（CSV，不需 token） -----------------
def _mops_shares_from_csv(stock_code: str) -> Optional[int]:
    cache = _read_cache()
    if cache and stock_code in cache:
        _dbg("[MOPS] from cache")
        return cache[stock_code]
    """
    從 MOPS/TWSE 開放資料 CSV 取得「普通股已發行股數」。
    - 支援 UTF-8 / UTF-8-SIG / Big5
    - 自動比對欄位別名與全形/半形數字
    - 兩個來源都嘗試：MOPS 與 TWSE OpenData
    """
    import io, csv, codecs, re

    def _normalize_code(s: str) -> str:
        if s is None: return ""
        # 轉半形、去逗號與空白，只留數字
        s = str(s).strip()
        # 全形到半形
        s = s.translate(dict((ord(c), ord('0') + i) for i, c in enumerate("０１２３４５６７８９")))
        # 只留數字
        s = re.sub(r"\D+", "", s)
        return s

    def _to_int(s: str) -> Optional[int]:
        if s is None: return None
        s = str(s).replace(",", "").strip()
        if not s: return None
        try:
            return int(float(s))
        except Exception:
            return None

    # 可能的欄位名稱（會逐一嘗試）
    code_keys = ["公司代號", "股票代號", "證券代號", "\ufeff公司代號"]  # 含 BOM 變體
    shares_keys = [
        "普通股已發行股數",
        "普通股已發行股數(股)",
        "已發行普通股數(股)",
        "已發行普通股數或TDR原發行股數(股)",
    ]
    capital_keys = ["資本額(元)", "實收資本額(元)", "實收資本額-普通股(元)", "實收資本額"]  # 備援

    targets = [
        # MOPS 財務開放資料（常見）
        ("MOPS", "https://mopsfin.twse.com.tw/opendata/t187ap03_L.csv"),
        # TWSE OpenData 的同份表（有時更穩）
        ("TWSE-OD", "https://openapi.twse.com.tw/v1/opendata/t187ap03_L"),
    ]

    want = _normalize_code(stock_code)
    headers = {
        "User-Agent": "Mozilla/5.0",
        "Accept": "text/csv,*/*;q=0.8",
        "Referer": "https://mops.twse.com.tw/",
        "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.8",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
    }

    for tag, url in targets:
        try:
            r = requests.get(url, headers=headers, timeout=15)
            if not r.ok:
                _dbg(f"[{tag}] bad status:", r.status_code)
                continue

            raw = r.content
            text = None
            # 依序嘗試常見編碼
            for enc in ("utf-8-sig", "utf-8", "big5", "cp950"):
                try:
                    text = raw.decode(enc)
                    break
                except Exception:
                    text = None
            if text is None:
                _dbg(f"[{tag}] decode fail")
                continue

            f = io.StringIO(text)
            reader = csv.DictReader(f)
            first_row = None
            for row in reader:
                if first_row is None:
                    first_row = row
                # 找公司代號欄位
                code_val = None
                for ck in code_keys:
                    if ck in row and row[ck]:
                        code_val = _normalize_code(row[ck])
                        break
                if not code_val:
                    continue
                if code_val != want:
                    continue

                # 1) 直接用已發行股數
                for sk in shares_keys:
                    if sk in row and row[sk]:
                        v = _to_int(row[sk])
                        if v and v > 0:
                            _dbg(f"[{tag}] shares={v}")
                            return v

                # 2) 用資本額(元) / 10 換股數
                for ck2 in capital_keys:
                    if ck2 in row and row[ck2]:
                        v = _to_int(row[ck2])
                        if v and v > 0:
                            shares = int(round(v / 10.0))
                            if shares > 0:
                                _dbg(f"[{tag}] shares from capital={shares}")
                                cache = _read_cache() or {}
                                cache[stock_code] = shares
                                _write_cache(cache)           
                                return shares

            if first_row:
                _dbg(f"[{tag}] sample keys:", list(first_row.keys())[:10])
            else:
                _dbg(f"[{tag}] empty csv?")

        except Exception as e:
            _dbg(f"[{tag}] error:", repr(e))

    _dbg("[MOPS/TWSE-OD] code not found after all sources:", stock_code)
    return None
    

def _fm_shares_outstanding(stock_code: str, token: Optional[str], price: Optional[float] = None) -> Optional[int]:
    # A) 先走 MOPS/TWSE OpenData（CSV）
    s = _mops_shares_from_csv(stock_code)
    if s:
        return s


    # B) 你的其它來源（可保留以後備援）
    # s = _twse_rwd_company_basic_shares(stock_code)
    # if s: return s
    # s = _twse_openapi_basic_shares(stock_code)
    # if s: return s
    # s = _twse_market_cap_to_shares_from_mi_index(stock_code, price)
    # if s: return s

    # C) FinMind（目前你的帳號會 422，保留未來升級）
    t = token or os.getenv("FINMIND_TOKEN", "")
    if t:
        s = _finmind_total_shares(stock_code, t)
        if s: return s
        s = _finmind_capital_to_shares(stock_code, t)
        if s: return s

    return None

# ---------- TWSE：BWIBBU（本益比、殖利率） ----------

def _twse_bwibbu_latest(stock_code: str) -> Dict[str, Optional[float]]:
    """
    從證交所 BWIBBU 報表抓『本益比、殖利率(%)』。
    回傳：{"pe": float|None, "yield": float|None}
    """
    # 近 10 天回溯，避免當天無資料
    for i in range(10):
        date = (dt.date.today() - dt.timedelta(days=i)).strftime("%Y%m%d")
        url = "https://www.twse.com.tw/exchangeReport/BWIBBU_d"
        params = {"response": "json", "selectType": "ALL", "date": date}
        try:
            r = requests.get(url, params=params, timeout=12)
            j = r.json() if r.ok else {}
        except Exception:
            continue

        data = j.get("data") or []
        fields = j.get("fields") or []
        if not data or not fields:
            continue

        # 欄位索引
        try:
            idx_code = fields.index("證券代號")
            idx_yld = fields.index("殖利率(%)")
            idx_pe = fields.index("本益比")
        except ValueError:
            # 欄位名稱有變更就跳過這天
            continue

        for row in data:
            if row[idx_code] == stock_code:
                pe = _to_float(row[idx_pe])
                yld = _to_float(row[idx_yld])
                return {"pe": pe, "yield": yld}

    return {"pe": None, "yield": None}

# ---------- 封裝：抓齊輸入 ----------

def fetch_inputs_finmind_twse(stock_code: str, price: Optional[float], token: str | None = None) -> Inputs:
    shares = _fm_shares_outstanding(stock_code, token, price=price)  # ← 傳 price
    bwibbu = _twse_bwibbu_latest(stock_code)
    inputs = Inputs(price=price, shares_outstanding=shares)
    inputs._pe_from_twse = bwibbu.get("pe")
    inputs._yld_from_twse = bwibbu.get("yield")
    return inputs

# ---------- 計算並格式化成字串，給 template ----------

def compute_metrics(inputs: Inputs) -> Dict[str, str]:
    p = inputs.price
    s = inputs.shares_outstanding

    # 市值
    market_cap = p * s if (p is not None and s is not None) else None

    # 本益比 / 殖利率：若 TWSE 有，優先用（避免 EPS / dividend 缺資料）
    pe = getattr(inputs, "_pe_from_twse", None)
    dy = getattr(inputs, "_yld_from_twse", None)  # 這裡是百分比，例如 2.34

    # 若沒有 TWSE 值，可以改用 p/eps、div/p，這段先保留做備援
    if pe is None and inputs.eps_ttm not in (None, 0):
        pe = _safe_div(p, inputs.eps_ttm)
    if dy is None and inputs.cash_dividend_ttm is not None and p not in (None, 0):
        dy = inputs.cash_dividend_ttm / p * 100  # 轉百分比

    return {
        "市值": _fmt_money(market_cap),
        "本益比": f"{pe:.2f}" if pe is not None else "--",
        "股息殖利率": f"{dy:.2f}%" if dy is not None else "--",
    }
