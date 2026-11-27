"""技術指標（儀表板與教學分頁共用）。輸入請用還原價，避免除權息缺口干擾。"""
from __future__ import annotations

import pandas as pd
import ta


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
