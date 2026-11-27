"""個股研究分頁：跨市場搜尋 → 個股研究頁。資料全部透過 core.providers（Provider 層）取得。

這一頁是第一個改用 Provider 層的頁面；「儀表板」與「教學」分頁暫時維持原本的資料流程。
"""
from __future__ import annotations

from datetime import date, timedelta
from zoneinfo import ZoneInfo

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from plotly.subplots import make_subplots

from core.indicators import enrich
from core.instruments import Instrument, load_master
from core.net import ProviderError, log
from core.providers.base import DataKind, Quote
from core.providers.registry import financial_provider, news_provider, price_provider
from core.quality import QualityReport, check
from core.search import search

UP, DOWN, S1, S2 = "#e34948", "#0ca30c", "#2a78d6", "#eb6834"
TAIPEI = ZoneInfo("Asia/Taipei")
KIND_BADGE = {DataKind.REALTIME: "🟢 即時", DataKind.DELAYED: "🟡 延遲", DataKind.HISTORICAL: "⚪ 歷史",
              DataKind.UNAVAILABLE: "⛔ 無法取得"}
RANGES = {"3 個月": 92, "6 個月": 183, "1 年": 366, "2 年": 731}
MARKET_FILTER = {"全部": None, "台股": "TW", "美股": "US"}


# ---- 格式化 ------------------------------------------------------------------
def fmt_time(ts, inst: Instrument) -> str:
    """同時顯示交易所當地時間與台北時間（台股兩者相同時只顯示一次）。"""
    if ts is None or pd.isna(ts):
        return "—"
    t = pd.Timestamp(ts)
    t = t.tz_localize("UTC") if t.tzinfo is None else t
    local = t.tz_convert(inst.timezone)
    tpe = t.tz_convert(TAIPEI)
    if inst.timezone == "Asia/Taipei":
        return f"{tpe:%Y-%m-%d %H:%M}（台北時間）"
    return f"{local:%Y-%m-%d %H:%M}（紐約時間）／{tpe:%m-%d %H:%M}（台北時間）"


def fmt_volume(v: float | None, inst: Instrument) -> str:
    if v is None or pd.isna(v):
        return "無法取得"
    if inst.market == "TW":
        return f"{v / 1000:,.0f} 張（{v:,.0f} 股）"
    return f"{v:,.0f} 股"


def fmt_money(v: float | None, inst: Instrument) -> str:
    if v is None or pd.isna(v):
        return "無法取得（來源未提供）"
    if inst.currency == "TWD":
        return f"{v / 1e8:,.2f} 億元"
    return f"{v / 1e6:,.1f} 百萬 USD"


def compact(v: float | None, unit: str, divisor: float = 1) -> str:
    """短格式：台股用萬／億，美股用 K／M／B。"""
    if v is None or pd.isna(v):
        return "—"
    x = v / divisor
    if unit in ("張", "元"):
        if x >= 1e8:
            return f"{x / 1e8:,.1f} 億{'' if unit == '元' else unit}"
        if x >= 1e4:
            return f"{x / 1e4:,.1f} 萬{unit if unit == '張' else ''}"
        return f"{x:,.0f} {unit}"
    for size, suffix in ((1e9, "B"), (1e6, "M"), (1e3, "K")):
        if x >= size:
            return f"{x / size:,.1f}{suffix}"
    return f"{x:,.0f}"


def fmt_fin(v: float, unit: str) -> str:
    if pd.isna(v):
        return "—"
    if "/" in unit:                        # EPS
        return f"{v:,.2f}"
    return f"{v / 1e8:,.1f} 億" if unit == "TWD" else f"{v / 1e9:,.2f} B"


# ---- 搜尋 --------------------------------------------------------------------
def _select(symbol: str) -> None:
    st.session_state["research_symbol"] = symbol
    st.query_params["symbol"] = symbol


def _clear() -> None:
    st.session_state.pop("research_symbol", None)
    st.query_params.pop("symbol", None)


