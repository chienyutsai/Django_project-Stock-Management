# 📈 股票管理分析系統 (Stock Management and Analysis System)

這是一個全方位的股票管理與分析平台，結合了**即時看盤**、**模擬交易**、**新聞情緒分析**、**社群討論**。使用者可以輕鬆建立自己的投資組合，並模擬、紀錄交易決策。

## ✨ 核心功能 (Features)

### 1. 📊 即時大盤與股票資訊

* **首頁概覽**：首頁固定顯示大盤與精選股票資訊。
* **互動式圖表**：K線圖與走勢圖皆支援滑鼠拖曳與滾輪放大縮小，方便查看細節。
* **精準搜尋**：支援輸入股票代碼或名稱進行搜尋（台股支援中文搜尋，美股價格自動換算為 USD）。

### 2. 👤 會員與資產管理

* **會員系統**：完整的註冊、登入機制。支援修改使用者名稱、密碼變更以及帳號永久刪除（防呆確認）。
* **自訂自選股**：可將喜愛的股票加入收藏，或透過「模擬購買」加入自選股清單。
* **彈性清單管理**：自選股清單支援拖曳自定義排序或依預設選項排序。
* **模擬交易與紀錄**：支援股票買賣與刪除功能，並自動生成交易紀錄（完整記錄買賣時間、股數、價格與餘額）。

### 3. 📰 新聞與情緒分析 (Sentiment Analysis)

* **媒體情緒篩選**：自動分析新聞情緒，提供「正向」、「中性」、「負向」三種情緒標籤篩選。
* **智慧排序**：可依照「日期」或「信心分數」對新聞列表進行排序。

### 4. 💬 社群討論區 (Community Forum)

* **分類與檢索**：貼文分為「分享」、「分析」、「問答」三大類，並支援關鍵字搜尋，方便快速檢索。
* **互動交流**：支援發布貼文（可標註股票代碼）、留言以及對喜歡的內容「按讚」。
* **權限控管**：任何人皆可管理自己的留言；貼文作者擁有最高權限，可刪除整篇貼文及底下的所有留言。

---

## 🛠️ 技術棧 (Tech Stack)

本專案採用前後端分離架構開發，並結合資料科學與自然語言處理技術：

* **Frontend (前端):** HTML5, CSS3, JavaScript, Bootstrap (響應式網頁設計), Plotly.js / Chart.js (負責K線圖、技術指標與互動式數據視覺化渲染)
* **Backend (後端):** Python (核心開發語言), Django (MVT 架構、RESTful API、會員與業務邏輯處理)
* **Database (資料庫):** MySQL / SQLite (搭配 Django ORM 進行使用者數據、自選股與交易紀錄的高效管理)
* **NLP (自然語言處理):** GNews API (獲取即時新聞), jieba, opencc-python-reimplemented (中文斷詞與情緒分析，標示市場輿情正負向)

---


## 🚀 本地端安裝與執行 (Installation)

本專案後端基於 Django 框架開發，並整合了多個金融數據 API 與新聞情緒分析模組。請依照以下步驟於本地端建立開發環境：

### 0. 系統需求 (Prerequisites)

* Python 3.8+ (建議版本)
* Git

### 1. 複製專案 (Clone the repository)

```bash
git clone https://github.com/chienyutsai/Django_project-Stock-Management.git
cd Django_project-Stock-Management
```

### 2. 建立虛擬環境與安裝依賴套件 (Install Dependencies)

強烈建議使用虛擬環境 (Virtual Environment) 來管理專案套件，避免與系統環境衝突。

```bash
# 建立虛擬環境 (名稱為 venv)
python -m venv venv

# 啟動虛擬環境 (Windows 系統)
venv\Scripts\activate
# 啟動虛擬環境 (macOS/Linux 系統)
source venv/bin/activate

# 安裝專案所需的所有套件
cd stock
pip install -r requirements.txt
```

若啟動虛擬環境時出現錯誤，可先執行以下程式碼，臨時允許目前視窗執行指令碼後，再重新啟動虛擬環境。

```bash
Set-ExecutionPolicy RemoteSigned -Scope Process
```

> **💡 核心套件包含**：`Django`、`python-decouple` (環境變數管理)、`requests` (API 請求)、`FinMind` (台股數據)、`Plotly` (圖表繪製)、`feedparser` (新聞 RSS)、`torch` 與 `transformers` (AI 預測模型)、`jieba` 與 `opencc-python-reimplemented` (文本處理) 等。
> 
> 

### 3. 環境變數設定 (Environment Variables Setup)

本專案透過 `python-decouple` 套件來保護敏感的 API 金鑰。請在專案的**根目錄**，也就是與manage.py同目錄處，建立一個名為 `.env` 的檔案，並填入以下必須的外部 API 金鑰（您可以至各平台免費註冊獲取）：

```env
# .env 檔案內容範例
FINMIND_TOKEN=你的_FinMind_API_Token         # 用於獲取台股每日報價與公司資訊[cite: 4]
TWELVE_DATA_API_KEY=你的_TwelveData_API_Key  # 用於獲取美股/外國股票報價與歷史走勢[cite: 4]
FINNHUB_API_KEY=你的_Finnhub_API_Key         # 用於獲取外國股票基本面與搜尋功能[cite: 4]
ALPHAVANTAGE_API_KEY=你的_AlphaVantage_Key   # 備用的美股報價與基本面資料來源[cite: 4]
```

### 4. 資料庫遷移 (Database Migrations)

專案內建了會員自選股 (`Watchlist`)、虛擬交易紀錄 (`BuyRecord`)、社群討論區貼文 (`Post`) 與留言 (`Comment`)，以及股價快取 (`StockSnapshot`) 等多張資料表。首次啟動前請先執行遷移指令以初始化 SQLite 資料庫：

```bash
python manage.py makemigrations
python manage.py migrate
```

### 5. 建立超級管理員 (Create Superuser) - 可選

若您需要登入 Django 內建的後台管理介面 (`/admin`) 來管理會員、貼文或留言，請建立一組管理員帳號：

```bash
python manage.py createsuperuser
```

*(依序輸入想設定的使用者名稱、信箱與密碼即可)*

### 6. 啟動本地端伺服器 (Run Local Server)

所有設定完成後，即可啟動 Django 開發伺服器：

```bash
python manage.py runserver
```

伺服器啟動後，請打開瀏覽器並前往 `[http://127.0.0.1:8000/](http://127.0.0.1:8000/)` 即可開始體驗完整的網頁功能。

---

## 💡 使用指南 (Usage)

1. **註冊/登入**：首次使用請點擊右上角註冊帳號，即可解鎖「自選股」與「模擬交易」功能。
2. **加入自選股**：在頂部搜尋欄輸入標的（如 `3008` 或 `台積電`），進入詳情頁後點擊「加入收藏」。
3. **情緒分析新聞**：切換至「新聞」頁籤，可透過下拉選單過濾出市場的正向/負向消息。

---
