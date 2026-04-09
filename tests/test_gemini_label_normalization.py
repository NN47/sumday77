import os

os.environ.setdefault("API_TOKEN", "test-token")
os.environ.setdefault("GEMINI_API_KEY", "test-gemini-key")

from services.gemini_service import GeminiService


def test_normalize_label_payload_from_flat_keys() -> None:
    payload = {
        "name": "Йогурт",
        "calories": "63",
        "protein": "3,5",
        "fat": "2.0",
        "carbs": "7",
        "weight": "250 г",
    }
    normalized = GeminiService._normalize_label_payload(payload)
    assert normalized is not None
    assert normalized["product_name"] == "Йогурт"
    assert normalized["kbju_per_100g"]["kcal"] == 63.0
    assert normalized["kbju_per_100g"]["protein"] == 3.5
    assert normalized["kbju_per_100g"]["fat"] == 2.0
    assert normalized["kbju_per_100g"]["carbs"] == 7.0
    assert normalized["package_weight"] == 250.0
    assert normalized["found_weight"] is True


def test_normalize_label_payload_from_nested_keys_and_synonyms() -> None:
    payload = {
        "product_name": "Протеиновый батончик",
        "kbju_per_100g": {
            "energy_kcal": 400,
            "proteins": 30,
            "fats": 12,
            "carbohydrates": 35,
        },
        "package_weight": None,
        "found_weight": False,
    }
    normalized = GeminiService._normalize_label_payload(payload)
    assert normalized is not None
    assert normalized["kbju_per_100g"] == {
        "kcal": 400.0,
        "protein": 30.0,
        "fat": 12.0,
        "carbs": 35.0,
    }
    assert normalized["package_weight"] is None
    assert normalized["found_weight"] is False


def test_normalize_label_payload_without_kbju_returns_none() -> None:
    payload = {"product_name": "Вода", "package_weight": 500}
    assert GeminiService._normalize_label_payload(payload) is None
