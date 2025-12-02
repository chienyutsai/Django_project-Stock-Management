import re
import os
import feedparser
from django.http import HttpResponse
from django.http import JsonResponse
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.models import User
from django.contrib.auth import authenticate, login
from django.contrib.auth import logout
from django.contrib.auth.decorators import login_required
from django.contrib.auth import update_session_auth_hash
from django.views.decorators.cache import never_cache
from django.conf import settings
import requests
from datetime import date
from datetime import timedelta
from datetime import timezone as py_tz
from FinMind.data import DataLoader
import plotly.graph_objs as go
from plotly.offline import plot
import plotly.io as pio
from decouple import config
from django.http import Http404
import pandas as pd
from django.contrib import messages
from .models import Watchlist, BuyRecord, Post, Comment, CommentLike, PostLike
from decimal import Decimal
from django.utils import timezone
from main.models import StockSnapshot
import datetime as dt
import difflib
import unicodedata
from django.db.models import Max, Q, OuterRef, Subquery, Value, DateTimeField, F, Count 
from django.db.models.functions import Coalesce
from django.db import transaction
from django.views.decorators.http import require_POST
from urllib.parse import urlparse, urlunparse
from .services.sentiment import simple_score
from .services.metrics_tw import Inputs, get_basic_name_price, fetch_inputs_finmind_twse, compute_metrics
from .services.metrics_tw import (
    compute_metrics,
    _fmt_money,
    _fmt_number,
    _fmt_percent,
)
from loguru import logger

logger.remove()          # 移除預設的 log handler
logger.add("finmind.log", level="ERROR")  # 只寫 ERROR 到檔案，不印出來

now = dt.datetime.now()


def _td_price(symbol: str) -> float | None:
    """Twelve Data 取即時價（免費方案可用），失敗回 None。"""
    api_key = config("TWELVE_DATA_API_KEY", default="")
    if not api_key:
        return None
    try:
        r = requests.get(
            "https://api.twelvedata.com/price",
            params={"symbol": symbol, "apikey": api_key},
            timeout=10,
        )
        j = r.json()
        # 成功時回 {"price":"..."}；錯誤時會有 {"status":"error", ...}
        p = j.get("price")
        return float(p) if p is not None and p != "" else None
    except Exception:
        return None

def hello(request):
    return HttpResponse("Hello Django from main app!")

def index(request):
    market_data = get_market_data()
    return render(request, 'index.html', {'market_data': market_data})

def login_view(request):
    if request.method == 'POST':
        username = request.POST.get('username')
        password = request.POST.get('password')

        # 驗證帳號密碼
        user = authenticate(request, username=username, password=password)
        if user is not None:
            # 登入成功
            login(request, user)
            return redirect('index')  # 跳回首頁
        else:
            # 登入失敗
            return render(request, 'login.html', {'error': '帳號或密碼錯誤'})
    return render(request, 'login.html')

def logout_view(request):
    # 這裡可以加登出邏輯
    logout(request)

    messages.success(request, "您已成功登出。")

    #return render(request, 'index.html')
    return redirect("index")

def register_view(request):
    if request.method == 'POST':
        username = request.POST.get('username')
        password = request.POST.get('password')
        confirm = request.POST.get('confirm_password')

         # 後端驗證：使用者名稱只能包含英文字母與數字
        if not re.fullmatch(r'[a-zA-Z0-9]+', username):
            return render(request, 'register.html', {'error': '使用者名稱只能包含英文字母與數字'})

        # 後端驗證：密碼至少 8 碼，包含英文字母與數字
        if not re.fullmatch(r'^(?=.*[A-Za-z])(?=.*\d)[A-Za-z\d]{8,}$', password):
            return render(request, 'register.html', {'error': '密碼至少需 8 碼，並包含英文字母與數字'})

        # 密碼確認
        if password != confirm:
            return render(request, 'register.html', {'error': '密碼與確認密碼不一致'})

        # 使用者是否存在
        if User.objects.filter(username=username).exists():
            return render(request, 'register.html', {'error': '使用者名稱已存在'})

        # 建立使用者
        user = User.objects.create_user(username=username, password=password)
        user.save()
        messages.success(request, "註冊成功！請使用新帳號登入。")

        # 成功後跳轉登入頁
        return redirect('login')
    
    return render(request, 'register.html')

@never_cache
@login_required(login_url='login')
def profile_view(request):
    return render(request, 'profile.html', {
        'username': request.user.username,
        })

@login_required
def update_profile_view(request):
    if request.method == 'POST':
        new_username = request.POST.get('username')
        
        # 可以加正則驗證暱稱
        if not re.fullmatch(r'[a-zA-Z0-9]+', new_username):
            return render(request, 'profile.html', {'error': '使用者名稱只能包含英文字母與數字'})
        
        # 更新使用者暱稱
        request.user.username = new_username
        request.user.save()
        return render(request, 'profile.html', {'success': '使用者名稱已更新'})
    
    return render(request, 'profile.html')

@login_required
def update_password_view(request):
    user = request.user
    if request.method == 'POST':
        old_password = request.POST.get('old_password')
        new_password = request.POST.get('new_password')
        confirm = request.POST.get('confirm_password')

        if not user.check_password(old_password):
            return render(request, 'profile.html', {
                'error': '目前密碼錯誤',
                'username': user.username,
                'active_section': 'security'  # 指定要顯示密碼區塊
            })
        
        if new_password != confirm:
            return render(request, 'profile.html', {
                'error': '新密碼與確認密碼不一致',
                'username': user.username,
                'active_section': 'security'
            })
        
        # 可以加密驗證規則
        if not re.fullmatch(r'^(?=.*[A-Za-z])(?=.*\d)[A-Za-z\d]{8,}$', new_password):
            return render(request, 'profile.html', {'error': '密碼至少需 8 碼，並包含英文字母與數字'})

        user.set_password(new_password)
        user.save()
        return render(request, 'profile.html', {
            'success': '密碼已更新',
            'username': user.username,
            'active_section': 'security'
        })
    
    return redirect('profile')

@login_required
def delete_account_view(request):
    if request.method == 'POST':
        request.user.delete()

        messages.success(request, "您已刪除帳號。")

        return redirect('index')
    return redirect('profile')

