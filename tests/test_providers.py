"""Provider 層離線測試：正規化、品質檢查、網路重試與限流、備援、搜尋、密鑰保護。"""
from datetime import date, datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from core import net
from core.instruments import load_master
from core.net import ProviderError, RateLimiter, Limit, cached, redact
from core.normalize import (apply_adjustment, finalize, from_finmind, from_tpex_row, from_twse_row, from_yahoo,
                            parse_roc_date, to_number)
from core.providers.base import Bars, DataKind
from core.quality import check
from core.search import search
from tests import fixtures as fx

M = load_master()
TSMC, TW0050, GWC = M["TW"]["2330"], M["TW"]["0050"], M["TW"]["6488"]
AAPL, SPY = M["US"]["AAPL"], M["US"]["SPY"]
FETCHED = datetime(2026, 11, 27, 7, 0, tzinfo=timezone.utc)   # 台北 15:00、紐約 02:00（皆已收盤）


def utc(s):
    return datetime.fromisoformat(s).replace(tzinfo=timezone.utc)


# ---- 正規化 ------------------------------------------------------------------
def test_roc_date_and_numbers():
    assert parse_roc_date("1151126") == date(2026, 11, 26)
    assert parse_roc_date("115/01/05") == date(2026, 1, 5)
    assert to_number("1,234.50") == 1234.5
    assert np.isnan(to_number("--")) and np.isnan(to_number("")) and np.isnan(to_number(None))


def test_twse_and_tpex_rows_use_shares_and_currency_amount():
    t = from_twse_row(fx.TWSE_2330)
    assert t["session_date"] == date(2026, 11, 26)
    assert (t["close"], t["volume"], t["turnover"]) == (2145.0, 45892693.0, 98655062940.0)
    p = from_tpex_row(fx.TPEX_6488)   # 櫃買中心欄位名稱不同：TradingShares / TransactionAmount
    assert (p["close"], p["volume"], p["turnover"], p["change"]) == (518.0, 7803697.0, 4004762640.0, 41.0)


def test_finmind_matches_official_twse():
    df = finalize(TSMC, from_finmind(TSMC, fx.FINMIND_2330), FETCHED)
    last = df.iloc[-1]
    official = from_twse_row(fx.TWSE_2330)
    assert df.index[-1].date() == official["session_date"]
    assert (last["close"], last["volume"], last["turnover"]) == (official["close"], official["volume"], official["turnover"])
    assert (last["high"], last["low"]) == (2170.0, 2120.0)          # FinMind 的 max / min
    assert str(df.index.tz) == "Asia/Taipei" and df.index.name == "session_date"


def test_session_timestamps_are_utc_close_times():
    tw = finalize(TSMC, from_finmind(TSMC, fx.FINMIND_2330), FETCHED)
    assert tw["ts_utc"].iloc[-1] == pd.Timestamp("2026-11-26 05:30", tz="UTC")     # 台北 13:30
    y = fx.YAHOO_AAPL
    hist = pd.DataFrame({k: y[k] for k in ("Open", "High", "Low", "Close", "Adj Close", "Volume")},
                        index=pd.DatetimeIndex(pd.to_datetime(y["dates"])).tz_localize("America/New_York"))
    raw, factor = from_yahoo(AAPL, hist)
    us = finalize(AAPL, raw, FETCHED)
    assert us["ts_utc"].iloc[-1] == pd.Timestamp("2026-11-25 21:00", tz="UTC")    # 紐約 16:00（冬令時間）
    assert us["close"].iloc[-1] == 355.58                                          # 浮點誤差已四捨五入
    assert us["turnover"].isna().all()                                            # Yahoo 沒有成交金額，不估算
    assert set(us["kind"]) == {DataKind.HISTORICAL.value}
    assert factor is not None and (factor == 1).all()


def test_partial_session_is_delayed_not_realtime():
    df = finalize(TSMC, from_finmind(TSMC, fx.FINMIND_2330), FETCHED, partial_session=date(2026, 11, 26))
    assert df["kind"].iloc[-1] == DataKind.DELAYED.value
    assert df["ts_utc"].iloc[-1] == pd.Timestamp(FETCHED)          # 盤中資料的時間 = 抓取時間
    assert (df["kind"].iloc[:-1] == DataKind.HISTORICAL.value).all()
    assert DataKind.REALTIME.value not in set(df["kind"])


