"""市場儀表板（Streamlit）

啟動：.venv/bin/streamlit run app.py
然後在瀏覽器打開 http://localhost:8501
"""
from __future__ import annotations

import warnings
from datetime import datetime

import feedparser
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
import ta
import yfinance as yf
from plotly.subplots import make_subplots
from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer

warnings.filterwarnings("ignore")

# ---- 配色（dataviz 參考色盤；台股慣例：紅漲綠跌）----------------------------
UP = "#e34948"      # 漲：紅
DOWN = "#0ca30c"    # 跌：綠
S1 = "#2a78d6"      # 系列 1：藍（SMA20、RSI、MACD）
S2 = "#eb6834"      # 系列 2：橘（SMA60、Signal）
GUIDE = "#8a8984"   # 參考線（RSI 30/70）

PERIODS = {"1 個月": "1mo", "3 個月": "3mo", "6 個月": "6mo", "1 年": "1y", "2 年": "2y", "5 年": "5y"}

st.set_page_config(page_title="市場儀表板", page_icon="📈", layout="wide")


# ---- 資料 ----------------------------------------------------------------
def candidates(raw: str) -> list[str]:
    """把使用者輸入轉成可能的 yfinance 代號。純數字視為台股（先上市 .TW，再上櫃 .TWO）。"""
    s = raw.strip().upper()
    if s.isdigit():
        return [f"{s}.TW", f"{s}.TWO"]
    return [s]


@st.cache_data(ttl=300, show_spinner=False)
def load_history(raw: str, period: str) -> tuple[str, pd.DataFrame]:
    for sym in candidates(raw):
        df = yf.Ticker(sym).history(period=period, auto_adjust=False)
        df = df.dropna(subset=["Close"])
        if not df.empty:
            return sym, df
    return "", pd.DataFrame()


def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    close = df["Close"]
    df["SMA20"] = close.rolling(20).mean()
    df["SMA60"] = close.rolling(60).mean()
    df["RSI14"] = ta.momentum.RSIIndicator(close, window=14).rsi()
    macd = ta.trend.MACD(close, window_slow=26, window_fast=12, window_sign=9)
    df["MACD"] = macd.macd()
    df["Signal"] = macd.macd_signal()
    df["Hist"] = macd.macd_diff()
    return df


@st.cache_data(ttl=600, show_spinner=False)
def load_news(symbol: str, n: int = 8) -> list[dict]:
    url = f"https://feeds.finance.yahoo.com/rss/2.0/headline?s={symbol}&region=US&lang=en-US"
    feed = feedparser.parse(url, agent="Mozilla/5.0 (market-dashboard)")
    sia = SentimentIntensityAnalyzer()
    rows = []
    for e in feed.entries[:n]:
        title = e.get("title", "")
        score = sia.polarity_scores(title)["compound"] if title.isascii() else None
        rows.append({
            "標題": title,
            "情緒": score,
            "時間": e.get("published", ""),
            "連結": e.get("link", ""),
        })
    return rows


@st.cache_data(ttl=60, show_spinner=False)
def load_btc() -> tuple[str, float] | None:
    import ccxt

    for ex_id in ("binance", "kraken", "okx"):
        try:
            ex = getattr(ccxt, ex_id)({"enableRateLimit": True, "timeout": 10000})
            return ex_id, float(ex.fetch_ticker("BTC/USDT")["last"])
        except Exception:  # noqa: BLE001
            continue
    return None


def sentiment_label(score: float | None) -> str:
    if score is None:
        return "—（非英文）"
    if score >= 0.05:
        return f"▲ 正面 {score:+.2f}"
    if score <= -0.05:
        return f"▼ 負面 {score:+.2f}"
    return f"● 中性 {score:+.2f}"


# ---- 圖表 ----------------------------------------------------------------
def build_chart(df: pd.DataFrame, symbol: str) -> go.Figure:
    fig = make_subplots(
        rows=4, cols=1, shared_xaxes=True, vertical_spacing=0.04,
        row_heights=[0.52, 0.14, 0.17, 0.17],
        subplot_titles=(f"{symbol} 價格", "成交量", "RSI(14)", "MACD 藍／Signal 橘"),
    )
    fig.add_trace(go.Candlestick(
        x=df.index, open=df["Open"], high=df["High"], low=df["Low"], close=df["Close"],
        name="K 線", increasing=dict(line=dict(color=UP, width=1), fillcolor=UP),
        decreasing=dict(line=dict(color=DOWN, width=1), fillcolor=DOWN),
    ), row=1, col=1)
    fig.add_trace(go.Scatter(x=df.index, y=df["SMA20"], name="SMA20", line=dict(color=S1, width=2)), row=1, col=1)
    fig.add_trace(go.Scatter(x=df.index, y=df["SMA60"], name="SMA60", line=dict(color=S2, width=2)), row=1, col=1)

    vol_colors = [UP if c >= o else DOWN for c, o in zip(df["Close"], df["Open"])]
    fig.add_trace(go.Bar(x=df.index, y=df["Volume"], name="成交量", marker_color=vol_colors,
                         showlegend=False), row=2, col=1)

    fig.add_trace(go.Scatter(x=df.index, y=df["RSI14"], name="RSI14", line=dict(color=S1, width=2),
                             showlegend=False), row=3, col=1)
    for level, text in ((70, "超買 70"), (30, "超賣 30")):
        fig.add_hline(y=level, line=dict(color=GUIDE, width=1, dash="dash"), row=3, col=1,
                      annotation_text=text, annotation_position="right",
                      annotation_font=dict(size=10, color=GUIDE))

    hist_colors = [UP if v >= 0 else DOWN for v in df["Hist"].fillna(0)]
    fig.add_trace(go.Bar(x=df.index, y=df["Hist"], name="MACD 柱", marker_color=hist_colors,
                         showlegend=False), row=4, col=1)
    fig.add_trace(go.Scatter(x=df.index, y=df["MACD"], name="MACD", line=dict(color=S1, width=2),
                             showlegend=False), row=4, col=1)
    fig.add_trace(go.Scatter(x=df.index, y=df["Signal"], name="Signal", line=dict(color=S2, width=2),
                             showlegend=False), row=4, col=1)

    fig.update_layout(
        height=860, hovermode="x unified", margin=dict(l=10, r=60, t=40, b=10),
        legend=dict(orientation="h", yanchor="bottom", y=1.03, xanchor="left", x=0),
        xaxis_rangeslider_visible=False, bargap=0.15,
    )
    fig.update_yaxes(range=[0, 100], row=3, col=1)
    # 跳過週末空白
    fig.update_xaxes(rangebreaks=[dict(bounds=["sat", "mon"])])
    return fig


