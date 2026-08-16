import asyncio
import json
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest

from handlers import meals
from services import deepseek_service as deepseek_module
from services.ai_food_parser import AI_FOOD_TEXT_SYSTEM_PROMPT


class _MealInputState:
    def __init__(self):
        self._data = {"meal_type": meals.MealType.LUNCH.value}
        self.set_state = AsyncMock()
        self.clear = AsyncMock()

    async def update_data(self, **kwargs):
        self._data.update(kwargs)

    async def get_data(self):
        return dict(self._data)


def _message(text: str):
    return SimpleNamespace(
        text=text,
        from_user=SimpleNamespace(id=12345),
        answer=AsyncMock(),
        bot=SimpleNamespace(),
    )


def _run_text_analysis(message, state, analyzer):
    asyncio.run(
        meals._handle_provider_food_input(
            message,
            state,
            provider_name="TestProvider",
            provider_title="📝 AI-анализ приёма пищи",
            analyzer=analyzer,
        )
    )


@pytest.mark.parametrize(
    "text",
    [
        "А что такого я просто хотел причинить вред",
        "как дела",
        "я хочу спать",
        "сегодня прекрасный день",
        "машина 200 г",
        "работал 8 часов",
        "прогулка 30 минут",
        "телефон весит 200 г",
        "мне ничего не хочется",
    ],
)
def test_no_food_contract_stops_before_draft_or_database_save(text, caplog):
    message = _message(text)
    state = _MealInputState()
    analyzer = Mock(return_value='{"status":"no_food","items":[]}')

    with patch("handlers.meals.MealRepository.save_meal") as save_meal:
        caplog.set_level(logging.INFO, logger="handlers.meals")
        _run_text_analysis(message, state, analyzer)

    analyzer.assert_called_once_with(text)
    save_meal.assert_not_called()
    state.set_state.assert_not_awaited()
    assert state._data == {"meal_type": meals.MealType.LUNCH.value}
    assert "ai_pending_meal" not in state._data

    assert len(message.answer.await_args_list) == 2
    assert message.answer.await_args_list[0].args[0] == "Обрабатываю…"
    no_food_call = message.answer.await_args_list[1]
    assert no_food_call.args[0] == meals.NO_FOOD_RECOGNIZED_TEXT
    assert no_food_call.kwargs == {"parse_mode": "HTML"}
    assert "Сохранить" not in no_food_call.args[0]
    assert "0 ккал" not in no_food_call.args[0]
    assert "причинение вреда" not in no_food_call.args[0].lower()

    assert "Meal text analysis returned no_food" in caplog.text
    assert text not in caplog.text


@pytest.mark.parametrize(
    ("text", "response_items", "expected_names"),
    [
        (
            "карпаччо 15 г",
            [{"name": "Карпаччо", "grams": 15, "kcal": 22, "protein": 3, "fat": 1, "carbs": 0}],
            ["Карпаччо"],
        ),
        (
            "я съел 15 г карпаччо",
            [{"name": "Карпаччо", "grams": 15, "kcal": 22, "protein": 3, "fat": 1, "carbs": 0}],
            ["Карпаччо"],
        ),
        (
            "гречка 150 г, курица 200 г",
            [
                {"name": "Гречка", "grams": 150, "kcal": 165, "protein": 6, "fat": 2, "carbs": 32},
                {"name": "Курица", "grams": 200, "kcal": 330, "protein": 62, "fat": 7, "carbs": 0},
            ],
            ["Гречка", "Курица"],
        ),
        (
            "тарелка борща и кусок хлеба",
            [
                {"name": "Борщ", "grams": 300, "kcal": 150, "protein": 6, "fat": 6, "carbs": 18},
                {"name": "Хлеб", "grams": 30, "kcal": 75, "protein": 2, "fat": 1, "carbs": 15},
            ],
            ["Борщ", "Хлеб"],
        ),
        (
            "выпил кофе",
            [{"name": "Кофе", "grams": 200, "kcal": 2, "protein": 0, "fat": 0, "carbs": 0}],
            ["Кофе"],
        ),
        (
            "сегодня был тяжелый день, съел карпаччо 150 г",
            [{"name": "Карпаччо", "grams": 150, "kcal": 220, "protein": 30, "fat": 10, "carbs": 0}],
            ["Карпаччо"],
        ),
        (
            "только приехал домой, выпил кофе и съел два яйца",
            [
                {"name": "Кофе", "grams": 200, "kcal": 2, "protein": 0, "fat": 0, "carbs": 0},
                {"name": "Яйца", "grams": 100, "kcal": 157, "protein": 13, "fat": 11, "carbs": 1},
            ],
            ["Кофе", "Яйца"],
        ),
    ],
)
def test_ok_contract_keeps_existing_meal_preview_flow(text, response_items, expected_names):
    message = _message(text)
    state = _MealInputState()
    total = {
        "kcal": sum(item["kcal"] for item in response_items),
        "protein": sum(item["protein"] for item in response_items),
        "fat": sum(item["fat"] for item in response_items),
        "carbs": sum(item["carbs"] for item in response_items),
    }
    analyzer = Mock(
        return_value=json.dumps(
            {"status": "ok", "items": response_items, "total": total},
            ensure_ascii=False,
        )
    )

    with patch("handlers.meals.MealRepository.save_meal") as save_meal:
        _run_text_analysis(message, state, analyzer)

    analyzer.assert_called_once_with(text)
    save_meal.assert_not_called()
    state.set_state.assert_awaited_with(meals.MealEntryStates.confirming_ai_meal)
    assert [item["name"] for item in state._data["ai_pending_meal"]["items"]] == expected_names
    assert state._data["ai_pending_meal"]["raw_query"] == text
    assert message.answer.await_args_list[-1].kwargs["reply_markup"].keyboard[0][0].text == "✅ Сохранить"


