"""OpenAI provider for extracting nutrition facts from product labels."""
import base64
import json
import logging
import re
import time
from typing import Optional

from openai import APITimeoutError, OpenAI, OpenAIError

from config import OPENAI_API_KEY
from services.ai_usage_logger import calculate_ai_cost, log_ai_usage

logger = logging.getLogger(__name__)


OPENAI_LABEL_PROMPT = """
Проанализируй фото этикетки продукта питания.
Найди название продукта, вес упаковки и пищевую ценность на 100 г.
Верни строго валидный JSON без пояснений.

JSON должен быть такого вида:
{
  "name": null,
  "weight_g": null,
  "calories_per_100g": null,
  "protein_per_100g": null,
  "fat_per_100g": null,
  "carbs_per_100g": null
}

Правила:
- значения должны быть числами, не строками;
- если значение не найдено на фото, верни null;
- не придумывай данные;
- не используй средние значения из интернета;
- извлекай только то, что видно на изображении.
""".strip()


OPENAI_FOOD_PHOTO_PROMPT = """
Проанализируй фото блюда.
Определи, какие продукты/ингредиенты видны на изображении.
Оцени примерную массу блюда и КБЖУ.

Верни строго валидный JSON без пояснений:

{
  "dish_name": null,
  "estimated_weight_g": null,
  "calories": null,
  "protein": null,
  "fat": null,
  "carbs": null,
  "items": [
    {
      "name": null,
      "estimated_weight_g": null,
      "calories": null,
      "protein": null,
      "fat": null,
      "carbs": null
    }
  ],
  "confidence": "low"
}

Правила:
- не придумывай точные данные;
- если фото неоднозначное, ставь confidence = "low";
- если блюдо видно хорошо, confidence = "medium" или "high";
- для каждого продукта/ингредиента обязательно оцени примерную массу в граммах и заполни estimated_weight_g числом;
- если точный вес неизвестен, оцени примерную порцию по фото;
- все значения КБЖУ должны быть примерной оценкой за estimated_weight_g, не на 100 г;
- значения должны быть числами, не строками;
- если значение невозможно оценить, верни null.
""".strip()


class OpenAILabelServiceError(Exception):
    """Base domain error for OpenAI label analysis."""


class OpenAILabelServiceConfigError(OpenAILabelServiceError):
    """OpenAI API key is not configured."""


class OpenAILabelServiceAPIError(OpenAILabelServiceError):
    """OpenAI API returned an error."""


class OpenAILabelServiceInvalidJSONError(OpenAILabelServiceError):
    """OpenAI model returned invalid JSON."""


class OpenAILabelServiceTimeoutError(OpenAILabelServiceError):
    """OpenAI request timed out."""