# ---- 介面 ----------------------------------------------------------------
from lessons import render_learning  # noqa: E402

with st.sidebar:
    st.header("📈 市場儀表板")
    st.caption("以下設定用在「儀表板」分頁；「技術分析教學」分頁有自己的代號欄位。")
    raw = st.text_input("股票代號", value="AAPL", help="美股輸入代號（AAPL、NVDA）；台股輸入數字（2330、0050）")
    period_label = st.selectbox("期間", list(PERIODS), index=2)
    show_news = st.toggle("顯示新聞與情緒", value=True)
    st.divider()
    btc = load_btc()
    if btc:
        st.metric(f"BTC/USDT（{btc[0]}）", f"{btc[1]:,.0f}")
    else:
        st.caption("BTC 價格暫時抓不到")
    if st.button("重新整理資料"):
        st.cache_data.clear()
        st.rerun()
    st.caption("資料來源：Yahoo Finance、交易所公開 API。僅供學習與研究，不構成投資建議。")


def render_dashboard() -> None:
    if not raw.strip():
        st.info("請在左側輸入股票代號。")
        return

    with st.spinner("抓取資料中…"):
        symbol, hist = load_history(raw, PERIODS[period_label])
    if hist.empty:
        st.error(f"找不到「{raw}」的資料。美股請輸入代號（例如 AAPL），台股請輸入數字（例如 2330）。")
        return

    df = add_indicators(hist)
    last, prev = df.iloc[-1], df.iloc[-2] if len(df) > 1 else df.iloc[-1]
    chg = (last["Close"] / prev["Close"] - 1) * 100
    currency = "TWD" if symbol.endswith((".TW", ".TWO")) else "USD"

    st.header(symbol)
    st.caption(f"最新交易日 {df.index[-1]:%Y-%m-%d}　·　幣別 {currency}　·　更新於 {datetime.now():%H:%M:%S}")

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("最新價", f"{last['Close']:,.2f}", f"{chg:+.2f}%", delta_color="inverse")  # 與圖表一致：紅漲綠跌
    c1.caption("盤中為即時價，收盤後為收盤價；漲跌幅對比前一交易日")
    # 狀態說明用 caption，不用 delta（delta 會加上漲跌箭頭，容易誤讀）
    if pd.notna(last["SMA20"]):
        c2.metric("SMA20", f"{last['SMA20']:,.2f}")
        c2.caption("收盤在均線之上" if last["Close"] > last["SMA20"] else "收盤在均線之下")
    else:
        c2.metric("SMA20", "資料不足")
    if pd.notna(last["RSI14"]):
        rsi = last["RSI14"]
        c3.metric("RSI(14)", f"{rsi:.1f}")
        c3.caption("超買區（>70）" if rsi > 70 else "超賣區（<30）" if rsi < 30 else "中性區（30–70）")
    else:
        c3.metric("RSI(14)", "資料不足")
    if pd.notna(last["MACD"]):
        c4.metric("MACD", f"{last['MACD']:.3f}")
        c4.caption("MACD 在訊號線之上" if last["MACD"] > last["Signal"] else "MACD 在訊號線之下")
    else:
        c4.metric("MACD", "資料不足")

    st.plotly_chart(build_chart(df, symbol), width="stretch", theme="streamlit")
    st.caption("顏色依台股慣例：紅色為上漲、綠色為下跌。均線與指標需要足夠天數才會出現（SMA60 需 60 個交易日）。")

    with st.expander("資料表（最近 30 個交易日）"):
        table = df[["Open", "High", "Low", "Close", "Volume", "SMA20", "SMA60", "RSI14", "MACD", "Signal"]].tail(30)
        table.index = table.index.strftime("%Y-%m-%d")
        st.dataframe(table.iloc[::-1].round(2), width="stretch")

    if show_news:
        st.subheader("新聞與情緒")
        news = load_news(symbol)
        if not news:
            st.caption("這個代號目前沒有 Yahoo Finance 新聞。")
        else:
            scored = [r["情緒"] for r in news if r["情緒"] is not None]
            if scored:
                avg = sum(scored) / len(scored)
                st.caption(f"VADER 平均情緒分數 {avg:+.3f}（-1 最負面、+1 最正面；只評英文標題）")
            st.dataframe(
                pd.DataFrame([{**r, "情緒": sentiment_label(r["情緒"])} for r in news]),
                width="stretch", hide_index=True,
                column_config={"連結": st.column_config.LinkColumn("連結", display_text="開啟")},
            )


tab_dash, tab_learn = st.tabs(["📊 儀表板", "🎓 技術分析教學"])
with tab_dash:
    render_dashboard()
with tab_learn:
    render_learning(load_history, PERIODS)
