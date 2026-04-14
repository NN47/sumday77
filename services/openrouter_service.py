"""Сервис для работы с OpenRouter (только free-модель)."""
from __future__ import annotations

import json
import logging
import time
import traceback
from typing import Optional

from openai import OpenAI

from config import (
    OPENROUTER_API_KEY,
    OPENROUTER_HTTP_REFERER,
    OPENROUTER_MODEL,
    OPENROUTER_BASE_URL,
    OPENROUTER_APP_TITLE,
)
from database.repositories import OpenRouterRepository

logger = logging.getLogger(__name__)

OPENROUTER_FREE_MODEL = "openrouter/free"


class OpenRouterServiceError(Exception):
    """Базовая ошибка OpenRouter."""


class OpenRouterServiceConfigError(OpenRouterServiceError):
    """Ошибка конфигурации OpenRouter."""


class OpenRouterServiceTemporaryError(OpenRouterServiceError):
    """Временная ошибка OpenRouter (timeout/network/rate limit)."""


class OpenRouterService:
    """Сервис анализа текста еды через OpenRouter free."""

    def __init__(self):
        self._client: OpenAI | None = None

    def _get_client(self) -> OpenAI:
        if not OPENROUTER_API_KEY:
            raise OpenRouterServiceConfigError("OPENROUTER_API_KEY is not configured")
        if self._client is None:
            self._client = OpenAI(
                api_key=OPENROUTER_API_KEY,
                base_url=OPENROUTER_BASE_URL,
                timeout=45.0,
            )
        return self._client

    def analyze_food_text(self, text: str) -> str:
        """Отправляет текст в OpenRouter и возвращает сырой ответ модели."""
        started = time.perf_counter()
        logger.info("OpenRouter: input text=%s", text)
        logger.info("OpenRouter: sending request model=%s", OPENROUTER_FREE_MODEL)

        try:
            client = self._get_client()
            response = client.chat.completions.create(
                model=OPENROUTER_FREE_MODEL,
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
                extra_headers={
                    "HTTP-Referer": OPENROUTER_HTTP_REFERER,
                    "X-Title": OPENROUTER_APP_TITLE,
                },
            )

            content = ((response.choices or [None])[0].message.content or "").strip()
            elapsed_ms = int((time.perf_counter() - started) * 1000)
            if not content:
                raise OpenRouterServiceTemporaryError("OpenRouter returned empty response")

            logger.info("OpenRouter: response=%s", content)
            logger.info("OpenRouter: done in %sms", elapsed_ms)
            OpenRouterRepository.log_success(
                model_name=OPENROUTER_FREE_MODEL,
                input_text=text,
                response_text=content,
                duration_ms=elapsed_ms,
            )
            return content
        except OpenRouterServiceError as exc:
            elapsed_ms = int((time.perf_counter() - started) * 1000)
            logger.error("OpenRouter error: %s", exc)
            OpenRouterRepository.log_error(
                model_name=OPENROUTER_FREE_MODEL,
                input_text=text,
                error_message=str(exc),
                duration_ms=elapsed_ms,
            )
            raise
        except Exception as exc:  # pragma: no cover - внешние исключения SDK
            elapsed_ms = int((time.perf_counter() - started) * 1000)
            message = str(exc).lower()
            if any(token in message for token in ("timeout", "timed out", "429", "rate", "network", "connection")):
                wrapped = OpenRouterServiceTemporaryError(str(exc))
            else:
                wrapped = OpenRouterServiceError(str(exc))
            logger.error("OpenRouter unexpected error: %s", exc, exc_info=True)
            OpenRouterRepository.log_error(
                model_name=OPENROUTER_FREE_MODEL,
                input_text=text,
                error_message=str(exc),
                duration_ms=elapsed_ms,
            )
            raise wrapped from exc

    def analyze_activity_prompt(self, prompt: str) -> str:
        """Отправляет промпт анализа активности в OpenRouter и возвращает текстовый ответ."""
        started = time.perf_counter()
        logger.info("OpenRouter activity: sending request model=%s", OPENROUTER_MODEL)
        raw_api_response = ""
        extracted_content = ""

        try:
            client = self._get_client()
            response = client.chat.completions.create(
                model=OPENROUTER_MODEL,
                messages=[
                    {
                        "role": "system",
                        "content": "Ты персональный фитнес-ассистент. Дай структурированный отчёт на русском языке в HTML-формате.",
                    },
                    {"role": "user", "content": prompt},
                ],
                extra_headers={
                    "HTTP-Referer": OPENROUTER_HTTP_REFERER,
                    "X-Title": OPENROUTER_APP_TITLE,
                },
            )
            try:
                raw_api_response = response.model_dump_json(indent=2)
            except Exception:
                raw_api_response = str(response)
            logger.info("OpenRouter activity: raw API response=%s", raw_api_response)

            content = ((response.choices or [None])[0].message.content or "").strip()
            extracted_content = content
            logger.info("OpenRouter activity: extracted choices[0].message.content=%s", extracted_content)
            elapsed_ms = int((time.perf_counter() - started) * 1000)
            if not content:
                raise OpenRouterServiceTemporaryError("OpenRouter returned empty activity response")

            OpenRouterRepository.log_success(
                model_name=OPENROUTER_MODEL,
                input_text=prompt,
                response_text=content,
                duration_ms=elapsed_ms,
            )
            return content
        except OpenRouterServiceError as exc:
            elapsed_ms = int((time.perf_counter() - started) * 1000)
            logger.error(
                "OpenRouter activity error: %s\ntraceback=%s\nresponse_snippet=%s",
                exc,
                traceback.format_exc(),
                self._response_edge_snippet(extracted_content or raw_api_response),
            )
            OpenRouterRepository.log_error(
                model_name=OPENROUTER_MODEL,
                input_text=prompt,
                error_message=str(exc),
                duration_ms=elapsed_ms,
            )
            raise
        except Exception as exc:  # pragma: no cover - внешние исключения SDK
            elapsed_ms = int((time.perf_counter() - started) * 1000)
            message = str(exc).lower()
            if any(token in message for token in ("timeout", "timed out", "429", "rate", "network", "connection")):
                wrapped = OpenRouterServiceTemporaryError(str(exc))
            else:
                wrapped = OpenRouterServiceError(str(exc))

            logger.error(
                "OpenRouter activity unexpected error: %s\ntraceback=%s\nresponse_snippet=%s",
                exc,
                traceback.format_exc(),
                self._response_edge_snippet(extracted_content or raw_api_response),
            )
            OpenRouterRepository.log_error(
                model_name=OPENROUTER_MODEL,
                input_text=prompt,
                error_message=str(exc),
                duration_ms=elapsed_ms,
            )
            raise wrapped from exc

    def analyze_label_ocr_text(self, cleaned_text: str) -> str:
        """Отправляет OCR-текст этикетки в OpenRouter и возвращает сырой JSON-ответ."""
        started = time.perf_counter()
        logger.info("OpenRouter OCR label: cleaned_text_len=%s model=%s", len(cleaned_text or ""), OPENROUTER_MODEL)

        try:
            client = self._get_client()
            response = client.chat.completions.create(
                model=OPENROUTER_MODEL,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "Ты анализируешь OCR-текст этикетки продукта питания. "
                            "Верни СТРОГО JSON без markdown и лишнего текста. "
                            "Нельзя выдумывать значения: если данных нет, ставь null. "
                            "Формат строго такой: "
                            '{'
                            '"product_name":"string|null",'
                            '"serving_description":"string|null",'
                            '"weight_grams":null,'
                            '"nutrition_per_100g":{"calories":null,"protein":null,"fat":null,"carbs":null},'
                            '"nutrition_total":{"calories":null,"protein":null,"fat":null,"carbs":null},'
                            '"confidence":"high|medium|low",'
                            '"notes":"string"'
                            '}. '
                            "Если есть только значения на 100 г, не придумывай nutrition_total."
                        ),
                    },
                    {
                        "role": "user",
                        "content": f"OCR-текст этикетки:\\n{cleaned_text}",
                    },
                ],
                extra_headers={
                    "HTTP-Referer": OPENROUTER_HTTP_REFERER,
                    "X-Title": OPENROUTER_APP_TITLE,
                },
            )

            content = ((response.choices or [None])[0].message.content or "").strip()
            elapsed_ms = int((time.perf_counter() - started) * 1000)
            if not content:
                raise OpenRouterServiceTemporaryError("OpenRouter returned empty response for OCR label")

            OpenRouterRepository.log_success(
                model_name=OPENROUTER_MODEL,
                input_text=cleaned_text,
                response_text=content,
                duration_ms=elapsed_ms,
            )
            return content
        except OpenRouterServiceError:
            raise
        except Exception as exc:  # pragma: no cover - внешние исключения SDK
            elapsed_ms = int((time.perf_counter() - started) * 1000)
            message = str(exc).lower()
            wrapped: OpenRouterServiceError
            if any(token in message for token in ("timeout", "timed out", "429", "rate", "network", "connection")):
                wrapped = OpenRouterServiceTemporaryError(str(exc))
            else:
                wrapped = OpenRouterServiceError(str(exc))

            OpenRouterRepository.log_error(
                model_name=OPENROUTER_MODEL,
                input_text=cleaned_text,
                error_message=str(exc),
                duration_ms=elapsed_ms,
            )
            raise wrapped from exc

    @staticmethod
    def parse_kbju_json(raw: str) -> Optional[dict]:
        """Парсит JSON-ответ (включая markdown-блок) и нормализует поля."""
        if not raw:
            return None

        try:
            payload = OpenRouterService.parse_ai_response(raw, mode="json")
        except json.JSONDecodeError:
            return None

        if not isinstance(payload, dict):
            return None

        def to_float(value) -> float:
            try:
                if value is None:
                    return 0.0
                return float(value)
            except (TypeError, ValueError):
                return 0.0

        def pick(source: dict, *keys: str) -> float:
            for key in keys:
                if key in source:
                    return to_float(source.get(key))
            return 0.0

        items = payload.get("items") if isinstance(payload.get("items"), list) else []
        normalized_items = []
        for item in items:
            if not isinstance(item, dict):
                continue
            normalized_items.append(
                {
                    "name": item.get("name") or "продукт",
                    "grams": pick(
                        item,
                        "grams",
                        "weight_g",
                        "weight",
                        "amount_g",
                        "weight_grams",
                        "mass_g",
                        "portion_g",
                        "g",
                        "mass",
                        "amount",
                        "вес",
                        "граммы",
                    ),
                    "kcal": pick(item, "kcal", "calories", "kcalories", "ккал", "калории"),
                    "protein": pick(item, "protein", "protein_g", "proteins", "p", "б", "белки"),
                    "fat": pick(item, "fat", "fat_g", "fats", "f", "ж", "жиры"),
                    "carbs": pick(
                        item,
                        "carbs",
                        "carbohydrates",
                        "carbohydrates_g",
                        "carb",
                        "углеводы",
                        "у",
                        "c",
                    ),
                }
            )

        total_raw = payload.get("total") if isinstance(payload.get("total"), dict) else {}
        total = {
            "kcal": pick(total_raw, "kcal", "calories", "kcalories", "ккал", "калории"),
            "protein": pick(total_raw, "protein", "protein_g", "proteins", "p", "б", "белки"),
            "fat": pick(total_raw, "fat", "fat_g", "fats", "f", "ж", "жиры"),
            "carbs": pick(
                total_raw,
                "carbs",
                "carbohydrates",
                "carbohydrates_g",
                "carb",
                "углеводы",
                "у",
                "c",
            ),
        }

        if not any(total.values()) and normalized_items:
            total = {
                "kcal": sum(item["kcal"] for item in normalized_items),
                "protein": sum(item["protein"] for item in normalized_items),
                "fat": sum(item["fat"] for item in normalized_items),
                "carbs": sum(item["carbs"] for item in normalized_items),
            }

        if not normalized_items and not any(total.values()):
            return None
        return {"items": normalized_items, "total": total}

    @staticmethod
    def parse_ai_response(raw: str, mode: str = "text") -> str | dict | list:
        """Универсальный парсер AI-ответов: text|json."""
        if mode == "text":
            return raw or ""
        if mode != "json":
            raise ValueError(f"Unsupported parse mode: {mode}")

        cleaned = (raw or "").strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.strip("`")
            cleaned = cleaned.replace("json\n", "", 1).replace("JSON\n", "", 1).strip()

        try:
            return json.loads(cleaned)
        except json.JSONDecodeError:
            start = cleaned.find("{")
            end = cleaned.rfind("}")
            if start != -1 and end != -1 and end > start:
                candidate = cleaned[start : end + 1]
                try:
                    return json.loads(candidate)
                except json.JSONDecodeError as exc:
                    OpenRouterService._log_response_parse_error(exc, raw)
                    raise
            exc = json.JSONDecodeError("No JSON object found in response", cleaned, 0)
            OpenRouterService._log_response_parse_error(exc, raw)
            raise exc

    @staticmethod
    def _response_edge_snippet(response_text: str, edge_size: int = 500) -> str:
        if not response_text:
            return "empty"
        if len(response_text) <= edge_size * 2:
            return response_text
        return f"{response_text[:edge_size]}\n...\n{response_text[-edge_size:]}"

    @staticmethod
    def _log_response_parse_error(exc: Exception, response_text: str) -> None:
        trace = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
        logger.error(
            "OpenRouter parse error: %s\ntraceback=%s\nresponse_snippet=%s",
            exc,
            trace,
            OpenRouterService._response_edge_snippet(response_text),
        )


openrouter_service = OpenRouterService()
