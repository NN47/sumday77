"""Парсинг и нормализация ответа OpenRouter для OCR-этикеток."""
from __future__ import annotations

import json
import re
from typing import Any


class OCRLabelParseError(ValueError):
    """Ошибка парсинга OCR JSON-ответа."""


def _to_float(value: Any) -> float | None:
    if value in (None, "", "null"):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        normalized = value.strip().replace(",", ".")
        normalized = re.sub(r"[^0-9.\-]", "", normalized)
        if not normalized:
            return None
        try:
            return float(normalized)
        except ValueError:
            return None
    return None


def _extract_json_object(raw: str) -> dict:
    payload = (raw or "").strip()
    if not payload:
        raise OCRLabelParseError("Empty model response")

    if payload.startswith("```"):
        payload = payload.strip("`")
        payload = payload.replace("json\n", "", 1).replace("JSON\n", "", 1).strip()

    try:
        parsed = json.loads(payload)
    except json.JSONDecodeError:
        start = payload.find("{")
        end = payload.rfind("}")
        if start < 0 or end <= start:
            raise OCRLabelParseError("JSON object not found")
        try:
            parsed = json.loads(payload[start : end + 1])
        except json.JSONDecodeError as exc:
            raise OCRLabelParseError("Invalid JSON payload") from exc

    if not isinstance(parsed, dict):
        raise OCRLabelParseError("Top-level JSON must be an object")
    return parsed


def parse_ocr_label_json(raw: str) -> dict:
    """Нормализует JSON-ответ модели в фиксированный формат."""
    parsed = _extract_json_object(raw)

    nutrition_100g = parsed.get("nutrition_per_100g") if isinstance(parsed.get("nutrition_per_100g"), dict) else {}
    nutrition_total = parsed.get("nutrition_total") if isinstance(parsed.get("nutrition_total"), dict) else {}

    normalized = {
        "product_name": parsed.get("product_name") or None,
        "serving_description": parsed.get("serving_description") or None,
        "weight_grams": _to_float(parsed.get("weight_grams")),
        "nutrition_per_100g": {
            "calories": _to_float(nutrition_100g.get("calories")),
            "protein": _to_float(nutrition_100g.get("protein")),
            "fat": _to_float(nutrition_100g.get("fat")),
            "carbs": _to_float(nutrition_100g.get("carbs")),
        },
        "nutrition_total": {
            "calories": _to_float(nutrition_total.get("calories")),
            "protein": _to_float(nutrition_total.get("protein")),
            "fat": _to_float(nutrition_total.get("fat")),
            "carbs": _to_float(nutrition_total.get("carbs")),
        },
        "confidence": parsed.get("confidence") if parsed.get("confidence") in {"high", "medium", "low"} else "low",
        "notes": parsed.get("notes") or "",
    }

    return normalized
