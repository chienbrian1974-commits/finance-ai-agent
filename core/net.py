"""網路存取基礎建設：限流、重試、快取、logging，以及 log 中的密鑰遮蔽。

所有 Provider 都透過這裡呼叫外部 API，才能集中管理：
- 每個來源各自的請求頻率上限（遵守各家使用規範，不嘗試繞過）
- 失敗重試（只重試暫時性錯誤：逾時、連線失敗、429、5xx）
- 程序內快取（同一個伺服器上的所有使用者共用，減少外部請求）
"""
from __future__ import annotations

import logging
import re
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable

import requests

log = logging.getLogger("finance")
if not log.handlers:
    _h = logging.StreamHandler()
    _h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    log.addHandler(_h)
    log.setLevel(logging.INFO)

USER_AGENT = "finance-ai-agent/1.0 (+https://github.com/chienbrian1974-commits/finance-ai-agent)"

_SECRET_PARAM = re.compile(r"(?i)(api[_-]?key|apikey|token|key|secret|password)=([^&\s]+)")


def redact(text: str) -> str:
    """把網址或訊息裡的 apikey=xxx、token=xxx 遮成 ***，避免寫進 log。"""
    return _SECRET_PARAM.sub(lambda m: f"{m.group(1)}=***", str(text))


class ProviderError(Exception):
    """外部資料來源無法取得資料。reason 會顯示給使用者，所以不能含密鑰。"""

    def __init__(self, source: str, reason: str):
        super().__init__(f"{source}: {reason}")
        self.source = source
        self.reason = redact(reason)


# ---- 限流 ------------------------------------------------------------------
@dataclass
class Limit:
    min_interval: float          # 兩次請求最少間隔幾秒
    per_window: int | None = None  # 一段時間內最多幾次（例如每小時 300 次）
    window: float = 3600.0


# 依各來源公開的使用規範設定；數值刻意保守
LIMITS: dict[str, Limit] = {
    "twse": Limit(2.0),                       # 證交所 OpenAPI：未公布上限，保守每 2 秒 1 次
    "tpex": Limit(2.0),                       # 櫃買中心 OpenAPI：同上
    "finmind": Limit(0.5, per_window=280),    # FinMind 免費：每小時 300 次（未登入），保留餘裕
    "yahoo": Limit(0.5),                      # Yahoo：非官方 API，放慢避免被封鎖
    "sec": Limit(0.15),                       # SEC：每秒最多 10 次
    "alphavantage": Limit(12.0, per_window=25, window=86400),  # 免費：每天 25 次
    # Gemini：官方文件未公布這些模型的免費額度數字，先保守設定每 4 秒 1 次、每天 200 次（整個伺服器共用）
    "gemini": Limit(4.0, per_window=200, window=86400),
    "nasdaqtrader": Limit(1.0),
}


class RateLimiter:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._last: dict[str, float] = {}
        self._history: dict[str, list[float]] = {}

    def acquire(self, source: str) -> None:
        limit = LIMITS.get(source, Limit(0.5))
        with self._lock:
            now = time.monotonic()
            if limit.per_window:
                hist = [t for t in self._history.get(source, []) if now - t < limit.window]
                if len(hist) >= limit.per_window:
                    raise ProviderError(source, f"已達每 {limit.window / 3600:g} 小時 {limit.per_window} 次的請求上限，請稍後再試")
                self._history[source] = hist
            wait = limit.min_interval - (now - self._last.get(source, 0.0))
            if wait > 0:
                time.sleep(wait)
            self._last[source] = time.monotonic()
            if limit.per_window:
                self._history[source].append(self._last[source])


limiter = RateLimiter()


# ---- 快取 ------------------------------------------------------------------
class TTLCache:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._data: dict[Any, tuple[float, Any]] = {}

    def get(self, key: Any) -> Any:
        with self._lock:
            hit = self._data.get(key)
            if hit and hit[0] > time.monotonic():
                return hit[1]
            self._data.pop(key, None)
            return None

    def set(self, key: Any, value: Any, ttl: float) -> None:
        with self._lock:
            self._data[key] = (time.monotonic() + ttl, value)

    def clear(self) -> None:
        with self._lock:
            self._data.clear()


cache = TTLCache()


def cached(ttl: float) -> Callable:
    """方法快取：key = (類別名稱, 方法名稱, 參數)。失敗（拋出例外）不會被快取。"""

    def deco(fn: Callable) -> Callable:
        def wrapper(self, *args, **kwargs):
            key = (type(self).__name__, fn.__name__, args, tuple(sorted(kwargs.items())))
            hit = cache.get(key)
            if hit is not None:
                return hit
            value = fn(self, *args, **kwargs)
            cache.set(key, value, ttl)
            return value

        wrapper.__wrapped__ = fn  # type: ignore[attr-defined]
        return wrapper

    return deco


# ---- HTTP ------------------------------------------------------------------
_session = requests.Session()
_session.headers["User-Agent"] = USER_AGENT
RETRY_STATUS = {429, 500, 502, 503, 504}


def http_get(source: str, url: str, *, params: dict | None = None, headers: dict | None = None,
             timeout: float = 30, retries: int = 2) -> requests.Response:
    """GET，含限流與暫時性錯誤重試。永久性錯誤（404、401…）直接拋出 ProviderError。"""
    last_err = ""
    for attempt in range(retries + 1):
        limiter.acquire(source)
        t0 = time.monotonic()
        try:
            resp = _session.get(url, params=params, headers=headers, timeout=timeout)
        except requests.RequestException as e:
            last_err = f"連線失敗（{type(e).__name__}）"
        else:
            ms = (time.monotonic() - t0) * 1000
            log.info("%s GET %s → %s（%.0f ms）", source, redact(resp.url), resp.status_code, ms)
            if resp.status_code < 400:
                return resp
            last_err = f"HTTP {resp.status_code}"
            if resp.status_code not in RETRY_STATUS:
                break
        if attempt < retries:
            time.sleep(1.5 * (2 ** attempt))
    log.warning("%s 請求失敗：%s（%s）", source, last_err, redact(url))
    raise ProviderError(source, last_err)