def render_search() -> None:
    c1, c2 = st.columns([3, 1])
    q = c1.text_input("搜尋股票代號或公司名稱", key="research_query", placeholder="例如：2330、台積電、00631L、AAPL、apple、BRK.B")
    mk = c2.radio("市場", list(MARKET_FILTER), horizontal=True, key="research_market")
    if not q.strip():
        st.caption("輸入代號或名稱開始搜尋。資料來自證交所、櫃買中心與 Nasdaq Trader 的官方代號清單。")
        return
    results = search(q, MARKET_FILTER[mk], limit=20)
    if not results:
        st.warning(f"官方代號清單裡找不到「{q.strip()}」。請確認代號，或改用公司名稱搜尋。")
        return
    st.caption(f"找到 {len(results)} 筆{'（只顯示前 20 筆）' if len(results) == 20 else ''}，點選進入個股研究頁：")
    for r in results:
        i = r.instrument
        cols = st.columns([1.1, 3, 2.4, 1])
        cols[0].markdown(f"**{i.code}**")
        cols[1].write(i.company_name)
        cols[2].caption(f"{'台股' if i.market == 'TW' else '美股'}・{i.exchange_label}・{i.asset_label}")
        cols[3].button("研究 →", key=f"go_{i.symbol}", on_click=_select, args=(i.symbol,), width="stretch")


# ---- 研究頁 ------------------------------------------------------------------
def quote_from(frame: pd.DataFrame, inst: Instrument, source: str) -> Quote:
    last = frame.iloc[-1]
    prev = frame["close"].iloc[-2] if len(frame) > 1 else None
    return Quote(inst, float(last["close"]), float(prev) if prev is not None else None, frame.index[-1].date(),
                 last["ts_utc"], DataKind(last["kind"]), source,
                 None if pd.isna(last["volume"]) else float(last["volume"]),
                 None if pd.isna(last["turnover"]) else float(last["turnover"]))


def render_quote(q: Quote, adj_change: float | None, rep: QualityReport) -> None:
    inst = q.instrument
    c1, c2, c3, c4 = st.columns(4)
    c1.metric(f"{'最新價' if q.kind == DataKind.DELAYED else '收盤價'}（{inst.currency}）", f"{q.price:,.2f}",
              f"{adj_change:+.2f}%" if adj_change is not None else None, delta_color="inverse")
    c1.caption(KIND_BADGE[q.kind] + ("：盤中、尚未收盤" if q.kind == DataKind.DELAYED else "：已收盤資料"))
    # 標題數字用短格式（窄螢幕不會被截斷），完整數字放在下方說明
    c2.metric("成交量", compact(q.volume, "張" if inst.market == "TW" else "股", 1000 if inst.market == "TW" else 1))
    c2.caption(fmt_volume(q.volume, inst))
    c3.metric("成交金額", compact(q.turnover, "元" if inst.currency == "TWD" else "USD"))
    c3.caption("來源未提供，不估算" if q.turnover is None else fmt_money(q.turnover, inst))
    c4.metric("資料日期", f"{q.session_date:%m/%d}")
    c4.caption(f"{q.session_date:%Y-%m-%d}（交易所當地日期）")
    st.caption(f"資料時間：{fmt_time(q.as_of_utc, inst)}　｜　來源：{q.source}　｜　"
               "漲跌幅以還原價計算（已扣除除權息）")


