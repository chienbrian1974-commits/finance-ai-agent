"""兩個分頁共用的 Streamlit 介面元件：選股、資料狀態列。"""
from __future__ import annotations

import streamlit as st

from core.instruments import Instrument, load_master, master_updated_at, resolve
from core.marketdata import PriceData, fetch_daily, probe_yahoo
from core.validation import DataStatus, fmt_display_time, validate


@st.cache_data(ttl=300, show_spinner=False)
def load_prices(inst: Instrument, period: str) -> PriceData:
    return fetch_daily(inst, period)


@st.cache_data(ttl=3600, show_spinner=False)
def _probe(raw: str) -> Instrument | None:
    return probe_yahoo(raw)


@st.cache_resource(show_spinner=False)
def _master():
    return load_master()


def pick_instrument(raw: str, key: str) -> Instrument | None:
    """把輸入轉成 Instrument；名稱有多筆符合時讓使用者選；主檔沒有時改問 Yahoo 並標示未驗證。"""
    if not raw.strip():
        st.info("請輸入股票代號或公司名稱。")
        return None
    res = resolve(raw, _master())
    if res.instrument and len(res.candidates) <= 1:
        return res.instrument
    if res.candidates:
        options = res.candidates
        default = options.index(res.instrument) if res.instrument in options else 0
        return st.selectbox(res.note or "符合的標的", options, index=default, key=f"{key}_pick",
                            format_func=lambda i: f"{i.code}　{i.company_name}（{i.market}・{i.exchange_label}・{i.asset_label}）")
    with st.spinner("官方主檔查不到，改向 Yahoo Finance 查詢…"):
        inst = _probe(raw)
    if inst:
        return inst
    st.error(f"{res.note}，Yahoo Finance 也查不到。台股請輸入代號（例如 2330、00631L），美股請輸入代號（例如 AAPL、BRK.B），"
             "也可以輸入公司名稱（例如 台積電）。")
    return None


def render_status(data: PriceData, status: DataStatus, basis: str) -> None:
    """標示資料來源、時間、市場資訊，以及驗證發現的問題。"""
    inst = data.instrument
    name = inst.company_name or "（名稱未驗證）"
    st.caption(
        f"**{inst.code} {name}**　{inst.market}・{inst.exchange_label}・{inst.asset_label}・{inst.currency}・"
        f"交易所時區 {inst.timezone}　｜　市場狀態：{status.phase}　｜　"
        f"最新 K 棒 {status.last_bar:%Y-%m-%d}（交易所當地日期）　｜　價格基準：{basis}　｜　"
        f"來源：{data.source}，抓取於 {fmt_display_time(data.fetched_at)}"
        if status.last_bar else f"{inst.label}　來源：{data.source}"
    )
    for level, text in status.messages:
        {"info": st.info, "warning": st.warning, "error": st.error}[level](text)


def footer_master_note() -> None:
    st.caption(f"代號主檔來自證交所、櫃買中心與 Nasdaq Trader 官方清單，更新於 {master_updated_at()}（UTC）。")


__all__ = ["load_prices", "pick_instrument", "render_status", "validate", "footer_master_note"]
