"""Provider 統一介面與資料結構。

業務邏輯只依賴這裡定義的介面（PriceProvider、FinancialDataProvider、NewsDataProvider），
不直接呼叫任何一家 API；換資料來源時只需要新增或替換 Provider。
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import date, datetime
from enum import Enum

import pandas as pd

from ..instruments import Instrument
from ..net import ProviderError  # noqa: F401  （讓呼叫端從 base 匯入即可）


class DataKind(str, Enum):
    """資料的時效類別。不得把歷史資料標成即時。"""

    REALTIME = "即時"        # 交易所授權的即時報價（目前沒有任何來源提供）
    DELAYED = "延遲"         # 盤中、尚未收盤的資料（免費來源都有延遲）
    HISTORICAL = "歷史"      # 已收盤、已結算的資料
    UNAVAILABLE = "無法取得"  # 來源失敗或沒有這筆資料


# 正規化後的日 K 欄位（每一列 = 一個交易日）
BAR_COLUMNS = ["open", "high", "low", "close", "volume", "turnover", "ts_utc", "kind"]
# - 索引：交易所當地日期（tz-aware，交易所時區的 00:00），名稱 session_date
# - volume：成交股數（股）。台股 1 張 = 1000 股，顯示時再換算
# - turnover：成交金額（交易幣別）；來源沒提供時為 NaN，不估算
# - ts_utc：這根 K 棒的資料時間（UTC）。已收盤 = 當日收盤時刻；盤中 = 抓取時刻
# - kind：DataKind 的值


@dataclass
class Bars:
    instrument: Instrument
    frame: pd.DataFrame                  # 原始價（交易所實際成交價）
    adjusted: pd.DataFrame | None        # 還原價（除權息調整）；無法計算時為 None
    source: str                          # 主要資料來源
    fetched_at: datetime                 # UTC
    volume_unit: str = "股"
    notes: list[str] = field(default_factory=list)  # 來源說明、備援、交叉比對結果

    @property
    def empty(self) -> bool:
        return self.frame.empty

    @property
    def last_session(self) -> date | None:
        return None if self.frame.empty else self.frame.index[-1].date()


@dataclass
class Quote:
    instrument: Instrument
    price: float | None
    prev_close: float | None
    session_date: date | None            # 交易所當地日期
    as_of_utc: datetime | None           # 資料時間（UTC）
    kind: DataKind
    source: str
    volume: float | None = None          # 股
    turnover: float | None = None        # 交易幣別
    note: str = ""

    @property
    def change_pct(self) -> float | None:
        if self.price is None or not self.prev_close:
            return None
        return (self.price / self.prev_close - 1) * 100


@dataclass
class FinancialItem:
    period_end: date
    period: str          # "FY" 年度 / "Q1".."Q4" 季度
    metric: str          # revenue / net_income / eps …
    value: float
    unit: str            # TWD、USD、USD/shares …
    source: str
    filed: date | None = None


@dataclass
class Financials:
    instrument: Instrument
    items: list[FinancialItem]
    source: str
    fetched_at: datetime
    kind: DataKind = DataKind.HISTORICAL
    note: str = ""

    def table(self) -> pd.DataFrame:
        if not self.items:
            return pd.DataFrame()
        df = pd.DataFrame([i.__dict__ for i in self.items])
        return df.pivot_table(index=["period_end", "period"], columns="metric", values="value", aggfunc="last").sort_index()


@dataclass
class NewsItem:
    title: str
    url: str
    published_utc: datetime | None
    source: str


class PriceProvider(ABC):
    name: str = "price"

    @abstractmethod
    def get_daily(self, inst: Instrument, start: date, end: date | None = None) -> Bars:
        """日 K。失敗時拋出 ProviderError，不回傳假資料。"""

    def get_quote(self, inst: Instrument) -> Quote:
        """最新報價：預設取日 K 的最後一根。"""
        from datetime import timedelta

        bars = self.get_daily(inst, date.today() - timedelta(days=14))
        if bars.empty:
            return Quote(inst, None, None, None, None, DataKind.UNAVAILABLE, bars.source, note="來源沒有資料")
        f = bars.frame
        last = f.iloc[-1]
        prev = f["close"].iloc[-2] if len(f) > 1 else None
        return Quote(inst, float(last["close"]), float(prev) if prev is not None else None, f.index[-1].date(),
                     last["ts_utc"].to_pydatetime(), DataKind(last["kind"]), bars.source,
                     float(last["volume"]) if pd.notna(last["volume"]) else None,
                     float(last["turnover"]) if pd.notna(last["turnover"]) else None)


class FinancialDataProvider(ABC):
    name: str = "financials"

    @abstractmethod
    def get_financials(self, inst: Instrument) -> Financials:
        """財報重點數字。失敗時拋出 ProviderError。"""


class NewsDataProvider(ABC):
    name: str = "news"

    @abstractmethod
    def get_news(self, inst: Instrument, limit: int = 10) -> list[NewsItem]:
        """新聞標題與連結。失敗時拋出 ProviderError。"""
