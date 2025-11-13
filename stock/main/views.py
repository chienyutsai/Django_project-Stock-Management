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
import datetime
from datetime import timedelta
from datetime import datetime, timezone as py_tz
from FinMind.data import DataLoader
import plotly.graph_objs as go
from plotly.offline import plot
import plotly.io as pio
from decouple import config
from django.http import Http404
import pandas as pd
from django.contrib import messages
from .models import Watchlist, BuyRecord, Post, Comment
from decimal import Decimal
from django.utils import timezone
from main.models import StockSnapshot
import datetime as dt
import difflib
import unicodedata
from django.db.models import Max, Q, OuterRef, Subquery, Value, DateTimeField, F
from django.db.models.functions import Coalesce
from django.db import transaction
from django.views.decorators.http import require_POST
from urllib.parse import urlparse, urlunparse
from .services.sentiment import simple_score
from .services.metrics_tw import Inputs, get_basic_name_price, fetch_inputs_finmind_twse, compute_metrics
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
    return render(request, 'index.html')

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
        return redirect('index')
    return redirect('profile')

def get_market_data():
    api = DataLoader()
    token = config("FINMIND_TOKEN")
    api.login_by_token(token)

    today = datetime.date.today()
    start_date = (today - datetime.timedelta(days=30)).strftime("%Y-%m-%d")
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
'''
def search_stock(request):
    query = request.GET.get("query", "").strip()
    if not query:
        return redirect("/")

    api = DataLoader()
    token = config("FINMIND_TOKEN")
    api.login_by_token(token)

    # 嘗試用 query 當股票代碼抓資料
    try:
        df = api.taiwan_stock_daily(stock_id=query, start_date="2025-01-01", end_date=str(datetime.date.today()))
        if df is not None and not df.empty:
            stock = {
                "名稱": df["stock_id"].iloc[0],  # 可改成對應名稱
                "代碼": query,
                "目前價格": df["close"].iloc[-1]
            }
            return redirect(f"/stock_detail/{stock['代碼']}/")
    except Exception as e:
        print("API error:", e)

    # 查不到股票
    return render(request, "search_not_found.html", {"query": query})
'''

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

    # a) 純數字 → 直接當台股代碼
    if query.isdigit():
        return redirect(f"/stock_detail/{query}/")

    # b) 中文/關鍵字 → FinMind 對應出 stock_id
    try:
        api = DataLoader()
        api.login_by_token(settings.FINMIND_TOKEN)
        info = api.taiwan_stock_info()
        match = info[info["stock_name"].str.contains(query, case=False, na=False)]
        if not match.empty:
            code = str(match.iloc[0]["stock_id"])
            return redirect(f"/stock_detail/{code}/")
    except Exception:
        pass

    # c) 其他（英文字母等）→ 當外國代碼交給 stock_detail 判斷
    return redirect(f"/stock_detail/{query.upper()}/")

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
    code = str(stock_code)
    snap = (StockSnapshot.objects
            .filter(code=code)
            .order_by("-created_at")
            .first())
    if snap and timezone.now() - snap.created_at <= CACHE_TTL:
        return float(snap.price)

    api = DataLoader()
    token = settings.FINMIND_TOKEN
    api.login_by_token(token)

    df = api.taiwan_stock_daily(
        stock_id=code,
        start_date=(datetime.date.today() - datetime.timedelta(days=30)).strftime("%Y-%m-%d"),
        end_date=str(datetime.date.today())
    )
    if df is None or df.empty:
        raise Http404("抓不到當前股價")

    price = float(df["close"].iloc[-1])

    # 取得中文名稱（若要）
    info = api.taiwan_stock_info()
    name_arr = info.loc[info["stock_id"] == code, "stock_name"].values
    stock_name = name_arr[0] if len(name_arr) else code

    # 也更新一次 snapshot（含你已有的 metrics）
    inputs = fetch_inputs_finmind_twse(code, price=price, token=token)
    metrics = compute_metrics(inputs)
    StockSnapshot.save_snapshot(code, stock_name, price, inputs, metrics)

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


