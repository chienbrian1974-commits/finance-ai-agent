# 金融市場分析 AI 代理開發環境

用於市場數據分析、財報檢索、技術指標計算與新聞追蹤的 Python 環境。macOS Intel（x86_64），Python 3.12.16（透過 uv 安裝）。

## 專案結構

```
.
├── .venv/              # 虛擬環境（不進 git）
├── .env                # 你的 API Key（不進 git，權限 600）
├── .env.example        # 變數名稱與說明（沒有值）
├── .gitignore
├── .mcp.json           # Claude Code 專案層級 MCP Server 設定（不含 Key 明文）
├── app.py              # 市場儀表板（Streamlit 網頁 App）：儀表板＋技術分析教學兩個分頁
├── lessons.py          # 技術分析教學分頁（8 堂課、自動解讀、小測驗）
├── ui.py               # 兩個分頁共用的介面：選股、資料狀態列
├── core/               # 跨市場資料層（不依賴 Streamlit）
│   ├── instruments.py  #   統一資料模型 Instrument、代號解析（代號／名稱）
│   ├── symbols_update.py # 從官方來源重建代號主檔
│   ├── marketdata.py   #   抓日 K，產生原始價與還原價
│   ├── validation.py   #   資料驗證：缺值、盤中、過期（含交易所假日）、異常跳動
│   └── indicators.py   #   技術指標
├── data/symbols/       # 代號主檔（證交所、櫃買中心、Nasdaq Trader 官方清單）
├── tests/              # 離線單元測試（pytest）
├── config.py           # 讀取 .env：get_key(name, required=False)
├── requirements.txt    # 固定版本號
├── test_setup.py       # 會連網的環境檢查腳本（不是單元測試）
├── docs/API_KEYS.md    # 各 Key 的申請網址與免費額度
├── data/raw/           # 原始資料（不進 git）
└── outputs/            # 圖表等輸出（不進 git）
```

## 啟用虛擬環境

```bash
source .venv/bin/activate
```

也可以不啟用，直接用 `.venv/bin/python`。要在新電腦重建環境：

```bash
uv venv .venv --python 3.12 && uv pip install --python .venv/bin/python -r requirements.txt
```

## 填寫 .env

```bash
open -e .env
```

各 Key 的申請方式見 [docs/API_KEYS.md](docs/API_KEYS.md)。`SEC_USER_AGENT` 必填，值含空格要加雙引號：`SEC_USER_AGENT="Jane Doe jane@example.com"`。其他 Key 都是選用。

程式中讀取：

```python
from config import get_key
ua = get_key("SEC_USER_AGENT", required=True)   # 缺值會丟出 MissingKeyError，並說明怎麼補
```

## 市場儀表板（網頁 App）

```bash
.venv/bin/streamlit run app.py
```

會自動打開瀏覽器到 http://localhost:8501；要關掉就在終端機按 Ctrl+C。

- 左側輸入代號：美股輸入代號（AAPL、NVDA），台股輸入數字（2330、0050；先試上市 `.TW`，再試上櫃 `.TWO`）
- K 線＋SMA20／SMA60、成交量、RSI(14)、MACD；顏色依台股慣例紅漲綠跌
- Yahoo Finance 新聞標題＋VADER 情緒分數（只評英文標題）、BTC/USDT 即時價
- 資料快取 5 分鐘，左側「重新整理資料」可強制更新
- 盤中顯示的是即時價；Yahoo 偶爾會給當天不完整的資料（收盤價空白），這時會顯示前一個完整交易日

### 技術分析教學分頁

在上方切到「🎓 技術分析教學」，輸入要練習的代號（預設 2330），照順序上 8 堂課：

1. K 線　2. 趨勢、支撐與壓力　3. 移動平均線　4. 成交量　5. RSI　6. MACD　7. 布林通道　8. 綜合分析

