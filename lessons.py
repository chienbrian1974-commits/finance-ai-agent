"""技術分析教學分頁。

每一課的結構：觀念 → 怎麼畫 → 用選定股票的真實資料示範 → 換你判斷（小測驗）。
所有「解讀」都是依固定規則自動產生的教學範例，不是投資建議。
"""
from __future__ import annotations

from typing import Callable

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
import ta
from plotly.subplots import make_subplots

# 配色與 app.py 一致（台股慣例：紅漲綠跌）
UP = "#e34948"
DOWN = "#0ca30c"
S1 = "#2a78d6"   # 藍
S2 = "#eb6834"   # 橘
S3 = "#1baf7a"   # 青綠（支撐、波段低點）
S4 = "#6250d6"   # 紫（壓力、波段高點）
GUIDE = "#8a8984"

# 圖表右上角工具列加上繪圖工具，讓使用者自己畫線
DRAW_CONFIG = {
    "modeBarButtonsToAdd": ["drawline", "drawopenpath", "drawrect", "eraseshape"],
    "displaylogo": False,
}

LESSONS = [
    "1. K 線",
    "2. 趨勢、支撐與壓力",
    "3. 移動平均線",
    "4. 成交量",
    "5. RSI",
    "6. MACD",
    "7. 布林通道",
    "8. 綜合分析",
]


# ---- 計算 ----------------------------------------------------------------
def enrich(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    c = df["Close"]
    for n in (5, 20, 60):
        df[f"SMA{n}"] = c.rolling(n).mean()
    df["VOL20"] = df["Volume"].rolling(20).mean()
    df["RSI14"] = ta.momentum.RSIIndicator(c, window=14).rsi()
    m = ta.trend.MACD(c, window_slow=26, window_fast=12, window_sign=9)
    df["MACD"], df["Signal"], df["Hist"] = m.macd(), m.macd_signal(), m.macd_diff()
    bb = ta.volatility.BollingerBands(c, window=20, window_dev=2)
    df["BB_up"], df["BB_mid"], df["BB_low"] = bb.bollinger_hband(), bb.bollinger_mavg(), bb.bollinger_lband()
    df["BB_pct"], df["BB_width"] = bb.bollinger_pband(), bb.bollinger_wband()
    return df


def swings(df: pd.DataFrame, k: int = 5) -> tuple[pd.Series, pd.Series]:
    """波段高低點：某天的最高價是前後 k 天裡最高的，就是波段高點（低點同理）。
    最後 k 天還沒有「後面」可以確認，所以不會被標出來。"""
    h, l = df["High"], df["Low"]
    win = 2 * k + 1
    hi = h[h == h.rolling(win, center=True).max()]
    lo = l[l == l.rolling(win, center=True).min()]
    return _dedupe(df, hi, k), _dedupe(df, lo, k)


def _dedupe(df: pd.DataFrame, pts: pd.Series, k: int) -> pd.Series:
    """連續幾天出現同樣的高（低）點時，只保留第一天，避免同一個山頂被算成兩個波段。"""
    keep, last_pos = [], -10**9
    for ts in pts.index:
        pos = df.index.get_loc(ts)
        if pos - last_pos > k:
            keep.append(ts)
            last_pos = pos
    return pts.loc[keep]


def direction(a: float, b: float) -> str:
    return "越來越高" if b > a else "越來越低" if b < a else "持平"


def trend_of(hi: pd.Series, lo: pd.Series) -> str:
    """用最近兩個波段高點與低點判斷趨勢。"""
    if len(hi) < 2 or len(lo) < 2:
        return "資料不足"
    hh, hl = hi.iloc[-1] > hi.iloc[-2], lo.iloc[-1] > lo.iloc[-2]
    if hh and hl:
        return "上升趨勢"
    if not hh and not hl:
        return "下降趨勢"
    return "盤整"


def crosses(fast: pd.Series, slow: pd.Series) -> pd.DataFrame:
    """找出快線穿越慢線的日期。+1 = 黃金交叉（往上穿），-1 = 死亡交叉（往下穿）。"""
    diff = (fast - slow).dropna()
    sign = diff.gt(0).astype(int) * 2 - 1
    change = sign.diff().fillna(0)
    pts = change[change != 0]
    return pd.DataFrame({"type": (pts > 0).map({True: 1, False: -1}), "price": slow.reindex(pts.index)})


def classify_candle(o: float, h: float, l: float, c: float) -> tuple[str, str]:
    body, rng = abs(c - o), max(h - l, 1e-9)
    upper, lower = h - max(o, c), min(o, c) - l
    color = "紅 K（收盤 > 開盤）" if c > o else "綠 K（收盤 < 開盤）" if c < o else "平盤（收盤 = 開盤）"
    if h - l <= max(h, 1e-9) * 0.001:
        return ("一字線（開＝高＝低＝收）",
                "一字線：整天幾乎只有一個價格。在台股通常是開盤就鎖住漲停或跌停，買不到（或賣不掉），代表單邊力道極強。")
    if body <= 0.1 * rng:
        kind = "十字線：開盤和收盤幾乎一樣，買賣雙方拉鋸、多空不明，常出現在轉折之前。"
    elif lower >= 2 * body and upper <= body:
        kind = "長下影線：盤中一度大跌，但又被買回來，代表下方有人願意承接。"
    elif upper >= 2 * body and lower <= body:
        kind = "長上影線：盤中一度大漲，但又被賣回去，代表上方有賣壓。"
    elif body >= 0.7 * rng:
        kind = ("長紅 K：幾乎沒有影線，買方從開盤強到收盤。" if c > o
                else "長綠 K（長黑 K）：幾乎沒有影線，賣方從開盤強到收盤。")
    else:
        kind = "一般 K 棒：實體和影線比例普通，單獨看訊號不強。"
    return color, kind


# ---- 圖表共用 --------------------------------------------------------------
def base_fig(df: pd.DataFrame, rows: int = 1, heights: list[float] | None = None,
             titles: tuple[str, ...] = (), height: int = 520) -> go.Figure:
    fig = make_subplots(rows=rows, cols=1, shared_xaxes=True, vertical_spacing=0.07,
                        row_heights=heights, subplot_titles=titles)
    fig.add_trace(go.Candlestick(
        x=df.index, open=df["Open"], high=df["High"], low=df["Low"], close=df["Close"], name="K 線",
        increasing=dict(line=dict(color=UP, width=1), fillcolor=UP),
        decreasing=dict(line=dict(color=DOWN, width=1), fillcolor=DOWN),
    ), row=1, col=1)
    fig.update_layout(
        height=height, hovermode="x unified", margin=dict(l=10, r=70, t=40, b=10),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0),
        xaxis_rangeslider_visible=False, bargap=0.15,
        newshape=dict(line=dict(color=S2, width=2)),
    )
    fig.update_xaxes(rangebreaks=[dict(bounds=["sat", "mon"])])
    return fig