def get_market_data():
    api = DataLoader()
    token = config("FINMIND_TOKEN")
    api.login_by_token(token)

    today = dt.date.today()
    start_date = (today - dt.timedelta(days=30)).strftime("%Y-%m-%d")
    end_date = today.strftime("%Y-%m-%d")

    market_data = []

    # --- 台灣加權指數 (0050) ---
    try:
        df_twii = api.taiwan_stock_daily(stock_id="0050", start_date=start_date, end_date=end_date)
        if df_twii is not None and not df_twii.empty:
            fig = go.Figure(data=[go.Candlestick(
                x=df_twii['date'],
                open=df_twii['open'],
                high=df_twii['max'],
                low=df_twii['min'],
                close=df_twii['close']
            )])
            fig.update_layout(
                title="0050 近 30天 K線圖",
                xaxis_title="日期",
                yaxis_title="價格",
                xaxis_rangeslider_visible=False
            )
            chart_div = pio.to_html(fig, full_html=False)
            market_data.append({
                'name': '台灣加權指數',
                'price': df_twii['close'].iloc[-1],
                'date': df_twii['date'].iloc[-1],
                'chart': chart_div
            })
    except Exception as e:
        print("0050 API error:", e)

    # --- 櫃買指數ETF (006201) ---
    try:
        df_otc = api.taiwan_stock_daily(stock_id="006201", start_date=start_date, end_date=end_date)
        if df_otc is not None and not df_otc.empty:
            fig = go.Figure(data=[go.Candlestick(
                x=df_otc['date'],
                open=df_otc['open'],
                high=df_otc['max'],
                low=df_otc['min'],
                close=df_otc['close']
            )])
            fig.update_layout(
                title="006201 近 30天 K線圖",
                xaxis_title="日期",
                yaxis_title="價格",
                xaxis_rangeslider_visible=False
            )
            chart_div = pio.to_html(fig, full_html=False)
            market_data.append({
                'name': '櫃買指數ETF',
                'price': df_otc['close'].iloc[-1],
                'date': df_otc['date'].iloc[-1],
                'chart': chart_div
            })
    except Exception as e:
        print("006201 API error:", e)

    return market_data

# 搜尋股票

def _fetch_foreign_quote(symbol: str):
    """
    回傳 {'symbol': 'AAPL', 'name': 'Apple Inc.', 'price': 191.22} 或 None
    優先用 quote 取得名稱與收盤價；若沒有 close 就退而求其次嘗試 price。
    """
    key = getattr(settings, "TWELVE_DATA_API_KEY", None)
    if not key:
        return None
    try:
        r = requests.get(
            "https://api.twelvedata.com/quote",
            params={"symbol": symbol, "apikey": key},
            timeout=10
        )
        j = r.json()
        if j.get("code") or j.get("status") == "error":
            return None
        name = j.get("name") or j.get("symbol") or symbol.upper()
        price = None
        for k in ("close", "previous_close", "open", "price"):
            if j.get(k):
                try:
                    price = float(j[k])
                    break
                except Exception:
                    pass
        # quote 可能沒有 price，就再打一次 price
        if price is None:
            r2 = requests.get(
                "https://api.twelvedata.com/price",
                params={"symbol": symbol, "apikey": key},
                timeout=10
            )
            j2 = r2.json()
            if isinstance(j2, dict) and j2.get("price"):
                price = float(j2["price"])
        return {"symbol": symbol.upper(), "name": name, "price": price}
    except Exception:
        return None
    

def _foreign_timeseries_chart(symbol: str):
    """
    從 Twelve Data 取 30 天 1day K 線，回傳 Plotly HTML（或 None）。
    """
    key = getattr(settings, "TWELVE_DATA_API_KEY", None)
    if not key:
        return None
    try:
        r = requests.get(
            "https://api.twelvedata.com/time_series",
            params={"symbol": symbol, "interval": "1day", "outputsize": 30, "apikey": key},
            timeout=12
        )
        j = r.json()
        values = j.get("values")
        if not isinstance(values, list) or not values:
            return None
        df = pd.DataFrame(values)
        # 轉型與排序
        df["datetime"] = pd.to_datetime(df["datetime"])
        for c in ("open", "high", "low", "close"):
            df[c] = pd.to_numeric(df[c], errors="coerce")
        df = df.sort_values("datetime")

        fig = go.Figure(data=[go.Candlestick(
            x=df["datetime"],
            open=df["open"], high=df["high"],
            low=df["low"], close=df["close"]
        )])
        fig.update_layout(title=f"{symbol.upper()} 近 30 天 K 線圖", xaxis_title="日期", yaxis_title="價格")
        return fig.to_html(include_plotlyjs="cdn", full_html=False)
    except Exception:
        return None
    

def search_stock(request):
    query = (request.GET.get("query") or "").strip()
    if not query:
        return redirect("/")

    q = query.strip()
    q_upper = q.upper()

    # 1) 純數字 → 台股代碼
    if q_upper.isdigit():
        return redirect(f"/stock_detail/{q_upper}/")

    # 2) 先試台股中文名稱搜尋
    try:
        api = DataLoader()
        api.login_by_token(settings.FINMIND_TOKEN)
        info = api.taiwan_stock_info()
        match = info[info["stock_name"].str.contains(q, case=False, na=False)]
        if not match.empty:
            code = str(match.iloc[0]["stock_id"])
            return redirect(f"/stock_detail/{code}/")
    except Exception:
        pass

    # 3) 再試國外股票：用 Finnhub 搜尋
    try:
        fh_key = settings.FINNHUB_API_KEY
        url = "https://finnhub.io/api/v1/search"
        r = requests.get(url, params={"q": q, "token": fh_key}, timeout=5)
        data = r.json()
        results = data.get("result") or []

        candidates = []
        for item in results:
            symbol = (item.get("symbol") or "").upper()
            desc = (item.get("description") or "").upper()

            # 只要「看起來像股票代碼」的：全英文＋長度不超過 5
            if not symbol.isalpha() or len(symbol) > 5:
                continue

            # 條件一：使用者輸入剛好就是這個代碼
            if q_upper == symbol:
                candidates.append(symbol)
                continue

            # 條件二：描述文字裡有使用者輸入（例如 NVIDIA）
            if q_upper in desc:
                candidates.append(symbol)

        if candidates:
            # 優先選 4～5 個字母的代碼（例如 NVDA 優先於 NVD）
            candidates = sorted(
                candidates,
                key=lambda s: (len(s) < 4, len(s))  # len>=4 排在前面
            )
            best = candidates[0]
            return redirect(f"/stock_detail/{best}/")

    except Exception as e:
        print("[search_stock] Finnhub 搜尋失敗：", e)

    # 4) 到這裡表示：台股沒找到、Finnhub 也沒有「像樣」的匹配 → 直接顯示查無資料
    return render(request, "search_not_found.html", {"query": query})



