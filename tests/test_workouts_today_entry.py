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


def test_gym_category_main_list_is_sorted_by_russian_alphabet():
    gym_exercises = sorted([
        workouts._normalize_exercise_name(ex)
        for ex in workouts.ACTIVITY_CATEGORIES["gym"]["activities"]
    ], key=workouts._russian_sort_key)

    page_items, page = workouts._paginate(gym_exercises, 0)

    assert page == 0
    assert page_items == sorted(page_items, key=workouts._russian_sort_key)


def test_gym_category_next_page_callback_opens_second_page():
    callback = SimpleNamespace(
        data="wrk_cat_page:gym:1",
        message=SimpleNamespace(bot=SimpleNamespace(menu_stack=[]), answer=AsyncMock()),
        from_user=SimpleNamespace(id=12345),
        answer=AsyncMock(),
    )
    state = SimpleNamespace(update_data=AsyncMock())

    asyncio.run(workouts.paginate_category_exercises(callback, state))

    callback.answer.assert_awaited_once()
    state.update_data.assert_awaited_once_with(add_activity_screen="category", category_id="gym", category_page=1)
    text = callback.message.answer.await_args.args[0]
    assert "🏋️ Тренажерный зал" in text
    button_rows = [[button.text for button in row] for row in callback.message.answer.await_args.kwargs["reply_markup"].inline_keyboard]
    assert ["⬅️ Предыдущая", "2/3", "Следующая ➡️"] in button_rows
    assert any("Разведения гантелей" in row for row in button_rows)


def test_gym_category_sets_search_back_reply_keyboard_and_recent_buttons():
    message = SimpleNamespace(
        from_user=SimpleNamespace(id=12345),
        bot=SimpleNamespace(menu_stack=[]),
        answer=AsyncMock(),
    )
    state = SimpleNamespace(update_data=AsyncMock())

    with patch("handlers.workouts._get_recent_exercises", return_value=["Жим штанги лёжа", "Жим штанги лёжа", "Бег", "Молот на бицепс"]):
        asyncio.run(workouts._show_category(message, state, "gym", user_id="12345", send_reply_keyboard=True))

    reply_call = message.answer.await_args_list[0]
    assert [[button.text for button in row] for row in reply_call.kwargs["reply_markup"].keyboard] == [
        ["🔍 Поиск упражнения"],
        ["⬅️ Назад"],
    ]
    list_call = message.answer.await_args_list[1]
    rows = [[button.text for button in row] for row in list_call.kwargs["reply_markup"].inline_keyboard]
    assert rows[0] == ["⭐ Жим штанги лёжа"]
    assert rows[1] == ["⭐ Молот на бицепс"]
    assert all("Бег" not in row[0] for row in rows)
    assert all("🔍 Поиск упражнения" not in row[0] for row in rows)


def test_pagination_first_and_last_page_hide_unavailable_directions():
    first = workouts._build_activity_inline(["Бег"], "wrk_search_page", 0, 3)
    last = workouts._build_activity_inline(["Йога"], "wrk_search_page", 2, 3)

    assert [button.text for button in first.inline_keyboard[-1]] == ["1/3", "Следующая ➡️"]
    assert [button.text for button in last.inline_keyboard[-1]] == ["⬅️ Предыдущая", "3/3"]


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
    assert "🏋️ Укажи рабочий вес упражнения" in text
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
    assert "⚖️ Рабочий вес: 30 кг" in text
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
    assert "⚖️ Рабочий вес: 30 кг" in text
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
    assert "🏋️ Укажи рабочий вес упражнения" in text


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
    assert "⚖️ Рабочий вес: 30 кг" in text
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
    assert "⚖️ Рабочий вес: 30 кг" in text
    assert "Выбери количество повторений" in text


def test_dumbbell_military_press_is_gym_exercise_with_stable_slug():
    exercise = "Армейский жим с гантелями"

    assert exercise in workouts.ACTIVITY_CATEGORIES["gym"]["activities"]
    assert workouts._is_gym_exercise(exercise)
    assert workouts._activity_id(exercise) == "dumbbell_military_press"
    assert workouts._activity_by_id("dumbbell_military_press") == exercise


def test_dumbbell_military_press_search_synonyms_and_deduplication():
    queries = ["  армейский   жим  ", "жим гантелей стоя", "shoulder press"]

    for query in queries:
        matches = [
            ex for ex in sorted(workouts._all_catalog_exercises(), key=workouts._russian_sort_key)
            if any(workouts._search_key(query) in workouts._search_key(token) for token in workouts._exercise_search_tokens(ex))
        ]
        assert "Армейский жим с гантелями" in matches

    multi_synonym_matches = [
        ex for ex in sorted(workouts._all_catalog_exercises(), key=workouts._russian_sort_key)
        if any(workouts._search_key("жим") in workouts._search_key(token) for token in workouts._exercise_search_tokens(ex))
    ]
    assert multi_synonym_matches.count("Армейский жим с гантелями") == 1