每一課都是「觀念 → 怎麼畫 → 用你選的股票示範 → 換你判斷（小測驗）」。趨勢、均線、布林通道的圖表右上角有畫線工具，可以自己畫趨勢線練習。解讀文字是依固定規則自動產生的教學範例，不是投資建議。

## 跨市場資料規則

| | 台股（TW） | 美股（US） |
|---|---|---|
| 代號範例 | 2330、6488（上櫃）、00631L、00679B、2881A | AAPL、BRK.B（也可輸入 BRK-B）、SPY |
| 交易所 | TWSE 上市、TPEX 上櫃 | NASDAQ、NYSE、NYSE Arca、Cboe BZX… |
| 幣別／時區 | TWD／Asia/Taipei | USD／America/New_York |
| 交易時段 | 09:00–13:30 | 09:30–16:00 |
| 交易所行事曆 | XTAI | XNYS |

- 代號一律先查官方主檔（`data/symbols/`），名稱、交易所、類型都來自官方清單；主檔查不到時才改問 Yahoo，並標示「未驗證」。
- 也可以輸入公司名稱（例如「台積電」），或用 `TW:`、`US:` 指定市場。
- 圖表和指標預設使用**還原價**（除權息調整），可在側邊欄切換成原始價。
- 每個頁面都會標示：資料來源、抓取時間（台北時間）、最新 K 棒日期（交易所當地日期）、市場狀態；資料不完整、盤中、過期、異常跳動都會明確提示，不會默默修正或補資料。

更新代號主檔（新上市、下市）：

```bash
.venv/bin/python -m core.symbols_update
```

## 單元測試（離線）

```bash
.venv/bin/python -m pytest
```

測試涵蓋：台美股 20 種代號寫法的解析、台股代號分類、還原權值、盤中／過期／交易所假日判斷、異常跳動、台北時間顯示。不需要網路。

## 執行測試

```bash
.venv/bin/python test_setup.py
```

測試項目：美股（AAPL）、台股（2330，與 FinMind 交叉比對）、技術指標（ta 與 pandas_ta 比對）、加密貨幣（ccxt）、SEC 財報、Yahoo RSS 新聞＋VADER 情緒、Alpha Vantage（有 Key 才測）、plotly 圖表（`outputs/test_chart.html`）。任何一項失敗時 exit code 為 1。

## MCP Server（Claude Code，project scope，記錄在 `.mcp.json`）

| 名稱 | 連線 | 驗證方式 |
|---|---|---|
| `alphavantage` | `https://mcp.alphavantage.co/mcp`（官方遠端，HTTP） | OAuth：在 Claude Code 執行 `/mcp` → 選 alphavantage → 在瀏覽器授權頁輸入 Key |
| `fmp` | `https://financialmodelingprep.com/mcp`（官方遠端，HTTP） | Header `X-API-Key: ${FMP_API_KEY}`，啟動時從環境變數展開 |

啟動 Claude Code 前先把 `.env` 載入環境，FMP 才拿得到 Key：

```bash
set -a; source .env; set +a; claude
```

第一次在這個資料夾啟動 `claude` 時，會詢問是否核准 `.mcp.json` 裡的伺服器，選核准即可。用以下指令檢查狀態：

```bash
claude mcp list
```

其他資料來源：

- Yahoo Finance 沒有官方 MCP，改用 `yfinance` 套件。
- 網路新聞搜尋用 Claude Code 內建的 WebSearch / WebFetch。

## 安裝備註

- `ccxt` 依賴的 `cryptography` 最新版（50.x）沒有 Intel Mac 的預編譯 wheel，會改從原始碼編譯（需要 Rust）而失敗。因此改用 `--only-binary cryptography`，裝的是 48.0.1。日後升級時也要加這個參數。
- `mplfinance` 0.12.10b0 用到 `six`，但沒有宣告這個依賴，所以另外補裝了 `six`。
- `pandas_ta` 0.4.71b0 可以正常匯入，與 `ta` 算出的 RSI/MACD 完全一致。
