"""事實（Fact）與事實清單（FactSheet）：AI 唯一可以引用的資料。

每個事實都有：編號、說明、數值、單位、資料日期、來源、時效類別。
"""
from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass, field
from datetime import date


@dataclass(frozen=True)
class Fact:
    id: str              # 例如 "tech.rsi14"
    label: str           # 例如 "RSI(14)"
    value: float | str   # 數值，或文字型事實（例如 "多頭排列"）
    unit: str = ""
    as_of: str = ""      # 資料日期（交易所當地）
    source: str = ""
    kind: str = "歷史"    # 歷史 / 延遲 / 無法取得
    decimals: int = 2

    def display(self) -> str:
        if isinstance(self.value, (int, float)) and not isinstance(self.value, bool):
            if math.isnan(self.value):
                return "無法取得"
            return f"{self.value:,.{self.decimals}f}{(' ' + self.unit) if self.unit else ''}"
        return f"{self.value}{(' ' + self.unit) if self.unit else ''}"


@dataclass
class FactSheet:
    subject: str                               # 例如 "2330 台積電"
    facts: dict[str, Fact] = field(default_factory=dict)
    missing: list[str] = field(default_factory=list)   # 無法取得的項目（要讓 AI 明說）

    def add(self, fact: Fact) -> Fact:
        if isinstance(fact.value, float) and math.isnan(fact.value):
            self.missing.append(fact.label)
        else:
            self.facts[fact.id] = fact
        return fact

    def get(self, fid: str) -> Fact | None:
        return self.facts.get(fid)

    def merge(self, other: "FactSheet") -> "FactSheet":
        self.facts.update(other.facts)
        self.missing += [m for m in other.missing if m not in self.missing]
        return self

    def numbers(self) -> list[float]:
        return [float(f.value) for f in self.facts.values()
                if isinstance(f.value, (int, float)) and not isinstance(f.value, bool)]

    def to_prompt(self) -> str:
        """給 AI 的事實清單（JSON），只含可引用的資料。"""
        rows = [{"id": f.id, "label": f.label, "value": f.display(), "as_of": f.as_of, "source": f.source,
                 "kind": f.kind} for f in self.facts.values()]
        return json.dumps({"subject": self.subject, "facts": rows, "unavailable": self.missing},
                          ensure_ascii=False, indent=1)

    def as_dict(self) -> dict:
        return {"subject": self.subject, "facts": [asdict(f) for f in self.facts.values()], "missing": self.missing}


def iso(d: date | None) -> str:
    return f"{d:%Y-%m-%d}" if d else ""
