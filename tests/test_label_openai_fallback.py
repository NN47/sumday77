import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("API_TOKEN", "test-token")
os.environ.setdefault("GEMINI_API_KEY", "test-gemini-key")

from handlers import meals


def test_label_analysis_uses_openai_after_gemini_failure(monkeypatch):
    calls = []
    expected = {"product_name": "Йогурт", "kbju_per_100g": {"kcal": 60}}

    async def fake_run_gemini_task(analyzer, image_data):
        calls.append(("gemini", analyzer, image_data))
        raise meals.GeminiServiceQuotaError("quota exceeded")

    async def fake_openai_analysis(image_data, *, user_id=None):
        calls.append(("openai", image_data, user_id))
        return expected

    monkeypatch.setattr(meals, "_run_gemini_task", fake_run_gemini_task)
    monkeypatch.setattr(meals, "_analyze_label_with_openai", fake_openai_analysis)

    result = asyncio.run(
        meals._run_label_analysis_with_openai_fallback("gemini-analyzer", b"image", user_id="42")
    )

    assert result == expected
    assert calls == [("gemini", "gemini-analyzer", b"image"), ("openai", b"image", "42")]


def test_label_analysis_raises_standard_error_when_both_providers_fail(monkeypatch):
    async def fake_run_gemini_task(analyzer, image_data):
        raise meals.GeminiServiceTemporaryUnavailableError("timeout")

    async def fake_openai_analysis(image_data, *, user_id=None):
        raise meals.OpenAILabelServiceTimeoutError("timeout")

    monkeypatch.setattr(meals, "_run_gemini_task", fake_run_gemini_task)
    monkeypatch.setattr(meals, "_analyze_label_with_openai", fake_openai_analysis)

    try:
        asyncio.run(meals._run_label_analysis_with_openai_fallback("gemini-analyzer", b"image", user_id="42"))
    except meals.AllProvidersUnavailableError as exc:
        assert "All providers unavailable" in str(exc)
    else:
        raise AssertionError("AllProvidersUnavailableError was not raised")