class OpenAILabelService:
    """Extracts product label nutrition data with OpenAI vision models."""

    def __init__(self, api_key: str | None = OPENAI_API_KEY, *, model: str = "gpt-4.1-mini"):
        self.api_key = api_key
        self.model = model
        self.timeout_seconds = 45.0

    def extract_kbju_from_label(self, image_bytes: bytes, *, user_id: str | int | None = None, feature: str = "label_analysis") -> Optional[dict]:
        """Return label data in the same normalized shape as Gemini label analysis."""
        if not self.api_key:
            log_ai_usage(
                provider="openai",
                feature=feature,
                model=self.model,
                status="error",
                user_id=user_id,
                error_message="OpenAI API key не настроен на сервере.",
            )
            raise OpenAILabelServiceConfigError("OpenAI API key не настроен на сервере.")

        started = time.perf_counter()
        image_base64 = base64.b64encode(image_bytes).decode("utf-8")
        mime_type = self._detect_mime_type(image_bytes)
        client = OpenAI(api_key=self.api_key, timeout=self.timeout_seconds)

        try:
            response = client.responses.create(
                model=self.model,
                input=[
                    {
                        "role": "user",
                        "content": [
                            {"type": "input_text", "text": OPENAI_LABEL_PROMPT},
                            {
                                "type": "input_image",
                                "image_url": f"data:{mime_type};base64,{image_base64}",
                            },
                        ],
                    }
                ],
                text={"format": {"type": "json_object"}},
            )
        except APITimeoutError as exc:
            latency_ms = int((time.perf_counter() - started) * 1000)
            logger.error("OpenAI label analysis timed out: %s", exc, exc_info=True)
            log_ai_usage(
                provider="openai",
                feature=feature,
                model=self.model,
                status="error",
                user_id=user_id,
                latency_ms=latency_ms,
                error_message=str(exc),
            )
            raise OpenAILabelServiceTimeoutError("OpenAI API request timed out") from exc
        except OpenAIError as exc:
            latency_ms = int((time.perf_counter() - started) * 1000)
            logger.error("OpenAI label analysis API error: %s", exc, exc_info=True)
            log_ai_usage(
                provider="openai",
                feature=feature,
                model=self.model,
                status="error",
                user_id=user_id,
                latency_ms=latency_ms,
                error_message=str(exc),
            )
            raise OpenAILabelServiceAPIError(str(exc)) from exc

        latency_ms = int((time.perf_counter() - started) * 1000)
        usage = getattr(response, "usage", None)
        input_tokens = getattr(usage, "input_tokens", None) if usage is not None else None
        output_tokens = getattr(usage, "output_tokens", None) if usage is not None else None
        total_tokens = getattr(usage, "total_tokens", None) if usage is not None else None
        log_ai_usage(
            provider="openai",
            feature=feature,
            model=self.model,
            status="success",
            user_id=user_id,
            latency_ms=latency_ms,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=total_tokens,
            estimated_cost_usd=calculate_ai_cost("openai", self.model, input_tokens, output_tokens),
            raw_metadata={"response_id": getattr(response, "id", None)},
        )

        raw = (getattr(response, "output_text", "") or "").strip()
        parsed = self._parse_json_response(raw)
        normalized = self._normalize_label_payload(parsed)
        return normalized if normalized else None

    def analyze_food_photo_openai(
        self,
        image_bytes: bytes,
        *,
        user_id: str | int | None = None,
        feature: str = "food_photo_analysis",
    ) -> Optional[dict]:
        """Return food photo analysis in the same normalized shape as Gemini photo analysis."""
        if not self.api_key:
            log_ai_usage(
                provider="openai",
                feature=feature,
                model=self.model,
                status="error",
                user_id=user_id,
                error_message="OpenAI API key не настроен на сервере.",
            )
            raise OpenAILabelServiceConfigError("OpenAI API key не настроен на сервере.")

        started = time.perf_counter()
        image_base64 = base64.b64encode(image_bytes).decode("utf-8")
        mime_type = self._detect_mime_type(image_bytes)
        client = OpenAI(api_key=self.api_key, timeout=self.timeout_seconds)

        try:
            response = client.responses.create(
                model=self.model,
                input=[
                    {
                        "role": "user",
                        "content": [
                            {"type": "input_text", "text": OPENAI_FOOD_PHOTO_PROMPT},
                            {
                                "type": "input_image",
                                "image_url": f"data:{mime_type};base64,{image_base64}",
                            },
                        ],
                    }
                ],
                text={"format": {"type": "json_object"}},
            )
        except APITimeoutError as exc:
            latency_ms = int((time.perf_counter() - started) * 1000)
            logger.error("OpenAI food photo analysis timed out: %s", exc, exc_info=True)
            log_ai_usage(
                provider="openai",
                feature=feature,
                model=self.model,
                status="error",
                user_id=user_id,
                latency_ms=latency_ms,
                error_message=str(exc),
            )
            raise OpenAILabelServiceTimeoutError("OpenAI API request timed out") from exc
        except OpenAIError as exc:
            latency_ms = int((time.perf_counter() - started) * 1000)
            logger.error("OpenAI food photo analysis API error: %s", exc, exc_info=True)
            log_ai_usage(
                provider="openai",
                feature=feature,
                model=self.model,
                status="error",
                user_id=user_id,
                latency_ms=latency_ms,
                error_message=str(exc),
            )
            raise OpenAILabelServiceAPIError(str(exc)) from exc

        latency_ms = int((time.perf_counter() - started) * 1000)
        usage = getattr(response, "usage", None)
        input_tokens = getattr(usage, "input_tokens", None) if usage is not None else None
        output_tokens = getattr(usage, "output_tokens", None) if usage is not None else None
        total_tokens = getattr(usage, "total_tokens", None) if usage is not None else None
        raw = (getattr(response, "output_text", "") or "").strip()

        try:
            parsed = self._parse_json_response(raw)
        except OpenAILabelServiceInvalidJSONError as exc:
            log_ai_usage(
                provider="openai",
                feature=feature,
                model=self.model,
                status="error",
                user_id=user_id,
                latency_ms=latency_ms,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                total_tokens=total_tokens,
                estimated_cost_usd=calculate_ai_cost("openai", self.model, input_tokens, output_tokens),
                error_message=str(exc),
                raw_metadata={"response_id": getattr(response, "id", None)},
            )
            raise

        log_ai_usage(
            provider="openai",
            feature=feature,
            model=self.model,
            status="success",
            user_id=user_id,
            latency_ms=latency_ms,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=total_tokens,
            estimated_cost_usd=calculate_ai_cost("openai", self.model, input_tokens, output_tokens),
            raw_metadata={"response_id": getattr(response, "id", None), "confidence": parsed.get("confidence")},
        )

        normalized = self._normalize_food_photo_payload(parsed)
        return normalized if normalized else None

    @staticmethod
    def _detect_mime_type(image_bytes: bytes) -> str:
        if image_bytes.startswith(b"\x89PNG"):
            return "image/png"
        if image_bytes.startswith(b"GIF"):
            return "image/gif"
        if image_bytes.startswith(b"WEBP"):
            return "image/webp"
        return "image/jpeg"

    @classmethod
    def _parse_json_response(cls, raw: str) -> dict:
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            start = raw.find("{")
            end = raw.rfind("}")
            if start != -1 and end != -1 and end > start:
                try:
                    parsed = json.loads(raw[start : end + 1])
                except json.JSONDecodeError as exc:
                    raise OpenAILabelServiceInvalidJSONError("OpenAI model returned invalid JSON") from exc
            else:
                raise OpenAILabelServiceInvalidJSONError("OpenAI model returned invalid JSON")
        if not isinstance(parsed, dict):
            raise OpenAILabelServiceInvalidJSONError("OpenAI model returned non-object JSON")
        return parsed

    @staticmethod
    def _to_float(value) -> float | None:
        if value is None or isinstance(value, bool):
            return None
        if isinstance(value, (int, float)):
            return float(value)
        if isinstance(value, str):
            cleaned = value.strip().replace(",", ".").replace("ккал", "").replace("г", "").strip()
            if not cleaned:
                return None
            try:
                return float(cleaned)
            except ValueError:
                match = re.search(r"-?\d+(?:\.\d+)?", cleaned)
                if match:
                    try:
                        return float(match.group(0))
                    except ValueError:
                        return None
        return None

    @classmethod
    def _pick_first_numeric(cls, payload: dict, keys: list[str]) -> float | None:
        for key in keys:
            if key in payload:
                numeric = cls._to_float(payload.get(key))
                if numeric is not None:
                    return numeric
        return None

    @classmethod
    def _normalize_label_payload(cls, payload: dict) -> Optional[dict]:
        if not isinstance(payload, dict):
            return None

        kcal = cls._pick_first_numeric(payload, ["calories_per_100g", "kcal", "calories", "energy_kcal"])
        protein = cls._pick_first_numeric(payload, ["protein_per_100g", "protein", "proteins"])
        fat = cls._pick_first_numeric(payload, ["fat_per_100g", "fat", "fats"])
        carbs = cls._pick_first_numeric(payload, ["carbs_per_100g", "carbs", "carbohydrates"])

        if all(value is None for value in (kcal, protein, fat, carbs)):
            return None

        package_weight = cls._pick_first_numeric(payload, ["weight_g", "package_weight", "weight", "net_weight"])
        found_weight = bool(package_weight and package_weight > 0)
        product_name = payload.get("name") or payload.get("product_name") or "Продукт"
        if not isinstance(product_name, str):
            product_name = str(product_name)

        return {
            "product_name": product_name.strip() or "Продукт",
            "kbju_per_100g": {
                "kcal": kcal,
                "protein": protein,
                "fat": fat,
                "carbs": carbs,
            },
            "package_weight": package_weight,
            "found_weight": found_weight,
            "source": "openai",
        }

    @classmethod
    def _normalize_food_photo_payload(cls, payload: dict) -> Optional[dict]:
        if not isinstance(payload, dict):
            return None

        def safe_float(value) -> float:
            parsed = cls._to_float(value)
            return parsed if parsed is not None else 0.0

        items = payload.get("items") if isinstance(payload.get("items"), list) else []
        normalized_items = []
        for item in items:
            if not isinstance(item, dict):
                continue
            name = item.get("name") or item.get("title") or item.get("dish") or "продукт"
            if not isinstance(name, str):
                name = str(name)
            normalized_items.append(
                {
                    "name": name.strip() or "продукт",
                    "grams": safe_float(item.get("estimated_weight_g") or item.get("grams") or item.get("weight_g")),
                    "kcal": safe_float(item.get("calories") or item.get("kcal")),
                    "protein": safe_float(item.get("protein") or item.get("protein_g")),
                    "fat": safe_float(item.get("fat") or item.get("fat_g")),
                    "carbs": safe_float(item.get("carbs") or item.get("carbohydrates") or item.get("carbohydrates_g")),
                }
            )

        total = {
            "kcal": safe_float(payload.get("calories") or payload.get("kcal")),
            "protein": safe_float(payload.get("protein") or payload.get("protein_g")),
            "fat": safe_float(payload.get("fat") or payload.get("fat_g")),
            "carbs": safe_float(payload.get("carbs") or payload.get("carbohydrates") or payload.get("carbohydrates_g")),
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
        return {"items": normalized_items, "total": total, "source": "openai", "confidence": payload.get("confidence")}


openai_label_service = OpenAILabelService()
