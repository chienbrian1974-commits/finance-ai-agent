"""連網整合測試（預設不執行）。執行方式：.venv/bin/python -m pytest -m live -v

需求：台股個股、台股 ETF、美股個股、美股 ETF 各一檔，驗證價格、幣別、時區、成交量、更新時間，
並與第二個來源交叉比對收盤價。
"""
import os
from datetime import date, datetime, timedelta, timezone

import pandas as pd
import pytest

from core.instruments import load_master
from core.providers.base import DataKind
from core.providers.registry import price_provider
from core.quality import check

pytestmark = pytest.mark.live
M = load_master()

CASES = [
    ("TW", "2330", "stock", "TWD", "Asia/Taipei", 1000, "05:30"),        # 台北 13:30 = UTC 05:30
    ("TW", "0050", "etf", "TWD", "Asia/Taipei", 1000, "05:30"),
    ("US", "AAPL", "stock", "USD", "America/New_York", 1, None),
    ("US", "SPY", "etf", "USD", "America/New_York", 1, None),
]


@pytest.fixture(autouse=True)
def online(monkeypatch):
    monkeypatch.delenv("FINANCE_OFFLINE", raising=False)   # 連網測試使用真正的台股交易日清單


@pytest.mark.parametrize("market,code,asset,ccy,tz,lot,close_utc", CASES)
def test_live_daily_bars(market, code, asset, ccy, tz, lot, close_utc):
    inst = M[market][code]
    assert (inst.asset_type, inst.currency, inst.timezone, inst.info.lot_size) == (asset, ccy, tz, lot)

    p = price_provider(inst)
    bars, rep = check(p.get_daily(inst, date.today() - timedelta(days=60)))
    f = bars.frame
    assert len(f) >= 30 and rep.ok, rep.messages
    assert str(f.index.tz) == tz
    assert (f[["open", "high", "low", "close"]] > 0).all().all()
    assert (f["volume"] > 0).all() and bars.volume_unit == "股"
    assert set(f["kind"]) <= {DataKind.HISTORICAL.value, DataKind.DELAYED.value}
    assert DataKind.REALTIME.value not in set(f["kind"])

    hist = f[f["kind"] == DataKind.HISTORICAL.value]
    last_ts = hist["ts_utc"].iloc[-1]
    assert last_ts.tzinfo is not None and last_ts <= pd.Timestamp(datetime.now(timezone.utc))
    if close_utc:
        assert last_ts.strftime("%H:%M") == close_utc
    else:   # 美股收盤 16:00 紐約 = UTC 20:00（夏令）或 21:00（冬令）
        assert last_ts.strftime("%H:%M") in ("20:00", "21:00")
    assert bars.fetched_at.tzinfo is not None

    if market == "TW":
        assert hist["turnover"].notna().all(), "台股成交金額應來自官方數字"
    else:
        assert f["turnover"].isna().all(), "美股沒有免費成交金額來源，不應出現估算值"

    cc = p.cross_check(inst, bars)
    assert cc.ok, f"與 {cc.other_source} 的收盤價差異 {cc.diff_pct}%"


@pytest.mark.parametrize("code", ["2330", "0050"])
def test_live_tw_volume_matches_official(code):
    inst = M["TW"][code]
    p = price_provider(inst)
    bars = p.get_daily(inst, date.today() - timedelta(days=14))
    q = p.official_quote(inst)
    row = bars.frame[bars.frame.index.date == q.session_date]
    if row.empty:
        pytest.skip(f"FinMind 尚未更新 {q.session_date} 的資料")
    assert row["volume"].iloc[0] == q.volume and row["turnover"].iloc[0] == q.turnover
    assert q.kind == DataKind.HISTORICAL


@pytest.mark.skipif(not os.getenv("RUN_ALPHAVANTAGE"), reason="會用掉 Alpha Vantage 每日 25 次額度，設 RUN_ALPHAVANTAGE=1 才執行")
def test_live_us_against_alpha_vantage():
    from core.providers.sources import AlphaVantagePriceProvider

    inst = M["US"]["AAPL"]
    ours = price_provider(inst).get_daily(inst, date.today() - timedelta(days=14)).frame
    theirs = AlphaVantagePriceProvider().get_daily(inst, date.today() - timedelta(days=14)).frame
    common = ours.index.intersection(theirs.index)
    d = common[-1]
    assert abs(ours.loc[d, "close"] / theirs.loc[d, "close"] - 1) < 0.001
    assert abs(ours.loc[d, "volume"] / theirs.loc[d, "volume"] - 1) < 0.001   # Yahoo 四捨五入到百股
