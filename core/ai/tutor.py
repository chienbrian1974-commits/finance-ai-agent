"""AI 家教：把事實清單交給 LLM，取得「初學者看得懂」的解說，並在顯示前查核。

查核不通過（數字對不上、引用不存在的事實、出現買賣訊號或保證用語）時，
回傳 status="rejected"，畫面只顯示規則式解說。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .facts import FactSheet
from .guard import check_numbers
from .llm import LLMUnavailable, get_backend

SYSTEM = """你是耐心的股票研究家教，對象是完全沒有基礎的初學者，請用繁體中文回答。
嚴格規則：
1. 只能使用使用者提供的 facts 裡的數字與事實。不可自行補充任何數字、價格、日期、新聞或財報資料。
2. 引用數字時要照 facts 的值寫，並在 facts_used 列出用到的事實 id。
3. facts 的 unavailable 列出的是無法取得的資料：如果和問題有關，要明確說「目前無法取得」，不可猜測。
4. 不可預測未來股價，不可給買進、賣出、加碼、停損價或目標價等操作建議，不可保證獲利。
5. 要解釋「為什麼」：先說指標的原理，再說目前數據代表什麼。
6. 至少提出兩種可能的解讀（例如偏多與偏空的情境），並說明各自要觀察什麼來驗證。
7. 最後鼓勵使用者自己判斷，並提出一個讓使用者思考的問題。"""

SCHEMA = {
    "type": "object",
    "properties": {
        "beginner_explanation": {"type": "string", "description": "用初學者聽得懂的方式解釋原理與目前數據"},
        "possibilities": {"type": "array", "items": {"type": "object", "properties": {
            "scenario": {"type": "string"}, "what_to_watch": {"type": "string"}},
            "required": ["scenario", "what_to_watch"]}},
        "uncertain": {"type": "array", "items": {"type": "string"},
                      "description": "資料不足或無法確定的地方"},
        "facts_used": {"type": "array", "items": {"type": "string"}},
        "question_for_user": {"type": "string"},
    },
    "required": ["beginner_explanation", "possibilities", "uncertain", "facts_used", "question_for_user"],
}

# 買賣訊號、保證獲利、確定性預測的用語
FORBIDDEN = re.compile(r"建議(買進|賣出|加碼|減碼|進場|出場)|(應該|可以|趕快)(買|賣)|目標價|停損價|保證|穩賺|必漲|必跌|一定會(漲|跌)|勢必(上漲|下跌)")


@dataclass
class TutorAnswer:
    status: str                      # ok / unavailable / rejected
    explanation: str = ""
    possibilities: list[dict] = field(default_factory=list)
    uncertain: list[str] = field(default_factory=list)
    facts_used: list[str] = field(default_factory=list)
    question: str = ""
    model: str = ""
    reason: str = ""                 # unavailable / rejected 的原因


def _all_text(d: dict) -> str:
    parts = [d.get("beginner_explanation", ""), d.get("question_for_user", "")]
    parts += [p.get("scenario", "") + " " + p.get("what_to_watch", "") for p in d.get("possibilities", [])]
    parts += d.get("uncertain", [])
    return "\n".join(parts)


def review(data: dict, facts: FactSheet) -> tuple[bool, str]:
    """回傳 (是否通過, 不通過原因)。"""
    text = _all_text(data)
    unknown = [f for f in data.get("facts_used", []) if f not in facts.facts]
    if unknown:
        return False, f"引用了不存在的事實：{', '.join(unknown[:3])}"
    g = check_numbers(text, facts)
    if not g.ok:
        return False, f"出現沒有資料依據的數字：{', '.join(g.unsupported[:5])}"
    m = FORBIDDEN.search(text)
    if m:
        return False, f"出現買賣建議或保證用語「{m.group(0)}」"
    if not data.get("beginner_explanation", "").strip():
        return False, "沒有解說內容"
    return True, ""


def explain(topic: str, facts: FactSheet, rule_summary: str = "", question: str | None = None) -> TutorAnswer:
    backend = get_backend()
    task = question or f"請用初學者聽得懂的方式，解釋「{topic}」這部分的原理，以及目前數據代表什麼。"
    prompt = (f"研究對象：{facts.subject}\n主題：{topic}\n\n"
              f"facts（只能使用這些資料）：\n{facts.to_prompt()}\n\n"
              + (f"程式依規則整理的現況（可參考，不可加入新數字）：\n{rule_summary}\n\n" if rule_summary else "")
              + f"使用者的問題：{task}")
    try:
        data = backend.generate_json(SYSTEM, prompt, SCHEMA)
    except LLMUnavailable as e:
        return TutorAnswer("unavailable", reason=str(e), model=backend.name)
    ok, why = review(data, facts)
    if not ok:
        return TutorAnswer("rejected", reason=why, model=f"{backend.name} {getattr(backend, 'model', '')}".strip())
    return TutorAnswer("ok", data["beginner_explanation"], data.get("possibilities", []), data.get("uncertain", []),
                       data.get("facts_used", []), data.get("question_for_user", ""),
                       f"{backend.name} {getattr(backend, 'model', '')}".strip())
