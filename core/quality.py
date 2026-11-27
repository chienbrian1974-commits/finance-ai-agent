"""正規化資料（Bars）的品質檢查。

檢查項目：缺漏交易日、重複資料、缺值、OHLC 不合理、非正價格、異常跳動、資料過期，
並整理出每根 K 棒的時效類別（即時／延遲／歷史／無法取得）。
原則：只報告問題，不補資料、不改數字；重複資料保留最後一筆並註明。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

import pandas as pd

from .providers.base import Bars, DataKind
from .validation import expected_last_session, is_session, local_now, sessions_between


def _previous_session(inst, d: date) -> date:
    d -= timedelta(days=1)
    while not is_session(inst, d)[0]:
        d -= timedelta(days=1)
    return d


@dataclass
class QualityReport:
    kind: DataKind                       # 最新一根的時效類別
    last_session: date | None
    stale_sessions: int = 0
    missing_sessions: list[date] = field(default_factory=list)
    duplicates: int = 0
    anomalies: list[str] = field(default_factory=list)
    messages: list[tuple[str, str]] = field(default_factory=list)  # (info / warning / error, 說明)

    @property
    def ok(self) -> bool:
        return not any(level == "error" for level, _ in self.messages)


def check(bars: Bars, now_utc: datetime | None = None) -> tuple[Bars, QualityReport]:
    """回傳（去除重複後的 Bars, 品質報告）。"""
    inst = bars.instrument
    if bars.empty:
        rep = QualityReport(DataKind.UNAVAILABLE, None)
        rep.messages.append(("error", f"{bars.source} 沒有回傳 {inst.label} 的資料，無法顯示，也不會用其他數字代替。"))
        return bars, rep

    frame = bars.frame
    msgs: list[tuple[str, str]] = []

    # 1. 重複資料
    dup = int(frame.index.duplicated(keep="last").sum())
    if dup:
        frame = frame[~frame.index.duplicated(keep="last")]
        msgs.append(("warning", f"來源回傳了 {dup} 筆重複日期的資料，已保留每個日期的最後一筆。"))
    adjusted = bars.adjusted
    if adjusted is not None:
        adjusted = adjusted[~adjusted.index.duplicated(keep="last")]

    # 2. 缺值
    na_rows = frame[frame[["open", "high", "low", "close"]].isna().any(axis=1)]
    if len(na_rows):
        msgs.append(("warning", f"{len(na_rows)} 個交易日的開高低收有缺值：{', '.join(f'{d:%Y-%m-%d}' for d in na_rows.index[:5])}。"))

    # 3. 缺漏交易日（依交易所行事曆，只檢查有行事曆可依據的日期）
    missing: list[date] = []
    have = set(frame.index.date)
    d, last = frame.index[0].date(), frame.index[-1].date()
    while d <= last:
        ok, used_cal = is_session(inst, d)
        if ok and used_cal and d not in have:
            missing.append(d)
        d += timedelta(days=1)
    if missing:
        listed = "、".join(f"{x:%m/%d}" for x in missing[:6]) + ("…" if len(missing) > 6 else "")
        msgs.append(("warning", f"依交易所行事曆，期間內有 {len(missing)} 個交易日沒有資料：{listed}"
                                "（可能是個股暫停交易，或資料來源缺漏）。"))

    # 4. OHLC 合理性、非正價格、負成交量
    anomalies: list[str] = []
    body_hi = frame[["open", "close"]].max(axis=1)
    body_lo = frame[["open", "close"]].min(axis=1)
    bad_ohlc = frame[(frame["high"] < body_hi - 1e-9) | (frame["low"] > body_lo + 1e-9) | (frame["high"] < frame["low"])]
    anomalies += [f"{x:%Y-%m-%d} 開高低收不合理（最高價低於實體或最低價高於實體）" for x in bad_ohlc.index[:5]]
    nonpos = frame[(frame[["open", "high", "low", "close"]] <= 0).any(axis=1)]
    anomalies += [f"{x:%Y-%m-%d} 出現 0 或負的價格" for x in nonpos.index[:5]]
    negvol = frame[frame["volume"] < 0]
    anomalies += [f"{x:%Y-%m-%d} 成交量為負數" for x in negvol.index[:5]]

    # 5. 異常跳動（用還原價計算，除權息造成的缺口不算）
    base = adjusted if adjusted is not None else frame
    chg = base["close"].pct_change() * 100
    limit = inst.info.price_limit_pct if inst.asset_type in ("stock", "preferred") else None
    threshold = (limit + 0.5) if limit else 40.0
    for ts, v in chg.dropna().items():
        if abs(v) > threshold:
            rule = f"超過 {limit:.0f}% 漲跌幅限制" if limit else f"單日漲跌超過 {threshold:.0f}%"
            anomalies.append(f"{ts:%Y-%m-%d} {rule}（{v:+.1f}%）")
    for a in anomalies:
        msgs.append(("warning", f"資料異常：{a}，請自行查證。"))

    # 6. 時效
    last_row = frame.iloc[-1]
    kind = DataKind(last_row["kind"])
    if kind == DataKind.REALTIME:
        # 目前沒有任何授權的即時來源；出現代表程式標錯，寧可報錯也不誤導
        msgs.append(("error", "資料被標示為即時，但目前沒有授權的即時來源，請回報此錯誤。"))
    expected = expected_last_session(inst, now_utc)
    today = local_now(inst, now_utc).date()
    if expected == today and last < today:
        # 今天的 K 棒：只提供收盤後資料的來源（例如 FinMind）要等收盤後才會更新，不算過期
        msgs.append(("info", f"今天（{today:%m/%d}）的資料尚未納入：此來源在收盤後才更新，目前顯示到 {last:%m/%d} 的收盤資料。"))
        expected = _previous_session(inst, today)
    stale = sessions_between(inst, last, expected)[0] if last < expected else 0
    if kind == DataKind.DELAYED:
        msgs.append(("info", f"最新一根（{last:%m/%d}）是盤中資料，屬於「延遲」資料，不是即時報價；價格會變動，成交量仍在累積。"))
    if stale:
        msgs.append(("warning" if stale <= 2 else "error",
                     f"最新資料停在 {last:%Y-%m-%d}，依交易所行事曆比預期少了 {stale} 個交易日，資料可能尚未更新。"))
    if frame["turnover"].isna().all():
        msgs.append(("info", "此資料來源沒有提供成交金額，相關欄位標示為無法取得（不會用價格×成交量估算）。"))

    cleaned = Bars(inst, frame, adjusted, bars.source, bars.fetched_at, bars.volume_unit, list(bars.notes))
    rep = QualityReport(kind, last, stale, missing, dup, anomalies, msgs)
    return cleaned, rep


def describe_kind(kind: DataKind, session: date | None, as_of: datetime | None) -> str:
    """給畫面用的一句話時效說明。"""
    if kind == DataKind.UNAVAILABLE:
        return "無法取得"
    when = f"{session:%Y-%m-%d}" if session else "—"
    stamp = pd.Timestamp(as_of).tz_convert("Asia/Taipei").strftime("%m/%d %H:%M") if as_of else "—"
    return {
        DataKind.REALTIME: f"即時（{stamp} 台北時間）",
        DataKind.DELAYED: f"延遲：{when} 盤中資料（抓取於 {stamp} 台北時間）",
        DataKind.HISTORICAL: f"歷史：{when} 收盤資料",
    }[kind]
