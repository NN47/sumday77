import asyncio
import os
import sys
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.setdefault("API_TOKEN", "test-token")

from handlers.weight import _build_weight_quick_adjust_keyboard, _detect_trend, _resolve_quick_weight_value, my_weight, show_weight_archive, weight_menu


class DummyBot:
    pass


class DummyMessage:
    def __init__(self):
        self.from_user = SimpleNamespace(id=12345)
        self.bot = DummyBot()
        self.answers = []

    async def answer(self, text, reply_markup=None):
        self.answers.append((text, reply_markup))


def weight_entry(value, entry_date, entry_id=1):
    return SimpleNamespace(value=str(value), date=entry_date, id=entry_id)


def test_weight_trend_uses_week_reference_not_last_three_fluctuations():
    weights = [
        weight_entry(76.9, date(2026, 5, 28), 7),
        weight_entry(76.7, date(2026, 5, 28), 6),
        weight_entry(76.0, date(2026, 5, 27), 5),
        weight_entry(79.5, date(2026, 4, 21), 4),
    ]

    assert _detect_trend(weights) == "Снижение веса 📉"


def test_weight_dashboard_hides_progress_bar_and_shows_decrease_trend():
    weights = [
        weight_entry(76.9, date(2026, 5, 28), 7),
        weight_entry(76.7, date(2026, 5, 28), 6),
        weight_entry(76.0, date(2026, 5, 27), 5),
        weight_entry(79.5, date(2026, 4, 21), 4),
    ]
    message = DummyMessage()

    with (
        patch("handlers.weight.AnalyticsRepository.track_event"),
        patch("handlers.weight.WeightRepository.get_weights", return_value=weights),
        patch("handlers.weight.WeightRepository.get_target_weight", return_value=73.0),
        patch("handlers.weight.push_menu_stack"),
    ):
        asyncio.run(my_weight(message))

    assert len(message.answers) == 1
    text, reply_markup = message.answers[0]
    assert reply_markup is weight_menu
    assert "Прогресс:" not in text
    assert "█" not in text
    assert "░" not in text
    assert "⚖️ <b>Вес</b>" in text
    assert "<b>Текущий вес:</b> <b>76.90 кг</b>" in text
    assert "📉 <b>Изменение:</b>" in text
    assert "📊 <b>Диапазон:</b>" in text
    assert "<b>Мин: 76.00 кг • Макс: 79.50 кг</b>" in text
    assert "<b>Снижение веса 📉</b>" in text
    assert "Рост веса 📈" not in text


def test_weight_menu_renames_graph_button_to_archive():
    rows = [[button.text for button in row] for row in weight_menu.keyboard]

    assert rows[1] == ["📏 Замеры тела", "📦 Архив"]
    assert all("📊 График веса" not in row for row in rows)


def test_weight_archive_shows_only_entered_weights_without_graph_or_range():
    weights = [
        weight_entry(76.4, date(2026, 5, 29), 8),
        weight_entry(76.9, date(2026, 5, 28), 7),
        weight_entry(76.7, date(2026, 5, 28), 6),
        weight_entry(76.0, date(2026, 5, 27), 5),
        weight_entry(79.5, date(2026, 4, 21), 4),
    ]
    message = DummyMessage()

    with patch("handlers.weight.WeightRepository.get_weights", return_value=weights):
        asyncio.run(show_weight_archive(message))

    assert len(message.answers) == 1
    text, reply_markup = message.answers[0]
    assert reply_markup is None
    assert text.startswith("📅 Все введённые веса:\n")
    assert "29.05.2026 — 76.40 кг" in text
    assert "28.05.2026 — 76.90 кг" in text
    assert "📊 График веса" not in text
    assert "Мин:" not in text
    assert "Макс:" not in text
    assert "█" not in text
    assert "▁" not in text


def test_weight_quick_adjust_keyboard_uses_requested_delta_buttons_without_duplicate_weight():
    keyboard = _build_weight_quick_adjust_keyboard(76.9)
    rows = [[button.text for button in row] for row in keyboard.keyboard]

    assert rows[0] == ["-1", "-0,5", "+0,5", "+1"]
    assert rows[1] == ["-0,2", "-0,1", "+0,1", "+0,2"]
    assert rows[2] == ["✍️ Ввести вручную"]
    assert rows[3] == ["⬅️ Назад", "🔄 Главное меню"]


def test_quick_weight_delta_resolves_against_base_weight():
    assert _resolve_quick_weight_value("-0,5", 76.9) == 76.4
    assert round(_resolve_quick_weight_value("+0,2", 76.9), 1) == 77.1
    assert _resolve_quick_weight_value("72.5", 76.9) is None


class DummyState:
    def __init__(self, data=None):
        self.data = data or {}
        self.state = None
        self.cleared = False

    async def get_data(self):
        return dict(self.data)

    async def update_data(self, **kwargs):
        self.data.update(kwargs)

    async def set_state(self, state):
        self.state = state

    async def clear(self):
        self.data.clear()
        self.cleared = True


def test_weight_entry_prompt_has_no_parenthetical_example():
    from handlers.weight import _weight_entry_prompt

    prompt = _weight_entry_prompt()

    assert "(" not in prompt
    assert ")" not in prompt
    assert "например" not in prompt.lower()


def test_weight_input_requires_save_confirmation_before_repository_write():
    from handlers.weight import handle_weight_input
    from states.user_states import WeightStates

    message = DummyMessage()
    state = DummyState({"entry_date": date(2026, 5, 29).isoformat(), "quick_base_weight": 76.9})
    message.text = "-0,5"

    with (
        patch("handlers.weight.WeightRepository.get_weights", return_value=[weight_entry(76.9, date(2026, 5, 28), 1)]),
        patch("handlers.weight.WeightRepository.save_weight") as save_weight,
        patch("handlers.weight.WeightRepository.update_weight") as update_weight,
    ):
        asyncio.run(handle_weight_input(message, state))

    save_weight.assert_not_called()
    update_weight.assert_not_called()
    assert state.state == WeightStates.confirming_weight
    assert state.data["draft_weight_value"] == 76.4
    text, reply_markup = message.answers[-1]
    rows = [[button.text for button in row] for row in reply_markup.keyboard]
    assert "✅ Сохранить" in rows[0]
    assert "<b>Вес:</b> 76.4 кг" in text
