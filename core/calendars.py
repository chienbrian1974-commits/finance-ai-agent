"""交易日曆來源。

- 台股：優先用 FinMind 的「台股交易日」清單（整理自證交所公告）。
  實測 exchange_calendars 的 XTAI 在 2026 年有缺漏（例如把 10/23 休市日當成交易日），所以只當備援。
- 美股：exchange_calendars 的 XNYS。
設定環境變數 FINANCE_OFFLINE=1 時不連網（單元測試用），直接使用 exchange_calendars。
"""
from __future__ import annotations

import os
from datetime import date, timedelta

from .net import ProviderError, cache, http_get, log

FINMIND_URL = "https://api.finmindtrade.com/api/v4/data"


def tw_trading_days() -> tuple[date, date, frozenset[date]] | None:
    """回傳 (最早日期, 最晚日期, 交易日集合)；取不到時回傳 None。快取 12 小時。"""
    if os.getenv("FINANCE_OFFLINE"):
        return None
    key = ("calendar", "TW")
    hit = cache.get(key)
    if hit is not None:
        return hit or None
    start = date.today() - timedelta(days=800)
    try:
        body = http_get("finmind", FINMIND_URL, params={"dataset": "TaiwanStockTradingDate",
                                                        "start_date": f"{start:%Y-%m-%d}"}, timeout=60).json()
        days = frozenset(date.fromisoformat(r["date"]) for r in body.get("data", []))
    except (ProviderError, ValueError, KeyError) as e:
        log.warning("台股交易日清單取得失敗，改用 XTAI 行事曆：%s", e)
        cache.set(key, (), 600)  # 失敗時 10 分鐘內不重試
        return None
    if not days:
        cache.set(key, (), 600)
        return None
    value = (min(days), max(days), days)
    cache.set(key, value, 43200)
    return value
