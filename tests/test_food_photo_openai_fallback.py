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