def hline(fig: go.Figure, y: float, text: str, color: str, row: int = 1) -> None:
    fig.add_hline(y=y, line=dict(color=color, width=1.5, dash="dash"), row=row, col=1,
                  annotation_text=text, annotation_position="right", annotation_font=dict(size=11, color=color))


def show(fig: go.Figure, draw: bool = False) -> None:
    st.plotly_chart(fig, width="stretch", theme="streamlit", config=DRAW_CONFIG if draw else {})
    if draw:
        st.caption("✏️ 圖表右上角工具列有畫線工具（直線、自由線、方框、橡皮擦），可以直接在圖上自己畫畫看。重新整理後畫的線會消失。")


def quiz(key: str, question: str, options: list[str], answer: str, why: str) -> None:
    st.markdown(f"**🧠 換你判斷：{question}**")
    choice = st.radio(question, options, index=None, key=key, horizontal=True, label_visibility="collapsed")
    if choice is None:
        st.caption("先自己看圖判斷，選了之後才會顯示答案。")
    elif choice == answer:
        st.success(f"✅ 答對了！{why}")
    else:
        st.warning(f"❌ 這次的答案是「{answer}」。{why}")


# ---- 各課 ----------------------------------------------------------------
def lesson_candle(df: pd.DataFrame, sym: str) -> None:
    st.markdown("""
### 觀念
每一根 K 棒記錄一天的四個價格：**開盤、收盤、最高、最低**。
- **實體**：開盤到收盤之間的粗柱子。收盤比開盤高是**紅 K**（漲），收盤比開盤低是**綠 K**（跌）。這是台股慣例，美股常用相反的顏色。
- **上影線**：實體上方的細線，頂端是當天最高價。
- **下影線**：實體下方的細線，底端是當天最低價。

實體越長，代表那一方（買方或賣方）越強；影線越長，代表盤中曾經衝過去又被打回來。
""")
    demo = go.Figure()
    demo.add_trace(go.Candlestick(x=["紅 K（上漲）", "綠 K（下跌）"], open=[100, 110], high=[115, 114],
                                  low=[96, 94], close=[110, 100], showlegend=False,
                                  increasing=dict(line=dict(color=UP), fillcolor=UP),
                                  decreasing=dict(line=dict(color=DOWN), fillcolor=DOWN)))
    for x, y, t in [("紅 K（上漲）", 115, "最高 115"), ("紅 K（上漲）", 110, "收盤 110"),
                    ("紅 K（上漲）", 100, "開盤 100"), ("紅 K（上漲）", 96, "最低 96"),
                    ("綠 K（下跌）", 114, "最高 114"), ("綠 K（下跌）", 110, "開盤 110"),
                    ("綠 K（下跌）", 100, "收盤 100"), ("綠 K（下跌）", 94, "最低 94")]:
        demo.add_annotation(x=x, y=y, text=t, showarrow=True, arrowhead=0, ax=70, ay=0, font=dict(size=12))
    demo.update_layout(height=320, xaxis_rangeslider_visible=False, margin=dict(l=10, r=10, t=10, b=10),
                       yaxis=dict(range=[90, 119]))
    st.plotly_chart(demo, width="stretch", theme="streamlit")

    st.markdown("### 怎麼看")
    st.markdown("1. 先看**顏色**：紅漲綠跌。\n2. 再看**實體長短**：長代表力道強。\n"
                "3. 最後看**影線**：長上影線是上面有賣壓，長下影線是下面有買盤。\n"
                "4. 不要只看一根，要配合前後幾根和位置（在高檔還是低檔）一起判斷。")

    recent = df.tail(30)
    st.markdown(f"### 看看 {sym} 最近 30 天")
    fig = base_fig(recent, height=420)
    last = recent.iloc[-1]
    fig.add_annotation(x=recent.index[-1], y=last["High"], text="最新一根", showarrow=True, arrowhead=2, ay=-40)
    show(fig)

    color, kind = classify_candle(last["Open"], last["High"], last["Low"], last["Close"])
    st.info(f"**最新一根（{recent.index[-1]:%m/%d}）**：開 {last['Open']:,.2f}／高 {last['High']:,.2f}／"
            f"低 {last['Low']:,.2f}／收 {last['Close']:,.2f}\n\n這是一根{color}。{kind}")

    rows = []
    for ts, r in recent.tail(10).iloc[::-1].iterrows():
        col, k = classify_candle(r["Open"], r["High"], r["Low"], r["Close"])
        rows.append({"日期": f"{ts:%m/%d}", "顏色": col.split("（")[0], "型態": k.split("：")[0]})
    with st.expander("最近 10 根 K 棒的型態"):
        st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")

    # 用最近一根「有實體」的 K 棒出題（一字線沒有紅綠之分，不適合當題目）
    q = recent[(recent["Close"] - recent["Open"]).abs() > 0]
    if len(q):
        qt, qr = q.index[-1], q.iloc[-1]
        answer = "紅 K" if qr["Close"] > qr["Open"] else "綠 K"
        quiz(f"q1_{sym}", f"{qt:%m/%d} 那一根是紅 K 還是綠 K？", ["紅 K", "綠 K"], answer,
             f"那天開盤 {qr['Open']:,.2f}、收盤 {qr['Close']:,.2f}。收盤比開盤高就是紅 K，比開盤低就是綠 K。")