def _twelve_symbol_search(q: str):
    """用 Twelve Data 的 symbol_search 把公司/代碼 → 標準 symbol 與顯示名稱"""
    key = config("TWELVE_DATA_API_KEY")
    if not key:
        print("[12DATA] missing TWELVE_DATA_API_KEY")
        return None, None

    try:
        r = requests.get(
            "https://api.twelvedata.com/symbol_search",
            params={"symbol": q, "outputsize": 1, "apikey": key},
            timeout=10
        )
        print("[12DATA search]", r.status_code, r.text[:200])
        j = r.json()
        data = j.get("data") or []
        if data:
            first = data[0]
            # e.g. {"symbol": "AAPL", "instrument_name": "Apple Inc", "exchange": "NASDAQ"}
            return first.get("symbol"), first.get("instrument_name") or first.get("name")
    except Exception as e:
        print("[12DATA] search error:", e)
    return None, None


# 股票細節
CACHE_TTL = timedelta(hours=1)

def get_current_price_now(stock_code: str) -> float:
    """
    取得「目前股價」：
    - 台股（純數字）：用 FinMind + StockSnapshot 快取
    - 外國股票（非純數字，含美股）：用 Finnhub
    """
    code = str(stock_code).strip()

    # ========= 1) 外國股票（非純數字）→ 用 Finnhub =========
    if not code.isdigit():
        data = fetch_foreign_fundamentals_finnhub(code)  # 你已經實作好的 Finnhub 函式
        price = data.get("price")

        if price is not None:
            return float(price)

        # Finnhub 拿不到，就退回最近一筆 snapshot（如果有）
        snap = (StockSnapshot.objects
                .filter(code=code)
                .order_by("-created_at")
                .first())
        if snap:
            return float(snap.price)

        # 真的完全沒資料
        raise Http404("抓不到當前股價")

    # ========= 2) 台股（純數字）→ 原本 FinMind 流程 =========
    snap = (StockSnapshot.objects
            .filter(code=code)
            .order_by("-created_at")
            .first())

    # 快取還沒過期就用快取
    if snap and timezone.now() - snap.created_at <= CACHE_TTL:
        return float(snap.price)

    api = DataLoader()
    token = settings.FINMIND_TOKEN
    api.login_by_token(token)

    df = api.taiwan_stock_daily(
        stock_id=code,
        start_date=(dt.date.today() - dt.timedelta(days=30)).strftime("%Y-%m-%d"),
        end_date=str(dt.date.today()),
    )
    if df is None or df.empty:
        # 退回最近一筆 snapshot，不丟 404
        snap = (StockSnapshot.objects
                .filter(code=code)
                .order_by("-created_at")
                .first())
        if snap:
            return float(snap.price)
        raise Http404("抓不到當前股價")

    price = float(df["close"].iloc[-1])

    # 取得名稱
    info = api.taiwan_stock_info()
    name_arr = info.loc[info["stock_id"] == code, "stock_name"].values
    stock_name = name_arr[0] if len(name_arr) else code

    # 更新一次快取
    try:
        inputs = fetch_inputs_finmind_twse(code, price=price, token=token)
        metrics = compute_metrics(inputs) if inputs is not None else {}
        StockSnapshot.save_snapshot(code, stock_name, price, inputs, metrics)
    except Exception as e:
        print(f"[TWSE] 更新 snapshot 失敗 {code}: {e}")

    return price


def _to_float(x):
    try:
        if x is None or x == "" or x == "--":
            return None
        return float(str(x).replace(",", ""))
    except Exception:
        return None
    


def fetch_us_overview(symbol: str, av_key: str):
    """
    Alpha Vantage OVERVIEW：取公司名稱、市值、本益比、殖利率
    """
    try:
        r = requests.get(
            "https://www.alphavantage.co/query",
            params={"function": "OVERVIEW", "symbol": symbol, "apikey": av_key},
            timeout=12,
        )
        j = r.json()
        return {
            "name": j.get("Name") or symbol,
            "market_cap": _to_float(j.get("MarketCapitalization")),  # 美元
            "pe": _to_float(j.get("PERatio")),
            "dividend_yield": _to_float(j.get("DividendYield")) * 100 if j.get("DividendYield") else None,  # 轉成 %
        }
    except Exception:
        return {"name": symbol, "market_cap": None, "pe": None, "dividend_yield": None}

def fetch_us_price(symbol: str, av_key: str):
    """
    Alpha Vantage GLOBAL_QUOTE：即時/最近成交價
    """
    try:
        r = requests.get(
            "https://www.alphavantage.co/query",
            params={"function": "GLOBAL_QUOTE", "symbol": symbol, "apikey": av_key},
            timeout=12,
        )
        j = r.json().get("Global Quote", {})
        return _to_float(j.get("05. price"))
    except Exception:
        return None

def fetch_us_series_30(symbol: str, av_key: str):
    """
    Alpha Vantage TIME_SERIES_DAILY_ADJUSTED：抓日線，取最近 30 天（compact 回 100 天，這裡切後 30）
    """
    try:
        r = requests.get(
            "https://www.alphavantage.co/query",
            params={
                "function": "TIME_SERIES_DAILY_ADJUSTED",
                "symbol": symbol,
                "outputsize": "compact",  # 最多 100 天
                "apikey": av_key,
            },
            timeout=12,
        )
        ts = r.json().get("Time Series (Daily)", {}) or {}
        dates = sorted(ts.keys())[-30:]  # 只要最後 30 筆
        return [{"date": d, "close": float(ts[d]["4. close"])} for d in dates]
    except Exception:
        return []


