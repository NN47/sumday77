"""Сервис для работы с Gemini API."""
import json
import logging
import random
import time
from typing import Literal, Optional

from google import genai

from config import (
    GEMINI_API_KEY,
    GEMINI_API_KEY2,
    GEMINI_API_KEY3,
    GEMINI_MAX_KEYS_PER_REQUEST,
    GEMINI_MAX_TOTAL_ATTEMPTS_PER_REQUEST,
    GEMINI_RATE_LIMIT_COOLDOWN_SECONDS,
    GEMINI_TEMP_ERROR_BACKOFF_SECONDS,
    GEMINI_TEMP_ERROR_JITTER_SECONDS,
    GEMINI_TEMP_ERROR_MAX_RETRIES,
    GEMINI_TEMP_KEY_COOLDOWN_SECONDS,
)
from database.repositories import GeminiRepository

logger = logging.getLogger(__name__)

GeminiErrorType = Literal["temporary", "quota", "auth", "unknown"]


class GeminiServiceError(Exception):
    """Базовая доменная ошибка сервиса Gemini."""

    def __init__(self, message: str, *, error_type: GeminiErrorType):
        super().__init__(message)
        self.error_type = error_type


class GeminiServiceTemporaryUnavailableError(GeminiServiceError):
    def __init__(self, message: str):
        super().__init__(message, error_type="temporary")


class GeminiServiceQuotaError(GeminiServiceError):
    def __init__(self, message: str):
        super().__init__(message, error_type="quota")


class GeminiServiceAuthError(GeminiServiceError):
    def __init__(self, message: str):
        super().__init__(message, error_type="auth")


class GeminiServiceUnknownError(GeminiServiceError):
    def __init__(self, message: str):
        super().__init__(message, error_type="unknown")