def lesson_trend(df: pd.DataFrame, sym: str) -> None:
    st.markdown("""
### 觀念
- **上升趨勢**：高點一個比一個高、低點也一個比一個高（Higher High + Higher Low）。
- **下降趨勢**：高點一個比一個低、低點也一個比一個低。
- **盤整**：高低點沒有一致方向，價格在區間裡來回。
- **支撐**：股價跌到這附近容易止跌，通常是之前的波段低點。
- **壓力**：股價漲到這附近容易遇到賣壓，通常是之前的波段高點。
- 支撐被跌破後常會變成新的壓力，壓力被突破後常會變成新的支撐。
""")
    st.markdown("""
### 怎麼畫
1. 先找出**波段高點**（左右兩邊都比它低的山頂）和**波段低點**（左右兩邊都比它高的谷底）。
2. **上升趨勢線**：連接兩個越來越高的波段低點，往右延伸。股價回到線附近常有支撐。
3. **下降趨勢線**：連接兩個越來越低的波段高點，往右延伸。股價反彈到線附近常有壓力。
4. **支撐線／壓力線**：在最近的波段低點／高點畫一條水平線。
""")

    hi, lo = swings(df)
    close = df["Close"].iloc[-1]
    trend = trend_of(hi, lo)
    below, above = lo[lo < close], hi[hi > close]
    support = below.iloc[-1] if len(below) else df["Low"].min()
    resistance = above.iloc[-1] if len(above) else df["High"].max()

    st.markdown(f"### 看看 {sym}")
    fig = base_fig(df, height=560)
    fig.add_trace(go.Scatter(x=hi.index, y=hi * 1.01, mode="markers", name="波段高點",
                             marker=dict(symbol="triangle-down", size=10, color=S4)))
    fig.add_trace(go.Scatter(x=lo.index, y=lo * 0.99, mode="markers", name="波段低點",
                             marker=dict(symbol="triangle-up", size=10, color=S3)))
    hline(fig, resistance, f"壓力 {resistance:,.2f}", S4)
    hline(fig, support, f"支撐 {support:,.2f}", S3)

    line_note = ""
    pts = lo if trend == "上升趨勢" else hi if trend == "下降趨勢" else None
    if pts is not None and len(pts) >= 2:
        (t1, p1), (t2, p2) = list(pts.items())[-2:]
        i1, i2 = df.index.get_loc(t1), df.index.get_loc(t2)
        slope = (p2 - p1) / (i2 - i1)
        xs = df.index[i1:]
        ys = [p1 + slope * (i - i1) for i in range(i1, len(df))]
        name = "上升趨勢線" if trend == "上升趨勢" else "下降趨勢線"
        fig.add_trace(go.Scatter(x=xs, y=ys, mode="lines", name=name, line=dict(color=S2, width=2)))
        line_note = (f"橘色的{name}連接 {t1:%m/%d}（{p1:,.2f}）和 {t2:%m/%d}（{p2:,.2f}）兩個"
                     f"{'波段低點' if trend == '上升趨勢' else '波段高點'}，延伸到今天約在 {ys[-1]:,.2f}。")
    show(fig, draw=True)

    if len(hi) >= 2 and len(lo) >= 2:
        detail = (f"最近兩個波段高點：{hi.index[-2]:%m/%d} {hi.iloc[-2]:,.2f} → {hi.index[-1]:%m/%d} {hi.iloc[-1]:,.2f}"
                  f"（{direction(hi.iloc[-2], hi.iloc[-1])}）\n\n"
                  f"最近兩個波段低點：{lo.index[-2]:%m/%d} {lo.iloc[-2]:,.2f} → {lo.index[-1]:%m/%d} {lo.iloc[-1]:,.2f}"
                  f"（{direction(lo.iloc[-2], lo.iloc[-1])}）")
    else:
        detail = "這段期間的波段高低點太少，試著把期間拉長。"
    st.info(f"{detail}\n\n依照高低點規則，目前是 **{trend}**。{line_note}\n\n"
            f"最新價 {close:,.2f}：下方最近的支撐約 {support:,.2f}（距離 {(close / support - 1) * 100:.1f}%），"
            f"上方最近的壓力約 {resistance:,.2f}（距離 {(resistance / close - 1) * 100:.1f}%）。")
    st.caption("波段高低點用「前後 5 天內最高／最低」的規則自動標出；最近 5 天還無法確認，所以不會標。實務上每個人畫的線可能不同，重點是找出大家都看得到的明顯高低點。")

    if trend in ("上升趨勢", "下降趨勢", "盤整"):
        quiz(f"q2_{sym}", "先遮住上面的說明，只看圖：目前比較像哪一種走勢？", ["上升趨勢", "下降趨勢", "盤整"], trend,
             "判斷方法是比較最近兩個波段高點和兩個波段低點：都變高是上升、都變低是下降、一高一低是盤整。")