def fetch_foreign_fundamentals_finnhub(symbol: str) -> dict:
    """
    使用 Finnhub 抓國外股票（例如美股）的基本資料：

    回傳：
    {
        "symbol": "NVDA",
        "name": "NVIDIA Corporation",
        "price": 181.11,          # 最新價 (USD)
        "market_cap": 1234.56,    # 市值 (billion USD)
        "pe": 35.4,               # 本益比
        "dividend_yield": 0.56,   # 殖利率，百分比 (%)
    }
    """
    api_key = getattr(settings, "FINNHUB_API_KEY", None)
    if not api_key:
        return {
            "symbol": symbol.upper(),
            "name": symbol.upper(),
            "price": None,
            "market_cap": None,
            "pe": None,
            "dividend_yield": None,
        }

    base = "https://finnhub.io/api/v1"
    sym = symbol.upper()

    out = {
        "symbol": sym,
        "name": sym,
        "price": None,
        "market_cap": None,
        "pe": None,
        "dividend_yield": None,
    }

    # 1) 公司基本資料（名稱、市值）
    try:
        prof = requests.get(
            f"{base}/stock/profile2",
            params={"symbol": sym, "token": api_key},
            timeout=8,
        ).json()
        if prof:
            out["name"] = prof.get("name") or prof.get("ticker") or sym
            # Finnhub 的 marketCapitalization 單位是「十億美元（billion USD）」
            out["market_cap"] = prof.get("marketCapitalization")
    except Exception:
        pass

    # 2) 財務指標（PE、殖利率）
    try:
        met = requests.get(
            f"{base}/stock/metric",
            params={"symbol": sym, "metric": "all", "token": api_key},
            timeout=8,
        ).json()
        d = met.get("metric", {}) or {}
        out["pe"] = d.get("peBasicExclExtraTTM") or d.get("peTTM")
        dy = d.get("dividendYieldTTM") or d.get("dividendYieldIndicatedAnnual")
        if dy is not None:
            out["dividend_yield"] = dy * 100  # 轉百分比 %
    except Exception:
        pass

    # 3) 最新價格
    try:
        q = requests.get(
            f"{base}/quote",
            params={"symbol": sym, "token": api_key},
            timeout=8,
        ).json()
        if isinstance(q, dict):
            out["price"] = q.get("c")  # c = current price
    except Exception:
        pass

    return out



def fetch_foreign_fundamentals(symbol: str) -> dict:
    """
    回傳：
      {
        "symbol": "AAPL",
        "name": "Apple Inc.",
        "price": 231.12,
        "market_cap": 3.68e12,          # 元 (USD)
        "pe": 35.4,
        "dividend_yield": 0.57,          # 百分比（%）
        "shares_outstanding": 15900000000
      }
    缺資料就放 None；會自動做多來源備援與回推市值。
    """
    out = {
        "symbol": symbol.upper(),
        "name": None,
        "price": None,
        "market_cap": None,
        "pe": None,
        "dividend_yield": None,
        "shares_outstanding": None,
    }

    # ---------- 第一層：Twelve Data quote ----------
    td_key = os.getenv("TWELVE_DATA_API_KEY") or getattr(settings, "TWELVE_DATA_API_KEY", "")
    try:
        if td_key:
            r = requests.get(
                "https://api.twelvedata.com/quote",
                params={"symbol": symbol, "apikey": td_key},
                timeout=10,
            )
            j = r.json() if r.ok else {}
            # 有些代碼會在最外層就回傳 error
            if isinstance(j, dict) and "symbol" in j:
                out["name"] = j.get("name") or out["name"]
                out["price"] = _to_float(j.get("close") or j.get("price")) or out["price"]
                # 有些標的會有 market_cap
                out["market_cap"] = _to_float(j.get("market_cap")) or out["market_cap"]
    except Exception:
        pass

    # ---------- 第二層：Alpha Vantage OVERVIEW ----------
    av_key = os.getenv("ALPHAVANTAGE_API_KEY") or getattr(settings, "ALPHAVANTAGE_API_KEY", "")
    try:
        if av_key:
            r2 = requests.get(
                "https://www.alphavantage.co/query",
                params={"function": "OVERVIEW", "symbol": symbol, "apikey": av_key},
                timeout=12,
            )
            j2 = r2.json() if r2.ok else {}
            # 成功時會是一個包含多個欄位的 dict；失敗時常是 {"Note": "..."} 或 {}
            if isinstance(j2, dict) and j2.get("Symbol"):
                out["name"] = j2.get("Name") or out["name"]
                # price OVERVIEW 沒有，仍以 TD quote / 走勢 API 為準
                out["market_cap"] = _to_float(j2.get("MarketCapitalization")) or out["market_cap"]
                out["pe"] = _to_float(j2.get("PERatio")) or out["pe"]
                # Alpha Vantage DividendYield 是小數（如 0.0067）；轉成百分比 %
                dy = _to_float(j2.get("DividendYield"))
                if dy is not None:
                    out["dividend_yield"] = dy * 100.0
                out["shares_outstanding"] = _to_float(j2.get("SharesOutstanding")) or out["shares_outstanding"]
    except Exception:
        pass

    # ---------- 資料補齊：以 price 與 shares 回推市值 ----------
    if out["market_cap"] is None and (out["price"] is not None) and (out["shares_outstanding"] is not None):
        out["market_cap"] = out["price"] * out["shares_outstanding"]

    return out


def _fetch_foreign_timeseries(symbol: str, days: int = 30):
    """從 Twelve Data 拉取最近 N 天的 1day time series，回傳 (dates, closes)。"""
    api_key = settings.TWELVE_DATA_API_KEY
    url = "https://api.twelvedata.com/time_series"
    params = {
        "symbol": symbol,
        "interval": "1day",
        "outputsize": days,
        "apikey": api_key,
    }
    r = requests.get(url, params=params, timeout=12)
    j = r.json()
    if not isinstance(j, dict) or "values" not in j:
        return None

    vals = j["values"]                      # 由新到舊
    vals = sorted(vals, key=lambda x: x["datetime"])  # 轉成由舊到新
    dates = [v["datetime"][:10] for v in vals]
    closes = [float(v["close"]) for v in vals]
    return dates, closes

def format_big_number(n):
    if n is None:
        return "--"
    n = float(n)
    # 先轉成「元」為基準
    if n >= 1_0000_0000_0000:  # 1 兆 = 10^12
        return f"{n / 1_0000_0000_0000:.2f} 兆"
    elif n >= 1_0000_0000:     # 1 億 = 10^8
        return f"{n / 1_0000_0000:.2f} 億"
    else:
        return f"{n:,.0f} 元"


