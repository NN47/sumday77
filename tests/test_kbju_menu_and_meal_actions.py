import asyncio
import os
from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

os.environ.setdefault("API_TOKEN", "test-token")

from handlers import meals
from states.user_states import MealEntryStates
from utils.meal_types import MealType
from utils.keyboards import (
    FINISH_MEAL_BUTTON_TEXT,
    KBJU_ADD_MEAL_BUTTON_ALIASES,
    KBJU_ADD_MEAL_BUTTON_TEXT,
    kbju_add_menu,
    kbju_add_method_back_menu,
    kbju_menu,
)
from utils.meal_formatters import build_meals_actions_keyboard


def _reply_keyboard_texts(markup) -> list[str]:
    return [button.text for row in markup.keyboard for button in row]


def test_kbju_menu_hides_duplicate_daily_report_button():
    texts = _reply_keyboard_texts(kbju_menu)

    assert "📊 Дневной отчёт" not in texts
    assert KBJU_ADD_MEAL_BUTTON_TEXT in texts
    assert "➕ Добавить" not in texts
    assert "📆 Календарь КБЖУ" in texts


def test_kbju_menu_shows_calendar_and_goal_buttons_on_one_row():
    rows = [[button.text for button in row] for row in kbju_menu.keyboard]

    assert ["📆 Календарь КБЖУ", "🎯 Цель / Норма КБЖУ"] in rows


def test_kbju_add_menu_exposes_primary_input_and_custom_product_buttons():
    texts = _reply_keyboard_texts(kbju_add_menu)

    assert texts == [
        "📝 Ввести приём пищи текстом (AI-анализ)",
        "📷 Анализ еды по фото",
        "📋 Анализ этикетки",
        "🧺 Мой продукт",
        FINISH_MEAL_BUTTON_TEXT,
    ]
    assert "🧪 Ввести текст через OpenRouter" not in texts
    assert "🤖 Ввести приём пищи через DeepSeek" not in texts
    assert "🧠 Ввести текст через GigaChat" not in texts
    assert "🧪 Анализ еды OpenAI" not in texts
    assert "🧪 Анализ этикетки OpenAI" not in texts
    assert "🔄 Главное меню" not in texts



def test_kbju_add_method_back_menu_only_shows_back_button():
    texts = _reply_keyboard_texts(kbju_add_method_back_menu)

    assert texts == ["⬅️ Назад"]
    assert "🔄 Главное меню" not in texts
    assert FINISH_MEAL_BUTTON_TEXT not in texts


def test_kbju_add_meal_button_aliases_keep_legacy_text():
    assert KBJU_ADD_MEAL_BUTTON_TEXT in KBJU_ADD_MEAL_BUTTON_ALIASES
    assert "➕ Добавить" in KBJU_ADD_MEAL_BUTTON_ALIASES


def test_meal_actions_keyboard_uses_supported_callback_prefixes():
    keyboard = build_meals_actions_keyboard(
        meals=[
            type("MealStub", (), {"meal_type": "breakfast"})(),
            type("MealStub", (), {"meal_type": "lunch"})(),
        ],
        target_date=date(2026, 4, 8),
    )

    callback_data = [button.callback_data for row in keyboard.inline_keyboard for button in row]

    assert all(not data.startswith("food:") for data in callback_data)
    assert "add_meal:breakfast:2026-04-08" in callback_data
    assert "edit_meal:breakfast:2026-04-08" in callback_data
    assert "clear_meal:breakfast:2026-04-08" in callback_data


class DummyState:
    def __init__(self):
        self.data = {"meal_type": MealType.LUNCH.value}
        self.state = MealEntryStates.waiting_for_ai_food_input

    async def get_data(self):
        return dict(self.data)

    async def update_data(self, **kwargs):
        self.data.update(kwargs)

    async def set_state(self, state):
        self.state = state


class DummyMessage:
    def __init__(self, text="⬅️ Назад"):
        self.text = text
        self.from_user = SimpleNamespace(id=12345)
        self.bot = SimpleNamespace()
        self.answers = []

    async def answer(self, text, reply_markup=None, parse_mode=None):
        self.answers.append((text, reply_markup, parse_mode))


def test_ai_text_back_returns_to_add_methods_without_running_analyzer():
    message = DummyMessage()
    state = DummyState()
    analyzer = AsyncMock(return_value='{"total": {}}')

    with patch("handlers.meals._show_input_methods", new=AsyncMock()) as show_input_methods:
        asyncio.run(
            meals._handle_provider_food_input(
                message,
                state,
                provider_name="DeepSeek",
                provider_title="📝 AI-анализ приёма пищи",
                analyzer=analyzer,
            )
        )

    analyzer.assert_not_called()
    show_input_methods.assert_awaited_once_with(message, state, user_id="12345")
    assert state.state == MealEntryStates.choosing_meal_type
    assert state.data["meal_type"] == MealType.LUNCH.value
    assert state.data["pending_add_method"] is None
