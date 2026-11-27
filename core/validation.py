"""資料驗證：判斷資料是否完整、是否為盤中、是否過期、有沒有異常跳動。

原則：有問題就明確告訴使用者，不默默修正、不補資料。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from functools import lru_cache
from zoneinfo import ZoneInfo

import pandas as pd

from .instruments import Instrument
from .marketdata import PriceData

DISPLAY_TZ = ZoneInfo("Asia/Taipei")  # 網站使用者在台灣，「更新時間」一律用台北時間顯示


@dataclass
class DataStatus:
    last_bar: date | None
    phase: str                 # "盤前" / "盤中" / "已收盤" / "休市日"
    partial: bool              # 最新一根是盤中尚未收盤的 K 棒
    stale_sessions: int        # 比預期少了幾個交易日（0 = 正常）
    messages: list[tuple[str, str]] = field(default_factory=list)  # (level, text)，level: info / warning / error

    @property
    def ok(self) -> bool:
        return not any(level == "error" for level, _ in self.messages)


def local_now(inst: Instrument, now_utc: datetime | None = None) -> datetime:
    now_utc = now_utc or datetime.now(timezone.utc)
    return now_utc.astimezone(ZoneInfo(inst.timezone))


@lru_cache(maxsize=4)
def _calendar(code: str):
    """交易所行事曆（含國定假日）。套件不存在或載入失敗時回傳 None，改用「只排除週末」的規則。"""
    try:
        import exchange_calendars as xc

        return xc.get_calendar(code)
    except Exception:  # noqa: BLE001
        return None


def is_session(inst: Instrument, d: date) -> tuple[bool, bool]:
    """回傳 (是否交易日, 是否有用到行事曆)。超出行事曆涵蓋範圍時只排除週末。"""
    if inst.market == "TW":
        from .calendars import tw_trading_days

        tw = tw_trading_days()
        if tw is not None and tw[0] <= d <= tw[1]:
            return d in tw[2], True
    cal = _calendar(inst.info.calendar)
    if cal is not None and cal.first_session.date() <= d <= cal.last_session.date():
        return bool(cal.is_session(pd.Timestamp(d))), True
    return d.weekday() < 5, False


def market_phase(inst: Instrument, now_utc: datetime | None = None) -> str:
    now = local_now(inst, now_utc)
    if not is_session(inst, now.date())[0]:
        return "休市日"
    t = now.time()
    if t < inst.info.open_time:
        return "盤前"
    if t < inst.info.close_time:
        return "盤中"
    return "已收盤"


def expected_last_session(inst: Instrument, now_utc: datetime | None = None) -> date:
    """依當地時間與交易所行事曆，推算「最新一根 K 棒應該是哪一天」。"""
    now = local_now(inst, now_utc)
    d = now.date()
    if is_session(inst, d)[0] and now.time() >= inst.info.open_time:
        return d
    d -= timedelta(days=1)
    while not is_session(inst, d)[0]:
        d -= timedelta(days=1)
    return d


def sessions_between(inst: Instrument, after: date, until: date) -> tuple[int, bool]:
    """(after, until] 之間有幾個交易日；第二個值表示是否全程有行事曆可依據。"""
    n, used_cal, d = 0, True, after + timedelta(days=1)
    while d <= until:
        ok, cal = is_session(inst, d)
        n += ok
        used_cal &= cal
        d += timedelta(days=1)
    return n, used_cal


def fmt_display_time(dt_utc: datetime) -> str:
    return dt_utc.astimezone(DISPLAY_TZ).strftime("%Y-%m-%d %H:%M:%S（台北時間）")


def validate(data: PriceData, now_utc: datetime | None = None) -> DataStatus:
    inst = data.instrument
    msgs: list[tuple[str, str]] = []
    if not inst.verified:
        msgs.append(("warning", "這個代號不在官方代號主檔裡（可能是新上市或輸入有誤），名稱、交易所與類型無法驗證。"))

    if data.empty:
        msgs.append(("error", f"{data.source} 沒有回傳 {inst.label} 的資料。"))
        return DataStatus(None, market_phase(inst, now_utc), False, 0, msgs)

    last_bar = data.raw.index[-1].date()
    phase = market_phase(inst, now_utc)
    today = local_now(inst, now_utc).date()
    partial = last_bar == today and phase == "盤中"
    if partial:
        msgs.append(("info", f"目前{inst.exchange_label if inst.market == 'TW' else '美股'}盤中，最新一根 K 棒（{last_bar:%m/%d}）"
                             "尚未收盤：價格會變動，成交量也還在累積。"))

    expected = expected_last_session(inst, now_utc)
    stale, used_cal = sessions_between(inst, last_bar, expected) if last_bar < expected else (0, True)
    if stale > 0:
        level = "warning" if stale <= 2 else "error"
        why = ("依交易所行事曆，這段期間應該有交易，資料來源可能尚未更新。" if used_cal
               else "可能是國定假日休市，或資料來源尚未更新；請以交易所公告為準。")
        msgs.append((level, f"最新資料停在 {last_bar:%Y-%m-%d}，比預期少了 {stale} 個交易日。{why}"))

    if data.dropped:
        msgs.append(("warning", f"資料來源在 {', '.join(data.dropped)} 給的資料不完整（開高低收有缺值），已排除，不列入圖表與指標。"))

    # 異常跳動：單日漲跌超過合理範圍，且當天沒有除權息或分割
    # 台股漲跌幅限制只拿來檢查一般股與特別股；追蹤海外指數的 ETF 沒有漲跌幅限制
    limit = inst.info.price_limit_pct if inst.asset_type in ("stock", "preferred") else None
    threshold = (limit + 0.5) if limit else 40.0
    chg = data.raw["Close"].pct_change() * 100
    event_days = set(data.events.index) if not data.events.empty else set()
    jumps = [(ts, v) for ts, v in chg.dropna().items() if abs(v) > threshold and ts not in event_days]
    if jumps:
        listed = "、".join(f"{ts:%m/%d}（{v:+.1f}%）" for ts, v in jumps[:5])
        rule = f"超過 {limit:.0f}% 漲跌幅限制" if limit else f"單日漲跌超過 {threshold:.0f}%"
        msgs.append(("warning", f"發現 {len(jumps)} 筆{rule}的價格跳動：{listed}，且當天沒有除權息紀錄。可能是資料錯誤或特殊事件，請自行查證。"))

    return DataStatus(last_bar, phase, partial, stale, msgs)
