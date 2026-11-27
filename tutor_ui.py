"""個股研究頁的「技術分析教學」區塊。

每個指標：原理 → 這檔股票現在的數據（規則式解讀）→ 不同可能性 → 已驗證事實 → 小測驗 → AI 家教（選用）。
AI 回答分成「已驗證事實／AI 解讀／不確定」三部分顯示；查核不通過時不顯示 AI 內容。
"""
from __future__ import annotations

import pandas as pd
import streamlit as st

from core.ai.llm import get_backend
from core.ai.tutor import explain
from core.analysis.technical import TechnicalReport, analyze
from core.instruments import Instrument
from core.net import ProviderError, log


@st.cache_data(ttl=900, show_spinner=False)
def _report(inst: Instrument) -> TechnicalReport:
    return analyze(inst)


def _facts_table(sheet, ids: list[str] | None = None) -> pd.DataFrame:
    facts = [sheet.facts[i] for i in ids if i in sheet.facts] if ids else list(sheet.facts.values())
    return pd.DataFrame([{"項目": f.label, "數值": f.display(), "資料日期": f.as_of, "時效": f.kind, "來源": f.source}
                         for f in facts])


def _render_ai(rep: TechnicalReport, section) -> None:
    backend = get_backend()
    key = f"ai_{rep.instrument.symbol}_{section.key}_{rep.as_of}"
    c1, c2 = st.columns([1, 3])
    if c1.button("🤖 請 AI 家教解釋", key=f"btn_{key}"):
        with st.spinner("AI 家教整理中…（只會使用上方的已驗證事實）"):
            st.session_state[key] = explain(section.title, rep.facts, "\n".join(section.reading))
    c2.caption(f"AI：{backend.name} {getattr(backend, 'model', '')}。AI 只能引用已驗證事實，回答中的每個數字與日期都會被查核。")
    ans = st.session_state.get(key)
    if ans is None:
        return
    if ans.status == "unavailable":
        st.info(f"⛔ AI 家教目前無法使用：{ans.reason}　上方的規則式解說仍然有效。")
        return
    if ans.status == "rejected":
        st.warning(f"⚠️ AI 的回答沒有通過資料查核（{ans.reason}），因此不顯示。請參考上方依資料產生的解說，或再試一次。")
        return
    st.markdown("**✅ 已驗證事實**（AI 引用的資料）")
    st.dataframe(_facts_table(rep.facts, ans.facts_used), hide_index=True, width="stretch")
    st.markdown("**🤖 AI 解讀**（依上述事實的說明，不是預測）")
    st.write(ans.explanation)
    for p in ans.possibilities:
        st.markdown(f"- **{p.get('scenario', '')}**：{p.get('what_to_watch', '')}")
    unsure = list(ans.uncertain) + [f"{m}：目前無法取得" for m in rep.facts.missing]
    if unsure:
        st.markdown("**❓ 不確定／資料不足**")
        for u in unsure:
            st.markdown(f"- {u}")
    if ans.question:
        st.markdown(f"**💭 想一想：** {ans.question}")
    st.caption(f"由 {ans.model} 產生，已通過數字與用語查核。AI 可能出錯，請以資料與自己的判斷為準；不構成投資建議。")


def render_technical_tutor(inst: Instrument) -> None:
    st.subheader("📚 技術分析教學")
    try:
        with st.spinner("計算技術指標中…"):
            rep = _report(inst)
    except (ProviderError, ValueError) as e:
        log.warning("技術分析失敗 %s：%s", inst.symbol, e)
        st.info(f"⛔ 無法進行技術分析：{getattr(e, 'reason', e)}")
        return
    st.caption(f"資料到 {rep.as_of:%Y-%m-%d}（{rep.kind.value}資料）・指標以{rep.basis}計算・"
               "每一段都先講原理，再看這檔股票現在的真實數據，最後用小測驗確認理解。")

    titles = [s.title for s in rep.sections]
    pick = st.radio("選擇要學的指標", titles, horizontal=True, key=f"ta_pick_{inst.symbol}")
    section = rep.sections[titles.index(pick)]

    st.markdown(f"#### {section.title}")
    st.markdown(f"**原理**：{section.principle}")
    st.markdown(f"**看看 {inst.code} 現在**（依資料自動產生）")
    for line in section.reading:
        st.markdown(f"- {line}")
    st.markdown("**可能的解讀（沒有標準答案，請自己判斷）**")
    for p in section.possibilities:
        st.markdown(f"- {p}")
    with st.expander("這一段用到的資料（已驗證事實）"):
        st.dataframe(_facts_table(section.facts), hide_index=True, width="stretch")

    if section.quiz:
        q = section.quiz
        st.markdown(f"**🧠 小測驗：{q.question}**")
        choice = st.radio(q.question, q.options, index=None, horizontal=True, label_visibility="collapsed",
                          key=f"quiz_{inst.symbol}_{section.key}_{rep.as_of}")
        if choice is not None:
            (st.success if choice == q.answer else st.warning)(
                ("✅ 答對了！" if choice == q.answer else f"❌ 答案是「{q.answer}」。") + q.why)

    _render_ai(rep, section)

    st.markdown("#### 比較不同時間週期")
    tf = rep.timeframes.copy()
    for col in ("收盤", "20 期均線", "RSI(14)"):
        tf[col] = tf[col].map(lambda v: "—" if pd.isna(v) else f"{v:,.2f}")
    st.dataframe(tf, hide_index=True, width="stretch")
    st.caption("同一檔股票在不同週期可能出現不同方向：例如日線偏空、月線偏多，代表短期回檔但長期趨勢仍向上。"
               "判斷規則：收盤在 20 期均線之上且均線往上＝偏多；兩者都相反＝偏空；其餘＝整理。")

    st.markdown(f"#### 歷史案例：{inst.code} 過去發生類似情況之後")
    rows = []
    for c in rep.cases:
        rows.append({"情況": c.kind, "發生次數": c.n,
                     "20 日後上漲比例": "—" if c.up_ratio20 is None else f"{c.up_ratio20:.0f}%",
                     "20 日後報酬中位數": "—" if c.median20 is None else f"{c.median20:+.2f}%",
                     "最近幾次（日期：20 日後報酬）": "；".join(
                         f"{x.date:%Y-%m-%d}：{'未滿 20 日' if x.ret20 is None else f'{x.ret20:+.1f}%'}" for x in c.recent) or "—"})
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
    with st.expander("這些案例怎麼看？"):
        for c in rep.cases:
            st.markdown(f"- **{c.kind}**：{c.explanation}")
        st.markdown("- 報酬以還原價計算，代表「事件發生當天收盤買進、持有 20 個交易日」的漲跌幅，只用來理解指標過去的表現。")
    st.warning("歷史統計只描述過去，不代表未來會重複發生；樣本數少時參考價值更低。這些資料不構成投資建議。")
