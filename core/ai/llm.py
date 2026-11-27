"""LLM 抽換層。業務邏輯只呼叫 get_backend().generate_json(...)，不直接依賴任何一家 SDK。

目前實作：GeminiBackend（Google Gemini API，官方 SDK google-genai）。
沒有設定 GEMINI_API_KEY 時回傳 DisabledBackend：呼叫會丟出 LLMUnavailable，畫面改用規則式解說。
"""
from __future__ import annotations

import json
import os
from abc import ABC, abstractmethod

from ..config_bridge import get_key
from ..net import limiter, log, redact

# 依官方文件（2026-11-27 查閱）：gemini-3.5-flash-lite 為穩定版、最便宜的模型
DEFAULT_GEMINI_MODEL = "gemini-3.5-flash-lite"


class LLMUnavailable(Exception):
    """沒有設定 Key、超過額度、或服務失敗。訊息會顯示給使用者，不能含密鑰。"""


class LLMBackend(ABC):
    name = "llm"
    model = ""

    @abstractmethod
    def generate_json(self, system: str, prompt: str, schema: dict) -> dict:
        """回傳符合 schema 的 dict；失敗時丟出 LLMUnavailable。"""


class DisabledBackend(LLMBackend):
    name = "未啟用"

    def __init__(self, reason: str):
        self.reason = reason

    def generate_json(self, system: str, prompt: str, schema: dict) -> dict:
        raise LLMUnavailable(self.reason)


class GeminiBackend(LLMBackend):
    name = "Google Gemini"

    def __init__(self, api_key: str, model: str | None = None):
        from google import genai

        self.model = model or os.getenv("GEMINI_MODEL") or DEFAULT_GEMINI_MODEL
        self._client = genai.Client(api_key=api_key)

    def generate_json(self, system: str, prompt: str, schema: dict) -> dict:
        from google.genai import types

        limiter.acquire("gemini")
        try:
            resp = self._client.models.generate_content(
                model=self.model,
                contents=prompt,
                config=types.GenerateContentConfig(
                    system_instruction=system,
                    response_mime_type="application/json",
                    response_json_schema=schema,
                    temperature=0.2,
                ),
            )
            text = resp.text or ""
        except Exception as e:  # noqa: BLE001  SDK 會丟各種例外（額度、網路、權限）
            msg = redact(str(e))[:200]
            log.warning("Gemini 呼叫失敗：%s", msg)
            raise LLMUnavailable(f"Gemini 服務暫時無法使用（{type(e).__name__}）") from e
        try:
            return json.loads(text)
        except json.JSONDecodeError as e:
            raise LLMUnavailable("Gemini 回傳的格式不正確") from e


_backend: LLMBackend | None = None


def get_backend() -> LLMBackend:
    global _backend
    if _backend is None:
        key = get_key("GEMINI_API_KEY")
        if not key:
            _backend = DisabledBackend("伺服器尚未設定 GEMINI_API_KEY，目前只提供規則式解說。")
        else:
            try:
                _backend = GeminiBackend(key)
            except Exception as e:  # noqa: BLE001
                _backend = DisabledBackend(f"Gemini 初始化失敗（{type(e).__name__}）")
    return _backend


def set_backend(backend: LLMBackend | None) -> None:
    """測試用：換成假的 backend。"""
    global _backend
    _backend = backend