def test_adjustment_fills_gaps_between_sources():
    raw = finalize(TSMC, from_finmind(TSMC, fx.FINMIND_2330), FETCHED)
    # 還原係數少了中間那天（例如 Yahoo 缺一天），應沿用相鄰日期的係數，而不是當作 1
    factor = pd.Series([0.9, 0.9], index=raw.index[[0, 2]])
    adj = apply_adjustment(raw, factor)
    assert adj["close"].iloc[1] == pytest.approx(2150.0 * 0.9)
    assert apply_adjustment(raw, None) is None


# ---- 品質檢查 ------------------------------------------------------------------
def bars_from(frame, inst=TSMC):
    return Bars(inst, frame, None, "test", FETCHED)


def test_quality_duplicates_and_anomalies():
    df = finalize(TSMC, from_finmind(TSMC, fx.FINMIND_2330), FETCHED)
    dup = pd.concat([df, df.iloc[[-1]]])
    dup.iloc[0, dup.columns.get_loc("high")] = 1.0      # 最高價低於實體 → 不合理
    cleaned, rep = check(bars_from(dup), FETCHED)
    assert rep.duplicates == 1 and len(cleaned.frame) == 3
    assert any("開高低收不合理" in a for a in rep.anomalies)


def test_quality_flags_missing_sessions_and_jumps():
    rows = [dict(r) for r in fx.FINMIND_2330]
    rows[0]["date"] = "2026-11-20"                      # 11/23（週一）沒有資料
    rows[2].update(close=2600.0, max=2600.0)            # 11/26 漲 +20.9%：超過台股 10% 限制
    df = finalize(TSMC, from_finmind(TSMC, rows), FETCHED)
    _, rep = check(bars_from(df), FETCHED)
    assert date(2026, 11, 23) in rep.missing_sessions
    assert any("漲跌幅限制" in a for a in rep.anomalies)


def test_quality_today_not_yet_published_is_info_not_stale():
    df = finalize(TSMC, from_finmind(TSMC, fx.FINMIND_2330), FETCHED)     # 最新 11/26，現在是 11/27 收盤後
    _, rep = check(bars_from(df), FETCHED)
    assert rep.stale_sessions == 0
    assert any(level == "info" and "尚未納入" in t for level, t in rep.messages)


def test_quality_never_accepts_realtime_label():
    df = finalize(TSMC, from_finmind(TSMC, fx.FINMIND_2330), FETCHED)
    df.iloc[-1, df.columns.get_loc("kind")] = DataKind.REALTIME.value
    _, rep = check(bars_from(df), FETCHED)
    assert not rep.ok


def test_quality_empty_is_unavailable():
    empty = finalize(TSMC, pd.DataFrame(), FETCHED)
    _, rep = check(bars_from(empty), FETCHED)
    assert rep.kind == DataKind.UNAVAILABLE and not rep.ok


# ---- 網路：密鑰遮蔽、重試、限流、快取 ---------------------------------------------
def test_redact_hides_keys():
    s = redact("https://x/query?function=GLOBAL_QUOTE&apikey=ABC123SECRET&symbol=AAPL token=zzz")
    assert "ABC123SECRET" not in s and "zzz" not in s and "symbol=AAPL" in s


class FakeResp:
    def __init__(self, code):
        self.status_code, self.url = code, "https://example.test/api?apikey=SHOULD_NOT_LOG"


def test_http_retries_transient_errors_then_succeeds(monkeypatch):
    calls = iter([FakeResp(503), FakeResp(200)])
    monkeypatch.setattr(net._session, "get", lambda *a, **k: next(calls))
    monkeypatch.setattr(net.time, "sleep", lambda s: None)
    assert net.http_get("test", "https://example.test").status_code == 200


def test_http_does_not_retry_permanent_errors(monkeypatch):
    n = {"calls": 0}

    def fake(*a, **k):
        n["calls"] += 1
        return FakeResp(404)

    monkeypatch.setattr(net._session, "get", fake)
    monkeypatch.setattr(net.time, "sleep", lambda s: None)
    with pytest.raises(ProviderError) as e:
        net.http_get("test", "https://example.test")
    assert n["calls"] == 1 and "404" in e.value.reason


