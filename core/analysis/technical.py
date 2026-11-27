"""技術分析模組：K 線、均線、MACD、RSI、KD、布林通道、成交量、支撐壓力，
加上日／週／月多週期比較，以及同一檔股票的歷史案例統計。

所有數字都來自已驗證的日 K（Provider 層 + 品質檢查），並登錄成事實（Fact）。
解讀文字依固定規則產生；AI 只能在這些事實之上做說明。
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date, timedelta

import numpy as np
import pandas as pd
import ta

from ..ai.facts import Fact, FactSheet, iso
from ..instruments import Instrument
from ..providers.base import Bars, DataKind
from ..providers.registry import price_provider
from ..quality import QualityReport, check


@dataclass
class Quiz:
    question: str
    options: list[str]
    answer: str
    why: str


@dataclass
class Section:
    key: str
    title: str
    principle: str                  # 指標原理（定義，不含資料）
    reading: list[str]              # 依規則對目前數據的解讀（每句都由事實組成）
    possibilities: list[str]        # 不同可能性
    facts: FactSheet
    quiz: Quiz | None = None


@dataclass
class Case:
    kind: str                       # 例如 "RSI 跌破 30"
    date: date
    close: float
    ret5: float | None              # 之後 5 個交易日報酬（%）
    ret20: float | None             # 之後 20 個交易日報酬（%）


@dataclass
class CaseStats:
    kind: str
    explanation: str
    n: int
    up_ratio20: float | None        # 20 日後上漲的比例（%）
    median20: float | None          # 20 日後報酬中位數（%）
    recent: list[Case]


@dataclass
class TechnicalReport:
    instrument: Instrument
    as_of: date
    kind: DataKind
    basis: str                      # 還原價 / 原始價
    sections: list[Section]
    timeframes: pd.DataFrame        # 日／週／月比較
    cases: list[CaseStats]
    facts: FactSheet                # 全部事實（合併）
    quality: QualityReport
    notes: list[str] = field(default_factory=list)
    daily: pd.DataFrame | None = None   # 含指標的日 K（給圖表用）


# ---- 計算工具 ---------------------------------------------------------------
def kd(df: pd.DataFrame, n: int = 9) -> tuple[pd.Series, pd.Series]:
    """KD（隨機指標），台股常用公式：RSV(n) → K = 2/3 前K + 1/3 RSV → D = 2/3 前D + 1/3 K，起始值 50。"""
    low_n, high_n = df["Low"].rolling(n).min(), df["High"].rolling(n).max()
    rng = (high_n - low_n).replace(0, np.nan)
    rsv = (df["Close"] - low_n) / rng * 100
    k_vals, d_vals, pk, pd_ = [], [], 50.0, 50.0
    for r in rsv:
        if math.isnan(r):
            k_vals.append(np.nan)
            d_vals.append(np.nan)
            continue
        pk = pk * 2 / 3 + r / 3
        pd_ = pd_ * 2 / 3 + pk / 3
        k_vals.append(pk)
        d_vals.append(pd_)
    return pd.Series(k_vals, index=df.index), pd.Series(d_vals, index=df.index)


def indicators(df: pd.DataFrame, kd_n: int) -> pd.DataFrame:
    out = df.copy()
    c = out["Close"]
    for n in (5, 20, 60):
        out[f"SMA{n}"] = c.rolling(n).mean()
    out["VOL20"] = out["Volume"].rolling(20).mean()
    out["RSI14"] = ta.momentum.RSIIndicator(c, window=14).rsi()
    m = ta.trend.MACD(c, window_slow=26, window_fast=12, window_sign=9)
    out["MACD"], out["Signal"], out["Hist"] = m.macd(), m.macd_signal(), m.macd_diff()
    bb = ta.volatility.BollingerBands(c, window=20, window_dev=2)
    out["BB_up"], out["BB_mid"], out["BB_low"] = bb.bollinger_hband(), bb.bollinger_mavg(), bb.bollinger_lband()
    out["BB_pct"], out["BB_width"] = bb.bollinger_pband(), bb.bollinger_wband()
    out["K"], out["D"] = kd(out, kd_n)
    return out


def swings(df: pd.DataFrame, k: int = 5) -> tuple[pd.Series, pd.Series]:
    """波段高低點：前後 k 天內的最高／最低；連續相同的點只算一次。"""
    h, l = df["High"], df["Low"]
    hi = h[h == h.rolling(2 * k + 1, center=True).max()]
    lo = l[l == l.rolling(2 * k + 1, center=True).min()]

    def dedupe(s: pd.Series) -> pd.Series:
        keep, last = [], -10**9
        for ts in s.index:
            pos = df.index.get_loc(ts)
            if pos - last > k:
                keep.append(ts)
                last = pos
        return s.loc[keep]

    return dedupe(hi), dedupe(lo)


def last_cross(fast: pd.Series, slow: pd.Series) -> tuple[pd.Timestamp | None, str | None]:
    diff = (fast - slow).dropna()
    sign = np.sign(diff)
    flips = sign[sign != sign.shift(1)].iloc[1:]
    if flips.empty:
        return None, None
    ts = flips.index[-1]
    return ts, "黃金交叉" if flips.iloc[-1] > 0 else "死亡交叉"


def forward_returns(close: pd.Series, ts: pd.Timestamp) -> tuple[float | None, float | None]:
    i = close.index.get_loc(ts)
    r = []
    for n in (5, 20):
        r.append((close.iloc[i + n] / close.iloc[i] - 1) * 100 if i + n < len(close) else None)
    return r[0], r[1]


def _f(x) -> float:
    return float(x) if x is not None and not pd.isna(x) else float("nan")


# ---- 主流程 ------------------------------------------------------------------
def analyze(inst: Instrument, years: int = 5) -> TechnicalReport:
    """抓資料（Provider 層）→ 品質檢查 → 計算。失敗時丟出 ProviderError。"""
    bars = price_provider(inst).get_daily(inst, date.today() - timedelta(days=365 * years + 10))
    bars, rep = check(bars)
    return compute(inst, bars, rep)


def compute(inst: Instrument, bars: Bars, rep: QualityReport) -> TechnicalReport:
    if bars.empty:
        raise ValueError("沒有可分析的資料")
    src_frame = bars.adjusted if bars.adjusted is not None else bars.frame
    basis = "還原價" if bars.adjusted is not None else "原始價"
    df = pd.DataFrame({"Open": src_frame["open"], "High": src_frame["high"], "Low": src_frame["low"],
                       "Close": src_frame["close"], "Volume": bars.frame["volume"]})
    kd_n = 9 if inst.market == "TW" else 14
    d = indicators(df, kd_n)
    last = d.iloc[-1]
    as_of = d.index[-1].date()
    kind = DataKind(bars.frame["kind"].iloc[-1])
    src = f"{bars.source}（{basis}）"
    subj = f"{inst.code} {inst.company_name}"
    asof = iso(as_of)

    def F(sheet: FactSheet, fid: str, label: str, value, unit: str = "", decimals: int = 2) -> Fact:
        return sheet.add(Fact(fid, label, value if isinstance(value, str) else _f(value), unit, asof, src, kind.value, decimals))

    sections: list[Section] = []
    ccy = inst.currency

    # 1. K 線 -----------------------------------------------------------------
    fs = FactSheet(subj)
    o, h, l, c = last["Open"], last["High"], last["Low"], last["Close"]
    body, rng = abs(c - o), max(h - l, 1e-9)
    upper, lower = h - max(o, c), min(o, c) - l
    if rng <= max(h, 1e-9) * 0.001:
        shape = "一字線"
    elif body <= 0.1 * rng:
        shape = "十字線"
    elif lower >= 2 * body and upper <= body:
        shape = "長下影線"
    elif upper >= 2 * body and lower <= body:
        shape = "長上影線"
    elif body >= 0.7 * rng:
        shape = "長紅K" if c > o else "長綠K（長黑K）"
    else:
        shape = "一般K棒"
    color = "紅K（收盤高於開盤）" if c > o else "綠K（收盤低於開盤）" if c < o else "平盤"
    for fid, lab, v in (("open", "開盤", o), ("high", "最高", h), ("low", "最低", l), ("close", "收盤", c)):
        F(fs, f"tech.candle.{fid}", f"最新一根{lab}價", v, ccy)
    F(fs, "tech.candle.shape", "最新一根 K 棒型態", shape)
    F(fs, "tech.candle.color", "最新一根 K 棒顏色", color)
    last5 = d.tail(5)
    reds = int((last5["Close"] > last5["Open"]).sum())
    F(fs, "tech.candle.red5", "最近 5 根中的紅 K 數", reds, "根", 0)
    sections.append(Section(
        "candle", "K 線", "每根 K 棒記錄一天的開盤、最高、最低、收盤。實體代表開盤到收盤的距離，越長代表單方向力道越強；"
        "上影線代表盤中衝高後被賣回，下影線代表盤中下跌後被買回。台股慣例紅漲綠跌。",
        [f"最新一根（{as_of:%m/%d}）是{color}，型態為「{shape}」。",
         f"最近 5 根 K 棒中有 {reds} 根紅 K。"]
        + (["⚠️ 最新一根是盤中尚未收盤的 K 棒，型態還會改變。"] if kind == DataKind.DELAYED else []),
        ["單根 K 棒訊號較弱，要搭配位置（高檔或低檔）與成交量一起看。",
         "長影線出現在高檔與低檔的意義不同：高檔長上影線常被視為賣壓，低檔長下影線常被視為有承接。"],
        fs, Quiz(f"{as_of:%m/%d} 這根 K 棒是紅 K 還是綠 K？", ["紅 K", "綠 K", "平盤"],
                 "紅 K" if c > o else "綠 K" if c < o else "平盤", f"開盤 {o:,.2f}、收盤 {c:,.2f}：收盤高於開盤是紅 K。")))

    # 2. 均線 -----------------------------------------------------------------
    fs = FactSheet(subj)
    s5, s20, s60 = last["SMA5"], last["SMA20"], last["SMA60"]
    F(fs, "tech.ma.sma5", "5 日均線", s5, ccy)
    F(fs, "tech.ma.sma20", "20 日均線（月線）", s20, ccy)
    F(fs, "tech.ma.sma60", "60 日均線（季線）", s60, ccy)
    arrange = ("多頭排列" if s5 > s20 > s60 else "空頭排列" if s5 < s20 < s60 else "均線糾結") if not pd.isna(s60) else "資料不足"
    F(fs, "tech.ma.arrangement", "均線排列", arrange)
    slope20 = (d["SMA20"].iloc[-1] / d["SMA20"].iloc[-6] - 1) * 100 if len(d) > 25 else float("nan")
    F(fs, "tech.ma.sma20_slope5", "月線近 5 日變化", slope20, "%")
    F(fs, "tech.ma.close_vs_sma60", "收盤相對季線", (c / s60 - 1) * 100 if not pd.isna(s60) else float("nan"), "%")
    cts, ctype = last_cross(d["SMA5"], d["SMA20"])
    if cts is not None:
        F(fs, "tech.ma.last_cross", "最近一次 5 日與 20 日均線交叉", f"{cts:%Y-%m-%d} {ctype}")
    sections.append(Section(
        "ma", "移動平均線", "移動平均線是最近 N 天收盤價的平均，用來抹平每天的雜訊、看出方向。"
        "5 日線看短線，20 日線（月線）看中短期，60 日線（季線）看中期。均線是落後指標，反映的是已經發生的走勢。",
        [f"5 日 {s5:,.2f}、20 日 {s20:,.2f}、60 日 {s60:,.2f}，目前是「{arrange}」。",
         f"月線近 5 日變化 {slope20:+.2f}%（{'往上' if slope20 > 0 else '往下'}彎）。" if not math.isnan(slope20) else "",
         f"收盤價比季線{'高' if c > s60 else '低'} {abs((c / s60 - 1) * 100):.2f}%。" if not pd.isna(s60) else "",
         f"最近一次 5 日與 20 日均線交叉：{cts:%Y-%m-%d} 的{ctype}。" if cts is not None else ""],
        ["多頭排列通常代表短中期買方較強，但排列已久、股價離均線太遠時，也可能出現回檔整理。",
         "均線糾結時方向不明，交叉訊號容易反覆出現（假訊號），可等均線重新發散再判斷。"],
        fs, Quiz("三條均線目前是哪一種排列？", ["多頭排列", "空頭排列", "均線糾結"], arrange,
                 f"5 日 {s5:,.2f}、20 日 {s20:,.2f}、60 日 {s60:,.2f}：由上到下依序 5>20>60 是多頭排列。")
        if arrange != "資料不足" else None))

    # 3. MACD ----------------------------------------------------------------
    fs = FactSheet(subj)
    F(fs, "tech.macd.macd", "MACD 線（DIF）", last["MACD"], "", 3)
    F(fs, "tech.macd.signal", "訊號線（DEA）", last["Signal"], "", 3)
    F(fs, "tech.macd.hist", "柱狀體（MACD − 訊號線）", last["Hist"], "", 3)
    h3 = d["Hist"].tail(3).abs().tolist()
    mom = "柱狀體連續放大（動能增強）" if h3[2] > h3[1] > h3[0] else "柱狀體連續縮小（動能減弱）" if h3[2] < h3[1] < h3[0] else "動能變化不明顯"
    F(fs, "tech.macd.momentum", "柱狀體近 3 日變化", mom)
    mts, mtype = last_cross(d["MACD"], d["Signal"])
    if mts is not None:
        F(fs, "tech.macd.last_cross", "最近一次 MACD 交叉", f"{mts:%Y-%m-%d} {mtype}")
    above = last["MACD"] > last["Signal"]
    sections.append(Section(
        "macd", "MACD", "MACD 用 12 日與 26 日指數平均線的差距衡量趨勢與動能：MACD 線 = 12 日 EMA − 26 日 EMA，"
        "訊號線是 MACD 線的 9 日平均，柱狀體是兩者差距。MACD 在 0 軸之上代表短期均線高於長期均線。",
        [f"MACD {last['MACD']:.3f}、訊號線 {last['Signal']:.3f}：MACD 在訊號線{'之上' if above else '之下'}，"
         f"位在 0 軸{'之上' if last['MACD'] > 0 else '之下'}。", f"{mom}。",
         f"最近一次交叉：{mts:%Y-%m-%d} 的{mtype}。" if mts is not None else ""],
        ["MACD 在 0 軸之上的黃金交叉，通常被視為較強的偏多訊號；0 軸之下的黃金交叉可能只是跌深反彈。",
         "盤整時 MACD 常在 0 軸附近反覆交叉，訊號可靠度較低。"],
        fs, Quiz("最新的 MACD 線在訊號線上面還是下面？", ["上面", "下面"], "上面" if above else "下面",
                 f"MACD {last['MACD']:.3f}、訊號線 {last['Signal']:.3f}。")))

    # 4. RSI -----------------------------------------------------------------
    fs = FactSheet(subj)
    rsi = last["RSI14"]
    zone = "超買區（>70）" if rsi > 70 else "超賣區（<30）" if rsi < 30 else "中性區（30–70）"
    F(fs, "tech.rsi.rsi14", "RSI(14)", rsi, "", 1)
    F(fs, "tech.rsi.zone", "RSI 所在區間", zone)
    over = d.index[d["RSI14"] > 70]
    under = d.index[d["RSI14"] < 30]
    if len(over):
        F(fs, "tech.rsi.last_over70", "最近一次 RSI 高於 70 的日期", f"{over[-1]:%Y-%m-%d}")
    if len(under):
        F(fs, "tech.rsi.last_under30", "最近一次 RSI 低於 30 的日期", f"{under[-1]:%Y-%m-%d}")
    sections.append(Section(
        "rsi", "RSI", "RSI 比較最近 14 天上漲與下跌的平均幅度，數值在 0 到 100 之間。高於 70 稱為超買（短期漲多），"
        "低於 30 稱為超賣（短期跌深）。強勢股可以在超買區停留很久，所以超買不等於馬上會跌。",
        [f"RSI(14) = {rsi:.1f}，位在{zone}。",
         f"最近一次 RSI 高於 70：{over[-1]:%Y-%m-%d}。" if len(over) else "期間內 RSI 沒有高於 70。",
         f"最近一次 RSI 低於 30：{under[-1]:%Y-%m-%d}。" if len(under) else "期間內 RSI 沒有低於 30。"],
        ["RSI 在 50 以上代表近期上漲力道大於下跌力道，50 以下則相反。",
         "股價創新高但 RSI 沒有跟著創高（頂背離），常被視為上漲力道減弱的警訊。"],
        fs, Quiz("目前的 RSI 落在哪一區？", ["超買區（>70）", "中性區（30–70）", "超賣區（<30）"], zone, f"RSI(14) = {rsi:.1f}。")))

    # 5. KD ------------------------------------------------------------------
    fs = FactSheet(subj)
    k_, d_ = last["K"], last["D"]
    kzone = "高檔（>80）" if k_ > 80 else "低檔（<20）" if k_ < 20 else "中間區（20–80）"
    F(fs, "tech.kd.k", f"K 值（{kd_n}）", k_, "", 1)
    F(fs, "tech.kd.d", "D 值", d_, "", 1)
    F(fs, "tech.kd.zone", "K 值所在區間", kzone)
    kts, ktype = last_cross(d["K"], d["D"])
    if kts is not None:
        F(fs, "tech.kd.last_cross", "最近一次 KD 交叉", f"{kts:%Y-%m-%d} {ktype}")
    sections.append(Section(
        "kd", "KD 隨機指標", f"KD 比較收盤價在最近 {kd_n} 天最高價與最低價之間的位置：收盤越接近區間高點，K 值越高。"
        "D 是 K 的平滑線。K 值高於 80 為高檔、低於 20 為低檔；K 由下往上穿過 D 稱為黃金交叉。"
        + ("台股常用參數 9 日。" if inst.market == "TW" else "美股常用參數 14 日（Stochastic Oscillator）。"),
        [f"K = {k_:.1f}、D = {d_:.1f}，K 值位在{kzone}，K {'高於' if k_ > d_ else '低於'} D。",
         f"最近一次 KD 交叉：{kts:%Y-%m-%d} 的{ktype}。" if kts is not None else ""],
        ["KD 反應很快，適合觀察短線轉折，但在強勢或弱勢趨勢中容易長時間鈍化（一直停在高檔或低檔）。",
         "低檔黃金交叉與高檔死亡交叉，是最常被觀察的兩種情況。"],
        fs, Quiz("目前 K 值在哪一區？", ["高檔（>80）", "中間區（20–80）", "低檔（<20）"], kzone, f"K = {k_:.1f}。")))

    # 6. 布林通道 -------------------------------------------------------------
    fs = FactSheet(subj)
    pct, width = last["BB_pct"], last["BB_width"]
    wr = (d["BB_width"].dropna() < width).mean() * 100
    F(fs, "tech.bb.upper", "布林上軌", last["BB_up"], ccy)
    F(fs, "tech.bb.middle", "布林中軌（20 日均線）", last["BB_mid"], ccy)
    F(fs, "tech.bb.lower", "布林下軌", last["BB_low"], ccy)
    F(fs, "tech.bb.pctb", "%B（價格在通道中的位置）", pct, "", 2)
    F(fs, "tech.bb.width_rank", "通道寬度在期間內的百分位", wr, "%", 0)
    pos = ("突破上軌" if pct > 1 else "靠近上軌" if pct > 0.8 else "跌破下軌" if pct < 0 else "靠近下軌" if pct < 0.2 else "在中軌附近")
    sections.append(Section(
        "bb", "布林通道", "布林通道以 20 日均線為中軌，上下各加減 2 倍標準差。價格大多數時間會在通道內；"
        "通道變窄代表波動縮小，常在大波動之前出現；%B 表示價格在通道中的位置（0 = 下軌、1 = 上軌）。",
        [f"上軌 {last['BB_up']:,.2f}、中軌 {last['BB_mid']:,.2f}、下軌 {last['BB_low']:,.2f}；%B = {pct:.2f}，價格{pos}。",
         f"目前通道寬度比期間內 {wr:.0f}% 的日子還寬。"],
        ["強勢上漲時價格會沿著上軌走，碰到上軌不一定代表會回落。",
         "通道很窄時，代表市場在等待方向，之後的突破方向比較值得觀察。"],
        fs, Quiz("最新價在通道的上半部還是下半部？", ["上半部", "下半部"], "上半部" if pct >= 0.5 else "下半部",
                 f"%B = {pct:.2f}，大於 0.5 代表在中軌之上。")))

    # 7. 成交量 ---------------------------------------------------------------
    fs = FactSheet(subj)
    vol, vol20 = last["Volume"], last["VOL20"]
    ratio = vol / vol20 if vol20 else float("nan")
    unit = "張" if inst.market == "TW" else "股"
    div = 1000 if inst.market == "TW" else 1
    F(fs, "tech.vol.last", "最新成交量", vol / div, unit, 0)
    F(fs, "tech.vol.avg20", "20 日平均成交量", vol20 / div, unit, 0)
    F(fs, "tech.vol.ratio", "成交量 ÷ 20 日均量", ratio, "倍", 2)
    up = c >= d["Close"].iloc[-2]
    pv = ("價漲量增" if up and ratio >= 1.2 else "價漲量縮" if up and ratio <= 0.8 else
          "價跌量增" if not up and ratio >= 1.2 else "價跌量縮" if not up and ratio <= 0.8 else "量能持平")
    F(fs, "tech.vol.price_volume", "價量關係", pv)
    sections.append(Section(
        "volume", "成交量", "成交量代表市場參與的熱度。觀察重點是「價量是否配合」：上漲時量增代表買盤積極，"
        "上漲時量縮代表追價意願不足；下跌時量增代表賣壓沉重，下跌時量縮代表賣壓減輕。通常拿當天量和 20 日均量比較。",
        [f"最新成交量 {vol / div:,.0f} {unit}，是 20 日均量（{vol20 / div:,.0f} {unit}）的 {ratio:.2f} 倍，"
         f"價格{'上漲' if up else '下跌'} → {pv}。"]
        + (["⚠️ 盤中成交量還在累積，倍數會偏低。"] if kind == DataKind.DELAYED else []),
        ["突然出現的大量常出現在趨勢的起點或轉折點，值得回頭看當時發生了什麼事。",
         "量縮整理不一定是壞事，常是在等待下一個方向。"],
        fs, Quiz("最新一天的成交量比 20 日均量多還是少？", ["比較多", "比較少"], "比較多" if ratio >= 1 else "比較少",
                 f"成交量 ÷ 20 日均量 = {ratio:.2f}。")))

    # 8. 支撐與壓力 -----------------------------------------------------------
    fs = FactSheet(subj)
    recent = d.tail(250)
    hi, lo = swings(recent)
    below, above_s = lo[lo < c], hi[hi > c]
    support = below.iloc[-1] if len(below) else recent["Low"].min()
    resist = above_s.iloc[-1] if len(above_s) else recent["High"].max()
    trend = "資料不足"
    if len(hi) >= 2 and len(lo) >= 2:
        hh, hl = hi.iloc[-1] > hi.iloc[-2], lo.iloc[-1] > lo.iloc[-2]
        trend = "上升趨勢" if hh and hl else "下降趨勢" if (not hh and not hl) else "盤整"
        F(fs, "tech.levels.swing_high_prev", "前一個波段高點", hi.iloc[-2], ccy)
        F(fs, "tech.levels.swing_high_last", "最近的波段高點", hi.iloc[-1], ccy)
        F(fs, "tech.levels.swing_low_prev", "前一個波段低點", lo.iloc[-2], ccy)
        F(fs, "tech.levels.swing_low_last", "最近的波段低點", lo.iloc[-1], ccy)
    F(fs, "tech.levels.support", "下方最近支撐", support, ccy)
    F(fs, "tech.levels.resistance", "上方最近壓力", resist, ccy)
    F(fs, "tech.levels.to_support", "距離支撐", (c / support - 1) * 100, "%")
    F(fs, "tech.levels.to_resistance", "距離壓力", (resist / c - 1) * 100, "%")
    F(fs, "tech.levels.trend", "依波段高低點判斷的趨勢", trend)
    sections.append(Section(
        "levels", "支撐與壓力", "支撐是股價跌到附近容易止跌的價位，通常是之前的波段低點；壓力是漲到附近容易遇到賣壓的價位，"
        "通常是之前的波段高點。高點與低點都越來越高是上升趨勢，都越來越低是下降趨勢。支撐被跌破後常會變成壓力。",
        [f"下方最近支撐約 {support:,.2f}（距離 {(c / support - 1) * 100:.2f}%），上方最近壓力約 {resist:,.2f}"
         f"（距離 {(resist / c - 1) * 100:.2f}%）。",
         (f"最近兩個波段高點 {hi.iloc[-2]:,.2f} → {hi.iloc[-1]:,.2f}，波段低點 {lo.iloc[-2]:,.2f} → {lo.iloc[-1]:,.2f}，"
          f"判斷為「{trend}」。") if trend != "資料不足" else "波段高低點不足，無法判斷趨勢。"],
        ["支撐與壓力是「區域」而不是精確的價格，每個人畫的線可能略有不同。",
         "價格突破壓力時如果伴隨成交量放大，通常被認為比較有效。"],
        fs, Quiz("依波段高低點，目前比較像哪一種走勢？", ["上升趨勢", "下降趨勢", "盤整"], trend,
                 "比較最近兩個波段高點與低點：都變高是上升、都變低是下降、一高一低是盤整。")
        if trend != "資料不足" else None))

    for s in sections:
        s.reading = [r for r in s.reading if r]

    # 多週期比較 --------------------------------------------------------------
    tf_rows, all_facts = [], FactSheet(subj)
    for name, rule in (("日線", None), ("週線", "W-FRI"), ("月線", "ME")):
        frame = df if rule is None else df.resample(rule).agg(
            {"Open": "first", "High": "max", "Low": "min", "Close": "last", "Volume": "sum"}).dropna()
        if len(frame) < 25:
            tf_rows.append({"週期": name, "收盤": np.nan, "20 期均線": np.nan, "均線方向": "資料不足", "RSI(14)": np.nan, "判斷": "資料不足"})
            continue
        sma = frame["Close"].rolling(20).mean()
        r14 = ta.momentum.RSIIndicator(frame["Close"], window=14).rsi().iloc[-1]
        slope = "往上" if sma.iloc[-1] > sma.iloc[-4] else "往下"
        above_ma = frame["Close"].iloc[-1] > sma.iloc[-1]
        verdict = "偏多" if above_ma and slope == "往上" else "偏空" if (not above_ma and slope == "往下") else "整理"
        key = {"日線": "daily", "週線": "weekly", "月線": "monthly"}[name]
        all_facts.add(Fact(f"tech.tf.{key}.close_vs_sma20", f"{name}收盤相對 20 期均線", f"{'高於' if above_ma else '低於'}均線", "", asof, src))
        all_facts.add(Fact(f"tech.tf.{key}.sma20_dir", f"{name} 20 期均線方向", slope, "", asof, src))
        all_facts.add(Fact(f"tech.tf.{key}.rsi14", f"{name} RSI(14)", float(r14), "", asof, src, kind.value, 1))
        all_facts.add(Fact(f"tech.tf.{key}.verdict", f"{name}判斷", verdict, "", asof, src))
        tf_rows.append({"週期": name, "收盤": frame["Close"].iloc[-1], "20 期均線": sma.iloc[-1], "均線方向": slope,
                        "RSI(14)": r14, "判斷": verdict})
    timeframes = pd.DataFrame(tf_rows)

    # 歷史案例（同一檔股票過去發生類似情況後的表現）---------------------------------
    close = d["Close"]
    events = {
        "RSI 跌破 30（進入超賣）": ((d["RSI14"] < 30) & (d["RSI14"].shift(1) >= 30),
                              "RSI 從 30 以上跌到 30 以下的日子。用來觀察「超賣之後是否反彈」。"),
        "RSI 突破 70（進入超買）": ((d["RSI14"] > 70) & (d["RSI14"].shift(1) <= 70),
                              "RSI 從 70 以下升到 70 以上的日子。用來觀察「超買之後是否回檔」。"),
        "5 日線向上穿過 20 日線（黃金交叉）": ((d["SMA5"] > d["SMA20"]) & (d["SMA5"].shift(1) <= d["SMA20"].shift(1)),
                                  "短期均線由下往上穿過中期均線的日子。用來觀察「黃金交叉之後是否延續上漲」。"),
        "收盤跌破布林下軌": ((d["Close"] < d["BB_low"]) & (d["Close"].shift(1) >= d["BB_low"].shift(1)),
                       "收盤價從通道內跌到下軌以下的日子。用來觀察「跌破下軌之後的表現」。"),
    }
    cases: list[CaseStats] = []
    years_span = (d.index[-1] - d.index[0]).days / 365
    for i, (name, (mask, expl)) in enumerate(events.items()):
        dates = list(d.index[mask.fillna(False)])
        rows = []
        for ts in dates:
            r5, r20 = forward_returns(close, ts)
            rows.append(Case(name, ts.date(), float(close.loc[ts]), r5, r20))
        done = [r.ret20 for r in rows if r.ret20 is not None]
        stats = CaseStats(name, expl, len(rows),
                          (sum(1 for x in done if x > 0) / len(done) * 100) if done else None,
                          float(np.median(done)) if done else None, rows[-3:][::-1])
        cases.append(stats)
        all_facts.add(Fact(f"tech.case.{i}.count", f"過去約 {years_span:.0f} 年「{name}」發生次數", float(len(rows)), "次", asof, src, kind.value, 0))
        if stats.up_ratio20 is not None:
            all_facts.add(Fact(f"tech.case.{i}.up_ratio20", f"「{name}」後 20 個交易日上漲的比例", stats.up_ratio20, "%", asof, src, kind.value, 0))
            all_facts.add(Fact(f"tech.case.{i}.median20", f"「{name}」後 20 個交易日報酬中位數", stats.median20, "%", asof, src, kind.value, 2))

    for s in sections:
        all_facts.merge(s.facts)
    notes = [f"指標以{basis}計算；成交量為交易所實際成交股數。", *bars.notes]
    return TechnicalReport(inst, as_of, kind, basis, sections, timeframes, cases, all_facts, rep, notes, d)
