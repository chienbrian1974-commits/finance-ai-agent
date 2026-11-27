"""讀取伺服器端的設定值（API Key 等）。

順序：環境變數 → 專案根目錄的 .env（本機開發用）。
Streamlit Community Cloud 的 Secrets 頂層鍵會自動變成環境變數，所以也適用。
這些值只在伺服器端使用，不會送到瀏覽器。
"""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path


@lru_cache(maxsize=1)
def _load_dotenv() -> None:
    env = Path(__file__).resolve().parent.parent / ".env"
    if env.exists():
        try:
            from dotenv import load_dotenv

            load_dotenv(env, override=False)
        except ImportError:
            pass


def get_key(name: str) -> str | None:
    _load_dotenv()
    value = (os.getenv(name) or "").strip()
    return value or None
