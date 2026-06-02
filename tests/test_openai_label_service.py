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
