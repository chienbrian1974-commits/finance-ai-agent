"""從官方來源重建代號主檔（data/symbols/*.csv）。

執行：.venv/bin/python -m core.symbols_update

來源（都不需要 API Key）：
- 台股上市：臺灣證券交易所 OpenAPI  STOCK_DAY_ALL
- 台股上櫃：證券櫃檯買賣中心 OpenAPI  tpex_mainboard_daily_close_quotes
- 美股：Nasdaq Trader Symbol Directory  nasdaqlisted.txt（NASDAQ）、otherlisted.txt（NYSE、NYSE Arca 等）
"""
from __future__ import annotations

import csv
import re
from datetime import datetime, timezone
from pathlib import Path

import requests

OUT_DIR = Path(__file__).resolve().parent.parent / "data" / "symbols"

TWSE_URL = "https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL"
TPEX_URL = "https://www.tpex.org.tw/openapi/v1/tpex_mainboard_daily_close_quotes"
NASDAQ_URL = "https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt"
OTHER_URL = "https://www.nasdaqtrader.com/dynamic/SymDir/otherlisted.txt"

FIELDS = ["code", "name", "exchange", "asset_type"]

# 台股 ETF 代號最後一個字母代表的類型（其餘字母歸為一般 ETF）
TW_ETF_SUFFIX = {"L": "etf_leveraged", "R": "etf_inverse", "B": "etf_bond", "A": "etf_active", "U": "etf_futures"}

# otherlisted.txt 的 Exchange 欄位
US_OTHER_EXCHANGE = {"N": "NYSE", "A": "NYSE American", "P": "NYSE Arca", "Z": "Cboe BZX", "V": "IEX"}


def classify_tw(code: str) -> str | None:
    """依官方代號規則分類；權證、ETN 等不收錄，回傳 None。"""
    # 先判斷 ETF：00 開頭的 4 碼（0050、0056）也是 ETF，不是一般股
    if re.fullmatch(r"00\d{2,4}[A-Z]?", code):
        return TW_ETF_SUFFIX.get(code[-1], "etf") if code[-1].isalpha() else "etf"
    if re.fullmatch(r"\d{4}", code):
        return "stock"
    if re.fullmatch(r"\d{4}[A-Z]\d?", code):
        return "preferred"
    return None


def fetch_tw() -> list[dict]:
    rows: list[dict] = []
    twse = requests.get(TWSE_URL, timeout=60).json()
    for r in twse:
        t = classify_tw(r["Code"].strip())
        if t:
            rows.append({"code": r["Code"].strip(), "name": r["Name"].strip(), "exchange": "TWSE", "asset_type": t})
    tpex = requests.get(TPEX_URL, timeout=120).json()
    for r in tpex:
        code = r["SecuritiesCompanyCode"].strip()
        t = classify_tw(code)
        if t:
            rows.append({"code": code, "name": r["CompanyName"].strip(), "exchange": "TPEX", "asset_type": t})
    return dedupe(rows)


def _clean_us_name(name: str) -> str:
    return re.sub(r"\s*-?\s*(Common Stock|Ordinary Shares|Class [A-Z] Common Stock)\s*$", "", name.strip()) or name.strip()


def _pipe_rows(url: str) -> list[dict]:
    text = requests.get(url, timeout=60).text
    lines = [ln for ln in text.splitlines() if ln and not ln.startswith("File Creation Time")]
    return list(csv.DictReader(lines, delimiter="|"))


def fetch_us() -> list[dict]:
    rows: list[dict] = []
    for r in _pipe_rows(NASDAQ_URL):
        if r.get("Test Issue") == "Y":
            continue
        rows.append({"code": r["Symbol"].strip(), "name": _clean_us_name(r["Security Name"]), "exchange": "NASDAQ",
                     "asset_type": "etf" if r.get("ETF") == "Y" else "stock"})
    for r in _pipe_rows(OTHER_URL):
        if r.get("Test Issue") == "Y":
            continue
        rows.append({"code": r["ACT Symbol"].strip(), "name": _clean_us_name(r["Security Name"]),
                     # 不認得的交易所代碼照實標示，不自行猜測名稱
                     "exchange": US_OTHER_EXCHANGE.get(r.get("Exchange", ""), f"其他（代碼 {r.get('Exchange', '')}）"),
                     "asset_type": "etf" if r.get("ETF") == "Y" else "stock"})
    return dedupe(rows)


def dedupe(rows: list[dict]) -> list[dict]:
    seen, out = set(), []
    for r in rows:
        if r["code"] not in seen:
            seen.add(r["code"])
            out.append(r)
    return sorted(out, key=lambda r: r["code"])


def write(market: str, rows: list[dict]) -> Path:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUT_DIR / f"{market.lower()}.csv"
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows)
    return path


def main() -> None:
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%MZ")
    for market, fn in (("TW", fetch_tw), ("US", fetch_us)):
        rows = fn()
        if len(rows) < 500:  # 防呆：來源異常時不要用殘缺資料覆蓋舊檔
            raise SystemExit(f"{market} 只取得 {len(rows)} 筆，疑似來源異常，未寫入。")
        path = write(market, rows)
        print(f"{market}: {len(rows)} 筆 → {path.relative_to(OUT_DIR.parent.parent)}")
    (OUT_DIR / "UPDATED_AT").write_text(stamp + "\n", encoding="utf-8")
    print("更新時間", stamp)


if __name__ == "__main__":
    main()
