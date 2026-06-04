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
    state = SimpleNamespace(
        get_data=AsyncMock(return_value={"entry_date": "2026-06-01"}),
        update_data=AsyncMock(),
        set_state=AsyncMock(),
    )

    with patch("handlers.workouts.start_exercise_selection", new=AsyncMock()) as start_selection:
        asyncio.run(workouts.add_another_exercise(message, state))

    start_selection.assert_awaited_once_with(message, state, date(2026, 6, 1))


def test_add_another_set_menu_contains_add_different_exercise_button():
    from utils.keyboards import add_another_set_menu

    rows = [[button.text for button in row] for row in add_another_set_menu.keyboard]

    assert rows == [
        ["💪 Добавить еще подход"],
        ["➕ Добавить другое упражнение"],
        ["✅ Завершить упражнение"],
    ]


def test_count_menu_uses_cancel_instead_of_back():
    from utils.keyboards import count_menu

    last_row = [button.text for button in count_menu.keyboard[-1]]

    assert last_row == ["❌ Отмена", "🔄 Главное меню"]


def test_add_different_exercise_from_reps_set_preserves_training_date():
    message = SimpleNamespace(
        text="➕ Добавить другое упражнение",
        from_user=SimpleNamespace(id=12345),
        bot=object(),
    )
    state = SimpleNamespace(
        get_data=AsyncMock(
            return_value={
                "entry_date": "2026-06-02",
                "exercise": "Подтягивания",
                "variant": "Прямой хват",
            }
        )
    )

    with patch("handlers.workouts.start_exercise_selection", new=AsyncMock()) as start_selection:
        asyncio.run(workouts.handle_count_input(message, state))

    start_selection.assert_awaited_once_with(message, state, date(2026, 6, 2))


def test_add_different_exercise_from_weighted_set_preserves_training_date():
    message = SimpleNamespace(
        text="➕ Добавить другое упражнение",
        from_user=SimpleNamespace(id=12345),
        bot=object(),
    )
    state = SimpleNamespace(
        get_data=AsyncMock(
            return_value={
                "entry_date": "2026-06-03",
                "exercise": "Жим штанги лёжа",
                "variant": "reps",
            }
        )
    )

    with patch("handlers.workouts.start_exercise_selection", new=AsyncMock()) as start_selection:
        asyncio.run(workouts.handle_count_input(message, state))

    start_selection.assert_awaited_once_with(message, state, date(2026, 6, 3))


def test_cancel_reps_input_clears_state_and_returns_training_menu_without_saving():
    message = SimpleNamespace(
        text="❌ Отмена",
        from_user=SimpleNamespace(id=12345),
        bot=SimpleNamespace(menu_stack=[]),
        answer=AsyncMock(),
    )
    state = SimpleNamespace(clear=AsyncMock())

    with patch("handlers.workouts.WorkoutRepository.save_workout") as save_workout:
        asyncio.run(workouts.handle_count_input(message, state))

    state.clear.assert_awaited_once()
    save_workout.assert_not_called()
    message.answer.assert_awaited_once()
    assert message.answer.await_args.kwargs["reply_markup"] is workouts.training_menu
