"""跨市場統一資料模型與代號解析。

台股與美股的代號格式、交易所、幣別、時區、交易時段都不同，
這裡用一個 Instrument 統一描述，並記錄各資料來源要用的代號。
"""
from __future__ import annotations

import csv
import re
from dataclasses import dataclass, field
from datetime import time
from functools import lru_cache
from pathlib import Path
from typing import Literal

SYMBOL_DIR = Path(__file__).resolve().parent.parent / "data" / "symbols"

Market = Literal["TW", "US"]


@dataclass(frozen=True)
class MarketInfo:
    currency: str
    timezone: str
    open_time: time
    close_time: time
    lot_size: int
    price_limit_pct: float | None  # 每日漲跌幅限制；美股沒有
    calendar: str                  # exchange_calendars 的交易所行事曆代碼（含國定假日）


MARKETS: dict[str, MarketInfo] = {
    "TW": MarketInfo("TWD", "Asia/Taipei", time(9, 0), time(13, 30), 1000, 10.0, "XTAI"),
    "US": MarketInfo("USD", "America/New_York", time(9, 30), time(16, 0), 1, None, "XNYS"),
}

EXCHANGE_LABEL = {"TWSE": "上市（證交所）", "TPEX": "上櫃（櫃買中心）"}

ASSET_LABEL = {
    "stock": "股票", "etf": "ETF", "etf_leveraged": "槓桿型 ETF", "etf_inverse": "反向型 ETF",
    "etf_bond": "債券 ETF", "etf_active": "主動式 ETF", "etf_futures": "期貨型 ETF", "preferred": "特別股",
}


@dataclass(frozen=True)
class Instrument:
    symbol: str          # 內部唯一代號，例如 "TW:2330"、"US:BRK.B"
    code: str            # 交易所原始代號，例如 "2330"、"BRK.B"
    company_name: str
    market: Market
    exchange: str        # TWSE / TPEX / NASDAQ / NYSE / NYSE Arca …
    asset_type: str      # stock / etf / etf_leveraged / … / preferred
    verified: bool = True  # False = 不在官方主檔，只是 Yahoo 查得到
    provider_ids: dict = field(default_factory=dict, compare=False, hash=False)

    @property
    def info(self) -> MarketInfo:
        return MARKETS[self.market]

    @property
    def currency(self) -> str:
        return self.info.currency

    @property
    def timezone(self) -> str:
        return self.info.timezone

    @property
    def yahoo(self) -> str:
        return self.provider_ids["yahoo"]

    @property
    def label(self) -> str:
        return f"{self.code} {self.company_name}".strip()

    @property
    def asset_label(self) -> str:
        return ASSET_LABEL.get(self.asset_type, self.asset_type)

    @property
    def exchange_label(self) -> str:
        return EXCHANGE_LABEL.get(self.exchange, self.exchange)


def yahoo_id(market: str, code: str, exchange: str) -> str:
    if market == "TW":
        return f"{code}.TW" if exchange == "TWSE" else f"{code}.TWO"
    # Yahoo 美股：BRK.B → BRK-B；特別股 ABR$D → ABR-PD
    return code.replace(".", "-").replace("$", "-P")


def make(market: str, code: str, name: str, exchange: str, asset_type: str, verified: bool = True) -> Instrument:
    ids = {"yahoo": yahoo_id(market, code, exchange)}
    if market == "TW":
        ids["finmind"] = code
    return Instrument(f"{market}:{code}", code, name, market, exchange, asset_type, verified, ids)  # type: ignore[arg-type]


@lru_cache(maxsize=1)
def load_master(directory: str | None = None) -> dict[str, dict[str, Instrument]]:
    """讀取 data/symbols/*.csv。回傳 {"TW": {code: Instrument}, "US": {...}}。"""
    base = Path(directory) if directory else SYMBOL_DIR
    out: dict[str, dict[str, Instrument]] = {}
    for market in MARKETS:
        path = base / f"{market.lower()}.csv"
        rows = csv.DictReader(path.open(encoding="utf-8")) if path.exists() else []
        out[market] = {r["code"]: make(market, r["code"], r["name"], r["exchange"], r["asset_type"]) for r in rows}
    return out


def master_updated_at(directory: str | None = None) -> str:
    p = (Path(directory) if directory else SYMBOL_DIR) / "UPDATED_AT"
    return p.read_text(encoding="utf-8").strip() if p.exists() else "未知"


def _us_key(code: str) -> str:
    """美股代號比對用：BRK-B、BRK/B、brk.b 都視為 BRK.B。"""
    return re.sub(r"[-/]", ".", code.upper())


@dataclass
class Resolution:
    instrument: Instrument | None
    candidates: list[Instrument]  # 名稱搜尋時可能有多筆
    note: str = ""


def resolve(raw: str, master: dict[str, dict[str, Instrument]] | None = None, limit: int = 8) -> Resolution:
    """把使用者輸入轉成 Instrument。

    支援：2330、00631L、6488（自動判斷上市/上櫃）、AAPL、BRK.B / BRK-B、
    「TW:2330」「US:AAPL」指定市場、以及公司名稱關鍵字（台積電、apple）。
    """
    master = master or load_master()
    s = raw.strip()
    if not s:
        return Resolution(None, [], "請輸入代號或名稱")

    forced = None
    m = re.fullmatch(r"(?i)(TW|US)\s*[:：]\s*(.+)", s)
    if m:
        forced, s = m.group(1).upper(), m.group(2).strip()

    up = s.upper()
    markets = [forced] if forced else (["TW", "US"] if up[:1].isdigit() else ["US", "TW"])
    for mk in markets:
        book = master.get(mk, {})
        if mk == "TW" and up in book:
            return Resolution(book[up], [])
        if mk == "US":
            key = _us_key(up)
            if key in book:
                return Resolution(book[key], [])

    # 名稱搜尋（中文名、英文名都可以）
    q = s.lower()
    hits: list[Instrument] = []
    for mk in markets:
        for inst in master.get(mk, {}).values():
            name = inst.company_name.lower()
            if q == name or (len(q) >= 2 and q in name):
                hits.append(inst)
    if hits:
        hits.sort(key=lambda i: (i.company_name.lower() != q, i.asset_type != "stock", len(i.company_name)))
        if len(hits) == 1 or hits[0].company_name.lower() == q:
            return Resolution(hits[0], hits[:limit])
        return Resolution(None, hits[:limit], f"找到 {len(hits)} 筆名稱符合的標的，請選一個")

    return Resolution(None, [], f"官方代號主檔裡找不到「{raw.strip()}」")


def unverified(raw: str, market: str, yahoo_symbol: str) -> Instrument:
    """主檔沒有、但 Yahoo 查得到的代號（例如剛上市）。標記為未驗證。"""
    code = raw.strip().upper()
    exchange = "TPEX" if yahoo_symbol.endswith(".TWO") else "TWSE" if market == "TW" else "未知"
    inst = make(market, code, "", exchange, "未知", verified=False)
    return Instrument(inst.symbol, inst.code, "", inst.market, inst.exchange, inst.asset_type, False,
                      {**inst.provider_ids, "yahoo": yahoo_symbol})
