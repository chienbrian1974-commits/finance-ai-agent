# 資料來源、授權與資料規則

> 內容依 2026-11-27 實測與各來源公開說明整理。各家條款可能變動，正式使用前請再確認。

## Provider 架構

```
業務邏輯 ──► core/providers/registry.py
               ├─ price_provider(inst)      → TaiwanStockProvider / USStockProvider
               ├─ financial_provider(inst)  → FinMindFinancialProvider / SecFinancialProvider
               └─ news_provider(inst)       → FinMindNewsProvider / YahooRssNewsProvider
                          │
                          ▼
             core/providers/sources.py（每個類別只負責一家 API）
                          │
                          ▼
             core/net.py：限流 → 重試 → 快取 → logging（遮蔽密鑰）
```

業務邏輯只依賴 `core/providers/base.py` 的介面（`PriceProvider`、`FinancialDataProvider`、`NewsDataProvider`），不直接呼叫任何一家 API。

## 各市場使用的來源

| 用途 | 台股 | 美股 |
|---|---|---|
| 日 K（主要） | FinMind `TaiwanStockPrice` | Yahoo Finance（yfinance） |
| 日 K（備援） | Yahoo Finance | FinMind `USStockPrice` |
| 交叉比對 | 證交所／櫃買中心 OpenAPI（官方最新收盤） | FinMind；Alpha Vantage（只在測試中使用） |
| 還原權值 | Yahoo 的 Adj Close / Close | Yahoo 的 Adj Close / Close |
| 財報 | FinMind `TaiwanStockFinancialStatements`（季） | SEC EDGAR XBRL companyfacts（10-K 年度） |
| 新聞 | FinMind `TaiwanStockNews`（中文） | Yahoo Finance RSS（英文） |
| 交易日曆 | FinMind `TaiwanStockTradingDate`（備援：XTAI） | exchange_calendars XNYS |
| 代號主檔 | 證交所、櫃買中心 OpenAPI | Nasdaq Trader Symbol Directory |

**為什麼台股不用 Yahoo 當主要來源：** 實測 2330 在 2026-11-26，證交所官方成交量是 45,892,693 股，FinMind 也是 45,892,693 股，但 Yahoo 是 42,245,442 股，少了約 8%，而且 Yahoo 沒有成交金額。兩者的價格一致。

## 授權與使用限制

| 來源 | 需要 Key | 頻率限制（程式內設定） | 授權重點 |
|---|---|---|---|
| 證交所 OpenAPI | 否 | 每 2 秒 1 次 | 政府資料開放授權，需標示來源 |
| 櫃買中心 OpenAPI | 否 | 每 2 秒 1 次 | 政府資料開放授權，需標示來源 |
| FinMind | 選用（`FINMIND_TOKEN`） | 每小時 280 次（未登入的上限是 300 次） | 免費方案限**非商業用途**，需標示「FinMind」 |
| Yahoo Finance | 否 | 每 0.5 秒 1 次 | 非官方 API，僅供個人研究；不保證可用性 |
| SEC EDGAR | 否，但必須提供 `SEC_USER_AGENT` | 每秒最多 10 次 | 公開資料；未設定 User-Agent 時直接回報「無法取得」 |
| Alpha Vantage | 是（`ALPHAVANTAGE_API_KEY`） | 每天 25 次 | 只在 `RUN_ALPHAVANTAGE=1` 的測試中使用，網頁不會呼叫 |
| Nasdaq Trader | 否 | 每秒 1 次 | 公開代號清單 |

程式不會嘗試繞過任何付費或存取限制：達到上限時會直接回報「已達請求上限，請稍後再試」。

## 正規化欄位（`core/providers/base.py` 的 `BAR_COLUMNS`）

| 欄位 | 說明 |
|---|---|
| 索引 `session_date` | 交易所當地日期（帶時區，例如 Asia/Taipei） |
| `open` `high` `low` `close` | 交易幣別；`Bars.frame` 是原始價，`Bars.adjusted` 是還原價 |
| `volume` | **成交股數（股）**。台股 1 張 = 1000 股，顯示時再換算 |
| `turnover` | 成交金額（交易幣別）。來源沒有提供時為空值，**不會用價格乘成交量估算** |
| `ts_utc` | 資料時間（UTC）。已收盤的 K 棒是收盤時刻（台股 13:30、美股 16:00 當地時間），盤中的 K 棒是抓取時刻 |
| `kind` | 時效類別，見下表 |

股票本身的屬性（代號、公司名稱、市場、交易所、幣別、時區）放在 `Bars.instrument`，資料來源和抓取時間放在 `Bars.source`、`Bars.fetched_at`。

## 資料時效類別（`DataKind`）

| 類別 | 什麼時候會出現 |
|---|---|
| 即時 | 交易所授權的即時報價。**目前沒有任何來源提供**；如果出現，品質檢查會直接報錯 |
| 延遲 | 盤中、尚未收盤的 K 棒（免費來源都有延遲） |
| 歷史 | 已收盤、已結算的資料 |
| 無法取得 | 來源失敗、沒有這筆資料，或沒有提供這個欄位 |

## 品質檢查（`core/quality.py`）

缺漏交易日（依交易日曆）、重複日期、開高低收缺值、開高低收不合理、0 或負價格、負成交量、異常跳動（台股一般股超過 10%、其他超過 40%，用還原價計算）、資料過期、沒有成交金額。

**原則：** 只回報問題，不補資料、不改數字。重複日期保留最後一筆，並在畫面上說明。

## API Key 與安全

- Key 只從伺服器端的環境變數或 `.env` 讀取（`core/config_bridge.py`）。Streamlit Cloud 的 Secrets 頂層鍵會自動變成環境變數。
- `.env` 已列在 `.gitignore`。測試 `test_secrets_are_not_committed` 會檢查 git 追蹤的檔案裡有沒有 `.env` 裡的 Key。
- log 會遮蔽網址裡的 `apikey=`、`token=` 等參數（`core.net.redact`），錯誤訊息也一樣。
- Streamlit 的程式只在伺服器執行，Key 不會傳到瀏覽器。
