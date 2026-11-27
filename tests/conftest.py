import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import os

# 單元測試不連網：交易日曆改用內建的 exchange_calendars（見 core/calendars.py）
os.environ.setdefault("FINANCE_OFFLINE", "1")