def lesson_ma(df: pd.DataFrame, sym: str) -> None:
    st.markdown("""
### 觀念
**移動平均線（MA）** 是最近 N 天收盤價的平均，把價格的雜訊抹平，看出方向。台股常用：
- **週線 5 日**：短線
- **月線 20 日**：中短線，很多人拿來當多空分界
- **季線 60 日**：中長線，常被視為趨勢的「生命線」

常見的看法：
- **多頭排列**：5 日 > 20 日 > 60 日，三條線都往上，趨勢偏多。
- **空頭排列**：5 日 < 20 日 < 60 日，三條線都往下，趨勢偏空。
- **黃金交叉**：短天期均線由下往上穿過長天期均線，代表短期轉強。
- **死亡交叉**：短天期均線由上往下穿過長天期均線，代表短期轉弱。
- 均線是「落後指標」，交叉常常發生在走勢已經走一段之後，盤整時也容易反覆交叉（假訊號）。
""")
    last = df.iloc[-1]
    st.markdown(f"### 看看 {sym}")
    fig = base_fig(df, height=540)
    for n, color in ((5, S3), (20, S1), (60, S2)):
        fig.add_trace(go.Scatter(x=df.index, y=df[f"SMA{n}"], name=f"{n} 日均線", line=dict(color=color, width=2)))
    cx = crosses(df["SMA5"], df["SMA20"])
    gold, dead = cx[cx["type"] == 1], cx[cx["type"] == -1]
    fig.add_trace(go.Scatter(x=gold.index, y=gold["price"], mode="markers", name="黃金交叉（5 穿 20 向上）",
                             marker=dict(symbol="star", size=13, color=UP, line=dict(width=1, color="white"))))
    fig.add_trace(go.Scatter(x=dead.index, y=dead["price"], mode="markers", name="死亡交叉（5 穿 20 向下）",
                             marker=dict(symbol="x", size=11, color=DOWN, line=dict(width=1, color="white"))))
    show(fig, draw=True)

    if pd.isna(last["SMA60"]):
        st.warning("期間太短，60 日均線還算不出來。請把期間改成 6 個月以上。")
        return
    s5, s20, s60 = last["SMA5"], last["SMA20"], last["SMA60"]
    if s5 > s20 > s60:
        arrange = "多頭排列"
    elif s5 < s20 < s60:
        arrange = "空頭排列"
    else:
        arrange = "均線糾結"
    slope20 = df["SMA20"].iloc[-1] - df["SMA20"].iloc[-6]
    last_cross = ""
    if len(cx):
        t, r = cx.index[-1], cx.iloc[-1]
        last_cross = f"最近一次 5 日與 20 日的交叉是 {t:%Y-%m-%d} 的**{'黃金交叉' if r['type'] == 1 else '死亡交叉'}**。"
    st.info(f"5 日 {s5:,.2f}、20 日 {s20:,.2f}、60 日 {s60:,.2f} → **{arrange}**。\n\n"
            f"月線（20 日）最近 5 天{'往上' if slope20 > 0 else '往下'}彎；最新價{'站上' if last['Close'] > s60 else '跌破'}季線（60 日）。"
            f"{last_cross}")
    quiz(f"q3_{sym}", "看三條均線的上下順序，現在是哪一種排列？", ["多頭排列", "空頭排列", "均線糾結"], arrange,
         "比較 5 日、20 日、60 日的高低：由上到下依序是 5 > 20 > 60 就是多頭排列，反過來是空頭排列，其他情況是糾結。")


