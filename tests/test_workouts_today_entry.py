import asyncio
import os
from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.setdefault("API_TOKEN", "test-token")

from handlers import workouts
from utils.workout_formatters import build_day_actions_keyboard


def _button_texts(keyboard):
    return [button.text for row in keyboard.inline_keyboard for button in row]


def test_day_actions_keyboard_can_hide_calendar_back_button():
    keyboard = build_day_actions_keyboard([], date(2026, 6, 1), include_calendar_back=False)

    texts = _button_texts(keyboard)

    assert "➕ Добавить упражнение" in texts
    assert "⬅️ Назад к календарю активности" not in texts


def test_day_actions_keyboard_keeps_calendar_back_button_by_default():
    keyboard = build_day_actions_keyboard([], date(2026, 6, 1))

    texts = _button_texts(keyboard)

    assert "➕ Добавить упражнение" in texts
    assert "⬅️ Назад к календарю активности" in texts


def test_training_button_opens_today_workouts_without_calendar_back():
    message = SimpleNamespace(from_user=SimpleNamespace(id=12345))
    state = SimpleNamespace(clear=AsyncMock())

    with patch("handlers.workouts.show_day_workouts", new=AsyncMock()) as show_day_workouts:
        asyncio.run(workouts.add_training_entry(message, state))

    state.clear.assert_awaited_once()
    show_day_workouts.assert_awaited_once_with(
        message,
        "12345",
        date.today(),
        include_calendar_back=False,
    )


def test_add_another_exercise_still_opens_exercise_picker():
    message = SimpleNamespace(bot=object())
    state = SimpleNamespace(update_data=AsyncMock(), set_state=AsyncMock())

    with patch("handlers.workouts.start_exercise_selection", new=AsyncMock()) as start_selection:
        asyncio.run(workouts.add_another_exercise(message, state))

    start_selection.assert_awaited_once_with(message, state)
