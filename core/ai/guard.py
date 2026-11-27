"""AI 回答查核：回答裡出現的每個數字，都必須能在事實清單中找到依據。

允許的數字：
- 事實清單裡的數值（容許四捨五入、百分比、千分位、台股張／股換算等表示法差異）
- 指標的參數與常用門檻（例如 14、20、60、70、30、80），以及步驟編號等小整數
其他數字一律視為「沒有依據」，整段 AI 回答不顯示。
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from .facts import FactSheet

# 技術分析的標準參數與門檻，屬於「定義」而不是資料，可以出現在說明中
ALLOWED_CONSTANTS = {0, 1, 2, 3, 5, 9, 10, 12, 14, 20, 26, 30, 50, 60, 70, 80, 100, 120, 240, 250}
_NUM = re.compile(r"(?<![\w.])[-+]?\d[\d,]*(?:\.\d+)?")


@dataclass
class GuardResult:
    ok: bool
    unsupported: list[str]


def _candidates(text: str) -> list[tuple[str, float, int]]:
    """回傳 (原文, 數值, 寫出的小數位數)。"""
    out = []
    for m in _NUM.finditer(text):
        raw = m.group(0)
        try:
            dec = len(raw.split(".")[1]) if "." in raw else 0
            out.append((raw, float(raw.replace(",", "")), dec))
        except ValueError:
            continue
    return out


# 允許的單位換算：股↔張、元↔億／萬、美元↔百萬／十億、比例↔百分比
_SCALES = (1, 1e-3, 1e3, 1e-4, 1e-8, 1e-6, 1e-9, 100, 0.01)


def _matches(x: float, dec: int, ref: float) -> bool:
    """x 必須等於「事實值（換算單位後）四捨五入到 x 寫出的小數位數」。

    例如事實 2,590.00：寫 2,590 或 2,590.0 都可以，寫 2,600 不行（不是四捨五入能得到的）。
    """
    tol = 0.5 * 10 ** (-dec) + 1e-9
    for scale in _SCALES:
        v = ref * scale
        if abs(x - v) <= tol:
            return True
    return False


_ISO = re.compile(r"(?<!\d)(\d{4})-(\d{1,2})-(\d{1,2})(?!\d)")
_MD = re.compile(r"(?<![\d/])(\d{1,2})/(\d{1,2})(?![\d/])")


def _fact_dates(facts: FactSheet) -> set[tuple[int, int, int | None]]:
    """事實裡出現過的日期（資料日期、文字型事實中的日期），存成 (月, 日, 年) 與 (月, 日, None)。"""
    out: set[tuple[int, int, int | None]] = set()
    blobs = [f.as_of for f in facts.facts.values()] + [str(f.value) for f in facts.facts.values() if isinstance(f.value, str)]
    for b in blobs:
        for y, m, d in _ISO.findall(b):
            out |= {(int(m), int(d), int(y)), (int(m), int(d), None)}
    return out


def check_numbers(text: str, facts: FactSheet, extra_allowed: set[float] | None = None) -> GuardResult:
    refs = facts.numbers()
    allowed = ALLOWED_CONSTANTS | (extra_allowed or set())
    bad: list[str] = []
    # 日期也是一種事實：必須出現在事實清單裡；查完後從文字移除，避免被當成一般數字
    known = _fact_dates(facts)
    for m in _ISO.finditer(text):
        if (int(m.group(2)), int(m.group(3)), int(m.group(1))) not in known:
            bad.append(m.group(0))
    text = _ISO.sub(" ", text)
    for m in _MD.finditer(text):
        if (int(m.group(1)), int(m.group(2)), None) not in known:
            bad.append(m.group(0))
    text = _MD.sub(" ", text)
    for raw, x, dec in _candidates(text):
        ax = abs(x)
        if ax in allowed or (ax.is_integer() and ax <= 12):   # 步驟編號、月份等小整數
            continue
        if 1900 <= ax <= 2100 and ax.is_integer():            # 年份
            continue
        if any(_matches(ax, dec, abs(r)) for r in refs):
            continue
        bad.append(raw)
    return GuardResult(not bad, bad)
