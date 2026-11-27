"""實際錄下的來源回應（2026-11-26 收盤資料，只保留測試需要的欄位與筆數）。

用途：在不連網的情況下，驗證各來源格式轉換是否正確。這些是真實資料的快照，不是虛構數字。
"""
TWSE_2330 = {"Date": "1151126", "Code": "2330", "Name": "台積電", "TradeVolume": "45892693",
             "TradeValue": "98655062940", "OpeningPrice": "2150.00", "HighestPrice": "2170.00",
             "LowestPrice": "2120.00", "ClosingPrice": "2145.00", "Change": "-5.0000", "Transaction": "129341"}

TPEX_6488 = {"Date": "1151126", "SecuritiesCompanyCode": "6488", "CompanyName": "環球晶", "Close": "518.00",
             "Change": "+41.00", "Open": "503.00", "High": "524.00", "Low": "498.50", "TradingShares": "7803697",
             "TransactionAmount": "4004762640"}

FINMIND_2330 = [
    {"date": "2026-11-24", "stock_id": "2330", "Trading_Volume": 13213039, "Trading_money": 29465076970,
     "open": 2230.0, "max": 2230.0, "min": 2230.0, "close": 2230.0, "spread": 200.0, "Trading_turnover": 31491},
    {"date": "2026-11-25", "stock_id": "2330", "Trading_Volume": 84911914, "Trading_money": 179487749840,
     "open": 2150.0, "max": 2170.0, "min": 2020.0, "close": 2150.0, "spread": -80.0, "Trading_turnover": 286506},
    {"date": "2026-11-26", "stock_id": "2330", "Trading_Volume": 45892693, "Trading_money": 98655062940,
     "open": 2150.0, "max": 2170.0, "min": 2120.0, "close": 2145.0, "spread": -5.0, "Trading_turnover": 129341},
]

# Yahoo（yfinance history, auto_adjust=False）AAPL，保留原始浮點誤差
YAHOO_AAPL = {
    "dates": ["2026-11-23", "2026-11-24", "2026-11-25"],
    "Open": [347.869995, 348.059998, 353.480011],
    "High": [352.600006, 354.399994, 356.75],
    "Low": [345.440002, 345.880005, 345.130005],
    "Close": [349.079987, 354.070007, 355.5799865722656],
    "Adj Close": [349.079987, 354.070007, 355.5799865722656],
    "Volume": [58588700, 41270400, 54285300],
}