def stock_detail(request, stock_code):
    code = str(stock_code).strip()

    # ===== 國外股票（非純數字）→ Finnhub =====
    if not code.isdigit():
        sym = code.upper()

        # 1) 取得基本資料
        f = fetch_foreign_fundamentals_finnhub(sym)

        # 如果全部都是 None，就當作查無此股
        if (
            f.get("price") is None
            and f.get("market_cap") is None
            and f.get("pe") is None
            and f.get("dividend_yield") is None
        ):
            return render(request, "search_not_found.html", {"query": sym})

        # 2) 近 30 天走勢圖（用 Finnhub /stock/candle）

        chart = None
        try:
            ts = _fetch_foreign_timeseries(sym, days=30)
            if ts is not None:
                dates, closes = ts
                fig = go.Figure()
                fig.add_trace(go.Scatter(
                    x=dates, y=closes, mode="lines", name="收盤價"
                ))
                fig.update_layout(
                    title=f"{sym} 近 30 天股價",
                    xaxis_title="日期",
                    yaxis_title="價格",
                )
                chart = fig.to_html(include_plotlyjs="cdn", full_html=False)
        except Exception:
            chart = None
            

        # 3) 把數字轉成顯示字串（市值用「多少 B USD」）
        mkt_display = "--"
        mcap = f.get("market_cap")
        if mcap is not None:
            trillion = mcap / 1_000_000   # 因為是 million → trillion 要除以 1,000,000
            if trillion >= 1:
                mkt_display = f"{trillion:.2f} T"
            else:
                mkt_display = f"{(mcap/1000):,.2f} B"

        pe_display = _fmt_number(f.get("pe"))
        yld_display = _fmt_percent(f.get("dividend_yield"))

        stock = {
            "名稱": f.get("name") or sym,
            "代碼": sym,
            "目前價格": f.get("price"),
            "市值": mkt_display,
            "本益比": pe_display,
            "股息殖利率": yld_display,
        }

        in_watchlist = False
        has_trades = False
        if request.user.is_authenticated:
            in_watchlist = Watchlist.objects.filter(
                user=request.user, stock_code=sym, collected=True
            ).exists()
            has_trades = BuyRecord.objects.filter(
                user=request.user, stock_code=sym
            ).exists()

        return render(request, "stock_detail.html", {
            "stock": stock,
            "chart": chart,
            "in_watchlist": in_watchlist,
            "has_trades": has_trades,
            "is_foreign": True,   # ★ 給模板判斷是否顯示 USD
        })

    # ===== 台股（純數字）→ 原本流程 =====

    metrics = {
        "市值": None,
        "本益比": None,
        "股息殖利率": None,
    }

    market_cap = pe = yld = None

    #stock_name, current_price = get_basic_name_price(code)

    stock_name = None
    current_price = None
    try:
        stock_name, current_price = get_basic_name_price(code)
    except Exception as e:
        print(f"[TWSE] get_basic_name_price 失敗 {code}: {e}")


    snap = (StockSnapshot.objects
            .filter(code=code)
            .order_by("-created_at")
            .first())

    api = DataLoader()
    token = settings.FINMIND_TOKEN
    api.login_by_token(token)

    need_metrics = False
    if snap and timezone.now() - snap.created_at <= CACHE_TTL:

        stock_name = snap.name
        current_price = float(snap.price)

        market_cap = float(snap.market_cap) if snap.market_cap is not None else None
        pe = float(snap.pe) if snap.pe is not None else None
        yld = float(snap.dividend_yield) if snap.dividend_yield is not None else None
        
        '''if market_cap is None or pe is None or yld is None:
            need_metrics = True'''
        # ★ 這裡把 "metrics" 字串也補起來（給前端用）
        if market_cap is not None:
            metrics["市值"] = _fmt_money(market_cap)
        if pe is not None:
            metrics["本益比"] = _fmt_number(pe)
        if yld is not None:
            metrics["股息殖利率"] = _fmt_percent(yld)

        # 如果還有缺，就標記 need_metrics=True 讓下面再算一次
        if market_cap is None or pe is None or yld is None:
            need_metrics = True

 
    else:
        # ========= 這段是「往回找最近一筆日線資料」的地方 =========
  
        base_start = "2025-01-01"
        today = dt.date.today()

        df = None
        for offset in range(0, 5):
            end_date = (today - dt.timedelta(days=offset)).strftime("%Y-%m-%d")
            tmp = api.taiwan_stock_daily(
                stock_id=code,
                start_date=base_start,
                end_date=end_date,
            )
            if tmp is not None and not tmp.empty:
                df = tmp
                break     # 找到最近一筆有資料的日線就跳出

        if df is None or df.empty:
            return render(request, "search_not_found.html", {"query": code})

        current_price = float(df["close"].iloc[-1])
        info = api.taiwan_stock_info()
        name_arr = info.loc[info["stock_id"] == code, "stock_name"].values
        stock_name = name_arr[0] if len(name_arr) else code
        need_metrics = True

        fig = go.Figure()
        fig.add_trace(go.Scatter(x=df['date'], y=df['close'], mode='lines', name='收盤價'))
        fig.update_layout(title=f'{code} 近 30 天股價', xaxis_title='日期', yaxis_title='價格')
        chart = fig.to_html(include_plotlyjs='cdn', full_html=False)
  

    if need_metrics:
        try:
            inputs = fetch_inputs_finmind_twse(code, price=current_price, token=token)
            if inputs is not None:
                metrics = compute_metrics(inputs)

                market_cap = metrics.get("市值")
                pe        = metrics.get("本益比")
                yld       = metrics.get("股息殖利率")

                # 有算成功就寫入快取
                StockSnapshot.save_snapshot(
                    code, stock_name, current_price, inputs, metrics
                )
            else:
                print(f"[metrics] {code} 沒拿到 inputs，先顯示 --")
        except Exception as e:
            # 算指標失敗的情況，例如 2454 在 FinMind 沒資料
            print(f"[metrics] {code} 計算失敗: {e}")
            # metrics 保持預設的 None，前端會顯示 "--"

    if 'chart' not in locals():
        try:
            df30 = api.taiwan_stock_daily(
                stock_id=code,
                start_date=(dt.date.today() - dt.timedelta(days=30)).strftime("%Y-%m-%d"),
                end_date=str(dt.date.today())
            )
            if df30 is not None and not df30.empty:
                fig = go.Figure()
                fig.add_trace(go.Scatter(x=df30['date'], y=df30['close'], mode='lines', name='收盤價'))
                fig.update_layout(title=f'{code} 近 30 天股價', xaxis_title='日期', yaxis_title='價格')
                chart = fig.to_html(include_plotlyjs='cdn', full_html=False)
            else:
                chart = None
        except Exception:
            chart = None

    # 注意：這裡不要再用 inputs._pe_from_twse（因為命中快取時 inputs 不一定存在）
    
    stock = {
        "名稱": stock_name,
        "代碼": code,
        "目前價格": current_price,
        "市值": metrics["市值"],           # ← 用字串版
        "本益比": metrics["本益比"],       # ← 用字串版
        "股息殖利率": metrics["股息殖利率"], # ← 用字串版
    }
        

    in_watchlist = False
    has_trades   = False

    if request.user.is_authenticated:
        in_watchlist = Watchlist.objects.filter(
            user=request.user, stock_code=code, collected=True
        ).exists()
        has_trades = BuyRecord.objects.filter(
        user=request.user, stock_code=code
        ).exists()

    return render(request, "stock_detail.html", {
        "stock": stock,
        "chart": chart,
        "in_watchlist": in_watchlist,
        "has_trades": has_trades,
        "is_foreign": False,
    })