def test_rate_limit_window_budget(monkeypatch):
    monkeypatch.setitem(net.LIMITS, "budget", Limit(0.0, per_window=2, window=60))
    rl = RateLimiter()
    rl.acquire("budget")
    rl.acquire("budget")
    with pytest.raises(ProviderError, match="請求上限"):
        rl.acquire("budget")


def test_cache_does_not_store_failures():
    net.cache.clear()

    class P:
        n = 0

        @cached(ttl=60)
        def get(self, x):
            P.n += 1
            if P.n == 1:
                raise ProviderError("p", "暫時失敗")
            return x * 2

    p = P()
    with pytest.raises(ProviderError):
        p.get(2)
    assert p.get(2) == 4 and p.get(2) == 4 and P.n == 2     # 失敗不快取、成功後命中快取


# ---- 備援與組合 Provider ---------------------------------------------------------
def test_tw_falls_back_to_yahoo_and_notes_do_not_accumulate(monkeypatch):
    from core.providers import registry

    tw = registry.TaiwanStockProvider()
    yahoo_bars = Bars(TSMC, finalize(TSMC, from_finmind(TSMC, fx.FINMIND_2330), FETCHED), None, "Yahoo Finance",
                      FETCHED, notes=["原始說明"])

    def boom(*a, **k):
        raise ProviderError("FinMind", "HTTP 402")

    monkeypatch.setattr(tw.primary, "get_daily", boom)
    monkeypatch.setattr(tw.fallback, "get_daily", lambda *a, **k: yahoo_bars)
    first = tw.get_daily(TSMC, date(2026, 11, 20))
    second = tw.get_daily(TSMC, date(2026, 11, 20))
    assert first.source == "Yahoo Finance" and "改用備援來源 Yahoo" in first.notes[0]
    assert len(second.notes) == len(first.notes) == 2 and yahoo_bars.notes == ["原始說明"]


def test_registry_routes_by_market():
    from core.providers.registry import financial_provider, news_provider, price_provider

    assert price_provider(TSMC).name.startswith("台股") and price_provider(AAPL).name.startswith("美股")
    assert "FinMind" in financial_provider(TSMC).name and "SEC" in financial_provider(AAPL).name
    assert "FinMind" in news_provider(TSMC).name and "Yahoo" in news_provider(AAPL).name


# ---- 搜尋 ------------------------------------------------------------------------
def test_search_code_name_and_market_filter():
    assert search("2330")[0].instrument.symbol == "TW:2330"
    assert search("台積電")[0].instrument.symbol == "TW:2330"
    assert search("brk-b")[0].instrument.symbol == "US:BRK.B"
    apple = search("apple", market="US")
    assert apple[0].instrument.symbol == "US:AAPL" and all(r.instrument.market == "US" for r in apple)
    tw_only = search("00631", market="TW")
    assert tw_only and all(r.instrument.market == "TW" for r in tw_only)
    assert search("") == [] and search("ZZZZ9Q") == []
    assert "上市" in search("2330")[0].display and "NASDAQ" in search("AAPL")[0].display


# ---- 密鑰保護 --------------------------------------------------------------------
def test_secrets_are_not_committed():
    root = Path(__file__).resolve().parent.parent
    assert ".env" in (root / ".gitignore").read_text().splitlines()
    env = root / ".env"
    if not env.exists():
        pytest.skip("沒有 .env")
    secrets = [line.split("=", 1)[1].strip().strip('"') for line in env.read_text().splitlines()
               if "=" in line and not line.startswith("#") and ("KEY" in line or "TOKEN" in line)]
    secrets = [s for s in secrets if len(s) >= 8]
    import subprocess
    tracked = subprocess.run(["git", "ls-files"], cwd=root, capture_output=True, text=True).stdout.split()
    for f in tracked:
        p = root / f
        if p.suffix in (".csv",) or not p.is_file():
            continue
        text = p.read_text(errors="ignore")
        for s in secrets:
            assert s not in text, f"{f} 含有 .env 裡的密鑰"
