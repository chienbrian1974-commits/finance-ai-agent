"""讀取 .env 設定。用法：

    from config import get_key
    av_key = get_key("ALPHAVANTAGE_API_KEY")                 # 沒填 → None
    ua = get_key("SEC_USER_AGENT", required=True)            # 沒填 → MissingKeyError
"""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
ENV_PATH = ROOT / ".env"

load_dotenv(ENV_PATH, override=False)

KEY_HELP = {
    "ALPHAVANTAGE_API_KEY": "到 https://www.alphavantage.co/support/#api-key 免費申請",
    "FMP_API_KEY": "到 https://site.financialmodelingprep.com/register 註冊後在 Dashboard 取得",
    "FINMIND_TOKEN": "選用；到 https://finmindtrade.com/ 註冊後取得",
    "SEC_USER_AGENT": "格式為「你的名字 your@email.com」，SEC EDGAR 規定必填",
}


class MissingKeyError(RuntimeError):
    """必填的設定值沒有填。"""


def get_key(name: str, required: bool = False) -> str | None:
    """取得設定值；空字串視為未設定。required=True 時缺值會丟出 MissingKeyError。"""
    value = (os.getenv(name) or "").strip()
    if value:
        return value
    if required:
        hint = KEY_HELP.get(name, "")
        where = "專案根目錄的 .env" if ENV_PATH.exists() else ".env（尚未建立，請先 cp .env.example .env）"
        raise MissingKeyError(
            f"缺少必填設定 {name}。請在 {where} 填入 {name}=...。" + (f"\n提示：{hint}" if hint else "")
        )
    return None


def mask(value: str | None) -> str:
    """顯示用：只露出末 4 碼，避免 Key 出現在輸出裡。"""
    if not value:
        return "(未設定)"
    return "****" + value[-4:] if len(value) > 8 else "****"