# 自選股
def norm_code(s: str) -> str:
    # 全形→半形 + 去空白 + 大寫
    return unicodedata.normalize('NFKC', (s or '')).strip().upper()

@login_required(login_url='/login/')
def my_watchlist(request):
    user = request.user
    sort = request.GET.get("sort", "custom")  # custom / recent / code_asc / code_desc / holdings_desc

    qs = Watchlist.objects.filter(user=user)

    # ---- 決定順序，得到「最終要渲染的 list」：watchlist ----
    if sort == "recent":
        last_trade_sq = Subquery(
            BuyRecord.objects
                .filter(user=user, stock_code=OuterRef('stock_code'))
                .order_by('-created_at')
                .values('created_at')[:1],
            output_field=DateTimeField()
        )
        default_dt = dt.datetime(1900, 1, 1, tzinfo=py_tz.utc)
        qs = qs.annotate(last_trade=last_trade_sq) \
               .order_by(Coalesce('last_trade', Value(default_dt)).desc())
        watchlist = list(qs)

    elif sort in ("code_asc", "code_desc"):
        items = list(qs)

        def code_key(item):
            s = norm_code(item.stock_code)
            return (0, int(s)) if s.isdigit() else (1, s)

        reverse = (sort == "code_desc")
        watchlist = sorted(items, key=code_key, reverse=reverse)

    elif sort == "holdings_desc":
        watchlist = list(qs.order_by("-quantity", "stock_code"))

    else:  # custom（拖曳）
        watchlist = list(qs.order_by("position", "stock_code"))


    for item in watchlist:
        # 張數
        item.lots = (item.quantity or 0) / 1000

        # 即時價（有快取）
        try:
            item.current_price = get_current_price_now(item.stock_code)
        except Exception:
            item.current_price = None

        # 最後一次交易（顯示用）
        last_record = BuyRecord.objects.filter(
            user=user, stock_code=item.stock_code
        ).order_by('-created_at').first()
        item.last_trade_price = last_record.price if last_record else None

        # ✅ 新增：只要有紀錄就顯示交易紀錄按鈕
        item.has_trades = BuyRecord.objects.filter(
            user=user, stock_code=item.stock_code
        ).exists()

    return render(request, "watchlist.html", {
        "watchlist": watchlist,
        "sort": sort,   # 傳給模板，讓選單保持選取
    })


# 新增自選股
@login_required(login_url='/login/')
def add_watchlist(request):
    if request.method == "POST":
        #stock_code = request.POST.get("stock_code")
        stock_code = request.POST.get("stock_code", "").strip().upper()
        next_url = request.POST.get("next") or "/watchlist/"

        # 建立或取得 Watchlist
        max_pos = Watchlist.objects.filter(user=request.user).aggregate(Max("position"))["position__max"] or 0
        watch, created = Watchlist.objects.get_or_create(
            user=request.user,
            stock_code=stock_code,
            defaults={"position": max_pos + 1, "collected": True}
            #defaults={"collected": True}
        )

        if not created:
            # 如果已存在，只把 collected 設為 True，不覆蓋購買資訊
            watch.collected = True
            watch.save()

        return redirect(next_url)
    
# 移除自選股
@login_required
def remove_watchlist(request):
    if request.method == "POST":
        #stock_code = request.POST.get("stock_code")
        stock_code = request.POST.get("stock_code", "").strip().upper()
        watch = Watchlist.objects.filter(user=request.user, stock_code=stock_code).first()
        if watch:
            # 如果該股票沒購買過，直接刪除
            if not watch.quantity:
                watch.delete()
            else:
                # 只取消收藏
                watch.collected = False
                watch.save()
        return redirect(request.POST.get("next") or "/watchlist/")
    return redirect("index")


@login_required
@require_POST
def reorder_watchlist(request):
    """
    期待前端送上 JSON: {"order": ["3008", "0050", "2330", ...]}
    只更新這位使用者的自選股 position
    """
    import json
    try:
        payload = json.loads(request.body.decode("utf-8"))
        order = payload.get("order", [])
        if not isinstance(order, list):
            return JsonResponse({"ok": False, "msg": "invalid format"}, status=400)

        qs = Watchlist.objects.filter(user=request.user, stock_code__in=order)
        wl_map = {w.stock_code: w for w in qs}

        with transaction.atomic():
            for idx, code in enumerate(order, start=1):
                w = wl_map.get(code)
                if w:
                    w.position = idx
                    w.save(update_fields=["position"])

        return JsonResponse({"ok": True})
    except Exception as e:
        return JsonResponse({"ok": False, "msg": str(e)}, status=400)
    

@login_required(login_url="/login/")
@require_POST
def delete_stock(request):
    code = (request.POST.get("code") or "").strip().upper()
    next_url = request.POST.get("next") or "my_watchlist"

    if not code:
        messages.error(request, "未提供股票代碼。")
        return redirect(next_url)

    # 你現有的 model 名稱可能不同，下面用 try/except 保守處理
    with transaction.atomic():
        # 1) 刪除收藏（不論是否已收藏，統一清掉）
        from .models import Watchlist
        Watchlist.objects.filter(user=request.user, stock_code=code).delete()

        # 2) 刪除該股票的交易紀錄（如果你有這個模型）
        #    請把 TransactionRecord 換成你專案中交易紀錄的模型名稱

        BuyRecord.objects.filter(user=request.user, stock_code=code).delete()
        '''
        try:
            from .models import TransactionRecord
            TransactionRecord.objects.filter(user=request.user, stock_code=code).delete()
        except Exception:
            pass
        '''

        # 3) 若你還有其他和該股票相關的資料（例如持倉/快取快照），在這裡一併清掉
        # try:
        #     from .models import Position
        #     Position.objects.filter(user=request.user, stock_code=code).delete()
        # except Exception:
        #     pass

    messages.success(request, f"已刪除 {code}（包含收藏與交易紀錄）。")
    # 若 next_url 是名稱，Django 會當作 path 名字處理；若是完整 URL 也能回去
    return redirect(next_url)


