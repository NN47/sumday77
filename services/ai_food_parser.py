"""Провайдер-независимый парсинг JSON с оценкой КБЖУ."""
from __future__ import annotations

import json
from typing import Optional


def parse_ai_json(raw: str) -> dict | list:
    """Извлекает JSON из обычного текста или markdown-блока ответа модели."""
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
            return json.loads(cleaned[start : end + 1])
        raise json.JSONDecodeError("No JSON object found in response", cleaned, 0)


def parse_kbju_json(raw: str) -> Optional[dict]:
    """Нормализует JSON-ответ AI в единый формат приёма пищи."""
    if not raw:
        return None

    try:
        payload = parse_ai_json(raw)
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