def lesson_volume(df: pd.DataFrame, sym: str) -> None:
    st.markdown("""
### 觀念
**成交量**是一天成交了多少股，代表市場的參與熱度。俗話說「量是價的先行指標」，重點是看**價量是否配合**：

| 價格 | 成交量 | 常見解讀 |
|---|---|---|
| 上漲 | 放大 | **價漲量增**：買盤積極，上漲比較健康 |
| 上漲 | 縮小 | **價漲量縮**：追價意願不足，漲勢可能後繼無力 |
| 下跌 | 放大 | **價跌量增**：賣壓沉重，要小心 |
| 下跌 | 縮小 | **價跌量縮**：賣壓減輕，跌勢可能趨緩 |

判斷「放大」或「縮小」，通常拿當天成交量和**最近 20 天的平均量**比較。
""")
    st.markdown(f"### 看看 {sym}")
    fig = base_fig(df, rows=2, heights=[0.65, 0.35], titles=(f"{sym} 價格", "成交量（灰線＝20 日均量）"), height=600)
    colors = [UP if c >= o else DOWN for c, o in zip(df["Close"], df["Open"])]
    fig.add_trace(go.Bar(x=df.index, y=df["Volume"], name="成交量", marker_color=colors, showlegend=False), row=2, col=1)
    fig.add_trace(go.Scatter(x=df.index, y=df["VOL20"], name="20 日均量", line=dict(color=GUIDE, width=2),
                             showlegend=False), row=2, col=1)
    big = df["Volume"].idxmax()
    fig.add_annotation(x=big, y=df.loc[big, "Volume"], text="期間最大量", showarrow=True, arrowhead=2, ay=-30, row=2, col=1)
    show(fig)

    last, prev = df.iloc[-1], df.iloc[-2]
    if pd.isna(last["VOL20"]) or last["VOL20"] == 0:
        st.warning("資料不足，無法計算 20 日均量。")
        return
    ratio = last["Volume"] / last["VOL20"]
    up = last["Close"] >= prev["Close"]
    vol_up = ratio >= 1.2
    vol_down = ratio <= 0.8
    if up and vol_up:
        verdict = "價漲量增"
    elif up and vol_down:
        verdict = "價漲量縮"
    elif not up and vol_up:
        verdict = "價跌量增"
    elif not up and vol_down:
        verdict = "價跌量縮"
    else:
        verdict = "量能持平（約等於 20 日均量）"
    big_dir = "上漲" if df.loc[big, "Close"] >= df.loc[big, "Open"] else "下跌"
    st.info(f"最新一天成交量是 20 日均量的 **{ratio:.2f} 倍**，價格比前一天{'上漲' if up else '下跌'} → **{verdict}**。\n\n"
            f"期間最大量出現在 {big:%Y-%m-%d}，那天是{big_dir} K。大量常出現在趨勢的起點或轉折點，值得回頭看看當時發生什麼事。")
    st.caption("如果現在是盤中，最新一天的成交量還在累積，倍數會偏低，收盤後再看比較準。")
    quiz(f"q4_{sym}", "最新一天的成交量，比 20 日均量多還是少？", ["比較多", "比較少"],
         "比較多" if ratio >= 1 else "比較少", f"最新量 ÷ 20 日均量 = {ratio:.2f}。大於 1 就是比平常多。")