# 購買股票
@login_required(login_url='/login/')
def buy_stock(request):
    if request.method == "POST":
        stock_code = request.POST.get("stock_code")
        amount = int(request.POST.get("amount", 0))
        unit = request.POST.get("unit")  # shares 或 lots

        if unit == "lots":
            amount *= 1000  # 1 張 = 1000 股

        # 自動帶入當前價
        price = Decimal(str(get_current_price_now(stock_code)))

        # 更新或建立 Watchlist
        watch, created = Watchlist.objects.get_or_create(
            user=request.user, stock_code=stock_code,
            defaults={"quantity": amount, "average_price": price, "collected": True}
        )
        if not created:
            old_avg = Decimal(watch.average_price or 0)
            old_qty = watch.quantity or 0
            new_qty = old_qty + amount
            new_avg = (old_avg * old_qty + price * amount) / new_qty if new_qty else price

            watch.average_price = new_avg
            watch.quantity = new_qty
            watch.collected = True
            watch.save()

        # 建立交易紀錄（買入 = 正數）
        BuyRecord.objects.create(
            user=request.user,
            stock_code=stock_code,
            quantity=amount,     # 正數 = BUY
            price=price
        )

        messages.success(request, f"成功以 {price} 買入 {amount} 股 {stock_code}！")
        return redirect(request.POST.get("next") or "/watchlist/")


@login_required(login_url='/login/')
def sell_stock(request):
    if request.method == "POST":
        stock_code = request.POST.get("stock_code")
        amount = int(request.POST.get("amount", 0))
        unit = request.POST.get("unit")  # shares 或 lots
        if unit == "lots":
            amount *= 1000

        # 當前價
        price = Decimal(str(get_current_price_now(stock_code)))

        # 先確認持股是否足夠
        watch = Watchlist.objects.filter(user=request.user, stock_code=stock_code).first()
        held = watch.quantity if watch and watch.quantity else 0
        if amount > held:
            messages.error(request, f"賣出失敗：持股不足（目前 {held} 股）")
            return redirect(request.POST.get("next") or "/watchlist/")

        # 更新持股：平均成本不再變動；若清倉，可選擇把 avg 清空
        watch.quantity = held - amount
        if watch.quantity == 0:
            watch.average_price = None  # 清倉後可清空成本（可依需求調整）
        watch.save()

        # 建立交易紀錄（賣出 = 負數）
        BuyRecord.objects.create(
            user=request.user,
            stock_code=stock_code,
            quantity=-amount,    # 負數 = SELL
            price=price
        )

        messages.success(request, f"成功以 {price} 賣出 {amount} 股 {stock_code}！")
        return redirect(request.POST.get("next") or "/watchlist/")


@login_required
def trade_history(request, stock_code):
    user = request.user
    code = str(stock_code)

    # 組交易資料（同你現有的）
    records_qs = (BuyRecord.objects
                  .filter(user=user, stock_code=code)
                  .order_by("created_at"))
    running_qty = 0
    records = []
    for r in records_qs:
        action = "BUY" if r.quantity >= 0 else "SELL"
        running_qty += r.quantity
        records.append({
            "created_at": r.created_at,
            "action": action,
            "quantity": abs(r.quantity),
            "price": r.price,
            "balance": running_qty
        })

    # 取得當前價（沿用你的 helper）
    try:
        current_price = get_current_price_now(code)
    except Exception:
        current_price = None

    # 取得中文名稱
    snap = (StockSnapshot.objects
            .filter(code=code)
            .order_by("-created_at")
            .first())
    stock_name = snap.name if snap and snap.name else code
    if not snap or not snap.name:
        try:
            api = DataLoader()
            api.login_by_token(settings.FINMIND_TOKEN)
            info = api.taiwan_stock_info()
            name_arr = info.loc[info["stock_id"] == code, "stock_name"].values
            if len(name_arr):
                stock_name = name_arr[0]
        except Exception:
            pass

    return render(request, "trade_history.html", {
        "stock_code": code,
        "stock_name": stock_name,
        "records": records,
        "current_price": current_price
    })


# 新聞

# 抽主體（保留顯示用）
def _canonical_title(title: str) -> str:
    t = (title or "").strip()
    # 砍掉最後一段「- 來源」或「— 來源」；支援 -, –, —
    t = re.split(r"\s*[-–—]\s*[^-–—｜|]{1,40}$", t)[0]
    # 砍掉管線分隔後的欄位（全形｜或半形|）
    t = re.split(r"\s*[|｜]\s*.*$", t)[0]
    # 砍掉尾端括號內容（作者/即時等）
    t = re.sub(r"(（.*?）|\(.*?\))\s*$", "", t)
    return t.strip()

# 轉成穩定的去重 key（不拿來顯示）
def _title_key(title: str) -> str:
    t = _canonical_title(title)
    # 移除所有非中文字母數字，並轉小寫（避免「、：！？」等差異）
    t = re.sub(r"[^\w\u4e00-\u9fff]+", "", t, flags=re.UNICODE).lower()
    return t

def _canonical_url(u: str) -> str:
    try:
        pr = urlparse(u or "")
        # 去掉查詢參數與 fragment，避免 utm 導致重複
        return urlunparse((pr.scheme, pr.netloc.lower(), pr.path, "", "", ""))
    except Exception:
        return u or ""