def render_chart(bars_frame: pd.DataFrame, title: str, tw: bool) -> None:
    df = enrich(bars_frame.rename(columns={"open": "Open", "high": "High", "low": "Low", "close": "Close", "volume": "Volume"}))
    fig = make_subplots(rows=2, cols=1, shared_xaxes=True, vertical_spacing=0.05, row_heights=[0.72, 0.28],
                        subplot_titles=(title, "成交量（張）" if tw else "成交量（股）"))
    fig.add_trace(go.Candlestick(x=df.index, open=df["Open"], high=df["High"], low=df["Low"], close=df["Close"], name="K 線",
                                 increasing=dict(line=dict(color=UP, width=1), fillcolor=UP),
                                 decreasing=dict(line=dict(color=DOWN, width=1), fillcolor=DOWN)), row=1, col=1)
    fig.add_trace(go.Scatter(x=df.index, y=df["SMA20"], name="20 日均線", line=dict(color=S1, width=2)), row=1, col=1)
    fig.add_trace(go.Scatter(x=df.index, y=df["SMA60"], name="60 日均線", line=dict(color=S2, width=2)), row=1, col=1)
    vol = df["Volume"] / (1000 if tw else 1)
    fig.add_trace(go.Bar(x=df.index, y=vol, name="成交量", showlegend=False,
                         marker_color=[UP if c >= o else DOWN for c, o in zip(df["Close"], df["Open"])]), row=2, col=1)
    fig.update_layout(height=620, hovermode="x unified", margin=dict(l=10, r=10, t=40, b=10), bargap=0.15,
                      xaxis_rangeslider_visible=False,
                      legend=dict(orientation="h", yanchor="bottom", y=1.03, xanchor="left", x=0))
    fig.update_xaxes(rangebreaks=[dict(bounds=["sat", "mon"])])
    st.plotly_chart(fig, width="stretch", theme="streamlit")


def render_financials(inst: Instrument) -> None:
    st.subheader("財報重點")
    try:
        fin = financial_provider(inst).get_financials(inst)
    except ProviderError as e:
        log.warning("財報取得失敗 %s：%s", inst.symbol, e)
        st.info(f"⛔ 無法取得財報：{e.reason}")
        return
    if fin.kind == DataKind.UNAVAILABLE or not fin.items:
        st.info(f"⛔ 無法取得財報。{fin.note}")
        return
    units = {i.metric: i.unit for i in fin.items}
    t = fin.table().tail(8 if inst.market == "TW" else 5).iloc[::-1]
    names = {"revenue": "營收", "gross_profit": "毛利", "operating_income": "營業利益", "net_income": "稅後淨利", "eps": "EPS"}
    t = t[[c for c in names if c in t.columns]]
    shown = pd.DataFrame({names[c]: [fmt_fin(v, units[c]) for v in t[c]] for c in t.columns},
                         index=[f"{pe:%Y-%m-%d}（{p}）" for pe, p in t.index])
    st.dataframe(shown, width="stretch")
    unit_note = "金額單位：億元（新台幣）；EPS：元" if inst.market == "TW" else "金額單位：十億美元（B）；EPS：美元"
    st.caption(f"⚪ 歷史資料　｜　來源：{fin.source}　｜　{fin.note}　｜　{unit_note}")


def render_news(inst: Instrument) -> None:
    st.subheader("相關新聞")
    try:
        items = news_provider(inst).get_news(inst, 8)
    except ProviderError as e:
        log.warning("新聞取得失敗 %s：%s", inst.symbol, e)
        st.info(f"⛔ 無法取得新聞：{e.reason}")
        return
    if not items:
        st.caption("目前沒有相關新聞。")
        return
    for n in items:
        when = n.published_utc.astimezone(TAIPEI).strftime("%m/%d %H:%M") if n.published_utc else "時間不明"
        st.markdown(f"- [{n.title}]({n.url})　<span style='color:gray;font-size:0.85em'>{when}（台北）・{n.source}</span>",
                    unsafe_allow_html=True)
    st.caption(f"來源：{news_provider(inst).name}。新聞只列標題與連結，內容請以原始網站為準。")


