"""OpenAI provider for extracting nutrition facts from product labels."""
import base64
import json
import logging
import re
from typing import Optional

from openai import APITimeoutError, OpenAI, OpenAIError

from config import OPENAI_API_KEY

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

    def extract_kbju_from_label(self, image_bytes: bytes) -> Optional[dict]:
        """Return label data in the same normalized shape as Gemini label analysis."""
        if not self.api_key:
            raise OpenAILabelServiceConfigError("OpenAI API key не настроен на сервере.")

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
            logger.error("OpenAI label analysis timed out: %s", exc, exc_info=True)
            raise OpenAILabelServiceTimeoutError("OpenAI API request timed out") from exc
        except OpenAIError as exc:
            logger.error("OpenAI label analysis API error: %s", exc, exc_info=True)
            raise OpenAILabelServiceAPIError(str(exc)) from exc

        raw = (getattr(response, "output_text", "") or "").strip()
        parsed = self._parse_json_response(raw)
        normalized = self._normalize_label_payload(parsed)
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


openai_label_service = OpenAILabelService()
