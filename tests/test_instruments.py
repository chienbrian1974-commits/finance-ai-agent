"""代號解析：用 repo 內的官方代號主檔（data/symbols），不需要網路。"""
import pytest

from core.instruments import load_master, resolve
from core.symbols_update import classify_tw

MASTER = load_master()

# (輸入, 內部代號, 市場, 交易所, 類型, 幣別, 時區, Yahoo 代號)
CASES = [
    ("2330", "TW:2330", "TW", "TWSE", "stock", "TWD", "Asia/Taipei", "2330.TW"),
    ("6488", "TW:6488", "TW", "TPEX", "stock", "TWD", "Asia/Taipei", "6488.TWO"),
    ("0050", "TW:0050", "TW", "TWSE", "etf", "TWD", "Asia/Taipei", "0050.TW"),
    ("00878", "TW:00878", "TW", "TWSE", "etf", "TWD", "Asia/Taipei", "00878.TW"),
    ("006208", "TW:006208", "TW", "TWSE", "etf", "TWD", "Asia/Taipei", "006208.TW"),
    ("00631L", "TW:00631L", "TW", "TWSE", "etf_leveraged", "TWD", "Asia/Taipei", "00631L.TW"),
    ("00632r", "TW:00632R", "TW", "TWSE", "etf_inverse", "TWD", "Asia/Taipei", "00632R.TW"),
    ("00679B", "TW:00679B", "TW", "TPEX", "etf_bond", "TWD", "Asia/Taipei", "00679B.TWO"),
    ("00400A", "TW:00400A", "TW", "TWSE", "etf_active", "TWD", "Asia/Taipei", "00400A.TW"),
    ("2881A", "TW:2881A", "TW", "TWSE", "preferred", "TWD", "Asia/Taipei", "2881A.TW"),
    ("台積電", "TW:2330", "TW", "TWSE", "stock", "TWD", "Asia/Taipei", "2330.TW"),
    ("TW:2330", "TW:2330", "TW", "TWSE", "stock", "TWD", "Asia/Taipei", "2330.TW"),
    ("AAPL", "US:AAPL", "US", "NASDAQ", "stock", "USD", "America/New_York", "AAPL"),
    ("aapl", "US:AAPL", "US", "NASDAQ", "stock", "USD", "America/New_York", "AAPL"),
    ("BRK.B", "US:BRK.B", "US", "NYSE", "stock", "USD", "America/New_York", "BRK-B"),
    ("BRK-B", "US:BRK.B", "US", "NYSE", "stock", "USD", "America/New_York", "BRK-B"),
    ("SPY", "US:SPY", "US", "NYSE Arca", "etf", "USD", "America/New_York", "SPY"),
    ("QQQ", "US:QQQ", "US", "NASDAQ", "etf", "USD", "America/New_York", "QQQ"),
    ("TSM", "US:TSM", "US", "NYSE", "stock", "USD", "America/New_York", "TSM"),
    ("us: tsm", "US:TSM", "US", "NYSE", "stock", "USD", "America/New_York", "TSM"),
]


@pytest.mark.parametrize("raw,symbol,market,exchange,asset,ccy,tz,yahoo", CASES)
def test_resolve(raw, symbol, market, exchange, asset, ccy, tz, yahoo):
    inst = resolve(raw, MASTER).instrument
    assert inst is not None, raw
    assert (inst.symbol, inst.market, inst.exchange, inst.asset_type) == (symbol, market, exchange, asset)
    assert (inst.currency, inst.timezone, inst.yahoo) == (ccy, tz, yahoo)
    assert inst.company_name, "名稱必須來自官方主檔"
    assert inst.verified


def test_unknown_symbol_is_not_invented():
    res = resolve("ZZZZ9Q", MASTER)
    assert res.instrument is None and not res.candidates
    assert "找不到" in res.note


def test_name_search_with_many_hits_asks_user_to_choose():
    res = resolve("國泰", MASTER)
    assert res.instrument is None
    assert len(res.candidates) > 1 and all(i.market == "TW" for i in res.candidates)


def test_markets_have_distinct_rules():
    tw, us = MASTER["TW"]["2330"], MASTER["US"]["AAPL"]
    assert tw.info.lot_size == 1000 and us.info.lot_size == 1
    assert tw.info.price_limit_pct == 10.0 and us.info.price_limit_pct is None
    assert tw.info.open_time != us.info.open_time


@pytest.mark.parametrize("code,expected", [
    ("2330", "stock"), ("0050", "etf"), ("0056", "etf"), ("006208", "etf"), ("00631L", "etf_leveraged"),
    ("00679B", "etf_bond"), ("2881A", "preferred"), ("2887Z1", "preferred"),
    ("030001", None), ("72572U", None),  # 權證不收錄
])
def test_classify_tw(code, expected):
    assert classify_tw(code) == expected
