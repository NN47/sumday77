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


def test_legacy_training_button_opens_activity_picker():
    message = SimpleNamespace(from_user=SimpleNamespace(id=12345))
    state = SimpleNamespace(clear=AsyncMock())

    with patch("handlers.workouts.start_exercise_selection", new=AsyncMock()) as start_selection:
        asyncio.run(workouts.add_training_entry(message, state))

    state.clear.assert_awaited_once()
    start_selection.assert_awaited_once_with(message, state, date.today())


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
        ["💪 Добавить еще подход", "⚖️ Изменить вес"],
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


def test_start_exercise_selection_shows_recent_inline_and_categories():
    message = SimpleNamespace(
        from_user=SimpleNamespace(id=12345),
        bot=SimpleNamespace(menu_stack=[]),
        answer=AsyncMock(),
    )
    state = SimpleNamespace(update_data=AsyncMock(), set_state=AsyncMock())

    with patch("handlers.workouts._get_recent_exercises", return_value=["Бег", "Отжимания"]):
        asyncio.run(workouts.start_exercise_selection(message, state, date.today()))

    assert message.answer.await_count == 2
    recent_call = message.answer.await_args_list[0]
    assert recent_call.args[0] == "⭐ Недавние активности:"
    assert [[button.text for button in row] for row in recent_call.kwargs["reply_markup"].inline_keyboard] == [
        ["1️⃣ Бег"],
        ["2️⃣ Отжимания"],
        ["🔎 Поиск упражнения"],
    ]
    category_call = message.answer.await_args_list[1]
    assert category_call.args[0] == "📂 Или выбери категорию:"
    assert category_call.kwargs["reply_markup"] is workouts.activity_category_menu


def test_catalog_exercise_inline_pick_continues_to_duration_input():
    callback = SimpleNamespace(
        data=f"wrk_pick:{workouts._activity_id('Бег')}",
        message=SimpleNamespace(bot=SimpleNamespace(menu_stack=[]), answer=AsyncMock()),
        answer=AsyncMock(),
    )
    state = SimpleNamespace(update_data=AsyncMock(), set_state=AsyncMock())

    asyncio.run(workouts.pick_catalog_exercise(callback, state))

    callback.answer.assert_awaited_once()
    state.set_state.assert_any_await(workouts.WorkoutStates.entering_duration)
    assert callback.message.answer.await_count == 1
    assert "Выбери время" in callback.message.answer.await_args_list[0].args[0]
    inline_keyboard = callback.message.answer.await_args_list[0].kwargs["reply_markup"].inline_keyboard
    assert [[button.text for button in row] for row in inline_keyboard] == [
        ["5 мин", "10 мин", "15 мин"],
        ["20 мин", "25 мин", "30 мин"],
        ["35 мин", "40 мин", "45 мин"],
        ["50 мин", "55 мин", "60 мин"],
    ]


def test_sup_boarding_is_available_in_all_exercises_and_uses_duration_input():
    assert "🏄 Сапбординг" in workouts._all_catalog_exercises()
    assert workouts._exercise_input_type("🏄 Сапбординг") == "duration"


def test_sup_boarding_search_matches_synonyms():
    tokens = tuple(token.lower() for token in workouts._exercise_search_tokens("🏄 Сапбординг"))

    assert "🏄 сапбординг" in tokens
    assert "sup" in tokens
    assert "сапсерфинг" in tokens
    assert "гребля на сапе" in tokens
    assert "катание на сапе" in tokens


def test_gym_exercise_starts_with_working_weight_input():
    callback = SimpleNamespace(
        data=f"wrk_pick:{workouts._activity_id('Тяга штанги в наклоне')}",
        message=SimpleNamespace(bot=SimpleNamespace(menu_stack=[]), answer=AsyncMock()),
        answer=AsyncMock(),
    )
    state = SimpleNamespace(update_data=AsyncMock(), set_state=AsyncMock())

    asyncio.run(workouts.pick_catalog_exercise(callback, state))

    state.set_state.assert_any_await(workouts.WorkoutStates.entering_working_weight)
    text = callback.message.answer.await_args.args[0]
    assert "🏋️ Тяга штанги в наклоне" in text
    assert "1️⃣ Укажи общий вес штанги" in text
    keyboard_rows = [[button.text for button in row] for row in callback.message.answer.await_args.kwargs["reply_markup"].keyboard]
    assert keyboard_rows[0] == ["Без веса"]
    assert "30 кг" in keyboard_rows[2]