def lesson_rsi(df: pd.DataFrame, sym: str) -> None:
    st.markdown("""
### 觀念
**RSI（相對強弱指標）** 比較最近 14 天上漲和下跌的力道，數值在 0～100 之間：
- **70 以上：超買區**，短期漲多了，可能回檔（但強勢股可以在超買區待很久）。
- **30 以下：超賣區**，短期跌深了，可能反彈（但弱勢股也可以一直超賣）。
- **50 附近**：多空力道差不多。
- **背離**：股價創新高、RSI 卻沒有創新高，叫**頂背離**，代表漲勢力道在減弱；股價創新低、RSI 卻沒有創新低，叫**底背離**，代表跌勢力道在減弱。
""")
    st.markdown(f"### 看看 {sym}")
    fig = base_fig(df, rows=2, heights=[0.6, 0.4], titles=(f"{sym} 價格", "RSI(14)"), height=600)
    fig.add_trace(go.Scatter(x=df.index, y=df["RSI14"], name="RSI14", line=dict(color=S1, width=2), showlegend=False),
                  row=2, col=1)
    hline(fig, 70, "超買 70", GUIDE, row=2)
    hline(fig, 30, "超賣 30", GUIDE, row=2)
    fig.update_yaxes(range=[0, 100], row=2, col=1)
    show(fig)

    rsi = df["RSI14"].iloc[-1]
    if pd.isna(rsi):
        st.warning("資料不足，無法計算 RSI。")
        return
    zone = "超買區" if rsi > 70 else "超賣區" if rsi < 30 else "中性區"
    hi, lo = swings(df)
    div = "沒有發現明顯背離。"
    if len(hi) >= 2:
        (t1, p1), (t2, p2) = list(hi.items())[-2:]
        r1, r2 = df.loc[t1, "RSI14"], df.loc[t2, "RSI14"]
        if pd.notna(r1) and pd.notna(r2) and p2 > p1 and r2 < r1:
            div = (f"⚠️ 可能有**頂背離**：股價高點 {t1:%m/%d} {p1:,.2f} → {t2:%m/%d} {p2:,.2f} 變高，"
                   f"但 RSI 從 {r1:.1f} 降到 {r2:.1f}。")
    if len(lo) >= 2 and div.startswith("沒有"):
        (t1, p1), (t2, p2) = list(lo.items())[-2:]
        r1, r2 = df.loc[t1, "RSI14"], df.loc[t2, "RSI14"]
        if pd.notna(r1) and pd.notna(r2) and p2 < p1 and r2 > r1:
            div = (f"💡 可能有**底背離**：股價低點 {t1:%m/%d} {p1:,.2f} → {t2:%m/%d} {p2:,.2f} 變低，"
                   f"但 RSI 從 {r1:.1f} 升到 {r2:.1f}。")
    st.info(f"目前 RSI(14) = **{rsi:.1f}**，位在**{zone}**。\n\n{div}")
    quiz(f"q5_{sym}", "目前的 RSI 落在哪一區？", ["超買區", "中性區", "超賣區"], zone,
         f"RSI = {rsi:.1f}。大於 70 是超買、小於 30 是超賣，中間是中性區。")


def lesson_macd(df: pd.DataFrame, sym: str) -> None:
    st.markdown("""
### 觀念
**MACD** 用兩條指數平均線（12 日和 26 日）的差距來看趨勢和動能，由三個部分組成：
- **MACD 線（快線）**：12 日 EMA − 26 日 EMA。
- **訊號線（慢線）**：MACD 線的 9 日平均。
- **柱狀體**：MACD 線 − 訊號線。柱子由短變長代表動能增強，由長變短代表動能減弱。

常見的看法：
- MACD 線**往上穿過**訊號線＝黃金交叉，偏多；**往下穿過**＝死亡交叉，偏空。
- MACD 在 **0 軸之上**代表中期偏多，**0 軸之下**代表中期偏空。
""")
    st.markdown(f"### 看看 {sym}")
    fig = base_fig(df, rows=2, heights=[0.6, 0.4], titles=(f"{sym} 價格", "MACD 藍／訊號線 橘／柱狀體"), height=600)
    colors = [UP if v >= 0 else DOWN for v in df["Hist"].fillna(0)]
    fig.add_trace(go.Bar(x=df.index, y=df["Hist"], name="柱狀體", marker_color=colors, showlegend=False), row=2, col=1)
    fig.add_trace(go.Scatter(x=df.index, y=df["MACD"], name="MACD", line=dict(color=S1, width=2), showlegend=False), row=2, col=1)
    fig.add_trace(go.Scatter(x=df.index, y=df["Signal"], name="訊號線", line=dict(color=S2, width=2), showlegend=False), row=2, col=1)
    cx = crosses(df["MACD"], df["Signal"])
    for t, r in cx.tail(4).iterrows():
        fig.add_annotation(x=t, y=r["price"], text="金叉" if r["type"] == 1 else "死叉", showarrow=True,
                           arrowhead=2, ay=-28 if r["type"] == 1 else 28, font=dict(size=11), row=2, col=1)
    show(fig)

    last = df.iloc[-1]
    if pd.isna(last["Signal"]):
        st.warning("資料不足，無法計算 MACD。")
        return
    above = last["MACD"] > last["Signal"]
    h3 = df["Hist"].tail(3).tolist()
    momentum = ("動能增強（柱子連續變長）" if abs(h3[2]) > abs(h3[1]) > abs(h3[0])
                else "動能減弱（柱子連續變短）" if abs(h3[2]) < abs(h3[1]) < abs(h3[0]) else "動能變化不明顯")
    cross_txt = ""
    if len(cx):
        t, r = cx.index[-1], cx.iloc[-1]
        cross_txt = f"最近一次交叉是 {t:%Y-%m-%d} 的**{'黃金交叉' if r['type'] == 1 else '死亡交叉'}**。"
    st.info(f"MACD {last['MACD']:.3f}，訊號線 {last['Signal']:.3f} → MACD 在訊號線**{'之上' if above else '之下'}**，"
            f"位在 0 軸**{'之上' if last['MACD'] > 0 else '之下'}**。柱狀體：{momentum}。{cross_txt}")
    quiz(f"q6_{sym}", "最新的 MACD 線在訊號線的上面還是下面？", ["上面", "下面"], "上面" if above else "下面",
         "看下方子圖最右邊：藍線在橘線上方就是「上面」，這時柱狀體會是紅色（正值）。")