def test_dumbbell_military_press_is_under_a_in_sorted_gym_list():
    gym_exercises = sorted([
        workouts._normalize_exercise_name(ex)
        for ex in workouts.ACTIVITY_CATEGORIES["gym"]["activities"]
    ], key=workouts._russian_sort_key)

    assert gym_exercises[0] == "Армейский жим с гантелями"


def test_dumbbell_military_press_appears_in_recent_after_use():
    entry = SimpleNamespace(exercise="Армейский жим с гантелями", variant="reps")

    with patch("handlers.workouts.WorkoutRepository.get_workouts_for_period", return_value=[entry]):
        assert workouts._get_recent_exercises("12345") == ["Армейский жим с гантелями"]


def test_dumbbell_military_press_flow_asks_weight_first_with_one_dumbbell_hint():
    callback = SimpleNamespace(
        data="wrk_pick:dumbbell_military_press",
        message=SimpleNamespace(bot=SimpleNamespace(menu_stack=[]), answer=AsyncMock()),
        answer=AsyncMock(),
    )
    state = SimpleNamespace(update_data=AsyncMock(), set_state=AsyncMock())

    asyncio.run(workouts.pick_catalog_exercise(callback, state))

    state.set_state.assert_any_await(workouts.WorkoutStates.entering_working_weight)
    text = callback.message.answer.await_args.args[0]
    assert "🏋️ Армейский жим с гантелями" in text
    assert "🏋️ Укажи рабочий вес упражнения" in text
    assert "🏋️ Укажи рабочий вес упражнения:" in text


def test_dumbbell_military_press_weight_then_reps_and_next_set_reuse_weight():
    message = SimpleNamespace(text="15 кг", bot=SimpleNamespace(menu_stack=[]), answer=AsyncMock())
    state = SimpleNamespace(
        get_data=AsyncMock(return_value={"exercise": "Армейский жим с гантелями"}),
        update_data=AsyncMock(),
        set_state=AsyncMock(),
    )

    asyncio.run(workouts.handle_working_weight_input(message, state))

    state.update_data.assert_any_await(working_weight=15.0)
    state.set_state.assert_any_await(workouts.WorkoutStates.entering_count)
    text = message.answer.await_args.args[0]
    assert "⚖️ Рабочий вес: 15 кг" in text
    assert "2️⃣ Выбери количество повторений" in text

    next_message = SimpleNamespace(text="💪 Добавить еще подход", from_user=SimpleNamespace(id=12345), bot=SimpleNamespace(menu_stack=[]), answer=AsyncMock())
    next_state = SimpleNamespace(
        get_data=AsyncMock(return_value={"exercise": "Армейский жим с гантелями", "variant": "reps", "working_weight": 15.0, "entry_date": "2026-06-03"}),
        update_data=AsyncMock(),
        set_state=AsyncMock(),
    )

    asyncio.run(workouts.handle_count_input(next_message, next_state))

    assert "⚖️ Рабочий вес: 15 кг" in next_message.answer.await_args.args[0]


def test_dumbbell_military_press_saved_sets_display_one_dumbbell_weight():
    activities = [
        SimpleNamespace(exercise="Армейский жим с гантелями", variant="reps", count=10, calories=12, input_method="repetitions", working_weight=15.0),
        SimpleNamespace(exercise="Армейский жим с гантелями", variant="reps", count=8, calories=10, input_method="repetitions", working_weight=15.0),
    ]

    summaries = workouts.format_activity_daily_summaries(activities, "12345")

    assert summaries == [
        "Армейский жим с гантелями — 10 раз, 15 кг (~12 ккал)",
        "Армейский жим с гантелями — 8 раз, 15 кг (~10 ккал)",
    ]


def test_dumbbell_military_press_catalog_initialization_is_idempotent():
    all_exercises = workouts._all_catalog_exercises()

    assert all_exercises.count("Армейский жим с гантелями") == 1
    assert workouts.ACTIVITY_CATEGORIES["gym"]["activities"].count("Армейский жим с гантелями") == 1


def _search_matches(query: str) -> list[str]:
    return [
        ex for ex in sorted(workouts._all_catalog_exercises(), key=workouts._russian_sort_key)
        if any(workouts._search_key(query) in workouts._search_key(token) for token in workouts._exercise_search_tokens(ex))
    ]


def test_forearm_dumbbell_exercises_are_gym_exercises_with_stable_slugs():
    flexion = "Сгибания кистей с гантелями"
    extension = "Разгибания кистей с гантелями"

    assert flexion in workouts.ACTIVITY_CATEGORIES["gym"]["activities"]
    assert extension in workouts.ACTIVITY_CATEGORIES["gym"]["activities"]
    assert workouts._is_gym_exercise(flexion)
    assert workouts._is_gym_exercise(extension)
    assert workouts._activity_id(flexion) == "dumbbell_wrist_curl"
    assert workouts._activity_id(extension) == "dumbbell_reverse_wrist_curl"
    assert workouts._activity_by_id("dumbbell_wrist_curl") == flexion
    assert workouts._activity_by_id("dumbbell_reverse_wrist_curl") == extension