class GeminiService:
    """Сервис для работы с Gemini API с fallback ключами и устойчивой обработкой ошибок."""

    def __init__(self):
        if not GEMINI_API_KEY:
            raise RuntimeError("GEMINI_API_KEY не задан в конфигурации")

        self.account_configs = [
            {"account_name": "GEMINI_API_KEY", "api_key": GEMINI_API_KEY, "priority_order": 1}
        ]
        if GEMINI_API_KEY2:
            self.account_configs.append(
                {"account_name": "GEMINI_API_KEY2", "api_key": GEMINI_API_KEY2, "priority_order": 2}
            )
            logger.info("✅ Резервный ключ Gemini API (GEMINI_API_KEY2) найден")
        if GEMINI_API_KEY3:
            self.account_configs.append(
                {"account_name": "GEMINI_API_KEY3", "api_key": GEMINI_API_KEY3, "priority_order": 3}
            )
            logger.info("✅ Третий резервный ключ Gemini API (GEMINI_API_KEY3) найден")

        self.api_key_by_account_name = {cfg["account_name"]: cfg["api_key"] for cfg in self.account_configs}
        self._accounts_synced = False

        self.model = "gemini-2.5-flash"
        self.client = genai.Client(api_key=self.account_configs[0]["api_key"])

        self.max_retries_per_key_for_temporary_errors = max(0, GEMINI_TEMP_ERROR_MAX_RETRIES)
        self.backoff_schedule = [max(0, int(v)) for v in GEMINI_TEMP_ERROR_BACKOFF_SECONDS]
        self.backoff_jitter_seconds = max(0.0, float(GEMINI_TEMP_ERROR_JITTER_SECONDS))
        self.temporary_cooldown_seconds = max(1, GEMINI_TEMP_KEY_COOLDOWN_SECONDS)
        self.rate_limit_cooldown_seconds = max(1, GEMINI_RATE_LIMIT_COOLDOWN_SECONDS)
        self.max_keys_per_request = max(1, GEMINI_MAX_KEYS_PER_REQUEST)
        self.max_total_attempts_per_request = max(1, GEMINI_MAX_TOTAL_ATTEMPTS_PER_REQUEST)

    def _ensure_accounts_synced(self):
        if self._accounts_synced:
            return
        GeminiRepository.sync_accounts(self.account_configs)
        self._accounts_synced = True

    def _build_client_for_account(self, account_name: str):
        api_key = self.api_key_by_account_name.get(account_name)
        if not api_key:
            raise RuntimeError(f"API ключ для аккаунта {account_name} не найден в окружении")
        return genai.Client(api_key=api_key)

    def classify_gemini_error(self, error: Exception) -> GeminiErrorType:
        """Классифицирует ошибку Gemini на temporary/quota/auth/unknown."""
        error_str = str(error).lower()
        error_type_name = type(error).__name__.lower()

        auth_indicators = [
            "401",
            "403",
            "invalid api key",
            "api key not valid",
            "permission denied",
            "forbidden",
            "unauthorized",
            "auth",
            "authentication",
        ]
        quota_indicators = [
            "429",
            "resource exhausted",
            "quota exceeded",
            "rate limit",
            "daily limit",
            "exhausted quota",
            "too many requests",
        ]
        temporary_indicators = [
            "503",
            "500",
            "unavailable",
            "internal",
            "deadline exceeded",
            "timed out",
            "timeout",
            "connection reset",
            "temporarily unavailable",
            "upstream",
            "high demand",
            "service unavailable",
            "client has been closed",
        ]

        if any(token in error_str for token in auth_indicators) or error_type_name in {
            "authenticationerror",
            "permissiondenied",
            "unauthorized",
            "forbidden",
        }:
            return "auth"
        if any(token in error_str for token in quota_indicators) or error_type_name in {
            "resourceexhausted",
            "ratelimiterror",
            "quotaexceeded",
        }:
            return "quota"
        if any(token in error_str for token in temporary_indicators) or error_type_name in {
            "servererror",
            "serviceunavailable",
            "internalservererror",
            "timeout",
            "connectionerror",
        }:
            return "temporary"
        return "unknown"

    @staticmethod
    def should_retry(error_type: GeminiErrorType) -> bool:
        return error_type == "temporary"

    def get_backoff_delay(self, attempt_number: int) -> float:
        """Возвращает задержку перед повтором для попытки (начиная с 1)."""
        if attempt_number <= 0:
            return 0.0
        if attempt_number <= len(self.backoff_schedule):
            base = float(self.backoff_schedule[attempt_number - 1])
        elif self.backoff_schedule:
            base = float(self.backoff_schedule[-1])
        else:
            base = float(2 ** attempt_number)
        jitter = random.uniform(0, self.backoff_jitter_seconds) if self.backoff_jitter_seconds else 0.0
        return max(0.0, base + jitter)

    def _select_next_available_key(self, *, current_account_id: int | None, excluded_account_ids: set[int]):
        return GeminiRepository.select_next_available_account(
            current_account_id=current_account_id,
            excluded_account_ids=excluded_account_ids,
        )

    def execute_gemini_request_with_failover(self, func, *args, **kwargs):
        """Единый поток выполнения запроса Gemini с retry/backoff/cooldown/failover."""
        self._ensure_accounts_synced()

        used_account_ids: set[int] = set()
        keys_tried = 0
        total_attempts = 0
        last_error: Exception | None = None
        last_error_type: GeminiErrorType = "unknown"

        active = GeminiRepository.get_active_account()
        current_account = (
            active if active else self._select_next_available_key(current_account_id=None, excluded_account_ids=set())
        )

        while current_account:
            if keys_tried >= self.max_keys_per_request:
                break
            keys_tried += 1
            used_account_ids.add(current_account.id)
            self.client = self._build_client_for_account(current_account.account_name)

            temp_attempt = 0
            while total_attempts < self.max_total_attempts_per_request:
                total_attempts += 1
                try:
                    response = func(*args, **kwargs)
                    GeminiRepository.record_request_success(current_account.id, model_name=self.model)
                    return response
                except Exception as err:  # pragma: no cover - runtime errors from SDK
                    last_error = err
                    error_type = self.classify_gemini_error(err)
                    last_error_type = error_type
                    GeminiRepository.record_key_error(
                        current_account.id,
                        error_type=error_type,
                        model_name=self.model,
                        error_message=str(err),
                    )

                    if error_type == "auth":
                        GeminiRepository.mark_key_auth_failed(current_account.id, reason=str(err))
                        break

                    if error_type == "quota":
                        GeminiRepository.mark_key_rate_limited(
                            current_account.id,
                            cooldown_seconds=self.rate_limit_cooldown_seconds,
                            reason=str(err),
                        )
                        break

                    if self.should_retry(error_type) and temp_attempt < self.max_retries_per_key_for_temporary_errors:
                        temp_attempt += 1
                        backoff = self.get_backoff_delay(temp_attempt)
                        logger.warning(
                            "⚠️ retry_temporary_error: key=%s attempt=%s/%s delay=%.2fs error=%s",
                            current_account.account_name,
                            temp_attempt,
                            self.max_retries_per_key_for_temporary_errors,
                            backoff,
                            err,
                        )
                        time.sleep(backoff)
                        continue

                    if error_type == "temporary":
                        GeminiRepository.mark_key_temporary_unavailable(
                            current_account.id,
                            cooldown_seconds=self.temporary_cooldown_seconds,
                            reason=(
                                f"cooldown {self.temporary_cooldown_seconds}s "
                                f"after {temp_attempt} retries: {err}"
                            ),
                        )
                    break

            if total_attempts >= self.max_total_attempts_per_request:
                break

            next_reason = "switch_due_to_temporary_failure" if last_error_type == "temporary" else "switch_due_to_quota"
            if last_error_type == "auth":
                next_reason = "switch_due_to_auth_error"

            if last_error_type == "temporary":
                GeminiRepository.increment_temporary_failover(current_account.id)

            current_account = GeminiRepository.switch_to_next_available_account(
                current_account.id,
                reason=next_reason,
                model_name=self.model,
                error_message=str(last_error) if last_error else None,
                excluded_account_ids=used_account_ids,
            )

        if last_error_type == "temporary":
            raise GeminiServiceTemporaryUnavailableError(
                "Сервис AI сейчас временно перегружен. Попробуй ещё раз чуть позже."
            )
        if last_error_type == "quota":
            raise GeminiServiceQuotaError("AI временно недоступен из-за лимита запросов.")
        if last_error_type == "auth":
            raise GeminiServiceAuthError("AI временно недоступен из-за ошибки настройки.")
        raise GeminiServiceUnknownError(str(last_error) if last_error else "Неизвестная ошибка Gemini")

    def analyze(self, text: str) -> str:
        """Анализирует текст через Gemini."""
        response = self.execute_gemini_request_with_failover(
            lambda **request_kwargs: self.client.models.generate_content(**request_kwargs),
            model=self.model,
            contents=text,
        )
        return response.text

    def estimate_kbju(self, food_text: str) -> Optional[dict]:
        prompt = f"""
Ты нутрициолог. Твоя задача — ОЦЕНИТЬ калории, белки, жиры и углеводы для списка продуктов.

Пользователь вводит на русском, например:
"200 г курицы, 100 г йогурта, 30 г орехов".

Требования:

1. Если вес не указан, оцени примерный (но лучше всегда использовать граммы из запроса).
2. Используй типичные значения для обычных продуктов (не бренд-специфично).
3. Ответь СТРОГО в формате JSON, БЕЗ объяснений, комментариев и оформления.

ФОРМАТ ОТВЕТА (пример):
{{
  "items": [
    {{
      "name": "курица",
      "grams": 200,
      "kcal": 330,
      "protein": 40,
      "fat": 15,
      "carbs": 0
    }},
    {{
      "name": "йогурт",
      "grams": 100,
      "kcal": 60,
      "protein": 5,
      "fat": 2,
      "carbs": 7
    }}
  ],
  "total": {{
    "kcal": 390,
    "protein": 45,
    "fat": 17,
    "carbs": 7
  }}
}}

Вот данные пользователя: "{food_text}"
"""
        try:
            response = self.execute_gemini_request_with_failover(
                lambda **request_kwargs: self.client.models.generate_content(**request_kwargs),
                model=self.model,
                contents=prompt,
            )
            raw = response.text.strip()
            try:
                parsed = json.loads(raw)
                return self._normalize_kbju_payload(parsed)
            except json.JSONDecodeError:
                start = raw.find("{")
                end = raw.rfind("}")
                if start != -1 and end != -1 and end > start:
                    parsed = json.loads(raw[start : end + 1])
                    return self._normalize_kbju_payload(parsed)
                raise
        except GeminiServiceError:
            raise
        except Exception as e:
            logger.error("Ошибка Gemini (КБЖУ): %s", e, exc_info=True)
            return None

    def _normalize_kbju_payload(self, payload: dict) -> Optional[dict]:
        """Нормализует ответ Gemini к единому формату с total и items."""
        if not isinstance(payload, dict):
            return None

        def safe_float(value) -> float:
            try:
                if value is None:
                    return 0.0
                return float(value)
            except (TypeError, ValueError):
                return 0.0

        def get_num(source: dict, *keys: str) -> float:
            for key in keys:
                if key in source:
                    return safe_float(source.get(key))
            return 0.0

        items = payload.get("items") if isinstance(payload.get("items"), list) else []
        normalized_items = []
        for item in items:
            if not isinstance(item, dict):
                continue
            normalized_items.append(
                {
                    "name": item.get("name") or item.get("title") or "продукт",
                    "grams": get_num(item, "grams", "weight_g", "weight"),
                    "kcal": get_num(item, "kcal", "calories"),
                    "protein": get_num(item, "protein", "protein_g"),
                    "fat": get_num(item, "fat", "fat_g"),
                    "carbs": get_num(item, "carbs", "carbohydrates", "carbohydrates_g"),
                }
            )

        total_dict = payload.get("total") if isinstance(payload.get("total"), dict) else {}
        total = {
            "kcal": get_num(total_dict, "kcal", "calories"),
            "protein": get_num(total_dict, "protein", "protein_g"),
            "fat": get_num(total_dict, "fat", "fat_g"),
            "carbs": get_num(total_dict, "carbs", "carbohydrates", "carbohydrates_g"),
        }

        if not any(total.values()) and normalized_items:
            total = {
                "kcal": sum(i["kcal"] for i in normalized_items),
                "protein": sum(i["protein"] for i in normalized_items),
                "fat": sum(i["fat"] for i in normalized_items),
                "carbs": sum(i["carbs"] for i in normalized_items),
            }

        if not normalized_items and not any(total.values()):
            return None
        return {"items": normalized_items, "total": total}

    def estimate_kbju_from_photo(self, image_bytes: bytes) -> Optional[dict]:
        prompt = """Ты нутрициолог. Оцени КБЖУ еды на фотографии. Ответь строго JSON с items и total."""
        try:
            from google.genai import types

            mime_type = "image/jpeg"
            if image_bytes.startswith(b"\x89PNG"):
                mime_type = "image/png"
            elif image_bytes.startswith(b"GIF"):
                mime_type = "image/gif"
            elif image_bytes.startswith(b"WEBP"):
                mime_type = "image/webp"

            response = self.execute_gemini_request_with_failover(
                lambda **request_kwargs: self.client.models.generate_content(**request_kwargs),
                model=self.model,
                contents=[types.Part.from_bytes(data=image_bytes, mime_type=mime_type), prompt],
            )
            raw = response.text.strip()
            try:
                return json.loads(raw)
            except json.JSONDecodeError:
                start = raw.find("{")
                end = raw.rfind("}")
                if start != -1 and end != -1 and end > start:
                    return json.loads(raw[start : end + 1])
                raise
        except GeminiServiceError:
            raise
        except Exception as e:
            logger.error("Ошибка Gemini (КБЖУ по фото): %s", e, exc_info=True)
            return None

    def extract_kbju_from_label(self, image_bytes: bytes) -> Optional[dict]:
        prompt = """Извлеки КБЖУ и вес из этикетки. Ответь только JSON."""
        try:
            from google.genai import types

            mime_type = "image/jpeg"
            if image_bytes.startswith(b"\x89PNG"):
                mime_type = "image/png"
            elif image_bytes.startswith(b"GIF"):
                mime_type = "image/gif"
            elif image_bytes.startswith(b"WEBP"):
                mime_type = "image/webp"

            response = self.execute_gemini_request_with_failover(
                lambda **request_kwargs: self.client.models.generate_content(**request_kwargs),
                model=self.model,
                contents=[types.Part.from_bytes(data=image_bytes, mime_type=mime_type), prompt],
            )
            raw = response.text.strip()
            try:
                return json.loads(raw)
            except json.JSONDecodeError:
                start = raw.find("{")
                end = raw.rfind("}")
                if start != -1 and end != -1 and end > start:
                    return json.loads(raw[start : end + 1])
                raise
        except GeminiServiceError:
            raise
        except Exception as e:
            logger.error("Ошибка Gemini (КБЖУ с этикетки): %s", e, exc_info=True)
            return None

    def scan_barcode(self, image_bytes: bytes) -> Optional[str]:
        prompt = """Прочитай штрих-код и верни только цифры или NOT_FOUND."""
        try:
            from google.genai import types

            mime_type = "image/jpeg"
            if image_bytes.startswith(b"\x89PNG"):
                mime_type = "image/png"
            elif image_bytes.startswith(b"GIF"):
                mime_type = "image/gif"
            elif image_bytes.startswith(b"WEBP"):
                mime_type = "image/webp"

            response = self.execute_gemini_request_with_failover(
                lambda **request_kwargs: self.client.models.generate_content(**request_kwargs),
                model=self.model,
                contents=[types.Part.from_bytes(data=image_bytes, mime_type=mime_type), prompt],
            )
            barcode = response.text.strip().replace(" ", "").replace("-", "").replace("_", "")
            if barcode.isdigit() and 8 <= len(barcode) <= 14:
                return barcode
            if barcode.upper() == "NOT_FOUND":
                return None
            digits = "".join(filter(str.isdigit, barcode))
            return digits if 8 <= len(digits) <= 14 else None
        except GeminiServiceError:
            raise
        except Exception as e:
            logger.error("Ошибка Gemini (распознавание штрих-кода): %s", e, exc_info=True)
            return None


# Глобальный экземпляр сервиса
try:
    gemini_service = GeminiService()
except RuntimeError as init_error:  # pragma: no cover - для тестовых окружений без ключей
    logger.warning("GeminiService не инициализирован: %s", init_error)
    gemini_service = None
