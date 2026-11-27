"""各家資料來源的實作（每個類別只負責一家 API）。

業務邏輯不要直接用這裡的類別，請透過 registry.py 取得組合好的 Provider。
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

import feedparser
import pandas as pd

from ..config_bridge import get_key
from ..instruments import Instrument
from ..net import ProviderError, cached, http_get, limiter, log
from ..normalize import apply_adjustment, finalize, from_finmind, from_tpex_row, from_twse_row, from_yahoo
from ..validation import local_now, market_phase
from .base import Bars, DataKind, FinancialDataProvider, FinancialItem, Financials, NewsDataProvider, NewsItem, PriceProvider, Quote


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _partial_session(inst: Instrument, last: date | None) -> date | None:
    """最新一根是今天、而且現在是盤中 → 這根是尚未收盤的延遲資料。"""
    if last is not None and last == local_now(inst).date() and market_phase(inst) == "盤中":
        return last
    return None


# ---- Yahoo Finance（台美股日 K、還原係數）----------------------------------------
class YahooPriceProvider(PriceProvider):
    name = "Yahoo Finance"

    @cached(ttl=900)
    def get_daily(self, inst: Instrument, start: date, end: date | None = None) -> Bars:
        import yfinance as yf

        limiter.acquire("yahoo")
        try:
            hist = yf.Ticker(inst.yahoo).history(start=start, end=(end or date.today()) + timedelta(days=1),
                                                 auto_adjust=False)
        except Exception as e:  # noqa: BLE001  yfinance 會丟各種例外
            raise ProviderError(self.name, f"抓取失敗（{type(e).__name__}）") from e
        fetched = _now()
        raw, factor = from_yahoo(inst, hist)
        notes = ["Yahoo Finance 為非官方資料來源，僅供個人研究使用。"]
        if inst.market == "TW":
            notes.append("Yahoo 的台股成交量不是交易所官方總量（實測比證交所少約 6–8%），且沒有成交金額。")
        if raw.empty:
            return Bars(inst, finalize(inst, raw, fetched), None, self.name, fetched, notes=notes)
        complete = raw.dropna(subset=["open", "high", "low", "close"])
        if len(complete) < len(raw):
            dropped = [f"{d:%Y-%m-%d}" for d in raw.index.difference(complete.index)]
            notes.append(f"Yahoo 在 {', '.join(dropped)} 的資料不完整，已排除。")
        frame = finalize(inst, complete, fetched, _partial_session(inst, complete.index[-1].date() if len(complete) else None))
        return Bars(inst, frame, apply_adjustment(frame, factor), self.name, fetched, notes=notes)

    @cached(ttl=900)
    def adjustment_factor(self, inst: Instrument, start: date, end: date | None = None) -> pd.Series | None:
        """只取還原係數（給其他來源的日 K 做除權息調整用）。"""
        import yfinance as yf

        limiter.acquire("yahoo")
        try:
            hist = yf.Ticker(inst.yahoo).history(start=start, end=(end or date.today()) + timedelta(days=1),
                                                 auto_adjust=False)
        except Exception as e:  # noqa: BLE001
            log.warning("Yahoo 還原係數取得失敗：%s", type(e).__name__)
            return None
        return from_yahoo(inst, hist)[1]


# ---- FinMind（台股日 K、財報、新聞；美股日 K）--------------------------------------
FINMIND_URL = "https://api.finmindtrade.com/api/v4/data"


def _finmind(dataset: str, **params) -> list[dict]:
    token = get_key("FINMIND_TOKEN")
    headers = {"Authorization": f"Bearer {token}"} if token else None
    resp = http_get("finmind", FINMIND_URL, params={"dataset": dataset, **params}, headers=headers, timeout=60)
    body = resp.json()
    if body.get("status") != 200:
        # 402 = 超過 FinMind 免費額度
        raise ProviderError("FinMind", f"{dataset}：{body.get('msg', '未知錯誤')}（status {body.get('status')}）")
    return body.get("data", [])


class FinMindPriceProvider(PriceProvider):
    name = "FinMind"

    @cached(ttl=900)
    def get_daily(self, inst: Instrument, start: date, end: date | None = None) -> Bars:
        if inst.market != "TW":
            raise ProviderError(self.name, "這個 Provider 只處理台股")
        params = {"data_id": inst.code, "start_date": f"{start:%Y-%m-%d}"}
        if end:
            params["end_date"] = f"{end:%Y-%m-%d}"
        rows = _finmind("TaiwanStockPrice", **params)
        fetched = _now()
        raw = from_finmind(inst, rows)
        # FinMind 用 0 表示當天無成交（例如暫停交易），不是真的價格 0
        if not raw.empty:
            raw = raw[(raw[["open", "high", "low", "close"]] > 0).all(axis=1)]
        frame = finalize(inst, raw, fetched)
        notes = ["FinMind 資料整理自證交所／櫃買中心公開資料，成交量與成交金額為官方數字。資料授權：非商業用途，需標示來源 FinMind。"]
        return Bars(inst, frame, None, self.name, fetched, notes=notes)


class FinMindFinancialProvider(FinancialDataProvider):
    name = "FinMind（綜合損益表）"
    METRICS = {"Revenue": ("revenue", "TWD"), "GrossProfit": ("gross_profit", "TWD"),
               "OperatingIncome": ("operating_income", "TWD"), "IncomeAfterTaxes": ("net_income", "TWD"),
               "EPS": ("eps", "TWD/股")}

    @cached(ttl=43200)
    def get_financials(self, inst: Instrument) -> Financials:
        if inst.market != "TW":
            raise ProviderError(self.name, "這個 Provider 只處理台股")
        start = date.today().replace(year=date.today().year - 3, month=1, day=1)
        rows = _finmind("TaiwanStockFinancialStatements", data_id=inst.code, start_date=f"{start:%Y-%m-%d}")
        items = []
        for r in rows:
            if r["type"] in self.METRICS:
                metric, unit = self.METRICS[r["type"]]
                pe = date.fromisoformat(r["date"])
                items.append(FinancialItem(pe, f"Q{(pe.month - 1) // 3 + 1}", metric, float(r["value"]), unit, self.name))
        if not items:
            why = "ETF 沒有公司財報" if inst.asset_type.startswith("etf") else "FinMind 沒有這檔的財報資料"
            return Financials(inst, [], self.name, _now(), DataKind.UNAVAILABLE, f"{why}。")
        note = "季度財報數字；單位為新台幣元（EPS 為元/股）。資料授權：非商業用途，需標示來源 FinMind。"
        return Financials(inst, items, self.name, _now(), DataKind.HISTORICAL, note)


class FinMindNewsProvider(NewsDataProvider):
    name = "FinMind（台股相關新聞）"

    @cached(ttl=900)
    def get_news(self, inst: Instrument, limit: int = 10) -> list[NewsItem]:
        if inst.market != "TW":
            raise ProviderError(self.name, "這個 Provider 只處理台股")
        items: list[NewsItem] = []
        today = local_now(inst).date()
        for back in range(3):  # FinMind 每次只能查一天，往回最多查 3 天
            d = today - timedelta(days=back)
            for r in _finmind("TaiwanStockNews", data_id=inst.code, start_date=f"{d:%Y-%m-%d}"):
                ts = pd.Timestamp(r["date"]).tz_localize(inst.timezone).tz_convert("UTC").to_pydatetime() if r.get("date") else None
                items.append(NewsItem(r.get("title", ""), r.get("link", ""), ts, r.get("source") or self.name))
            if len(items) >= limit:
                break
        items.sort(key=lambda n: n.published_utc or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
        return items[:limit]


# ---- 證交所 / 櫃買中心 OpenAPI（台股最新一日官方收盤）---------------------------
class TwOfficialDailyProvider:
    """官方「最近一個交易日」全市場收盤資料（一次請求拿到所有股票）。只有最新一天，沒有歷史。"""

    name = "證交所／櫃買中心 OpenAPI"
    TWSE = "https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL"
    TPEX = "https://www.tpex.org.tw/openapi/v1/tpex_mainboard_daily_close_quotes"

    @cached(ttl=1800)
    def _table(self, exchange: str) -> dict[str, dict]:
        if exchange == "TWSE":
            rows = http_get("twse", self.TWSE, timeout=60).json()
            return {r["Code"].strip(): from_twse_row(r) for r in rows}
        rows = http_get("tpex", self.TPEX, timeout=120).json()
        return {r["SecuritiesCompanyCode"].strip(): from_tpex_row(r) for r in rows}

    def get_quote(self, inst: Instrument) -> Quote:
        if inst.market != "TW":
            raise ProviderError(self.name, "只處理台股")
        row = self._table(inst.exchange).get(inst.code)
        src = "證交所 OpenAPI" if inst.exchange == "TWSE" else "櫃買中心 OpenAPI"
        if not row or pd.isna(row["close"]):
            return Quote(inst, None, None, row["session_date"] if row else None, None, DataKind.UNAVAILABLE, src,
                         note="官方資料當天沒有這檔的成交紀錄")
        from ..normalize import session_close_utc

        prev = row["close"] - row["change"] if pd.notna(row["change"]) else None
        return Quote(inst, row["close"], prev, row["session_date"], session_close_utc(inst, row["session_date"]),
                     DataKind.HISTORICAL, src, row["volume"], row["turnover"],
                     note="官方最近一個交易日的收盤資料（盤後公布，不是即時）")


# ---- SEC EDGAR（美股財報）-------------------------------------------------------
class SecFinancialProvider(FinancialDataProvider):
    name = "SEC EDGAR（XBRL companyfacts）"
    TICKERS = "https://www.sec.gov/files/company_tickers.json"
    FACTS = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json"
    CONCEPTS = {  # metric: 依序嘗試的 us-gaap 概念
        "revenue": ["RevenueFromContractWithCustomerExcludingAssessedTax", "Revenues", "SalesRevenueNet"],
        "net_income": ["NetIncomeLoss"],
        "operating_income": ["OperatingIncomeLoss"],
        "eps": ["EarningsPerShareDiluted", "EarningsPerShareBasic"],
    }

    def _headers(self) -> dict:
        ua = get_key("SEC_USER_AGENT")
        if not ua:
            raise ProviderError(self.name, "伺服器未設定 SEC_USER_AGENT（SEC 規定必須提供聯絡資訊），無法取得美股財報")
        return {"User-Agent": ua}

    @cached(ttl=86400)
    def _cik_map(self) -> dict[str, int]:
        data = http_get("sec", self.TICKERS, headers=self._headers()).json()
        return {v["ticker"].upper(): int(v["cik_str"]) for v in data.values()}

    @cached(ttl=43200)
    def get_financials(self, inst: Instrument) -> Financials:
        if inst.market != "US":
            raise ProviderError(self.name, "只處理美股")
        cik = self._cik_map().get(inst.yahoo.upper()) or self._cik_map().get(inst.code.upper())
        if not cik:
            return Financials(inst, [], self.name, _now(), DataKind.UNAVAILABLE, "SEC 沒有這檔的公司代碼（ETF 通常沒有財報）")
        try:
            facts = http_get("sec", self.FACTS.format(cik=cik), headers=self._headers()).json().get("facts", {}).get("us-gaap", {})
        except ProviderError as e:
            if "404" in e.reason:
                return Financials(inst, [], self.name, _now(), DataKind.UNAVAILABLE,
                                  "SEC 沒有這檔的 XBRL 財報資料（ETF、信託等通常沒有）。")
            raise
        items: list[FinancialItem] = []
        for metric, concepts in self.CONCEPTS.items():
            for c in concepts:
                units = facts.get(c, {}).get("units", {})
                if not units:
                    continue
                unit, entries = next(iter(units.items()))
                seen = set()
                # 只取 10-K 的完整年度數字（期間約一年），避免把季報累計數誤當年度
                for e in entries:
                    if e.get("form") != "10-K" or e.get("fp") != "FY" or "start" not in e:
                        continue
                    days = (date.fromisoformat(e["end"]) - date.fromisoformat(e["start"])).days
                    if not 350 <= days <= 380 or e["end"] in seen:
                        continue
                    seen.add(e["end"])
                    items.append(FinancialItem(date.fromisoformat(e["end"]), "FY", metric, float(e["val"]), unit,
                                               self.name, date.fromisoformat(e["filed"]) if e.get("filed") else None))
                break
        items = [i for i in items if i.period_end.year >= date.today().year - 5]
        return Financials(inst, items, self.name, _now(), DataKind.HISTORICAL if items else DataKind.UNAVAILABLE,
                          "年度數字取自 10-K（完整會計年度）。")


# ---- Yahoo RSS（美股新聞）--------------------------------------------------------
class YahooRssNewsProvider(NewsDataProvider):
    name = "Yahoo Finance RSS"
    URL = "https://feeds.finance.yahoo.com/rss/2.0/headline"

    @cached(ttl=600)
    def get_news(self, inst: Instrument, limit: int = 10) -> list[NewsItem]:
        resp = http_get("yahoo", self.URL, params={"s": inst.yahoo, "region": "US", "lang": "en-US"})
        feed = feedparser.parse(resp.content)
        out = []
        for e in feed.entries[:limit]:
            ts = None
            if e.get("published"):
                try:
                    ts = parsedate_to_datetime(e["published"]).astimezone(timezone.utc)
                except (TypeError, ValueError):
                    ts = None
            out.append(NewsItem(e.get("title", ""), e.get("link", ""), ts, self.name))
        return out


# ---- Alpha Vantage（只用於交叉驗證，不在網頁上自動呼叫）--------------------------
class AlphaVantagePriceProvider(PriceProvider):
    """免費每天只有 25 次，只在測試／驗證腳本中使用，不接到網頁上。"""

    name = "Alpha Vantage"

    @cached(ttl=3600)
    def get_daily(self, inst: Instrument, start: date, end: date | None = None) -> Bars:
        key = get_key("ALPHAVANTAGE_API_KEY")
        if not key:
            raise ProviderError(self.name, "伺服器未設定 ALPHAVANTAGE_API_KEY")
        if inst.market != "US":
            raise ProviderError(self.name, "只用於美股驗證")
        body = http_get("alphavantage", "https://www.alphavantage.co/query",
                        params={"function": "TIME_SERIES_DAILY", "symbol": inst.yahoo, "apikey": key}).json()
        ts = body.get("Time Series (Daily)")
        if not ts:
            raise ProviderError(self.name, str(body.get("Note") or body.get("Information") or body.get("Error Message") or "沒有資料")[:200])
        from ..normalize import session_index

        df = pd.DataFrame.from_dict(ts, orient="index").astype(float)
        df = df.rename(columns={"1. open": "open", "2. high": "high", "3. low": "low", "4. close": "close", "5. volume": "volume"})
        df.index = session_index(inst, df.index)
        df = df[(df.index.date >= start) & (df.index.date <= (end or date.today()))]
        fetched = _now()
        return Bars(inst, finalize(inst, df, fetched, _partial_session(inst, df.index.max().date() if len(df) else None)),
                    None, self.name, fetched)
