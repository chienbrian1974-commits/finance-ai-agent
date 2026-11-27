"""市場層級的組合 Provider 與註冊表。

- TaiwanStockProvider：FinMind（官方成交量與成交金額）為主 → 失敗改用 Yahoo；
  還原係數取自 Yahoo；最新收盤可與證交所／櫃買中心 OpenAPI 交叉比對。
- USStockProvider：Yahoo 為主 → 失敗改用 FinMind 美股；可與 FinMind 交叉比對收盤價。
業務邏輯只呼叫 price_provider(inst)、financial_provider(inst)、news_provider(inst)。
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date

import pandas as pd

from ..instruments import Instrument
from ..net import ProviderError, cached, log
from ..normalize import apply_adjustment, finalize, session_index, to_number
from .base import Bars, FinancialDataProvider, NewsDataProvider, PriceProvider, Quote
from .sources import (
    FinMindFinancialProvider, FinMindNewsProvider, FinMindPriceProvider, SecFinancialProvider,
    TwOfficialDailyProvider, YahooPriceProvider, YahooRssNewsProvider, _finmind, _now, _partial_session,
)


def _copy(bars: Bars) -> Bars:
    """快取回傳的是共用物件；修改前先複製，避免說明文字在每次呼叫時重複累加。"""
    return replace(bars, notes=list(bars.notes))


@dataclass
class CrossCheck:
    other_source: str
    session_date: date | None
    ours: float | None
    theirs: float | None
    field: str = "close"
    tolerance_pct: float = 1.0

    @property
    def diff_pct(self) -> float | None:
        if self.ours is None or self.theirs in (None, 0):
            return None
        return abs(self.ours / self.theirs - 1) * 100

    @property
    def ok(self) -> bool | None:
        d = self.diff_pct
        return None if d is None else d <= self.tolerance_pct


class TaiwanStockProvider(PriceProvider):
    name = "台股（FinMind 為主、Yahoo 備援）"

    def __init__(self) -> None:
        self.primary = FinMindPriceProvider()
        self.fallback = YahooPriceProvider()
        self.official = TwOfficialDailyProvider()

    def get_daily(self, inst: Instrument, start: date, end: date | None = None) -> Bars:
        try:
            bars = _copy(self.primary.get_daily(inst, start, end))
            if bars.empty:
                raise ProviderError(self.primary.name, "沒有資料")
        except ProviderError as e:
            log.warning("台股主要來源失敗，改用 Yahoo：%s", e)
            bars = _copy(self.fallback.get_daily(inst, start, end))
            bars.notes.insert(0, f"主要來源 FinMind 無法使用（{e.reason}），改用備援來源 Yahoo。")
            return bars
        factor = self.fallback.adjustment_factor(inst, start, end)
        bars.adjusted = apply_adjustment(bars.frame, factor)
        if bars.adjusted is None:
            bars.notes.append("無法取得除權息還原係數，只有原始價。")
        else:
            bars.notes.append("還原價使用 Yahoo 的還原係數（Adj Close / Close）計算。")
        return bars

    def cross_check(self, inst: Instrument, bars: Bars) -> CrossCheck:
        """和證交所／櫃買中心官方最新收盤比對。"""
        q = self.official.get_quote(inst)
        ours = None
        if q.session_date is not None and not bars.empty:
            day = bars.frame[bars.frame.index.date == q.session_date]
            ours = float(day["close"].iloc[0]) if len(day) else None
        return CrossCheck(q.source, q.session_date, ours, q.price)

    def official_quote(self, inst: Instrument) -> Quote:
        return self.official.get_quote(inst)


class FinMindUSPriceProvider(PriceProvider):
    name = "FinMind（美股）"

    @cached(ttl=900)
    def get_daily(self, inst: Instrument, start: date, end: date | None = None) -> Bars:
        params = {"data_id": inst.yahoo, "start_date": f"{start:%Y-%m-%d}"}
        if end:
            params["end_date"] = f"{end:%Y-%m-%d}"
        rows = _finmind("USStockPrice", **params)
        fetched = _now()
        if not rows:
            return Bars(inst, finalize(inst, pd.DataFrame(), fetched), None, self.name, fetched)
        raw = pd.DataFrame(rows)
        df = pd.DataFrame({"open": raw["Open"].map(to_number), "high": raw["High"].map(to_number),
                           "low": raw["Low"].map(to_number), "close": raw["Close"].map(to_number),
                           "volume": raw["Volume"].map(to_number)})
        df.index = session_index(inst, raw["date"])
        frame = finalize(inst, df, fetched, _partial_session(inst, df.index[-1].date()))
        factor = pd.Series((raw["Adj_Close"].map(to_number) / raw["Close"].map(to_number)).values, index=df.index)
        return Bars(inst, frame, apply_adjustment(frame, factor), self.name, fetched,
                    notes=["FinMind 美股資料；資料授權：非商業用途，需標示來源 FinMind。"])


class USStockProvider(PriceProvider):
    name = "美股（Yahoo 為主、FinMind 備援）"

    def __init__(self) -> None:
        self.primary = YahooPriceProvider()
        self.fallback = FinMindUSPriceProvider()

    def get_daily(self, inst: Instrument, start: date, end: date | None = None) -> Bars:
        try:
            bars = _copy(self.primary.get_daily(inst, start, end))
            if bars.empty:
                raise ProviderError(self.primary.name, "沒有資料")
            bars.notes.append("美股沒有免費來源提供成交金額，此欄位標示為無法取得。")
            return bars
        except ProviderError as e:
            log.warning("美股主要來源失敗，改用 FinMind：%s", e)
            bars = _copy(self.fallback.get_daily(inst, start, end))
            bars.notes.insert(0, f"主要來源 Yahoo 無法使用（{e.reason}），改用備援來源 FinMind。")
            return bars

    def cross_check(self, inst: Instrument, bars: Bars) -> CrossCheck:
        """和 FinMind 美股最近一個共同交易日的收盤價比對。"""
        if bars.empty:
            return CrossCheck(self.fallback.name, None, None, None)
        other = self.fallback.get_daily(inst, bars.frame.index[0].date())
        common = bars.frame.index.intersection(other.frame.index)
        hist = [d for d in common if bars.frame.loc[d, "kind"] == "歷史"]
        if not hist:
            return CrossCheck(self.fallback.name, None, None, None)
        d = hist[-1]
        return CrossCheck(self.fallback.name, d.date(), float(bars.frame.loc[d, "close"]), float(other.frame.loc[d, "close"]))


_PRICE: dict[str, PriceProvider] = {"TW": TaiwanStockProvider(), "US": USStockProvider()}
_FIN: dict[str, FinancialDataProvider] = {"TW": FinMindFinancialProvider(), "US": SecFinancialProvider()}
_NEWS: dict[str, NewsDataProvider] = {"TW": FinMindNewsProvider(), "US": YahooRssNewsProvider()}


def price_provider(inst: Instrument) -> PriceProvider:
    return _PRICE[inst.market]


def financial_provider(inst: Instrument) -> FinancialDataProvider:
    return _FIN[inst.market]


def news_provider(inst: Instrument) -> NewsDataProvider:
    return _NEWS[inst.market]
