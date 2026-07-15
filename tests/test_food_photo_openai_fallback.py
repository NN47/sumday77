import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("API_TOKEN", "test-token")
os.environ.setdefault("GEMINI_API_KEY", "test-gemini-key")

from handlers import meals


def _food_result(name="Салат"):
    return {
        "items": [{"name": name, "grams": 100, "kcal": 120, "protein": 5, "fat": 6, "carbs": 10}],
        "total": {"kcal": 120, "protein": 5, "fat": 6, "carbs": 10},
    }


def test_food_photo_analysis_uses_gemini_without_openai_when_gemini_succeeds(monkeypatch):
    calls = []
    expected = _food_result("Омлет")

    async def fake_run_gemini_task(analyzer, image_data):
        calls.append(("gemini", analyzer, image_data))
        return expected

    def fake_openai(*args, **kwargs):  # pragma: no cover - must not be called
        calls.append(("openai", args, kwargs))
        raise AssertionError("OpenAI fallback must not be called after Gemini success")

    monkeypatch.setattr(meals, "_run_gemini_task", fake_run_gemini_task)
    monkeypatch.setattr(meals.openai_label_service, "analyze_food_photo_openai", fake_openai)

    result = asyncio.run(
        meals._run_food_photo_analysis_with_openai_fallback("gemini-analyzer", b"image", user_id="42")
    )

    assert result.provider == "gemini"
    assert result.payload == expected
    assert calls == [("gemini", "gemini-analyzer", b"image")]


def test_food_photo_analysis_falls_back_to_openai_after_gemini_failure(monkeypatch):
    calls = []
    expected = _food_result("Паста")

    async def fake_run_gemini_task(analyzer, image_data):
        calls.append(("gemini", analyzer, image_data))
        raise meals.GeminiServiceTemporaryUnavailableError("timeout")

    async def fake_analyze_image_with_openai(openai_analyzer, image_data, *, user_id=None, feature, operation_log_name):
        calls.append(("openai", openai_analyzer, image_data, user_id, feature, operation_log_name))
        return expected

    monkeypatch.setattr(meals, "_run_gemini_task", fake_run_gemini_task)
    monkeypatch.setattr(meals, "_analyze_image_with_openai", fake_analyze_image_with_openai)

    result = asyncio.run(
        meals._run_food_photo_analysis_with_openai_fallback("gemini-analyzer", b"image", user_id="42")
    )

    assert result.provider == "openai"
    assert result.payload == expected
    assert calls == [
        ("gemini", "gemini-analyzer", b"image"),
        (
            "openai",
            meals.openai_label_service.analyze_food_photo_openai,
            b"image",
            "42",
            "food_photo_analysis",
            "анализа еды по фото",
        ),
    ]


def test_food_photo_analysis_passes_comment_to_gemini(monkeypatch):
    calls = []
    expected = _food_result("Салат с фетой")

    async def fake_run_gemini_task(analyzer, image_data, comment):
        calls.append(("gemini", analyzer, image_data, comment))
        return expected

    monkeypatch.setattr(meals, "_run_gemini_task", fake_run_gemini_task)

    result = asyncio.run(
        meals._run_food_photo_analysis_with_openai_fallback(
            "gemini-analyzer",
            b"image",
            user_id="42",
            comment="общий вес 500 г, без масла",
        )
    )

    assert result.provider == "gemini"
    assert result.payload == expected
    assert calls == [("gemini", "gemini-analyzer", b"image", "общий вес 500 г, без масла")]


def test_food_photo_analysis_passes_comment_to_openai_fallback(monkeypatch):
    calls = []
    expected = _food_result("Паста с соусом")

    async def fake_run_gemini_task(analyzer, image_data, comment):
        calls.append(("gemini", analyzer, image_data, comment))
        raise meals.GeminiServiceTemporaryUnavailableError("timeout")

    async def fake_analyze_image_with_openai(
        openai_analyzer,
        image_data,
        *,
        user_id=None,
        feature,
        operation_log_name,
        comment=None,
    ):
        calls.append(("openai", openai_analyzer, image_data, user_id, feature, operation_log_name, comment))
        return expected

    monkeypatch.setattr(meals, "_run_gemini_task", fake_run_gemini_task)
    monkeypatch.setattr(meals, "_analyze_image_with_openai", fake_analyze_image_with_openai)

    result = asyncio.run(
        meals._run_food_photo_analysis_with_openai_fallback(
            "gemini-analyzer",
            b"image",
            user_id="42",
            comment="съел половину порции",
        )
    )

    assert result.provider == "openai"
    assert result.payload == expected
    assert calls == [
        ("gemini", "gemini-analyzer", b"image", "съел половину порции"),
        (
            "openai",
            meals.openai_label_service.analyze_food_photo_openai,
            b"image",
            "42",
            "food_photo_analysis",
            "анализа еды по фото",
            "съел половину порции",
        ),
    ]


def test_food_photo_analysis_raises_standard_error_when_both_providers_fail(monkeypatch):
    async def fake_run_gemini_task(analyzer, image_data):
        raise meals.GeminiServiceQuotaError("quota")

    async def fake_analyze_image_with_openai(openai_analyzer, image_data, *, user_id=None, feature, operation_log_name):
        raise meals.OpenAILabelServiceTimeoutError("timeout")

    monkeypatch.setattr(meals, "_run_gemini_task", fake_run_gemini_task)
    monkeypatch.setattr(meals, "_analyze_image_with_openai", fake_analyze_image_with_openai)

    try:
        asyncio.run(meals._run_food_photo_analysis_with_openai_fallback("gemini-analyzer", b"image", user_id="42"))
    except meals.AllProvidersUnavailableError as exc:
        assert "All providers unavailable" in str(exc)
    else:
        raise AssertionError("AllProvidersUnavailableError was not raised")


def test_label_analysis_falls_back_to_openai_after_gemini_failed_precondition(monkeypatch):
    calls = []
    expected = {
        "product_name": "Йогурт",
        "kbju_per_100g": {"kcal": 80, "protein": 5, "fat": 2, "carbs": 10},
        "source": "openai",
    }

    async def fake_run_gemini_task(analyzer, image_data):
        calls.append(("gemini", analyzer, image_data))
        raise meals.GeminiServiceUnknownError(
            "400 FAILED_PRECONDITION User location is not supported for the API use."
        )

    async def fake_analyze_label_with_openai(image_data, *, user_id=None):
        calls.append(("openai", image_data, user_id))
        return expected

    monkeypatch.setattr(meals, "_run_gemini_task", fake_run_gemini_task)
    monkeypatch.setattr(meals, "_analyze_label_with_openai", fake_analyze_label_with_openai)

    result = asyncio.run(
        meals._run_label_analysis_with_openai_fallback("gemini-label-analyzer", b"image", user_id="42")
    )

    assert result == expected
    assert calls == [
        ("gemini", "gemini-label-analyzer", b"image"),
        ("openai", b"image", "42"),
    ]
