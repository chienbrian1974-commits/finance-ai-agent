"""金融分析環境功能驗證。執行：.venv/bin/python test_setup.py

每一項獨立用 try/except 包起來，最後印出總表。
狀態：✅ 通過、❌ 失敗、⏭ 跳過（例如沒有選用的 Key）。
"""
from __future__ import annotations

import math
import sys
import warnings
from datetime import date, timedelta
from pathlib import Path

warnings.filterwarnings("ignore")
sys.stdout.reconfigure(line_buffering=True)  # 讓 print 與套件 log 依序出現

from config import MissingKeyError, get_key  # noqa: E402

ROOT = Path(__file__).resolve().parent
OUTPUTS = ROOT / "outputs"
OUTPUTS.mkdir(exist_ok=True)

SECRET_NAMES = ["ALPHAVANTAGE_API_KEY", "FMP_API_KEY", "FINMIND_TOKEN"]


class Skip(Exception):
    """這一項不適用（例如沒有選用 Key），不算失敗。"""


def scrub(text: str) -> str:
    """把錯誤訊息裡可能出現的 Key 遮掉。"""
    for name in SECRET_NAMES:
        v = get_key(name)
        if v:
            text = text.replace(v, "****")
    return text


results: list[tuple[str, str, str]] = []  # (名稱, 狀態, 說明)
_cache: dict[str, object] = {}


def run(name: str):
    def deco(fn):
        print(f"\n=== {name} ===")
        try:
            note = fn() or ""
            results.append((name, "✅", note))
        except Skip as e:
            print(f"  ⏭ 跳過：{e}")
            results.append((name, "⏭", str(e)))
        except Exception as e:  # noqa: BLE001
            msg = scrub(f"{type(e).__name__}: {e}")
            print("  ❌ " + msg.replace("\n", "\n     "))
            results.append((name, "❌", msg))
        return fn
    return deco


def yf_history(ticker: str, period: str = "3mo"):
    import yfinance as yf

    key = f"{ticker}:{period}"
    if key not in _cache:
        df = yf.Ticker(ticker).history(period=period, auto_adjust=False)
        # 盤中或剛收盤時，yfinance 可能多給一列尚未結算的 NaN，先去掉
        df = df.dropna(subset=["Close"])
        if df.empty:
            raise RuntimeError(f"yfinance 沒有回傳 {ticker} 的資料")
        _cache[key] = df
    return _cache[key]


def sma_report(ticker: str) -> str:
    df = yf_history(ticker).copy()
    df["SMA20"] = df["Close"].rolling(20).mean()
    last = df.iloc[-1]
    d = df.index[-1].date()
    pos = "之上" if last["Close"] > last["SMA20"] else "之下"
    print(f"  資料筆數 {len(df)}，最新日期 {d}")
    print(f"  收盤 {last['Close']:.2f}，SMA20 {last['SMA20']:.2f} → 收盤價在均線{pos}")
    return f"{d} 收盤 {last['Close']:.2f} / SMA20 {last['SMA20']:.2f}（均線{pos}）"


# 1. 美股 ---------------------------------------------------------------
@run("1. 美股 AAPL（yfinance + SMA20）")
def t_us():
    return sma_report("AAPL")


# 2. 台股 ---------------------------------------------------------------
@run("2. 台股 2330（yfinance × FinMind 比對）")
def t_tw():
    from FinMind.data import DataLoader

    note = sma_report("2330.TW")
    yf_df = yf_history("2330.TW")

    dl = DataLoader()
    token = get_key("FINMIND_TOKEN")
    if token:
        dl.login_by_token(api_token=token)
        print("  FinMind：使用 token 登入")
    else:
        print("  FinMind：未設定 token，以免登入額度查詢")
    start = (date.today() - timedelta(days=30)).isoformat()
    fm = dl.taiwan_stock_daily(stock_id="2330", start_date=start, end_date=date.today().isoformat())
    if fm is None or fm.empty:
        raise RuntimeError("FinMind 沒有回傳 2330 資料")

    fm_close = fm.set_index("date")["close"]
    yf_close = yf_df["Close"].copy()
    yf_close.index = yf_close.index.strftime("%Y-%m-%d")
    common = sorted(set(fm_close.index) & set(yf_close.index))
    if not common:
        raise RuntimeError("兩邊沒有共同交易日可比對")
    d = common[-1]
    a, b = float(yf_close[d]), float(fm_close[d])
    diff = abs(a - b) / b * 100
    print(f"  比對日 {d}：yfinance {a:.2f} vs FinMind {b:.2f}，差異 {diff:.3f}%")
    if not math.isfinite(diff):
        raise RuntimeError(f"比對值不是有效數字（yfinance {a}, FinMind {b}）")
    if diff > 1:
        raise RuntimeError(f"收盤價差異 {diff:.2f}% 超過 1%")
    return f"{note}；FinMind 比對差異 {diff:.3f}%"


