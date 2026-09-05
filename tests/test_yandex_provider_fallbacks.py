import asyncio
from contextlib import contextmanager
from datetime import date
import logging
from unittest.mock import AsyncMock, Mock

from handlers import meals
from services.deepseek_service import DeepSeekServiceTemporaryError
from services.openai_token_budget_service import OpenAIDailyTokenLimitExceeded
from services.extended_activity_analysis_service import (
    AnalysisPeriod,
    ExtendedActivityAnalysisService,
)


def _food_result(name="Салат"):
    return {
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
        "total": {"kcal": 120, "protein": 5, "fat": 6, "carbs": 10},
        "source": "yandex",
    }


def test_food_photo_falls_back_from_openai_to_yandex(monkeypatch):
    expected = _food_result("Паста")
    monkeypatch.setattr(
        meals,
        "_analyze_image_with_openai",
        AsyncMock(side_effect=meals.OpenAILabelServiceTimeoutError("timeout")),
    )
    yandex_call = AsyncMock(return_value=expected)
    monkeypatch.setattr(meals, "_run_yandex_task", yandex_call)

    result = asyncio.run(
        meals._run_openai_image_with_yandex_fallback(
            meals.openai_label_service.analyze_food_photo_openai,
            meals.yandex_ai_service.analyze_food_photo,
            b"image",
            user_id="42",
            feature="food_photo_analysis",
            operation_log_name="анализа еды по фото",
            success_validator=meals._has_food_photo_result,
            comment="без масла",
        )
    )

    assert result.provider == "yandex"
    assert result.payload == expected
    yandex_call.assert_awaited_once_with(
        meals.yandex_ai_service.analyze_food_photo,
        b"image",
        user_id="42",
        feature="food_photo_analysis",
        comment="без масла",
    )


def test_food_photo_full_chain_reaches_yandex_after_gemini_and_openai_fail(monkeypatch):
    expected = _food_result("Рис с курицей")
    monkeypatch.setattr(
        meals,
        "_run_gemini_task",
        AsyncMock(side_effect=meals.GeminiServiceTemporaryUnavailableError("timeout")),
    )
    monkeypatch.setattr(
        meals,
        "_analyze_image_with_openai",
        AsyncMock(side_effect=meals.OpenAILabelServiceTimeoutError("timeout")),
    )
    monkeypatch.setattr(meals, "_run_yandex_task", AsyncMock(return_value=expected))

    result = asyncio.run(
        meals._run_food_photo_analysis_with_openai_fallback(
            "gemini-analyzer",
            b"image",
            user_id="42",
        )
    )

    assert result.provider == "yandex"
    assert result.payload == expected


def test_label_falls_back_from_openai_to_yandex(monkeypatch):
    expected = {
        "product_name": "Йогурт",
        "kbju_per_100g": {"kcal": 60},
        "source": "yandex",
    }
    monkeypatch.setattr(
        meals,
        "_analyze_label_with_openai",
        AsyncMock(side_effect=meals.OpenAILabelServiceTimeoutError("timeout")),
    )
    yandex_call = AsyncMock(return_value=expected)
    monkeypatch.setattr(meals, "_run_yandex_task", yandex_call)

    result = asyncio.run(
        meals._run_openai_label_with_yandex_fallback(b"image", user_id="42")
    )

    assert result == expected
    yandex_call.assert_awaited_once_with(
        meals.yandex_ai_service.extract_kbju_from_label,
        b"image",
        user_id="42",
        feature="label_analysis",
    )


def test_food_photo_skips_openai_when_daily_budget_is_unavailable(monkeypatch, caplog):
    expected = _food_result("Гречка")
    caplog.set_level(logging.INFO, logger="handlers.meals")

    @contextmanager
    def denied_reservation(**_kwargs):
        raise OpenAIDailyTokenLimitExceeded("daily limit")
        yield  # pragma: no cover

    openai_call = AsyncMock()
    yandex_call = AsyncMock(return_value=expected)
    monkeypatch.setattr(meals.openai_token_budget_service, "reservation", denied_reservation)
    monkeypatch.setattr(meals, "_analyze_image_with_openai", openai_call)
    monkeypatch.setattr(meals, "_run_yandex_task", yandex_call)

    result = asyncio.run(
        meals._run_openai_image_with_yandex_fallback(
            meals.openai_label_service.analyze_food_photo_openai,
            meals.yandex_ai_service.analyze_food_photo,
            b"image",
            user_id="42",
            feature="food_photo_analysis",
            operation_log_name="анализа еды по фото",
            success_validator=meals._has_food_photo_result,
        )
    )

    assert result.provider == "yandex"
    openai_call.assert_not_awaited()
    yandex_call.assert_awaited_once()
    assert "fallback_reason=openai_daily_token_limit" in caplog.text


