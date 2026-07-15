import os

os.environ.setdefault("API_TOKEN", "test-token")

import pytest

from services.openai_label_service import (
    OpenAILabelService,
    OpenAILabelServiceConfigError,
    OpenAILabelServiceInvalidJSONError,
)


def test_openai_label_normalization_matches_gemini_label_shape() -> None:
    payload = {
        "name": "Творог 5%",
        "weight_g": 180,
        "calories_per_100g": 121,
        "protein_per_100g": 17,
        "fat_per_100g": 5,
        "carbs_per_100g": 3.2,
    }

    normalized = OpenAILabelService._normalize_label_payload(payload)

    assert normalized == {
        "product_name": "Творог 5%",
        "kbju_per_100g": {
            "kcal": 121.0,
            "protein": 17.0,
            "fat": 5.0,
            "carbs": 3.2,
        },
        "package_weight": 180.0,
        "found_weight": True,
        "source": "openai",
    }


def test_openai_label_normalization_without_kbju_returns_none() -> None:
    assert OpenAILabelService._normalize_label_payload({"name": "Неизвестно", "weight_g": 100}) is None


def test_openai_label_parser_rejects_invalid_json() -> None:
    with pytest.raises(OpenAILabelServiceInvalidJSONError):
        OpenAILabelService._parse_json_response("not json")


def test_openai_label_service_requires_api_key() -> None:
    service = OpenAILabelService(api_key=None)

    with pytest.raises(OpenAILabelServiceConfigError):
        service.extract_kbju_from_label(b"fake-image")


def test_openai_food_photo_normalization_matches_gemini_photo_shape() -> None:
    payload = {
        "dish_name": "Омлет с овощами",
        "estimated_weight_g": 260,
        "calories": 310,
        "protein": 20,
        "fat": 22,
        "carbs": 8,
        "items": [
            {
                "name": "омлет",
                "estimated_weight_g": 200,
                "calories": 260,
                "protein": 18,
                "fat": 20,
                "carbs": 3,
            },
            {
                "name": "овощи",
                "estimated_weight_g": 60,
                "calories": 50,
                "protein": 2,
                "fat": 2,
                "carbs": 5,
            },
        ],
        "confidence": "medium",
    }

    normalized = OpenAILabelService._normalize_food_photo_payload(payload)

    assert normalized == {
        "items": [
            {"name": "омлет", "grams": 200.0, "kcal": 260.0, "protein": 18.0, "fat": 20.0, "carbs": 3.0},
            {"name": "овощи", "grams": 60.0, "kcal": 50.0, "protein": 2.0, "fat": 2.0, "carbs": 5.0},
        ],
        "total": {"kcal": 310.0, "protein": 20.0, "fat": 22.0, "carbs": 8.0},
        "source": "openai",
        "confidence": "medium",
    }


def test_openai_food_photo_normalization_sums_items_when_total_missing() -> None:
    payload = {
        "items": [
            {"name": "йогурт", "estimated_weight_g": 150, "calories": 95, "protein": 7, "fat": 3, "carbs": 10},
        ],
        "confidence": "low",
    }

    normalized = OpenAILabelService._normalize_food_photo_payload(payload)

    assert normalized["total"] == {"kcal": 95.0, "protein": 7.0, "fat": 3.0, "carbs": 10.0}


def test_openai_food_photo_normalization_without_kbju_returns_none() -> None:
    assert OpenAILabelService._normalize_food_photo_payload({"dish_name": "неизвестно", "items": []}) is None


def test_openai_food_photo_service_requires_api_key() -> None:
    service = OpenAILabelService(api_key=None)

    with pytest.raises(OpenAILabelServiceConfigError):
        service.analyze_food_photo_openai(b"fake-image")


def test_extract_label_uses_label_prompt_without_comment_name_error(monkeypatch) -> None:
    captured = {}

    class _FakeResponses:
        def create(self, **kwargs):
            captured.update(kwargs)
            return type(
                "Response",
                (),
                {
                    "output_text": '{"name":"Йогурт","calories_per_100g":80,"protein_per_100g":5,"fat_per_100g":2,"carbs_per_100g":10}',
                    "usage": None,
                    "id": "resp_test",
                },
            )()

    class _FakeOpenAI:
        def __init__(self, **kwargs):
            captured["client_kwargs"] = kwargs
            self.responses = _FakeResponses()

    monkeypatch.setattr("services.openai_label_service.OpenAI", _FakeOpenAI)

    result = OpenAILabelService(api_key="test-key").extract_kbju_from_label(b"fake-image")

    prompt_text = captured["input"][0]["content"][0]["text"]
    assert "Проанализируй фото этикетки" in prompt_text
    assert "Проанализируй фото блюда" not in prompt_text
    assert result["product_name"] == "Йогурт"


def test_extract_label_accepts_optional_comment(monkeypatch) -> None:
    captured = {}

    class _FakeResponses:
        def create(self, **kwargs):
            captured.update(kwargs)
            return type(
                "Response",
                (),
                {
                    "output_text": '{"name":"Кефир","calories_per_100g":50,"protein_per_100g":3,"fat_per_100g":1,"carbs_per_100g":4}',
                    "usage": None,
                    "id": "resp_test",
                },
            )()

    class _FakeOpenAI:
        def __init__(self, **kwargs):
            self.responses = _FakeResponses()

    monkeypatch.setattr("services.openai_label_service.OpenAI", _FakeOpenAI)

    result = OpenAILabelService(api_key="test-key").extract_kbju_from_label(
        b"fake-image",
        comment="вес указан справа",
    )

    prompt_text = captured["input"][0]["content"][0]["text"]
    assert "вес указан справа" in prompt_text
    assert result["product_name"] == "Кефир"
