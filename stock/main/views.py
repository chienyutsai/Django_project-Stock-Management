import re
import feedparser
from django.http import HttpResponse
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
from .services.metrics_tw import fetch_inputs_finmind_twse, compute_metrics
from loguru import logger

logger.remove()          # 移除預設的 log handler
logger.add("finmind.log", level="ERROR")  # 只寫 ERROR 到檔案，不印出來
    
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

# 股票細節

def stock_detail(request, stock_code):
    # print("DEBUG token_len in view:", len(config("FINMIND_TOKEN", default="")))
    api = DataLoader()
    token = settings.FINMIND_TOKEN           # ← 統一只從 settings 拿
    api.login_by_token(token)

    # 1) 取日線（你原本的）
    df = api.taiwan_stock_daily(
        stock_id=stock_code,
        start_date="2025-01-01",
        end_date=str(datetime.date.today())
    )
    if df is None or df.empty:
        return render(request, "search_not_found.html", {"query": stock_code})

    current_price = float(df["close"].iloc[-1])

    # 2) 名稱（你原本的）
    info = api.taiwan_stock_info()
    stock_name_arr = info.loc[info["stock_id"] == stock_code, "stock_name"].values
    stock_name = stock_name_arr[0] if len(stock_name_arr) > 0 else stock_code

    # 3) << 新增：抓三個欄位 >>
    inputs = fetch_inputs_finmind_twse(
        stock_code,
        price=current_price,
        token=token                          # ← 一定要傳進去
    )
    metrics = compute_metrics(inputs)
    # print("DEBUG inputs:", inputs.__dict__)

    # 4) 組合給模板（把三個欄位塞進去）
    stock = {
        "名稱": stock_name,
        "代碼": stock_code,
        "目前價格": current_price,
        "市值": metrics["市值"],
        "本益比": metrics["本益比"],
        "股息殖利率": metrics["股息殖利率"],
    }

    # 5) 圖與收藏（你原本的）
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=df['date'], y=df['close'], mode='lines', name='收盤價'))
    fig.update_layout(title=f'{stock_code} 近 30 天股價', xaxis_title='日期', yaxis_title='價格')
    chart = fig.to_html(include_plotlyjs='cdn', full_html=False)

    in_watchlist = False
    if request.user.is_authenticated:
        in_watchlist = Watchlist.objects.filter(
            user=request.user, stock_code=stock_code, collected=True
        ).exists()

    return render(request, "stock_detail.html", {
        "stock": stock,
        "chart": chart,
        "in_watchlist": in_watchlist
    })


# 自選股

@login_required(login_url='/login/')
def my_watchlist(request):
    user = request.user
    watchlist = Watchlist.objects.filter(user=user)

    for item in watchlist:
        # 計算張數
        if item.quantity:
            item.lots = item.quantity / 1000
        else:
            item.lots = 0

        # 取最後一次購買價格
        last_record = BuyRecord.objects.filter(
            user=user, stock_code=item.stock_code
        ).order_by('-created_at').first()
        if last_record:
            item.current_price = last_record.price
        else:
            item.current_price = None

    return render(request, "watchlist.html", {"watchlist": watchlist})

# 新增自選股
@login_required
def add_watchlist(request):
    if request.method == "POST":
        stock_code = request.POST.get("stock_code")
        next_url = request.POST.get("next") or "/watchlist/"

        # 建立或取得 Watchlist
        watch, created = Watchlist.objects.get_or_create(
            user=request.user,
            stock_code=stock_code,
            defaults={"collected": True}
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
        stock_code = request.POST.get("stock_code")
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

# 購買股票
@login_required
def buy_stock(request):
    if request.method == "POST":
        stock_code = request.POST.get("stock_code")
        amount = int(request.POST.get("amount", 0))
        unit = request.POST.get("unit")  # shares 或 lots
        price = Decimal(request.POST.get("price", "0"))

        if unit == "lots":
            amount *= 1000  # 1 張 = 1000 股

        # 更新或建立 Watchlist
        watch, created = Watchlist.objects.get_or_create(
            user=request.user, stock_code=stock_code,
            defaults={"quantity": amount, "average_price": price}
        )
        if not created:
            # 更新平均成本與數量
            old_avg = Decimal(watch.average_price or 0)
            old_qty = watch.quantity or 0
            new_qty = old_qty + amount
            new_avg = (old_avg * old_qty + price * amount) / new_qty

            watch.average_price = new_avg
            watch.quantity = new_qty
            watch.save()

        # 建立購買紀錄
        BuyRecord.objects.create(
            user=request.user,
            stock_code=stock_code,
            quantity=amount,
            price=price
        )

        messages.success(request, f"成功購買 {amount} 股 {stock_code}！")
        return redirect(request.POST.get("next") or "/watchlist/")

# 新聞

def news_list(request):
    news = []
    feed = feedparser.parse("https://news.google.com/rss/search?q=股票&hl=zh-TW&gl=TW&ceid=TW:zh-Hant")
    
    seen_titles = set()
    now = datetime.datetime.now()
    days_limit = 7  # 只抓最近 7 天的新聞
    
    for item in feed.entries:
        # 取得日期
        date_str = getattr(item, "published", getattr(item, "updated", ""))
        try:
            date_obj = datetime.strptime(date_str, "%a, %d %b %Y %H:%M:%S %Z")
        except Exception:
            date_obj = now  # 如果沒日期就當今天
        
        if (now - date_obj).days > days_limit:
            continue  # 超過 7 天就跳過
        
        # 去重
        title = item.title
        if title in seen_titles:
            continue
        seen_titles.add(title)
        
        # 篩選主題: 標題或描述包含「股票」
        description = getattr(item, "summary", "")
        if "股票" not in title and "股票" not in description:
            continue
        
        news.append({
            "title": title,
            "url": item.link,
            "date": date_obj.strftime("%Y-%m-%d")
        })
    
    return render(request, "news.html", {"news": news})

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


