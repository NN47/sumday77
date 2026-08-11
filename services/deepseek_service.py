"""Сервис для работы с DeepSeek API."""
from __future__ import annotations

import logging
import time

from openai import OpenAI

from config import DEEPSEEK_API_KEY, DEEPSEEK_BASE_URL, DEEPSEEK_MODEL
from services.ai_usage_logger import calculate_ai_cost, log_ai_usage
from utils.log_sanitizer import safe_exception_summary

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

    @staticmethod
    def _is_temporary_error(error: Exception) -> bool:
        message = str(error).lower()
        return any(
            token in message
            for token in ("timeout", "timed out", "429", "rate", "network", "connection", "500", "502", "503", "504")
        )

    def analyze_food_text(self, text: str, *, user_id: str | int | None = None, feature: str = "text_meal") -> str:
        """Отправляет текст еды в DeepSeek и возвращает сырой JSON-ответ модели."""
        if not text:
            raise ValueError("Text is empty")

        started = time.perf_counter()
        logger.info(
            "DeepSeek request started operation=text_meal model=%s input_chars=%s",
            DEEPSEEK_MODEL,
            len(text),
        )

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
            usage = getattr(response, "usage", None)
            input_tokens = getattr(usage, "prompt_tokens", None) if usage is not None else None
            output_tokens = getattr(usage, "completion_tokens", None) if usage is not None else None
            total_tokens = getattr(usage, "total_tokens", None) if usage is not None else None
            if not content:
                log_ai_usage(
                    provider="deepseek",
                    feature=feature,
                    model=DEEPSEEK_MODEL,
                    status="error",
                    user_id=user_id,
                    latency_ms=elapsed_ms,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    total_tokens=total_tokens,
                    estimated_cost_usd=calculate_ai_cost("deepseek", DEEPSEEK_MODEL, input_tokens, output_tokens),
                    error_message="DeepSeek returned empty response",
                    raw_metadata={
                        "response_id": getattr(response, "id", None),
                        "input_chars": len(text),
                        "output_chars": 0,
                    },
                )
                raise DeepSeekServiceTemporaryError("DeepSeek returned empty response")

            log_ai_usage(
                provider="deepseek",
                feature=feature,
                model=DEEPSEEK_MODEL,
                status="success",
                user_id=user_id,
                latency_ms=elapsed_ms,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                total_tokens=total_tokens,
                estimated_cost_usd=calculate_ai_cost("deepseek", DEEPSEEK_MODEL, input_tokens, output_tokens),
                raw_metadata={
                    "response_id": getattr(response, "id", None),
                    "input_chars": len(text),
                    "output_chars": len(content),
                },
            )

            logger.info(
                "DeepSeek request completed operation=text_meal model=%s status=success latency_ms=%s output_chars=%s",
                DEEPSEEK_MODEL,
                elapsed_ms,
                len(content),
            )
            return content
        except DeepSeekServiceError as exc:
            if isinstance(exc, DeepSeekServiceTemporaryError) and str(exc) == "DeepSeek returned empty response":
                raise
            elapsed_ms = int((time.perf_counter() - started) * 1000)
            log_ai_usage(
                provider="deepseek",
                feature=feature,
                model=DEEPSEEK_MODEL,
                status="error",
                user_id=user_id,
                latency_ms=elapsed_ms,
                error_message=safe_exception_summary(exc),
                raw_metadata={"input_chars": len(text)},
            )
            raise
        except Exception as exc:  # pragma: no cover - внешние исключения SDK/API
            elapsed_ms = int((time.perf_counter() - started) * 1000)
            log_ai_usage(
                provider="deepseek",
                feature=feature,
                model=DEEPSEEK_MODEL,
                status="error",
                user_id=user_id,
                latency_ms=elapsed_ms,
                error_message=safe_exception_summary(exc),
                raw_metadata={"input_chars": len(text)},
            )
            if self._is_temporary_error(exc):
                raise DeepSeekServiceTemporaryError("DeepSeek request temporarily unavailable") from exc
            raise DeepSeekServiceError("DeepSeek request failed") from exc

    def analyze_activity_prompt(
        self,
        prompt: str,
        *,
        user_id: str | int | None = None,
        system_prompt: str | None = None,
        feature: str = "activity_analysis",
    ) -> str:
        """Отправляет промпт анализа активности в DeepSeek и возвращает текстовый ответ."""
        if not prompt:
            raise ValueError("Prompt is empty")

        started = time.perf_counter()
        logger.info(
            "DeepSeek request started operation=activity_analysis model=%s input_chars=%s",
            DEEPSEEK_MODEL,
            len(prompt),
        )

        try:
            client = self._get_client()
            response = client.chat.completions.create(
                model=DEEPSEEK_MODEL,
                messages=[
                    {
                        "role": "system",
                        "content": system_prompt or (
                            "Ты — тёплый персональный помощник Telegram-бота Sumday77. "
                            "Sumday77 всегда на стороне пользователя: сначала замечай реальные успехи, "
                            "затем мягко объясняй точки роста без стыда, вины, наказаний и драматизации. "
                            "Не используй формулировки о провале, потерянном дне, компенсации или отработке еды. "
                            "Подчёркивай, что один день или один выбор не определяет общий прогресс, "
                            "а следующий хороший выбор всё ещё имеет значение. Следуй инструкциям пользователя "
                            "и верни только итоговый анализ."
                        ),
                    },
                    {"role": "user", "content": prompt},
                ],
            )

            content = ((response.choices or [None])[0].message.content or "").strip()
            elapsed_ms = int((time.perf_counter() - started) * 1000)
            usage = getattr(response, "usage", None)
            input_tokens = getattr(usage, "prompt_tokens", None) if usage is not None else None
            output_tokens = getattr(usage, "completion_tokens", None) if usage is not None else None
            total_tokens = getattr(usage, "total_tokens", None) if usage is not None else None
            if not content:
                log_ai_usage(
                    provider="deepseek",
                    feature=feature,
                    model=DEEPSEEK_MODEL,
                    status="error",
                    user_id=user_id,
                    latency_ms=elapsed_ms,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    total_tokens=total_tokens,
                    estimated_cost_usd=calculate_ai_cost("deepseek", DEEPSEEK_MODEL, input_tokens, output_tokens),
                    error_message="DeepSeek returned empty activity analysis response",
                    raw_metadata={
                        "response_id": getattr(response, "id", None),
                        "input_chars": len(prompt),
                        "output_chars": 0,
                    },
                )
                raise DeepSeekServiceTemporaryError("DeepSeek returned empty response")

            log_ai_usage(
                provider="deepseek",
                feature=feature,
                model=DEEPSEEK_MODEL,
                status="success",
                user_id=user_id,
                latency_ms=elapsed_ms,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                total_tokens=total_tokens,
                estimated_cost_usd=calculate_ai_cost("deepseek", DEEPSEEK_MODEL, input_tokens, output_tokens),
                raw_metadata={
                    "response_id": getattr(response, "id", None),
                    "input_chars": len(prompt),
                    "output_chars": len(content),
                },
            )

            logger.info(
                "DeepSeek request completed operation=activity_analysis model=%s status=success latency_ms=%s output_chars=%s",
                DEEPSEEK_MODEL,
                elapsed_ms,
                len(content),
            )
            return content
        except DeepSeekServiceError:
            raise
        except Exception as exc:  # pragma: no cover - внешние исключения SDK/API
            elapsed_ms = int((time.perf_counter() - started) * 1000)
            log_ai_usage(
                provider="deepseek",
                feature=feature,
                model=DEEPSEEK_MODEL,
                status="error",
                user_id=user_id,
                latency_ms=elapsed_ms,
                error_message=safe_exception_summary(exc),
                raw_metadata={"input_chars": len(prompt)},
            )
            if self._is_temporary_error(exc):
                raise DeepSeekServiceTemporaryError("DeepSeek request temporarily unavailable") from exc
            raise DeepSeekServiceError("DeepSeek request failed") from exc

    def generate_meal_completion_comment(
        self,
        prompt: str,
        *,
        user_id: str | int | None = None,
        system_prompt: str,
        feature: str = "meal_completion_comment",
    ) -> tuple[str, dict]:
        """Генерирует короткий комментарий по завершённому приёму пищи."""
        if not prompt:
            raise ValueError("Prompt is empty")

        started = time.perf_counter()
        logger.info(
            "DeepSeek request started operation=meal_completion_comment model=%s input_chars=%s",
            DEEPSEEK_MODEL,
            len(prompt),
        )
        try:
            client = self._get_client()
            response = client.chat.completions.create(
                model=DEEPSEEK_MODEL,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": prompt},
                ],
            )
            content = ((response.choices or [None])[0].message.content or "").strip()
            elapsed_ms = int((time.perf_counter() - started) * 1000)
            usage = getattr(response, "usage", None)
            input_tokens = getattr(usage, "prompt_tokens", None) if usage is not None else None
            output_tokens = getattr(usage, "completion_tokens", None) if usage is not None else None
            total_tokens = getattr(usage, "total_tokens", None) if usage is not None else None
            cost = calculate_ai_cost("deepseek", DEEPSEEK_MODEL, input_tokens, output_tokens)
            metadata = {
                "model": DEEPSEEK_MODEL,
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "total_tokens": total_tokens,
                "estimated_cost_usd": cost,
                "response_id": getattr(response, "id", None),
            }
            if not content:
                log_ai_usage(provider="deepseek", feature=feature, model=DEEPSEEK_MODEL, status="error", user_id=user_id, latency_ms=elapsed_ms, input_tokens=input_tokens, output_tokens=output_tokens, total_tokens=total_tokens, estimated_cost_usd=cost, error_message="empty_response", raw_metadata={"response_id": metadata["response_id"], "input_chars": len(prompt), "output_chars": 0})
                raise DeepSeekServiceTemporaryError("DeepSeek returned empty response")
            log_ai_usage(provider="deepseek", feature=feature, model=DEEPSEEK_MODEL, status="success", user_id=user_id, latency_ms=elapsed_ms, input_tokens=input_tokens, output_tokens=output_tokens, total_tokens=total_tokens, estimated_cost_usd=cost, raw_metadata={"response_id": metadata["response_id"], "input_chars": len(prompt), "output_chars": len(content)})
            return content, metadata
        except DeepSeekServiceError:
            raise
        except Exception as exc:  # pragma: no cover
            elapsed_ms = int((time.perf_counter() - started) * 1000)
            log_ai_usage(provider="deepseek", feature=feature, model=DEEPSEEK_MODEL, status="error", user_id=user_id, latency_ms=elapsed_ms, error_message=safe_exception_summary(exc), raw_metadata={"input_chars": len(prompt)})
            if self._is_temporary_error(exc):
                raise DeepSeekServiceTemporaryError("DeepSeek request temporarily unavailable") from exc
            raise DeepSeekServiceError("DeepSeek request failed") from exc


deepseek_service = DeepSeekService()