def lesson_bb(df: pd.DataFrame, sym: str) -> None:
    st.markdown("""
### 觀念
**布林通道**由三條線組成：
- **中軌**：20 日均線。
- **上軌**：中軌 + 2 倍標準差。
- **下軌**：中軌 − 2 倍標準差。

理論上大約 95% 的時間價格會落在通道內。常見的看法：
- 價格**碰到上軌**代表短期偏強或過熱，**碰到下軌**代表短期偏弱或超跌。
- 通道**變窄（收斂）**代表波動變小，常是大行情的前兆；通道**變寬（擴張）**代表行情正在發動。
- 強勢股會「沿著上軌走」，所以碰到上軌不一定就要賣。
- **%B** 表示價格在通道裡的位置：0 = 下軌、0.5 = 中軌、1 = 上軌。
""")
    st.markdown(f"### 看看 {sym}")
    fig = base_fig(df, height=540)
    fig.add_trace(go.Scatter(x=df.index, y=df["BB_up"], name="上軌", line=dict(color=S4, width=1.5)))
    fig.add_trace(go.Scatter(x=df.index, y=df["BB_low"], name="下軌", line=dict(color=S4, width=1.5),
                             fill="tonexty", fillcolor="rgba(98,80,214,0.08)"))
    fig.add_trace(go.Scatter(x=df.index, y=df["BB_mid"], name="中軌（20 日均線）", line=dict(color=S1, width=1.5, dash="dot")))
    show(fig, draw=True)

    last = df.iloc[-1]
    if pd.isna(last["BB_pct"]):
        st.warning("資料不足，無法計算布林通道。")
        return
    pct = last["BB_pct"]
    pos = ("突破上軌" if pct > 1 else "靠近上軌" if pct > 0.8 else "跌破下軌" if pct < 0 else "靠近下軌" if pct < 0.2
           else "在中軌附近")
    width = df["BB_width"].dropna()
    rank = (width < width.iloc[-1]).mean() * 100
    squeeze = ("通道**很窄**（比這段期間 80% 以上的日子都窄），可能正在醞釀大波動。" if rank <= 20
               else "通道**很寬**，行情正在發動或波動很大。" if rank >= 80 else "通道寬度普通。")
    st.info(f"最新價 {last['Close']:,.2f}，上軌 {last['BB_up']:,.2f}、中軌 {last['BB_mid']:,.2f}、下軌 {last['BB_low']:,.2f}。\n\n"
            f"%B = **{pct:.2f}** → 價格**{pos}**。{squeeze}")
    answer = "上半部" if pct >= 0.5 else "下半部"
    quiz(f"q7_{sym}", "最新價在布林通道的上半部還是下半部？", ["上半部", "下半部"], answer,
         f"%B = {pct:.2f}，大於 0.5 就是在中軌之上（上半部）。")


