import asyncio
from contextlib import contextmanager
from unittest.mock import AsyncMock, Mock

import pytest

from services import food_photo_analysis_service as module
from services.food_photo_analysis_service import (
    FoodPhotoAnalysisService,
    FoodPhotoProvider,
    FoodPhotoProvidersUnavailableError,
)


def _payload(name: str = "Салат") -> dict:
    return {
        "dish_name": name,
        "items": [
            {
                "name": name,
                "grams": 100,
                "kcal": 120,
                "protein": 5,
                "fat": 6,
                "carbs": 10,
            }
        ],
    }


def test_analyze_returns_provider_independent_validated_result(monkeypatch):
    gemini = Mock()
    gemini.estimate_kbju_from_photo.return_value = _payload("Омлет")
    monkeypatch.setattr(module, "gemini_service", gemini)

    result = asyncio.run(
        FoodPhotoAnalysisService().analyze(
            image_bytes=b"image",
            user_id="42",
            comment="без масла",
        )
    )

    assert result.status == "ok"
    assert result.provider_used == "gemini"
    assert result.fallback_used is False
    assert result.dish_name == "Омлет"
    assert result.items[0]["grams"] == 100.0
    assert result.total == {"kcal": 120.0, "protein": 5.0, "fat": 6.0, "carbs": 10.0}
    gemini.estimate_kbju_from_photo.assert_called_once_with(b"image", "без масла")


def test_analyze_falls_back_to_openai_for_invalid_gemini_payload(monkeypatch):
    gemini = Mock()
    gemini.estimate_kbju_from_photo.return_value = {"items": []}
    openai = Mock()
    openai.analyze_food_photo_openai.return_value = _payload("Паста")
    monkeypatch.setattr(module, "gemini_service", gemini)
    monkeypatch.setattr(module, "openai_label_service", openai)

    result = asyncio.run(FoodPhotoAnalysisService().analyze(image_bytes=b"image", user_id="42"))

    assert result.provider_used == "openai"
    assert result.fallback_used is True
    assert result.dish_name == "Паста"
    openai.analyze_food_photo_openai.assert_called_once_with(
        b"image", user_id="42", feature="food_photo_analysis"
    )


def test_openai_budget_limit_falls_back_to_yandex(monkeypatch):
    @contextmanager
    def denied_reservation(**_kwargs):
        raise module.OpenAIDailyTokenLimitExceeded("daily limit")
        yield

    budget = Mock()
    budget.reservation = denied_reservation
    yandex = Mock()
    yandex.analyze_food_photo = AsyncMock(return_value=_payload("Гречка"))
    monkeypatch.setattr(module, "openai_token_budget_service", budget)
    monkeypatch.setattr(module, "yandex_ai_service", yandex)

    result = asyncio.run(
        FoodPhotoAnalysisService().analyze(
            image_bytes=b"image",
            primary_provider=FoodPhotoProvider.OPENAI,
        )
    )

    assert result.provider_used == "yandex"
    assert result.fallback_used is True


def test_analyze_classifies_exhausted_provider_chain(monkeypatch):
    gemini = Mock()
    gemini.estimate_kbju_from_photo.side_effect = RuntimeError("gemini down")
    openai = Mock()
    openai.analyze_food_photo_openai.side_effect = RuntimeError("openai down")
    yandex = Mock()
    yandex.analyze_food_photo = AsyncMock(side_effect=RuntimeError("yandex down"))
    monkeypatch.setattr(module, "gemini_service", gemini)
    monkeypatch.setattr(module, "openai_label_service", openai)
    monkeypatch.setattr(module, "yandex_ai_service", yandex)

    with pytest.raises(FoodPhotoProvidersUnavailableError):
        asyncio.run(FoodPhotoAnalysisService().analyze(image_bytes=b"image"))