def render_stock(inst: Instrument) -> None:
    st.button("← 回到搜尋", on_click=_clear)
    st.header(f"{inst.code} {inst.company_name}")
    st.caption(f"{'台股' if inst.market == 'TW' else '美股'}・{inst.exchange_label}・{inst.asset_label}・"
               f"幣別 {inst.currency}・交易所時區 {inst.timezone}・交易時段 "
               f"{inst.info.open_time:%H:%M}–{inst.info.close_time:%H:%M}（當地時間）")

    c1, c2 = st.columns([1, 1])
    span = c1.radio("期間", list(RANGES), index=1, horizontal=True, key="research_span")
    adjusted = c2.toggle("還原權值（除權息調整）", value=True, key="research_adj",
                         help="開啟：用還原價畫圖，除息缺口不會被誤判成下跌。關閉：交易所實際成交價。")
    provider = price_provider(inst)
    try:
        with st.spinner("抓取資料中…"):
            bars = provider.get_daily(inst, date.today() - timedelta(days=RANGES[span]))
    except ProviderError as e:
        log.warning("行情取得失敗 %s：%s", inst.symbol, e)
        st.error(f"⛔ 無法取得 {inst.label} 的行情資料：{e.reason}。不會用其他數字代替，請稍後再試。")
        return
    bars, rep = check(bars)
    if bars.empty:
        for level, text in rep.messages:
            getattr(st, level)(text)
        return

    base = bars.adjusted if (adjusted and bars.adjusted is not None) else bars.frame
    adj_close = (bars.adjusted if bars.adjusted is not None else bars.frame)["close"]
    change = (adj_close.iloc[-1] / adj_close.iloc[-2] - 1) * 100 if len(adj_close) > 1 else None
    render_quote(quote_from(bars.frame, inst, bars.source), change, rep)

    for level, text in rep.messages:
        getattr(st, level)(text)
    try:
        cc = provider.cross_check(inst, bars)
        if cc.ok is True:
            st.success(f"✅ 交叉比對：{cc.session_date:%Y-%m-%d} 收盤價 {cc.ours:,.2f}，與 {cc.other_source}（{cc.theirs:,.2f}）一致。")
        elif cc.ok is False:
            st.warning(f"⚠️ 交叉比對：{cc.session_date:%Y-%m-%d} 收盤價與 {cc.other_source} 差異 {cc.diff_pct:.2f}%"
                       f"（本頁 {cc.ours:,.2f}／對方 {cc.theirs:,.2f}），請自行查證。")
        else:
            st.caption(f"交叉比對：{cc.other_source} 目前沒有可比對的同日資料。")
    except ProviderError as e:
        st.caption(f"交叉比對：第二來源暫時無法取得（{e.reason}）。")

    basis = "還原價" if base is bars.adjusted else "原始價"
    if adjusted and bars.adjusted is None:
        st.caption("無法取得還原係數，圖表改用原始價。")
    render_chart(base, f"{inst.code} 價格（{basis}）", inst.market == "TW")
    with st.expander("資料來源說明"):
        for n in bars.notes:
            st.markdown(f"- {n}")
        st.markdown(f"- 抓取時間：{fmt_time(bars.fetched_at, inst)}")
        st.markdown("- 時效類別：⚪ 歷史＝已收盤；🟡 延遲＝盤中尚未收盤（免費來源都不是即時報價）；⛔ 無法取得")

    with st.expander("日 K 資料表（最近 20 個交易日，原始價）"):
        t = bars.frame.tail(20).iloc[::-1].copy()
        table = pd.DataFrame({
            "開": t["open"], "高": t["high"], "低": t["low"], "收": t["close"],
            "成交量": [fmt_volume(v, inst) for v in t["volume"]],
            "成交金額": [fmt_money(v, inst).split("（")[0] for v in t["turnover"]],
            "時效": [KIND_BADGE[DataKind(k)] for k in t["kind"]],
            "資料時間": [fmt_time(ts, inst) for ts in t["ts_utc"]],
        }, index=[f"{d:%Y-%m-%d}" for d in t.index])
        st.dataframe(table, width="stretch")

    col_f, col_n = st.columns([1.2, 1])
    with col_f:
        render_financials(inst)
    with col_n:
        render_news(inst)
    st.caption("本頁資料僅供學習與研究，不構成投資建議。")


def render_research() -> None:
    st.header("🔎 個股研究")
    # 支援網址參數 ?symbol=TW:2330，可直接分享某一檔的研究頁
    sym = st.query_params.get("symbol")
    if sym and st.session_state.get("research_symbol") != sym:
        st.session_state["research_symbol"] = sym
    selected = st.session_state.get("research_symbol")
    if selected:
        market, _, code = selected.partition(":")
        inst = load_master().get(market, {}).get(code)
        if inst is None:
            st.error(f"代號「{selected}」不在官方代號清單裡。")
            st.button("← 回到搜尋", on_click=_clear)
            return
        render_stock(inst)
    else:
        render_search()