def news_list(request):
    feed = feedparser.parse(
        "https://news.google.com/rss/search?q=股票&hl=zh-TW&gl=TW&ceid=TW:zh-Hant"
    )

    news = []
    seen_keys = set()                   # ← 用 key 去重
    now = dt.datetime.now()
    days_limit = int(request.GET.get("days", 7))
    wanted_sentiment = request.GET.get("sentiment")
    sort_key = request.GET.get("sort", "rank")

    for item in feed.entries:
        # ---- 日期 ----
        date_str = getattr(item, "published", getattr(item, "updated", "")) or ""
        try:
            date_obj = dt.datetime.strptime(date_str, "%a, %d %b %Y %H:%M:%S %Z")
        except Exception:
            date_obj = now
        if (now - date_obj).days > days_limit:
            continue

        # ---- 標題 / 連結 ----
        title_raw = (getattr(item, "title", "") or "").strip()
        if not title_raw:
            continue
        key = _title_key(title_raw)     # ← 生成穩定 key
        if key in seen_keys:            # ← 去重（同文不同站會被擋掉）
            continue
        seen_keys.add(key)

        title = _canonical_title(title_raw)               # 顯示用主體
        url = _canonical_url(getattr(item, "link", ""))   # 乾淨連結
        description = getattr(item, "summary", "") or ""

        # 主題過濾（保留你的條件）
        if "股票" not in title_raw and "股票" not in description:
            continue

        # ---- 情緒 ----
        label, score = simple_score(title_raw + " " + description)
        if wanted_sentiment and label != wanted_sentiment:
            continue

        news.append({
            "title": title,                       # 顯示主體，不帶站名
            "url": url,
            "date": date_obj.strftime("%Y-%m-%d"),
            "sentiment": label,
            "sentiment_score": f"{score:.2f}",
        })

    # ---- 排序：rank(正>中>負) → score desc → date desc ----
    if sort_key == "score":
        news.sort(key=lambda x: float(x["sentiment_score"]), reverse=True)
    elif sort_key == "date":
        news.sort(key=lambda x: x["date"], reverse=True)
    else:  # rank
        rank = {"positive": 2, "neutral": 1, "negative": 0}
        news.sort(key=lambda x: (rank.get(x["sentiment"], 1),
                                 float(x["sentiment_score"]),
                                 x["date"]),
                  reverse=True)

    return render(request, "news.html", {
        "news": news,
        "days": days_limit,
        "current_sentiment": wanted_sentiment or "",
        "sort": sort_key,
    })



# 討論區

def forum_list(request):
    # 取得搜尋關鍵字（沒有就給空字串）
    q = request.GET.get("q", "").strip()

    category = request.GET.get("category")

    # 先拿到基礎 queryset
    base_qs = Post.objects.all()

    if category in ["share", "analysis", "qa"]:
        base_qs = base_qs.filter(category=category)

    # 如果有搜尋關鍵字，就用 title / content / stock_code 模糊查詢
    if q:
        base_qs = base_qs.filter(
            Q(title__icontains=q) |
            Q(content__icontains=q) |
            Q(stock_code__icontains=q)
        )

    # 在最後統一 annotate 出按讚數 & 留言數，再照時間排序
    posts = (
        base_qs
        .annotate(
            likes_count=Count("likes", distinct=True),
            comments_count=Count("comments", distinct=True),
        )
        .order_by("-created_at")
    )

    return render(
        request,
        "forum.html",
        {
            "posts": posts,
            "q": q,   # 回傳給模板，讓搜尋框可以保留剛剛輸入的字
            "category": category, # 給模板判斷哪個 tab 要亮起
        },
    )


# 發布貼文

@login_required(login_url='/login/')
def forum_new(request):
    if request.method == "POST":
        title = request.POST["title"]
        stock_code = request.POST["stock_code"]
        content = request.POST["content"]
        # ⬇ 新增：分類
        category = request.POST.get("category", "share")

        Post.objects.create(
            title=title,
            stock_code=stock_code,
            content=content,
            category=category,      # ⬅ 把分類存進去
            author=request.user,
            created_at=timezone.now(),
        )
        return redirect("forum_list")
    return render(request, "forum_new.html")


def forum_post(request, post_id):
    # 先抓貼文
    post = get_object_or_404(Post, id=post_id)

    # 留言：維持你原本的寫法（有 likes_count）
    comments = (
        post.comments
            .all()
            .annotate(likes_count=Count("likes"))
            .order_by("created_at")
    )

    # 目前登入者 id（沒有登入就會是 None）
    user_id = request.user.id if request.user.is_authenticated else None

    # ========= 1. 處理「留言」的 liked_by_user =========
    liked_comment_ids = set()
    if user_id:
        liked_comment_ids = set(
            CommentLike.objects
                .filter(user_id=user_id, comment__post=post)
                .values_list("comment_id", flat=True)
        )

    for c in comments:
        c.liked_by_user = c.id in liked_comment_ids

    # ========= 2. 處理「貼文本身」的 liked_by_user & likes_count =========
    # 貼文總讚數
    post.likes_count = PostLike.objects.filter(post=post).count()

    # 這個使用者有沒有按過這篇貼文的讚
    if user_id:
        post.liked_by_user = PostLike.objects.filter(
            post=post,
            user_id=user_id,
        ).exists()
    else:
        post.liked_by_user = False

    # ========= 3. 回傳給模板 =========
    return render(
        request,
        "forum_post.html",
        {
            "post": post,
            "comments": comments,
            "user_id": user_id,
        },
    )

@login_required
def toggle_post_like(request, post_id):
    post = get_object_or_404(Post, id=post_id)
    like, created = PostLike.objects.get_or_create(
        user=request.user,
        post=post,
    )
    if not created:
        # 已經按過讚 → 這次就視為「取消讚」
        like.delete()

    return redirect("forum_post", post_id=post_id)


# 刪除貼文

@login_required
def forum_delete(request, post_id):
    post = get_object_or_404(Post, id=post_id)
    if request.user == post.author:
        post.delete()
    return redirect("forum_list")


# 新增留言

@login_required
def add_comment(request, post_id):
    post = get_object_or_404(Post, id=post_id)
    if request.method == "POST":
        content = request.POST.get("content")
        if content:
            Comment.objects.create(
                post=post,
                author=request.user,
                content=content,
                created_at=timezone.now(),
            )
    return redirect("forum_post", post_id=post.id)


# 刪除留言

@login_required
def comment_delete(request, post_id, comment_id):
    comment = get_object_or_404(Comment, id=comment_id, post_id=post_id)
    if request.user == comment.author or request.user == comment.post.author:
        comment.delete()
    return redirect("forum_post", post_id=post_id)


@login_required
def toggle_comment_like(request, post_id, comment_id):
    comment = get_object_or_404(Comment, id=comment_id, post_id=post_id)
    like, created = CommentLike.objects.get_or_create(
        user=request.user,
        comment=comment,
    )
    if not created:
        # 已經按過讚 → 這次就視為「取消讚」
        like.delete()

    return redirect("forum_post", post_id=post_id)