def test_gym_weight_selection_opens_reps_without_asking_weight_again():
    message = SimpleNamespace(text="30 кг", bot=SimpleNamespace(menu_stack=[]), answer=AsyncMock())
    state = SimpleNamespace(
        get_data=AsyncMock(return_value={"exercise": "Тяга штанги в наклоне"}),
        update_data=AsyncMock(),
        set_state=AsyncMock(),
    )

    asyncio.run(workouts.handle_working_weight_input(message, state))

    state.update_data.assert_any_await(working_weight=30.0)
    state.set_state.assert_any_await(workouts.WorkoutStates.entering_count)
    text = message.answer.await_args.args[0]
    assert "⚖️ Общий вес штанги: 30 кг" in text
    assert "2️⃣ Выбери количество повторений" in text


def test_add_another_gym_set_reuses_selected_working_weight():
    message = SimpleNamespace(text="💪 Добавить еще подход", from_user=SimpleNamespace(id=12345), bot=SimpleNamespace(menu_stack=[]), answer=AsyncMock())
    state = SimpleNamespace(
        get_data=AsyncMock(return_value={"exercise": "Тяга штанги в наклоне", "variant": "reps", "working_weight": 30.0, "entry_date": "2026-06-03"}),
        update_data=AsyncMock(),
        set_state=AsyncMock(),
    )

    asyncio.run(workouts.handle_count_input(message, state))

    text = message.answer.await_args.args[0]
    assert "⚖️ Общий вес штанги: 30 кг" in text
    assert "Выбери количество повторений" in text


def test_hammer_curl_is_gym_dumbbell_exercise_and_asks_one_dumbbell_weight():
    callback = SimpleNamespace(
        data=f"wrk_pick:{workouts._activity_id('Молот на бицепс')}",
        message=SimpleNamespace(bot=SimpleNamespace(menu_stack=[]), answer=AsyncMock()),
        answer=AsyncMock(),
    )
    state = SimpleNamespace(update_data=AsyncMock(), set_state=AsyncMock())

    asyncio.run(workouts.pick_catalog_exercise(callback, state))

    state.set_state.assert_any_await(workouts.WorkoutStates.entering_working_weight)
    text = callback.message.answer.await_args.args[0]
    assert "🏋️ Молот на бицепс" in text
    assert "1️⃣ Укажи вес одной гантели" in text


def test_hammer_curl_weight_selection_saves_single_dumbbell_weight_and_reps_prompt():
    message = SimpleNamespace(text="30 кг", bot=SimpleNamespace(menu_stack=[]), answer=AsyncMock())
    state = SimpleNamespace(
        get_data=AsyncMock(return_value={"exercise": "Молот на бицепс"}),
        update_data=AsyncMock(),
        set_state=AsyncMock(),
    )

    asyncio.run(workouts.handle_working_weight_input(message, state))

    state.update_data.assert_any_await(working_weight=30.0)
    text = message.answer.await_args.args[0]
    assert "⚖️ Вес одной гантели: 30 кг" in text
    assert "2️⃣ Выбери количество повторений" in text


def test_hammer_curl_add_another_set_reuses_one_dumbbell_weight():
    message = SimpleNamespace(text="💪 Добавить еще подход", from_user=SimpleNamespace(id=12345), bot=SimpleNamespace(menu_stack=[]), answer=AsyncMock())
    state = SimpleNamespace(
        get_data=AsyncMock(return_value={"exercise": "Молот на бицепс", "variant": "reps", "working_weight": 30.0, "entry_date": "2026-06-03"}),
        update_data=AsyncMock(),
        set_state=AsyncMock(),
    )

    asyncio.run(workouts.handle_count_input(message, state))

    text = message.answer.await_args.args[0]
    assert "⚖️ Вес одной гантели: 30 кг" in text
    assert "Выбери количество повторений" in text