def stock_detail(request, stock_code):
    code = str(stock_code).strip()
    
    # ===== 外國股票（非純數字）→ 統一大寫 =====

    if not code.isdigit():
        sym = code.upper()
        f = fetch_foreign_fundamentals(sym)     # ← 這裡
        if (f.get("price") is None) and (f.get("market_cap") is None) and (f.get("pe") is None):
            # 幾乎沒拿到任何資料 → 視為查無
            return render(request, "search_not_found.html", {"query": sym})

        # 走勢圖（30 天）：可繼續用 Twelve Data 的 time_series 畫「折線圖」統一風格
        chart = None
        td_key = os.getenv("TWELVE_DATA_API_KEY") or getattr(settings, "TWELVE_DATA_API_KEY", "")
        try:
            if td_key:
                rr = requests.get(
                    "https://api.twelvedata.com/time_series",
                    params={"symbol": sym, "interval": "1day", "outputsize": 60, "apikey": td_key, "format": "JSON"},
                    timeout=12
                )
                jj = rr.json() if rr.ok else {}
                series = jj.get("values") or jj.get("data") or []
                if series:
                    # 轉成時間順序（API 回傳通常是新到舊）
                    xs = [row["datetime"] for row in reversed(series)]
                    ys = [float(row["close"]) for row in reversed(series)]
                    fig = go.Figure()
                    fig.add_trace(go.Scatter(x=xs, y=ys, mode='lines', name='收盤價'))
                    fig.update_layout(title=f'{sym} 近 30 天股價', xaxis_title='日期', yaxis_title='價格')
                    chart = fig.to_html(include_plotlyjs='cdn', full_html=False)
        except Exception:
            chart = None

        # 讓模板使用同一組欄位（跟台股一致）
        stock = {
            "名稱": f.get("name") or sym,
            "代碼": sym,
            "目前價格": f.get("price") if f.get("price") is not None else "無資料",
            "市值": f.get("market_cap"),
            "本益比": f.get("pe"),
            "股息殖利率": f.get("dividend_yield"),
        }

        in_watchlist = False
        if request.user.is_authenticated:
            in_watchlist = Watchlist.objects.filter(
                user=request.user, stock_code=sym, collected=True
            ).exists()

        return render(request, "stock_detail.html", {
            "stock": stock,
            "chart": chart,
            "in_watchlist": in_watchlist,
        })
    
    '''
    # ===== 外國股票（非純數字）→ 走 Alpha Vantage =====
    if not code.isdigit():
        sym = code.upper()
        av_key = getattr(settings, "ALPHA_VANTAGE_API_KEY", None)  # 你已經設定好 key
        overview = fetch_us_overview(sym, av_key) if av_key else {"name": sym, "market_cap": None, "pe": None, "dividend_yield": None}
        price = fetch_us_price(sym, av_key) if av_key else None
        series = fetch_us_series_30(sym, av_key) if av_key else []

        # 畫「最近 30 天 折線圖」與台股統一
        chart = None
        if series:
            fig = go.Figure()
            fig.add_trace(go.Scatter(
                x=[row["date"] for row in series],
                y=[row["close"] for row in series],
                mode="lines",
                name=f"{sym} 收盤價",
            ))
            fig.update_layout(title=f"{sym} 近 30 天股價", xaxis_title="日期", yaxis_title="價格")
            chart = fig.to_html(include_plotlyjs="cdn", full_html=False)

        # 讓表格共用同一個模板結構
        stock = {
            "名稱": overview["name"],
            "代碼": sym,
            "目前價格": price,
            "市值": overview["market_cap"],         # 單位：美元（你可以在模板加上顯示單位）
            "本益比": overview["pe"],
            "股息殖利率": overview["dividend_yield"], # 百分比數字，模板加上 % 號
        }

        in_watchlist = False
        if request.user.is_authenticated:
            in_watchlist = Watchlist.objects.filter(
                user=request.user, stock_code=sym, collected=True
            ).exists()

        return render(request, "stock_detail.html", {
            "stock": stock,
            "chart": chart,
            "in_watchlist": in_watchlist,
        })'''

    # ===== 台股（純數字）→ 原本流程 =====
    stock_name, current_price = get_basic_name_price(code)

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
        if market_cap is None or pe is None or yld is None:
            need_metrics = True
    else:
        df = api.taiwan_stock_daily(
            stock_id=code,
            start_date="2025-01-01",
            end_date=str(datetime.date.today())
        )
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
        inputs = fetch_inputs_finmind_twse(code, price=current_price, token=token)
        metrics = compute_metrics(inputs)
        market_cap = metrics["市值"]
        pe = metrics["本益比"]
        yld = metrics["股息殖利率"]
        StockSnapshot.save_snapshot(code, stock_name, current_price, inputs, {
            "市值": market_cap, "本益比": pe, "股息殖利率": yld
        })

    if 'chart' not in locals():
        try:
            df30 = api.taiwan_stock_daily(
                stock_id=code,
                start_date=(datetime.date.today() - datetime.timedelta(days=30)).strftime("%Y-%m-%d"),
                end_date=str(datetime.date.today())
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
        "名稱": stock_name or "",
        "代碼": code,
        "目前價格": current_price,
        "市值": market_cap,
        "本益比": pe,
        "股息殖利率": yld,
    }

    in_watchlist = False
    if request.user.is_authenticated:
        in_watchlist = Watchlist.objects.filter(
            user=request.user, stock_code=code, collected=True
        ).exists()

    return render(request, "stock_detail.html", {
        "stock": stock,
        "chart": chart,
        "in_watchlist": in_watchlist
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
        default_dt = datetime(1900, 1, 1, tzinfo=py_tz.utc)
        qs = qs.annotate(last_trade=last_trade_sq) \
               .order_by(Coalesce('last_trade', Value(default_dt)).desc())
        watchlist = list(qs)

    elif sort in ("code_asc", "code_desc"):
        items = list(qs)

        def code_key(item):
            s = norm_code(item.stock_code)
            return (0, int(s)) if s.isdigit() else (1, s)

        print("DEBUG sort keys:", [(i.stock_code, code_key(i)) for i in items])  # 你剛剛加的那行
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
    
    print("FINAL ORDER:", [i.stock_code for i in watchlist])

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
        try:
            from .models import TransactionRecord
            TransactionRecord.objects.filter(user=request.user, stock_code=code).delete()
        except Exception:
            pass

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



'''
def news_list(request):
    api_key = config("GNEWS_API_KEY")
    url = f"https://gnews.io/api/v4/top-headlines?country=tw&topic=business&token={api_key}&max=20&lang=zh"


    # url = f"https://gnews.io/api/v4/top-headlines?country=tw&topic=business&q=股票&token={api_key}&max=20&lang=zh"
    
    news = []
    seen_titles = set()  # 用標題去重

    try:
        response = requests.get(url)

        print("GNEWS STATUS:", response.status_code)
        print("GNEWS RAW:", response.text)

        data = response.json()

        #RSS
        if not data.get("articles"):  # GNews沒回新聞
            print("⚠️ GNews 無資料，改用 Google News RSS 備援")
            feed = feedparser.parse("https://news.google.com/rss/search?q=股票&hl=zh-TW&gl=TW&ceid=TW:zh-Hant")
            for item in feed.entries[:20]:
                title = item.title
                if title not in seen_titles:
                    news.append({
                        "title": title,
                        "url": item.link,
                        "date": item.published[:10]
                    })
                    seen_titles.add(title)

        else:
            for item in data.get("articles", []):
                title = item["title"]
                if title not in seen_titles:
                    news.append({
                        "title": title,
                        "url": item["url"],
                        "date": item["publishedAt"][:10]
                    })
                    seen_titles.add(title)
    except Exception as e:
        print("GNews API error:", e)

    return render(request, "news.html", {"news": news})
'''

# 討論區

def forum_list(request):
    posts = Post.objects.all().order_by("-created_at")
    return render(request, "forum.html", {"posts": posts})


# 發布貼文

@login_required(login_url='/login/')
def forum_new(request):
    if request.method == "POST":
        title = request.POST["title"]
        stock_code = request.POST["stock_code"]
        content = request.POST["content"]

        Post.objects.create(
            title=title,
            stock_code=stock_code,
            content=content,
            author=request.user,
            created_at=timezone.now(),
        )
        return redirect("forum_list")
    return render(request, "forum_new.html")


def forum_post(request, post_id):
    post = get_object_or_404(Post, id=post_id)
    comments = post.comments.all().order_by("created_at")

    if request.method == "POST":
        content = request.POST.get("content")
        if content and request.user.is_authenticated:
            Comment.objects.create(
                post=post,
                author=request.user,
                content=content,
                created_at=timezone.now(),
            )
            return redirect("forum_post", post_id=post.id)

    return render(
        request,
        "forum_post.html",
        {
            "post": post,
            "comments": comments,
            "user_id": request.user.id if request.user.is_authenticated else None,
        },
    )


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