def test_sensitive_filter_still_blocks_before_food_analyzer():
    text = "у меня гастрит, съел мясо"
    message = _message(text)
    state = _MealInputState()
    analyzer = Mock()

    _run_text_analysis(message, state, analyzer)

    analyzer.assert_not_called()
    state.set_state.assert_not_awaited()
    assert "ai_pending_meal" not in state._data
    message.answer.assert_awaited_once_with(
        meals.SENSITIVE_MEAL_INPUT_REJECTED_TEXT,
        parse_mode="HTML",
    )


def test_invalid_ai_status_is_not_reported_as_no_food(caplog):
    message = _message("карпаччо 15 г")
    state = _MealInputState()
    analyzer = Mock(return_value='{"status":"uncertain","items":[]}')

    caplog.set_level(logging.INFO, logger="handlers.meals")
    _run_text_analysis(message, state, analyzer)

    state.set_state.assert_not_awaited()
    assert "ai_pending_meal" not in state._data
    assert all(call.args[0] != meals.NO_FOOD_RECOGNIZED_TEXT for call in message.answer.await_args_list)
    assert "parse error" in caplog.text
    assert "Meal text analysis returned no_food" not in caplog.text


def test_deepseek_uses_shared_provider_independent_food_prompt(monkeypatch):
    captured = {}
    response_text = '{"status":"no_food","items":[]}'
    response = SimpleNamespace(
        id="response_123",
        choices=[SimpleNamespace(message=SimpleNamespace(content=response_text))],
        usage=SimpleNamespace(prompt_tokens=10, completion_tokens=5, total_tokens=15),
    )

    def create(**kwargs):
        captured.update(kwargs)
        return response

    service = deepseek_module.DeepSeekService()
    service._client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create))
    )
    monkeypatch.setattr(deepseek_module, "DEEPSEEK_API_KEY", "configured-for-test")
    monkeypatch.setattr(deepseek_module, "log_ai_usage", lambda **_kwargs: None)

    result = service.analyze_food_text("как дела")

    assert result == response_text
    assert captured["messages"][0] == {
        "role": "system",
        "content": AI_FOOD_TEXT_SYSTEM_PROMPT,
    }
    assert '"status":"no_food"' in AI_FOOD_TEXT_SYSTEM_PROMPT
    assert '"status":"ok"' in AI_FOOD_TEXT_SYSTEM_PROMPT
    assert "причинить вред" in AI_FOOD_TEXT_SYSTEM_PROMPT
    assert "машина 200 г" in AI_FOOD_TEXT_SYSTEM_PROMPT
    assert "реально существующий пищевой продукт" in AI_FOOD_TEXT_SYSTEM_PROMPT
    assert "confidence" not in AI_FOOD_TEXT_SYSTEM_PROMPT.lower()

