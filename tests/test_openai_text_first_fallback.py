import asyncio
import logging
from contextlib import nullcontext
from datetime import date

from handlers import meals
from services import extended_activity_analysis_service as activity_module


FOOD_JSON = """{
  "status": "ok",
  "items": [{"name": "яблоко", "grams": 100, "calories": 52, "protein": 0.3, "fat": 0.2, "carbs": 14}],
  "total": {"calories": 52, "protein": 0.3, "fat": 0.2, "carbs": 14}
}"""


def test_text_meal_uses_openai_before_existing_analyzer(monkeypatch, caplog):
    calls = []
    caplog.set_level(logging.INFO, logger=meals.__name__)
    monkeypatch.setattr(meals.openai_token_budget_service, "reservation", lambda **_: nullcontext())
    monkeypatch.setattr(meals.openai_text_service, "analyze_food_text", lambda *a, **k: calls.append("openai") or FOOD_JSON)

    def old_analyzer(*args, **kwargs):
        calls.append("deepseek")
        return FOOD_JSON

    _, parsed, provider = asyncio.run(
        meals._run_text_analysis_with_yandex_fallback(old_analyzer, "яблоко", user_id="1", feature="meal_text_ai")
    )
    assert parsed["status"] == "ok"
    assert provider == "openai"
    assert calls == ["openai"]
    assert "AI text meal analysis provider=openai" in caplog.messages
    assert "AI text meal analysis provider=deepseek" not in caplog.messages


def test_text_meal_continues_existing_chain_when_openai_fails(monkeypatch, caplog):
    calls = []
    caplog.set_level(logging.INFO, logger=meals.__name__)
    monkeypatch.setattr(meals.openai_token_budget_service, "reservation", lambda **_: nullcontext())

    def failed_openai(*args, **kwargs):
        calls.append("openai")
        raise RuntimeError("unavailable")

    monkeypatch.setattr(meals.openai_text_service, "analyze_food_text", failed_openai)

    def old_analyzer(*args, **kwargs):
        calls.append("deepseek")
        assert "AI text meal analysis provider=deepseek" not in caplog.messages
        return FOOD_JSON

    _, _, provider = asyncio.run(
        meals._run_text_analysis_with_yandex_fallback(old_analyzer, "яблоко", user_id="1", feature="meal_text_ai")
    )
    assert provider == "deepseek"
    assert calls == ["openai", "deepseek"]
    assert caplog.messages.count("AI text meal analysis provider=deepseek") == 1
    assert "AI text meal analysis provider=openai" not in caplog.messages


def test_meal_comment_uses_openai_first(monkeypatch):
    monkeypatch.setattr(meals.openai_token_budget_service, "reservation", lambda **_: nullcontext())
    monkeypatch.setattr(
        meals.openai_text_service,
        "generate_meal_completion_comment",
        lambda *a, **k: ("Отличный приём пищи", {"model": "test"}),
    )
    monkeypatch.setattr(
        meals.deepseek_service,
        "generate_meal_completion_comment",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("DeepSeek must not run")),
    )
    text, metadata = asyncio.run(
        meals._generate_meal_completion_comment_with_yandex_fallback("prompt", user_id="1")
    )
    assert text == "Отличный приём пищи"
    assert metadata["provider"] == "openai"


def test_day_analysis_continues_with_deepseek_after_openai_failure(monkeypatch):
    service = activity_module.ExtendedActivityAnalysisService()
    monkeypatch.setattr(service, "collect_period_context", lambda *a, **k: {"day": "data"})
    monkeypatch.setattr(activity_module.openai_token_budget_service, "reservation", lambda **_: nullcontext())
    monkeypatch.setattr(
        activity_module.openai_text_service,
        "analyze_activity_prompt",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("unavailable")),
    )
    monkeypatch.setattr(activity_module.deepseek_service, "analyze_activity_prompt", lambda *a, **k: "Анализ")
    result = asyncio.run(
        service.generate(
            "1",
            activity_module.AnalysisPeriod(date(2026, 9, 10), date(2026, 9, 10), "за день"),
            include_provider=True,
        )
    )
    assert result == ("Анализ", "deepseek")