def test_openai_no_usable_data_falls_back_to_yandex(monkeypatch, caplog):
    expected = _food_result("Суп")
    monkeypatch.setattr(meals, "_analyze_image_with_openai", AsyncMock(return_value=None))
    yandex_call = AsyncMock(return_value=expected)
    monkeypatch.setattr(meals, "_run_yandex_task", yandex_call)

    result = asyncio.run(
        meals._run_openai_image_with_yandex_fallback(
            meals.openai_label_service.analyze_food_photo_openai,
            meals.yandex_ai_service.analyze_food_photo,
            b"image",
            user_id="42",
            feature="food_photo_analysis",
            operation_log_name="анализа еды по фото",
            success_validator=meals._has_food_photo_result,
        )
    )

    assert result.provider == "yandex"
    yandex_call.assert_awaited_once()
    assert "fallback_reason=openai_no_usable_data" in caplog.text


def test_label_skips_openai_when_daily_budget_is_unavailable(monkeypatch):
    expected = {
        "product_name": "Творог",
        "kbju_per_100g": {"kcal": 121},
        "source": "yandex",
    }

    @contextmanager
    def denied_reservation(**_kwargs):
        raise OpenAIDailyTokenLimitExceeded("daily limit")
        yield  # pragma: no cover

    openai_call = AsyncMock()
    yandex_call = AsyncMock(return_value=expected)
    monkeypatch.setattr(meals.openai_token_budget_service, "reservation", denied_reservation)
    monkeypatch.setattr(meals, "_analyze_label_with_openai", openai_call)
    monkeypatch.setattr(meals, "_run_yandex_task", yandex_call)

    result = asyncio.run(
        meals._run_openai_label_with_yandex_fallback(b"image", user_id="42")
    )

    assert result == expected
    openai_call.assert_not_awaited()
    yandex_call.assert_awaited_once()


def test_text_food_falls_back_from_deepseek_to_yandex(monkeypatch):
    raw = (
        '{"status":"ok","items":[{"name":"Курица","grams":200,"kcal":330,'
        '"protein":62,"fat":7,"carbs":0}],'
        '"total":{"kcal":330,"protein":62,"fat":7,"carbs":0}}'
    )
    deepseek = Mock(side_effect=DeepSeekServiceTemporaryError("timeout"))
    yandex_call = AsyncMock(return_value=raw)
    monkeypatch.setattr(meals, "_run_yandex_task", yandex_call)

    result_raw, parsed, provider = asyncio.run(
        meals._run_text_analysis_with_yandex_fallback(
            deepseek,
            "курица 200 г",
            user_id="42",
            feature="meal_text_ai",
        )
    )

    assert result_raw == raw
    assert parsed["total"]["kcal"] == 330
    assert provider == "yandex"
    yandex_call.assert_awaited_once_with(
        meals.yandex_ai_service.analyze_food_text,
        "курица 200 г",
        user_id="42",
        feature="meal_text_ai",
    )


def test_meal_recommendation_falls_back_from_deepseek_to_yandex(monkeypatch):
    deepseek = Mock(side_effect=DeepSeekServiceTemporaryError("timeout"))
    monkeypatch.setattr(
        meals.deepseek_service,
        "generate_meal_completion_comment",
        deepseek,
    )
    metadata = {"model": "aliceai-llm-flash"}
    yandex_call = AsyncMock(return_value=("Хороший приём пищи.", metadata))
    monkeypatch.setattr(meals, "_run_yandex_task", yandex_call)

    text, result_metadata = asyncio.run(
        meals._generate_meal_completion_comment_with_yandex_fallback(
            "данные приёма",
            user_id="42",
        )
    )

    assert text == "Хороший приём пищи."
    assert result_metadata["provider"] == "yandex"
    yandex_call.assert_awaited_once_with(
        meals.yandex_ai_service.generate_meal_completion_comment,
        "данные приёма",
        user_id="42",
        system_prompt=meals.MEAL_COMPLETION_COMMENT_SYSTEM_PROMPT,
    )


def test_detailed_day_analysis_falls_back_from_deepseek_to_yandex(monkeypatch):
    service = ExtendedActivityAnalysisService()
    monkeypatch.setattr(service, "collect_period_context", lambda *_args: {"day": "data"})
    monkeypatch.setattr(
        "services.extended_activity_analysis_service.deepseek_service.analyze_activity_prompt",
        Mock(side_effect=DeepSeekServiceTemporaryError("timeout")),
    )
    yandex_call = AsyncMock(return_value="Подробный анализ дня")
    monkeypatch.setattr(
        "services.extended_activity_analysis_service.yandex_ai_service.analyze_activity_prompt",
        yandex_call,
    )

    result = asyncio.run(
        service.generate(
            "42",
            AnalysisPeriod(date(2026, 9, 5), date(2026, 9, 5), "за день"),
            include_provider=True,
        )
    )

    assert result == ("Подробный анализ дня", "yandex")
    assert yandex_call.await_args.kwargs["feature"] == "detailed_activity_analysis"
