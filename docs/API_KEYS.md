# API Key 申請與免費額度

> 資料於 2026-11-24 直接查閱各官方網站。各家方案隨時可能調整，以官網為準。

| 變數 | 服務 | 申請網址 | 免費額度 | 必填？ |
|---|---|---|---|---|
| `ALPHAVANTAGE_API_KEY` | Alpha Vantage | https://www.alphavantage.co/support/#api-key | **25 次／天**（官網未列每分鐘上限）。付費方案從 75 次／分鐘起 | 選用（`test_setup.py` 沒有 Key 就跳過） |
| `FMP_API_KEY` | Financial Modeling Prep | https://site.financialmodelingprep.com/register（方案：https://site.financialmodelingprep.com/pricing-plans） | **Basic $0：250 次／天**，僅日線歷史、公司簡介與參考資料，30 天流量上限 500MB | 選用（MCP Server 需要） |
| `FINMIND_TOKEN` | FinMind | 在官網登入後於使用者頁面取得：https://finmindtrade.com/analysis/#/account/user（說明：https://finmind.github.io/login/） | **不登入 300 次／小時；註冊會員帶 token 600 次／小時**，46 種資料集，限非商業用途 | 選用 |
| `SEC_USER_AGENT` | SEC EDGAR | 不需要申請。規範：https://www.sec.gov/search-filings/edgar-search-assistance/accessing-edgar-data | 免費。**最多 10 次／秒**，必須帶 User-Agent（官方範例：`Sample Company Name AdminContact@<sample company domain>.com`） | **必填**（財報測試會用到） |

## 填寫方式

```bash
open -e .env   # 用文字編輯器開啟，填好後存檔
```

- 等號前後不要有空格。值本身含空格時要用雙引號，例如 `SEC_USER_AGENT="Jane Doe jane@example.com"`（這樣 Python 讀得到，`source .env` 也不會出錯）。
- `SEC_USER_AGENT` 只能用英文字元：它會放進 HTTP header，中文名字會造成 `UnicodeEncodeError`，請改用英文或拼音。
- `.env` 已列在 `.gitignore`，不會進版本控制。不要把 Key 貼到聊天、issue 或截圖裡。
- 查看 FinMind 目前用量：`FinMindApi().login_by_token(token)` 後讀 `api_usage_limit`（https://finmind.github.io/api_usage_count/）。超過上限時 API 會回 HTTP 402。

## MCP Server 用的 Key

- **Alpha Vantage MCP**：建議用官方遠端 OAuth（`https://mcp.alphavantage.co/mcp`），連線時在瀏覽器授權頁輸入 Key，Key 不會寫進任何設定檔。
- **FMP MCP**：用 `X-API-Key` header。`.mcp.json` 裡寫的是 `${FMP_API_KEY}`，Claude Code 啟動時才從環境變數展開，所以啟動 `claude` 前要先載入 `.env`（見 README）。FMP 會以 401 拒絕還沒展開的佔位字串。
