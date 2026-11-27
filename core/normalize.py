"""把各家來源的原始資料轉成統一欄位（見 core.providers.base.BAR_COLUMNS）。

各來源差異：
- 證交所：民國日期（1151126）、數字是字串、成交股數 TradeVolume、成交金額 TradeValue
- 櫃買中心：民國日期、成交股數 TradingShares、成交金額 TransactionAmount
- FinMind：西元日期、Trading_Volume（股）、Trading_money（元）、max/min 代表最高/最低
- Yahoo：tz-aware 索引、Volume（股；台股不是官方總量）、沒有成交金額
"""
from __future__ import annotations

from datetime import date, datetime, time, timezone
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from .instruments import Instrument
from .providers.base import BAR_COLUMNS, DataKind


def parse_roc_date(s: str) -> date:
    """民國日期 1151126 → 2026-11-26。也接受 115/11/26。"""
    digits = "".join(ch for ch in str(s) if ch.isdigit())
    if len(digits) < 6:
        raise ValueError(f"無法解析民國日期：{s!r}")
    y, m, d = int(digits[:-4]), int(digits[-4:-2]), int(digits[-2:])
    return date(y + 1911, m, d)


def to_number(v) -> float:
    """'1,234.50' → 1234.5；'--'、''、None → NaN（交易所用 -- 表示當天無成交）。"""
    if v is None:
        return np.nan
    if isinstance(v, (int, float, np.floating)):
        return float(v)
    s = str(v).replace(",", "").strip()
    if s in ("", "--", "---", "-", "N/A", "null"):
        return np.nan
    try:
        return float(s)
    except ValueError:
        return np.nan


def session_close_utc(inst: Instrument, d: date) -> datetime:
    local = datetime.combine(d, inst.info.close_time, tzinfo=ZoneInfo(inst.timezone))
    return local.astimezone(timezone.utc)


def session_index(inst: Instrument, dates) -> pd.DatetimeIndex:
    idx = pd.DatetimeIndex(pd.to_datetime(list(dates))).normalize()
    idx = idx.tz_localize(inst.timezone) if idx.tz is None else idx.tz_convert(inst.timezone).normalize()
    return idx.rename("session_date")


def finalize(inst: Instrument, df: pd.DataFrame, fetched_at: datetime, partial_session: date | None = None) -> pd.DataFrame:
    """補上 ts_utc 與 kind 欄位，排序並固定欄位順序。

    partial_session：盤中尚未收盤的那一天（標成延遲、資料時間 = 抓取時間）。
    """
    if df.empty:
        return pd.DataFrame(columns=BAR_COLUMNS, index=session_index(inst, []))
    df = df.sort_index().copy()
    ts, kinds = [], []
    for idx in df.index:
        d = idx.date()
        if partial_session is not None and d == partial_session:
            ts.append(pd.Timestamp(fetched_at).tz_convert("UTC"))
            kinds.append(DataKind.DELAYED.value)
        else:
            ts.append(pd.Timestamp(session_close_utc(inst, d)))
            kinds.append(DataKind.HISTORICAL.value)
    df["ts_utc"] = ts
    df["kind"] = kinds
    for col in ("open", "high", "low", "close", "volume", "turnover"):
        if col not in df.columns:
            df[col] = np.nan
        df[col] = pd.to_numeric(df[col], errors="coerce")
    return df[BAR_COLUMNS]


def from_yahoo(inst: Instrument, hist: pd.DataFrame) -> tuple[pd.DataFrame, pd.Series | None]:
    """回傳 (正規化前的 OHLCV, 還原係數 Adj Close / Close)。"""
    if hist.empty:
        return pd.DataFrame(), None
    # Yahoo 回傳的價格有浮點誤差（例如 355.5799865…），四捨五入到 4 位小數
    df = pd.DataFrame({
        "open": hist["Open"].round(4), "high": hist["High"].round(4), "low": hist["Low"].round(4),
        "close": hist["Close"].round(4), "volume": hist["Volume"], "turnover": np.nan,
    })
    df.index = session_index(inst, hist.index.tz_convert(inst.timezone).date if hist.index.tz else hist.index)
    factor = None
    if "Adj Close" in hist.columns:
        factor = pd.Series((hist["Adj Close"] / hist["Close"]).values, index=df.index)
    return df, factor


def from_finmind(inst: Instrument, rows: list[dict]) -> pd.DataFrame:
    if not rows:
        return pd.DataFrame()
    raw = pd.DataFrame(rows)
    df = pd.DataFrame({
        "open": raw["open"].map(to_number), "high": raw["max"].map(to_number), "low": raw["min"].map(to_number),
        "close": raw["close"].map(to_number), "volume": raw["Trading_Volume"].map(to_number),
        "turnover": raw["Trading_money"].map(to_number),
    })
    df.index = session_index(inst, raw["date"])
    return df


def from_twse_row(row: dict) -> dict:
    return {
        "session_date": parse_roc_date(row["Date"]),
        "open": to_number(row.get("OpeningPrice")), "high": to_number(row.get("HighestPrice")),
        "low": to_number(row.get("LowestPrice")), "close": to_number(row.get("ClosingPrice")),
        "volume": to_number(row.get("TradeVolume")), "turnover": to_number(row.get("TradeValue")),
        "change": to_number(row.get("Change")),
    }


def from_tpex_row(row: dict) -> dict:
    return {
        "session_date": parse_roc_date(row["Date"]),
        "open": to_number(row.get("Open")), "high": to_number(row.get("High")),
        "low": to_number(row.get("Low")), "close": to_number(row.get("Close")),
        "volume": to_number(row.get("TradingShares")), "turnover": to_number(row.get("TransactionAmount")),
        "change": to_number(row.get("Change")),
    }


def apply_adjustment(frame: pd.DataFrame, factor: pd.Series | None) -> pd.DataFrame | None:
    """用還原係數調整開高低收；全部缺漏則回傳 None。

    還原係數只在除權息、分割時改變，所以兩個來源日期對不齊時（某來源少一天），
    用前後相鄰交易日的係數補上，不會造成價格斷層。
    """
    if factor is None or frame.empty:
        return None
    f = factor.reindex(frame.index.union(factor.index)).sort_index().ffill().bfill().reindex(frame.index)
    if f.isna().all():
        return None
    out = frame.copy()
    for col in ("open", "high", "low", "close"):
        out[col] = frame[col] * f
    return out