# 3. 技術指標 -----------------------------------------------------------
@run("3. 技術指標 AAPL RSI(14) / MACD（ta，pandas_ta 比對）")
def t_ta():
    import ta

    close = yf_history("AAPL", "1y")["Close"]  # 用 1 年資料讓指數平滑收斂
    rsi = ta.momentum.RSIIndicator(close, window=14).rsi().iloc[-1]
    m = ta.trend.MACD(close, window_slow=26, window_fast=12, window_sign=9)
    macd, sig = m.macd().iloc[-1], m.macd_signal().iloc[-1]
    print(f"  ta        RSI14 {rsi:.2f}  MACD {macd:.3f}  Signal {sig:.3f}")
    note = f"RSI14 {rsi:.2f}，MACD {macd:.3f} / Signal {sig:.3f}"

    try:
        import pandas_ta as pta
    except Exception as e:  # noqa: BLE001
        print(f"  pandas_ta 無法匯入，略過比對：{e}")
        return note + "（pandas_ta 未安裝）"
    rsi2 = pta.rsi(close, length=14).iloc[-1]
    mdf = pta.macd(close, fast=12, slow=26, signal=9)
    macd2, sig2 = mdf["MACD_12_26_9"].iloc[-1], mdf["MACDs_12_26_9"].iloc[-1]
    print(f"  pandas_ta RSI14 {rsi2:.2f}  MACD {macd2:.3f}  Signal {sig2:.3f}")
    d_rsi, d_macd = abs(rsi - rsi2), abs(macd - macd2)
    print(f"  差異：RSI {d_rsi:.3f}，MACD {d_macd:.4f}")
    if d_rsi > 1 or d_macd > 0.1:
        raise RuntimeError(f"ta 與 pandas_ta 結果差異過大（RSI {d_rsi:.2f}, MACD {d_macd:.3f}）")
    return note + f"；與 pandas_ta 差異 RSI {d_rsi:.3f}"


# 4. 加密貨幣 -----------------------------------------------------------
@run("4. 加密貨幣 BTC/USDT（ccxt 公開 API）")
def t_crypto():
    import ccxt

    errors = []
    for ex_id in ("binance", "kraken", "okx"):
        try:
            ex = getattr(ccxt, ex_id)({"enableRateLimit": True, "timeout": 15000})
            t = ex.fetch_ticker("BTC/USDT")
            print(f"  {ex_id}：BTC/USDT last = {t['last']:,.2f}（{t.get('datetime')}）")
            return f"{ex_id} {t['last']:,.2f} USDT"
        except Exception as e:  # noqa: BLE001
            errors.append(f"{ex_id}: {type(e).__name__}")
            print(f"  {ex_id} 失敗（{type(e).__name__}），改試下一家")
    raise RuntimeError("；".join(errors))


# 5. 財報 ---------------------------------------------------------------
@run("5. 財報 AAPL 最近 3 份 10-K/10-Q（edgartools / SEC API）")
def t_sec():
    ua = get_key("SEC_USER_AGENT", required=True)  # 缺值 → MissingKeyError（清楚的錯誤訊息）
    if not ua.isascii():
        raise ValueError("SEC_USER_AGENT 只能用英文字元（HTTP header 限 ASCII），名字請改用英文或拼音，"
                         '例如 SEC_USER_AGENT="Jane Doe jane@example.com"')
    if "@" not in ua or " " not in ua:
        raise ValueError('SEC_USER_AGENT 格式應為「名字 email」，例如 SEC_USER_AGENT="Jane Doe jane@example.com"')
    rows = []
    try:
        from edgar import Company, set_identity

        set_identity(ua)
        filings = Company("AAPL").get_filings(form=["10-K", "10-Q"])
        for f in list(filings)[:3]:
            rows.append((f.form, str(f.filing_date)))
        src = "edgartools"
    except Exception as e:  # noqa: BLE001
        print(f"  edgartools 失敗（{type(e).__name__}: {e}），改用 SEC submissions API")
        import requests

        r = requests.get(
            "https://data.sec.gov/submissions/CIK0000320193.json",
            headers={"User-Agent": ua}, timeout=20,
        )
        r.raise_for_status()
        rec = r.json()["filings"]["recent"]
        for form, d in zip(rec["form"], rec["filingDate"]):
            if form in ("10-K", "10-Q"):
                rows.append((form, d))
            if len(rows) == 3:
                break
        src = "SEC API"
    if not rows:
        raise RuntimeError("沒有找到 10-K/10-Q")
    for form, d in rows:
        print(f"  {form:5s} {d}")
    return f"{src}：" + "、".join(f"{f} {d}" for f, d in rows)


