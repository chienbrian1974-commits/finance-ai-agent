"""抓取日 K 行情，並產生「原始價」與「還原價」兩份資料。

- 原始價：交易所實際成交價格。
- 還原價：用 Yahoo 的 Adj Close 把歷史價格依除權息、分割調整，
  除息缺口不會被指標誤判成下跌。最新一天的還原價等於原始價。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

import pandas as pd
import yfinance as yf

from .instruments import Instrument, unverified

SOURCE = "Yahoo Finance（yfinance）"
OHLC = ["Open", "High", "Low", "Close"]


@dataclass
class PriceData:
    instrument: Instrument
    raw: pd.DataFrame          # 原始價 OHLCV，索引為交易所時區
    adjusted: pd.DataFrame     # 還原價 OHLCV
    fetched_at: datetime       # UTC
    dropped: list[str] = field(default_factory=list)   # 被排除的不完整資料日期
    events: pd.DataFrame = field(default_factory=pd.DataFrame)  # 除權息、分割
    source: str = SOURCE

    @property
    def empty(self) -> bool:
        return self.raw.empty


def split_incomplete(df: pd.DataFrame) -> tuple[pd.DataFrame, list[str]]:
    """把開高低收有缺值或 ≤ 0 的資料列分出來（例如 Yahoo 當天還沒結算的那一列）。"""
    if df.empty:
        return df, []
    bad = df[OHLC].isna().any(axis=1) | (df[OHLC] <= 0).any(axis=1)
    dropped = [f"{ts:%Y-%m-%d}" for ts in df.index[bad]]
    return df[~bad].copy(), dropped


def adjust(df: pd.DataFrame) -> pd.DataFrame:
    """用 Adj Close / Close 的比例還原開高低收。沒有 Adj Close 時回傳原始價。"""
    out = df[OHLC + ["Volume"]].copy()
    if "Adj Close" not in df.columns:
        return out
    factor = (df["Adj Close"] / df["Close"]).fillna(1.0)
    for col in OHLC:
        out[col] = df[col] * factor
    return out


def from_frame(inst: Instrument, hist: pd.DataFrame, fetched_at: datetime | None = None) -> PriceData:
    """把 yfinance 的原始資料整理成 PriceData（測試也直接用這個函式）。"""
    fetched_at = fetched_at or datetime.now(timezone.utc)
    if hist.empty:
        return PriceData(inst, hist, hist, fetched_at)
    hist = hist.copy()
    hist.index = hist.index.tz_convert(inst.timezone) if hist.index.tz else hist.index.tz_localize(inst.timezone)
    clean, dropped = split_incomplete(hist)
    ev_cols = [c for c in ("Dividends", "Stock Splits") if c in clean.columns]
    events = clean.loc[(clean[ev_cols] != 0).any(axis=1), ev_cols] if ev_cols else pd.DataFrame()
    return PriceData(inst, clean[OHLC + ["Volume"]], adjust(clean), fetched_at, dropped, events)


def fetch_daily(inst: Instrument, period: str = "6mo") -> PriceData:
    hist = yf.Ticker(inst.yahoo).history(period=period, auto_adjust=False)
    return from_frame(inst, hist)


def probe_yahoo(raw: str) -> Instrument | None:
    """主檔查不到時，直接問 Yahoo 有沒有這個代號。查得到就回傳「未驗證」的標的。"""
    code = raw.strip().upper()
    if not code:
        return None
    tries = ([("TW", f"{code}.TW"), ("TW", f"{code}.TWO")] if code[:1].isdigit()
             else [("US", code.replace(".", "-"))])
    for market, sym in tries:
        try:
            h = yf.Ticker(sym).history(period="5d", auto_adjust=False)
        except Exception:  # noqa: BLE001
            continue
        if not h.dropna(subset=["Close"]).empty:
            return unverified(code, market, sym)
    return None
