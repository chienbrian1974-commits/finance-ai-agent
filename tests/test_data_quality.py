"""行情整理與資料驗證。

這裡的價格是測試用的人工資料（不是任何股票的真實行情），
用來確認程式在「缺值、盤中、過期、除權息、異常跳動」等情況下的行為。
"""
from datetime import datetime, timezone

import numpy as np
import pandas as pd
import pytest

from core.instruments import load_master
from core.marketdata import from_frame
from core.validation import expected_last_session, fmt_display_time, market_phase, validate

TW = load_master()["TW"]["2330"]
US = load_master()["US"]["AAPL"]


def frame(dates, closes, tz, adj=None, dividends=None):
    idx = pd.DatetimeIndex(pd.to_datetime(dates)).tz_localize(tz)
    c = np.array(closes, dtype=float)
    df = pd.DataFrame({"Open": c, "High": c * 1.01, "Low": c * 0.99, "Close": c, "Volume": 1000.0}, index=idx)
    df["Adj Close"] = adj if adj is not None else c
    df["Dividends"] = dividends if dividends is not None else 0.0
    df["Stock Splits"] = 0.0
    return df


def utc(s):
    return datetime.fromisoformat(s).replace(tzinfo=timezone.utc)


# 2026-11-27 是週五。台北 10:00 = UTC 02:00（台股盤中）；台北 15:00 = UTC 07:00（台股已收盤）
TW_OPEN = utc("2026-11-27T02:00:00")
TW_CLOSED = utc("2026-11-27T07:00:00")


def test_incomplete_row_is_excluded_and_reported():
    df = frame(["2026-11-25", "2026-11-26", "2026-11-27"], [100, 101, 102], "Asia/Taipei")
    df.loc[df.index[-1], "Close"] = np.nan
    data = from_frame(TW, df)
    assert len(data.raw) == 2 and data.dropped == ["2026-11-27"]
    status = validate(data, TW_CLOSED)
    assert any("不完整" in t for _, t in status.messages)


def test_partial_bar_during_session():
    data = from_frame(TW, frame(["2026-11-26", "2026-11-27"], [100, 101], "Asia/Taipei"))
    s = validate(data, TW_OPEN)
    assert s.phase == "盤中" and s.partial and s.stale_sessions == 0
    s2 = validate(data, TW_CLOSED)
    assert s2.phase == "已收盤" and not s2.partial


def test_stale_data_is_flagged():
    data = from_frame(TW, frame(["2026-11-20", "2026-11-23"], [100, 101], "Asia/Taipei"))
    s = validate(data, TW_CLOSED)  # 預期最新是 11/27，少了 11/24~27 共 4 個交易日
    assert s.stale_sessions == 4
    assert any(level == "error" and "少了 4 個交易日" in t for level, t in s.messages)


def test_us_uses_new_york_time_and_holidays():
    # UTC 02:00 週五 = 紐約週四 11/26 21:00。11/26 是感恩節，美股休市，最新應為 11/25
    assert market_phase(US, TW_OPEN) == "休市日"
    assert str(expected_last_session(US, TW_OPEN)) == "2026-11-25"
    # 週六台北早上 = 紐約週五 11/27 晚上，週五有交易，已收盤
    assert market_phase(US, utc("2026-11-28T02:00:00")) == "已收盤"


def test_holiday_is_not_reported_as_stale():
    # 美股最新資料停在 11/25，紐約時間 11/26（感恩節）晚上看：不算過期
    data = from_frame(US, frame(["2026-11-24", "2026-11-25"], [100, 101], "America/New_York"))
    s = validate(data, TW_OPEN)
    assert s.stale_sessions == 0
    assert not any("少了" in t for _, t in s.messages)


def test_tw_lunar_new_year_is_not_a_session():
    # 2026 農曆年：台股 2/16 休市（依 XTAI 行事曆）
    assert market_phase(TW, utc("2026-02-16T03:00:00")) == "休市日"


def test_adjusted_prices_remove_dividend_gap():
    # 第 3 天除息 2 元：原始價 100 → 98（看起來跌 2%），還原後應該沒有缺口
    closes = [100, 100, 98, 98]
    adj = [98, 98, 98, 98]
    data = from_frame(TW, frame(["2026-11-24", "2026-11-25", "2026-11-26", "2026-11-27"], closes, "Asia/Taipei",
                                adj=adj, dividends=[0, 0, 2.0, 0]))
    assert data.raw["Close"].pct_change().iloc[2] == pytest.approx(-0.02)
    assert data.adjusted["Close"].pct_change().iloc[2] == pytest.approx(0.0)
    assert data.adjusted["High"].iloc[0] == pytest.approx(98 * 1.01)
    assert data.adjusted["Close"].iloc[-1] == data.raw["Close"].iloc[-1]  # 最新一天還原價 = 原始價
    assert len(data.events) == 1


def test_price_jump_beyond_tw_limit_is_flagged_but_not_on_dividend_day():
    data = from_frame(TW, frame(["2026-11-25", "2026-11-26", "2026-11-27"], [100, 120, 121], "Asia/Taipei"))
    s = validate(data, TW_CLOSED)
    assert any("漲跌幅限制" in t and "11/26" in t for _, t in s.messages)

    data2 = from_frame(TW, frame(["2026-11-25", "2026-11-26", "2026-11-27"], [100, 120, 121], "Asia/Taipei",
                                 dividends=[0, 1.0, 0]))
    assert not any("漲跌幅限制" in t for _, t in validate(data2, TW_CLOSED).messages)


def test_empty_data_is_an_error_not_a_guess():
    data = from_frame(TW, pd.DataFrame())
    s = validate(data, TW_CLOSED)
    assert not s.ok and s.last_bar is None


def test_display_time_is_taipei():
    assert fmt_display_time(utc("2026-11-27T02:56:40")) == "2026-11-27 10:56:40（台北時間）"