# 6. 新聞與情緒 ---------------------------------------------------------
@run("6. 新聞 Yahoo Finance AAPL RSS + VADER 情緒")
def t_news():
    import feedparser
    from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer

    url = "https://feeds.finance.yahoo.com/rss/2.0/headline?s=AAPL&region=US&lang=en-US"
    feed = feedparser.parse(url, agent="Mozilla/5.0 (finance-env-test)")
    if not feed.entries:
        raise RuntimeError(f"RSS 沒有內容（HTTP {feed.get('status')}，{feed.get('bozo_exception', '')}）")
    sia = SentimentIntensityAnalyzer()
    scores = []
    for e in feed.entries[:5]:
        s = sia.polarity_scores(e.title)["compound"]
        scores.append(s)
        print(f"  {s:+.3f}  {e.title}")
    avg = sum(scores) / len(scores)
    return f"{len(scores)} 則，平均 compound {avg:+.3f}"


# 7. Alpha Vantage ------------------------------------------------------
@run("7. Alpha Vantage AAPL GLOBAL_QUOTE")
def t_av():
    import requests

    key = get_key("ALPHAVANTAGE_API_KEY")
    if not key:
        raise Skip("未設定 ALPHAVANTAGE_API_KEY")
    r = requests.get(
        "https://www.alphavantage.co/query",
        params={"function": "GLOBAL_QUOTE", "symbol": "AAPL", "apikey": key},
        timeout=20,
    )
    r.raise_for_status()
    data = r.json()
    q = data.get("Global Quote")
    if not q:
        # 額度用完或 Key 錯誤時，回傳的是 Note / Information / Error Message
        msg = data.get("Note") or data.get("Information") or data.get("Error Message") or str(data)[:200]
        raise RuntimeError(scrub(msg))
    print(f"  價格 {q['05. price']}，交易日 {q['07. latest trading day']}，漲跌 {q['10. change percent']}")
    return f"{q['07. latest trading day']} {q['05. price']}"


# 8. 視覺化 -------------------------------------------------------------
@run("8. 視覺化 plotly → outputs/test_chart.html")
def t_plot():
    import plotly.graph_objects as go
    from plotly.subplots import make_subplots

    fig = make_subplots(rows=2, cols=1, shared_xaxes=False, vertical_spacing=0.12,
                        subplot_titles=("AAPL（USD）", "2330.TW 台積電（TWD）"))
    for row, tk in ((1, "AAPL"), (2, "2330.TW")):
        df = yf_history(tk)
        sma = df["Close"].rolling(20).mean()
        fig.add_trace(go.Scatter(x=df.index, y=df["Close"], name=f"{tk} 收盤", line=dict(width=2)), row=row, col=1)
        fig.add_trace(go.Scatter(x=df.index, y=sma, name=f"{tk} SMA20", line=dict(width=1.5, dash="dash")), row=row, col=1)
    fig.update_layout(title="收盤價與 20 日均線（近 3 個月）", height=750, template="plotly_white",
                      hovermode="x unified")
    out = OUTPUTS / "test_chart.html"
    fig.write_html(out, include_plotlyjs=True)
    print(f"  已存檔：{out.relative_to(ROOT)}（{out.stat().st_size / 1024:.0f} KB）")
    return str(out.relative_to(ROOT))


# 總表 ------------------------------------------------------------------
print("\n" + "=" * 70)
print("總表")
print("=" * 70)
for name, status, note in results:
    print(f"{status} {name}")
    if note:
        print("     " + note.replace("\n", "\n     "))
ok = sum(s == "✅" for _, s, _ in results)
fail = sum(s == "❌" for _, s, _ in results)
skip = sum(s == "⏭" for _, s, _ in results)
print(f"\n通過 {ok}、失敗 {fail}、跳過 {skip}（共 {len(results)} 項）")
raise SystemExit(1 if fail else 0)