def test_forearm_dumbbell_exercises_search_synonyms_and_deduplication():
    assert "Сгибания кистей с гантелями" in _search_matches("  сгибания   кистей  ")
    assert "Сгибания кистей с гантелями" in _search_matches("wrist curl")
    assert "Разгибания кистей с гантелями" in _search_matches("разгибания кистей")
    assert "Разгибания кистей с гантелями" in _search_matches("reverse wrist curl")

    wrist_matches = _search_matches("кисти")
    assert "Сгибания кистей с гантелями" in wrist_matches
    assert "Разгибания кистей с гантелями" in wrist_matches
    assert len(wrist_matches) == len(set(wrist_matches))


def test_forearm_dumbbell_exercises_appear_in_sorted_gym_list():
    gym_exercises = sorted([
        workouts._normalize_exercise_name(ex)
        for ex in workouts.ACTIVITY_CATEGORIES["gym"]["activities"]
    ], key=workouts._russian_sort_key)

    assert "Сгибания кистей с гантелями" in gym_exercises
    assert "Разгибания кистей с гантелями" in gym_exercises


def test_forearm_dumbbell_exercises_appear_in_recent_after_use():
    entries = [
        SimpleNamespace(exercise="Разгибания кистей с гантелями", variant="reps"),
        SimpleNamespace(exercise="Сгибания кистей с гантелями", variant="reps"),
    ]

    with patch("handlers.workouts.WorkoutRepository.get_workouts_for_period", return_value=entries):
        recent = workouts._get_recent_exercises("12345")

    assert recent == ["Сгибания кистей с гантелями", "Разгибания кистей с гантелями"]


def test_forearm_dumbbell_exercise_flow_asks_weight_first_with_one_dumbbell_hint():
    callback = SimpleNamespace(
        data="wrk_pick:dumbbell_wrist_curl",
        message=SimpleNamespace(bot=SimpleNamespace(menu_stack=[]), answer=AsyncMock()),
        answer=AsyncMock(),
    )
    state = SimpleNamespace(update_data=AsyncMock(), set_state=AsyncMock())

    asyncio.run(workouts.pick_catalog_exercise(callback, state))

    state.set_state.assert_any_await(workouts.WorkoutStates.entering_working_weight)
    text = callback.message.answer.await_args.args[0]
    assert "🏋️ Сгибания кистей с гантелями" in text
    assert "🏋️ Укажи рабочий вес упражнения" in text

    message = SimpleNamespace(text="8 кг", bot=SimpleNamespace(menu_stack=[]), answer=AsyncMock())
    next_state = SimpleNamespace(
        get_data=AsyncMock(return_value={"exercise": "Сгибания кистей с гантелями"}),
        update_data=AsyncMock(),
        set_state=AsyncMock(),
    )

    asyncio.run(workouts.handle_working_weight_input(message, next_state))

    next_state.update_data.assert_any_await(working_weight=8.0)
    next_state.set_state.assert_any_await(workouts.WorkoutStates.entering_count)
    assert "⚖️ Рабочий вес: 8 кг" in message.answer.await_args.args[0]
    assert "2️⃣ Выбери количество повторений" in message.answer.await_args.args[0]


def test_forearm_dumbbell_next_set_reuses_weight_and_summary_displays_sets():
    message = SimpleNamespace(text="💪 Добавить еще подход", from_user=SimpleNamespace(id=12345), bot=SimpleNamespace(menu_stack=[]), answer=AsyncMock())
    state = SimpleNamespace(
        get_data=AsyncMock(return_value={"exercise": "Разгибания кистей с гантелями", "variant": "reps", "working_weight": 5.0, "entry_date": "2026-06-03"}),
        update_data=AsyncMock(),
        set_state=AsyncMock(),
    )

    asyncio.run(workouts.handle_count_input(message, state))

    assert "⚖️ Рабочий вес: 5 кг" in message.answer.await_args.args[0]
    summaries = workouts.format_activity_daily_summaries([
        SimpleNamespace(exercise="Разгибания кистей с гантелями", variant="reps", count=15, calories=7, input_method="repetitions", working_weight=5.0),
        SimpleNamespace(exercise="Разгибания кистей с гантелями", variant="reps", count=12, calories=6, input_method="repetitions", working_weight=5.0),
        SimpleNamespace(exercise="Разгибания кистей с гантелями", variant="reps", count=10, calories=5, input_method="repetitions", working_weight=5.0),
    ], "12345")

    assert summaries == [
        "Разгибания кистей с гантелями — 15 раз, 5 кг (~7 ккал)",
        "Разгибания кистей с гантелями — 12 раз, 5 кг (~6 ккал)",
        "Разгибания кистей с гантелями — 10 раз, 5 кг (~5 ккал)",
    ]


def test_forearm_dumbbell_catalog_initialization_is_idempotent():
    all_exercises = workouts._all_catalog_exercises()

    assert all_exercises.count("Сгибания кистей с гантелями") == 1
    assert all_exercises.count("Разгибания кистей с гантелями") == 1
    assert workouts.ACTIVITY_CATEGORIES["gym"]["activities"].count("Сгибания кистей с гантелями") == 1
    assert workouts.ACTIVITY_CATEGORIES["gym"]["activities"].count("Разгибания кистей с гантелями") == 1