def lesson_summary(df: pd.DataFrame, sym: str) -> None:
    st.markdown("""
### 觀念
單一指標都有盲點，實務上會把幾個指標放在一起，看它們是否**互相確認**：
- 趨勢類（高低點、均線、MACD）告訴你**方向**。
- 擺盪類（RSI、布林通道）告訴你**位置**是不是過熱或過冷。
- 成交量告訴你這個走勢**有沒有人跟**。

下面把前面幾課的判斷整理成一張表。每一項依固定規則給「偏多 +1／中性 0／偏空 −1」，只是讓你練習把訊號整合起來。
""")
    last, prev = df.iloc[-1], df.iloc[-2]
    hi, lo = swings(df)
    trend = trend_of(hi, lo)
    rows = []

    def add(item: str, score: int, why: str) -> None:
        rows.append({"項目": item, "判斷": {1: "偏多 +1", 0: "中性 0", -1: "偏空 −1"}[score], "依據": why, "_s": score})

    add("趨勢（高低點）", {"上升趨勢": 1, "下降趨勢": -1}.get(trend, 0), trend)
    if pd.notna(last["SMA60"]):
        s = 1 if last["SMA5"] > last["SMA20"] > last["SMA60"] else -1 if last["SMA5"] < last["SMA20"] < last["SMA60"] else 0
        add("均線排列", s, {1: "多頭排列", -1: "空頭排列", 0: "均線糾結"}[s])
        add("季線位置", 1 if last["Close"] > last["SMA60"] else -1,
            f"最新價{'站上' if last['Close'] > last['SMA60'] else '跌破'} 60 日均線")
    if pd.notna(last["Signal"]):
        add("MACD", 1 if last["MACD"] > last["Signal"] else -1,
            f"MACD 在訊號線{'之上' if last['MACD'] > last['Signal'] else '之下'}")
    if pd.notna(last["RSI14"]):
        r = last["RSI14"]
        add("RSI", -1 if r > 70 else 1 if r < 30 else 0,
            f"RSI {r:.1f}，" + ("超買，留意回檔" if r > 70 else "超賣，留意反彈" if r < 30 else "中性"))
    if pd.notna(last["VOL20"]) and last["VOL20"] > 0:
        ratio = last["Volume"] / last["VOL20"]
        up = last["Close"] >= prev["Close"]
        s = 1 if up and ratio >= 1.2 else -1 if (not up) and ratio >= 1.2 else 0
        add("價量", s, f"{'上漲' if up else '下跌'}、量為均量 {ratio:.2f} 倍")

    table = pd.DataFrame(rows)
    st.markdown(f"### {sym} 的訊號整理")
    st.dataframe(table.drop(columns="_s"), hide_index=True, width="stretch")
    total = int(table["_s"].sum())
    n = len(table)
    lean = "訊號偏多" if total >= 2 else "訊號偏空" if total <= -2 else "訊號分歧"
    st.info(f"合計 **{total:+d}**（共 {n} 項）→ **{lean}**。\n\n"
            "怎麼用這張表練習：\n"
            "1. 先看趨勢類和擺盪類是否一致。例如趨勢向上但 RSI 已經超買，代表方向對、但位置偏高。\n"
            "2. 找出「互相矛盾」的項目，回到對應的課程看圖，想想為什麼。\n"
            "3. 把今天的判斷記下來，過一兩週再回來看結果，這是學技術分析最有效的方法。")
    st.warning("這張表是教學用的固定規則，**不是買賣建議**。真實交易還要考慮基本面、消息面、資金控管和停損，"
               "技術分析只能提高判斷的勝率，無法預測未來。")


RENDER: dict[str, Callable[[pd.DataFrame, str], None]] = dict(zip(LESSONS, [
    lesson_candle, lesson_trend, lesson_ma, lesson_volume, lesson_rsi, lesson_macd, lesson_bb, lesson_summary,
]))


# ---- 分頁入口 --------------------------------------------------------------
def _go(target: str) -> None:
    st.session_state["lesson"] = target


def render_learning(load_history: Callable[[str, str], tuple[str, pd.DataFrame]], periods: dict[str, str]) -> None:
    st.header("🎓 技術分析教學")
    st.caption("選一檔股票，照課程順序一步一步學。每一課都用你選的股票的真實資料示範，最後有小測驗。")

    c1, c2 = st.columns([2, 1])
    raw = c1.text_input("練習用的股票代號", value="2330", key="learn_symbol",
                        help="台股輸入數字（2330、0050），美股輸入代號（AAPL）")
    plabel = c2.selectbox("期間", list(periods), index=list(periods).index("1 年") if "1 年" in periods else 0,
                          key="learn_period", help="學趨勢和均線建議用 6 個月到 1 年")
    if not raw.strip():
        st.info("請輸入股票代號。")
        return

    with st.spinner("抓取資料中…"):
        sym, hist = load_history(raw, periods[plabel])
    if hist.empty or len(hist) < 30:
        st.error(f"找不到「{raw}」的資料，或資料太少。台股請輸入數字（例如 2330），美股請輸入代號（例如 AAPL）。")
        return
    df = enrich(hist)

    lesson = st.radio("課程", LESSONS, key="lesson", horizontal=True)
    st.divider()
    RENDER[lesson](df, sym)

    st.divider()
    i = LESSONS.index(lesson)
    b1, _, b2 = st.columns([1, 2, 1])
    # 用 on_click 回呼切換課程：回呼在下一輪執行前跑，才能合法修改 radio 的值
    if i > 0:
        b1.button(f"⬅ 上一課：{LESSONS[i - 1]}", width="stretch",
                  on_click=_go, args=(LESSONS[i - 1],))
    if i < len(LESSONS) - 1:
        b2.button(f"下一課：{LESSONS[i + 1]} ➡", width="stretch",
                  on_click=_go, args=(LESSONS[i + 1],))
    st.caption("圖中的解讀是依固定規則自動產生的教學範例，不構成投資建議。")
