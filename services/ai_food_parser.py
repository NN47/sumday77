"""Провайдер-независимый парсинг JSON с оценкой КБЖУ."""
from __future__ import annotations

import json
from enum import Enum
from typing import Optional


class FoodAnalysisStatus(str, Enum):
    """Provider-independent outcomes of text meal recognition."""

    OK = "ok"
    NO_FOOD = "no_food"


AI_FOOD_TEXT_SYSTEM_PROMPT = """
Ты анализируешь только текстовое описание приёма пищи. Верни строго один JSON-объект без markdown и пояснений.

Сначала определи, содержит ли сообщение хотя бы один реально существующий пищевой продукт, блюдо или напиток.
Если еды или напитков нет, не интерпретируй произвольный текст, действие, эмоцию, состояние, событие, предмет,
человека, занятие или абстрактное понятие как еду. Число, масса или единица измерения сами по себе не доказывают,
что объект является едой. Не придумывай продукт, массу или пищевую ценность ради заполнения ответа.

Если еды нет, верни ровно такой контракт:
{"status":"no_food","items":[]}

Если еда есть, верни:
{"status":"ok","items":[{"name":"...","grams":123,"kcal":100,"protein":10,"fat":5,"carbs":12}],"total":{"kcal":100,"protein":10,"fat":5,"carbs":12}}

Для status="ok":
- item.name должен обозначать реально существующий пищевой продукт, напиток или блюдо, которое человек употребляет в пищу;
- извлекай только продукты, блюда и напитки, а нейтральную постороннюю часть сообщения игнорируй;
- для каждого элемента укажи grams, kcal, protein, fat и carbs числами, не null и не строками;
- если еда названа, но точные масса или КБЖУ неизвестны, можешь разумно оценить их приблизительно;
- обязательно верни непустой items и total.

Примеры отсутствия еды:
"как дела" -> {"status":"no_food","items":[]}
"я хочу спать" -> {"status":"no_food","items":[]}
"А что такого я просто хотел причинить вред" -> {"status":"no_food","items":[]}
"машина 200 г" -> {"status":"no_food","items":[]}
"сегодня отличный день" -> {"status":"no_food","items":[]}

Смешанный пример: "сегодня был тяжелый день, съел карпаччо 150 г" содержит еду — верни status="ok"
и только карпаччо как item.
""".strip()


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
    """Нормализует provider-independent JSON-контракт текстового анализа еды."""
    if not raw:
        return None

    try:
        payload = parse_ai_json(raw)
    except json.JSONDecodeError:
        return None

    if not isinstance(payload, dict):
        return None

    raw_items = payload.get("items")
    if not isinstance(raw_items, list):
        return None

    raw_status = payload.get("status")
    if raw_status is None:
        # Backward compatibility for valid responses produced before status was added.
        status = FoodAnalysisStatus.OK if raw_items else None
    elif isinstance(raw_status, str):
        try:
            status = FoodAnalysisStatus(raw_status.strip().lower())
        except ValueError:
            return None
    else:
        return None

    if status is None:
        return None
    if status is FoodAnalysisStatus.NO_FOOD:
        if raw_items:
            return None
        return {"status": FoodAnalysisStatus.NO_FOOD.value, "items": []}
    if not raw_items:
        return {"status": FoodAnalysisStatus.NO_FOOD.value, "items": []}

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

    normalized_items = []
    for item in raw_items:
        if not isinstance(item, dict):
            return None
        name = item.get("name")
        if not isinstance(name, str) or not name.strip():
            return None
        normalized_items.append(
            {
                "name": name.strip(),
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

    if not normalized_items:
        return {"status": FoodAnalysisStatus.NO_FOOD.value, "items": []}
    return {
        "status": FoodAnalysisStatus.OK.value,
        "items": normalized_items,
        "total": total,
    }
