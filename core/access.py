"""AI 家教的存取控制：只有知道密碼的人可以使用（避免公開網站耗用 Gemini 額度、也符合 Gemini 免費方案的使用條款）。

規則：
- 伺服器沒有設定 AI_TUTOR_PASSWORD → AI 家教停用（預設關閉，不會意外公開）
- 密碼用固定時間比對（hmac.compare_digest），避免從回應時間猜出密碼
- 同一個瀏覽器工作階段連續輸錯 5 次 → 鎖定 10 分鐘
"""
from __future__ import annotations

import hmac
import time
from dataclasses import dataclass, field

from .config_bridge import get_key

MAX_ATTEMPTS = 5
LOCKOUT_SECONDS = 600


@dataclass
class Gate:
    unlocked: bool = False
    failures: int = 0
    locked_until: float = 0.0
    history: list[float] = field(default_factory=list)

    def locked(self, now: float | None = None) -> float:
        """回傳還要鎖定幾秒（0 = 沒有鎖定）。"""
        now = time.time() if now is None else now
        return max(0.0, self.locked_until - now)

    def attempt(self, entered: str, password: str | None, now: float | None = None) -> str:
        """回傳 "ok" / "wrong" / "locked" / "disabled"。"""
        now = time.time() if now is None else now
        if not password:
            return "disabled"
        if self.locked(now):
            return "locked"
        if hmac.compare_digest(entered.encode("utf-8"), password.encode("utf-8")):
            self.unlocked, self.failures = True, 0
            return "ok"
        self.failures += 1
        self.history.append(now)
        if self.failures >= MAX_ATTEMPTS:
            self.locked_until, self.failures = now + LOCKOUT_SECONDS, 0
            return "locked"
        return "wrong"


def configured_password() -> str | None:
    return get_key("AI_TUTOR_PASSWORD")
