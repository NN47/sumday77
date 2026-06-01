"""Сервис для работы с DeepSeek API."""
from __future__ import annotations

import logging
import time

from openai import OpenAI

from config import DEEPSEEK_API_KEY, DEEPSEEK_BASE_URL, DEEPSEEK_MODEL

logger = logging.getLogger(__name__)


class DeepSeekServiceError(Exception):
    """Базовая ошибка DeepSeek."""


class DeepSeekServiceConfigError(DeepSeekServiceError):
    """Ошибка конфигурации DeepSeek."""


class DeepSeekServiceTemporaryError(DeepSeekServiceError):
    """Временная ошибка DeepSeek (timeout/network/rate limit/empty response)."""


class DeepSeekService:
    """Сервис анализа текста еды через DeepSeek."""

    def __init__(self) -> None:
        self._client: OpenAI | None = None

    def _get_client(self) -> OpenAI:
        if not DEEPSEEK_API_KEY:
            raise DeepSeekServiceConfigError("DEEPSEEK_API_KEY is not configured")
        if self._client is None:
            self._client = OpenAI(
                api_key=DEEPSEEK_API_KEY,
                base_url=DEEPSEEK_BASE_URL,
                timeout=60.0,
            )
        return self._client

    def analyze_food_text(self, text: str) -> str:
        """Отправляет текст еды в DeepSeek и возвращает сырой JSON-ответ модели."""
        if not text:
            raise ValueError("Text is empty")

        started = time.perf_counter()
        logger.info("DeepSeek: input text=%s", text)
        logger.info("DeepSeek: sending request model=%s", DEEPSEEK_MODEL)

        try:
            client = self._get_client()
            response = client.chat.completions.create(
                model=DEEPSEEK_MODEL,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "Ты нутрициолог. Верни строго JSON без markdown и текста. "
                            "Формат ответа: "
                            '{"items":[{"name":"...", "grams":123, "kcal":100, "protein":10, "fat":5, "carbs":12}],'
                            '"total":{"kcal":200,"protein":20,"fat":10,"carbs":24}}. '
                            "Обязательно поля items и total. "
                            "Для КАЖДОГО элемента в items укажи grams, kcal, protein, fat, carbs числами (не null, не строки). "
                            "Если нет точных данных — оцени приблизительно, но не оставляй поля пустыми."
                        ),
                    },
                    {"role": "user", "content": text},
                ],
            )

            content = ((response.choices or [None])[0].message.content or "").strip()
            elapsed_ms = int((time.perf_counter() - started) * 1000)
            if not content:
                raise DeepSeekServiceTemporaryError("DeepSeek returned empty response")

            logger.info("DeepSeek: response=%s", content)
            logger.info("DeepSeek: done in %sms", elapsed_ms)
            return content
        except DeepSeekServiceError:
            raise
        except Exception as exc:  # pragma: no cover - внешние исключения SDK/API
            message = str(exc).lower()
            if any(token in message for token in ("timeout", "timed out", "429", "rate", "network", "connection", "500", "502", "503", "504")):
                raise DeepSeekServiceTemporaryError(str(exc)) from exc
            raise DeepSeekServiceError(str(exc)) from exc


deepseek_service = DeepSeekService()
