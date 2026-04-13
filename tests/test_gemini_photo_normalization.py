import os

os.environ.setdefault("API_TOKEN", "test-token")
os.environ.setdefault("GEMINI_API_KEY", "test-gemini-key")

from services.gemini_service import GeminiService


def test_normalize_kbju_payload_handles_nested_item_fields() -> None:
    service = GeminiService()
    payload = {
        "items": [
            {
                "name": "Салат",
                "weight": "180 г",
                "nutrition_total": {"calories": 270, "protein": 3, "fat": 22, "carbs": 10},
            },
            {
                "title": "Колбаски",
                "weight": "2 шт",
                "nutrition": {"calories": 365, "protein": 16, "fat": 32, "carbohydrates": 2},
            },
        ],
        "nutrition_total": {"calories": 635, "protein": 19, "fat": 54, "carbs": 12},
    }

    normalized = service._normalize_kbju_payload(payload)

    assert normalized is not None
    assert normalized["items"][0]["grams"] == 180.0
    assert normalized["items"][0]["kcal"] == 270.0
    assert normalized["items"][1]["grams"] == 2.0
    assert normalized["items"][1]["protein"] == 16.0
    assert normalized["total"] == {"kcal": 635.0, "protein": 19.0, "fat": 54.0, "carbs": 12.0}


def test_normalize_kbju_payload_calculates_from_per_100g() -> None:
    service = GeminiService()
    payload = {
        "items": [
            {
                "dish": "Брокколи",
                "grams": 200,
                "kbju_per_100g": {"kcal": 35, "protein": 3, "fat": 0.4, "carbs": 7},
            }
        ]
    }

    normalized = service._normalize_kbju_payload(payload)

    assert normalized is not None
    item = normalized["items"][0]
    assert item["kcal"] == 70.0
    assert item["protein"] == 6.0
    assert item["fat"] == 0.8
    assert item["carbs"] == 14.0
    assert normalized["total"] == {"kcal": 70.0, "protein": 6.0, "fat": 0.8, "carbs": 14.0}
