"""跨市場股票搜尋：代號、公司名稱，可限定台股或美股。只搜尋官方代號主檔。"""
from __future__ import annotations

from dataclasses import dataclass

from .instruments import Instrument, load_master


@dataclass(frozen=True)
class SearchResult:
    instrument: Instrument
    score: int       # 越小越相關
    matched: str     # "代號" / "名稱"

    @property
    def display(self) -> str:
        i = self.instrument
        return f"{i.code}　{i.company_name}（{i.market}・{i.exchange_label}・{i.asset_label}）"


def _norm_code(s: str) -> str:
    return s.upper().replace("-", ".").replace("/", ".")


def search(query: str, market: str | None = None, limit: int = 20,
           master: dict[str, dict[str, Instrument]] | None = None) -> list[SearchResult]:
    """依相關度排序：代號完全符合 > 代號開頭符合 > 名稱完全符合 > 名稱開頭符合 > 名稱包含。
    同分時一般股排在 ETF 前面、台股依代號、名稱較短者優先。"""
    master = master or load_master()
    q = query.strip()
    if not q:
        return []
    qc, ql = _norm_code(q), q.lower()
    markets = [market] if market in ("TW", "US") else ["TW", "US"]
    out: list[SearchResult] = []
    for mk in markets:
        for code, inst in master.get(mk, {}).items():
            c, name = _norm_code(code), inst.company_name.lower()
            if c == qc:
                out.append(SearchResult(inst, 0, "代號"))
            elif len(qc) >= 1 and c.startswith(qc) and (qc[:1].isdigit() or len(qc) >= 2 or mk == "US"):
                out.append(SearchResult(inst, 10 + len(c) - len(qc), "代號"))
            elif name == ql:
                out.append(SearchResult(inst, 1, "名稱"))
            elif len(ql) >= 2 and name.startswith(ql):
                out.append(SearchResult(inst, 20, "名稱"))
            elif len(ql) >= 2 and ql in name:
                out.append(SearchResult(inst, 30, "名稱"))
    out.sort(key=lambda r: (r.score, r.instrument.asset_type != "stock", len(r.instrument.company_name), r.instrument.code))
    return out[:limit]
