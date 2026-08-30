from __future__ import annotations

import asyncio
from contextlib import contextmanager
from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import SendMessage
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import Message
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from database.models import Base
import database.repositories.activity_repository as repository_module
from database.repositories.activity_repository import ActivityRepository
from handlers import activity_tracking
from states.user_states import ActivityTrackingStates
from services.activity_energy_service import (
    ActivityValidationError,
    calculate_met_energy,
    calculate_steps_energy,
    calculate_timed_activity_energy,
    calculate_workout_energy,
    estimate_workout_duration_seconds,
    summarize_daily_activity_energy,
)
from utils.activity_catalog import (
    EXERCISE_BY_CODE,
    EXERCISES,
    TIMED_ACTIVITIES,
    TIMED_ACTIVITY_BY_CODE,
    WORKOUT_INTENSITY_METS,
    WORKOUT_INTENSITY_LABELS,
)
from handlers.activity_tracking import (
    ACTIVITY_BUTTON_ALIASES,
    WORKOUT_BACK_TO_ACTIVITY,
    _exercise_categories_keyboard,
    _exercise_list_keyboard,
    _exercise_search_matches,
    _format_set,
    _legacy_workout_actions_text,
    _load_prompt,
    _show_legacy_workout_actions,
    _show_repetitions_input,
    _timed_search_matches,
    format_activity_overview,
    receive_set_repetitions,
    start_workout,
)
from utils.keyboards import (
    TRAINING_BUTTON_TEXT,
    WORKOUT_BUTTON_TEXT,
    add_another_set_menu,
    count_menu,
    training_menu,
)


@pytest.fixture()
def activity_store(monkeypatch):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)

    @contextmanager
    def get_test_session():
        session = session_factory()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    monkeypatch.setattr(repository_module, "get_db_session", get_test_session)
    ActivityRepository.seed_catalog()
    yield
    engine.dispose()


def test_met_formula_stores_gross_and_only_net_is_credited():
    estimate = calculate_met_energy(met=5, weight_kg=70, duration_minutes=30)

    assert estimate.gross_calories == pytest.approx(183.75)
    assert estimate.credited_calories == pytest.approx(147.0)


def test_workout_intensity_profile_is_centralized():
    assert WORKOUT_INTENSITY_METS == {"light": 3.5, "moderate": 5.0, "high": 6.0}
    calm = calculate_workout_energy(intensity="light", weight_kg=70, duration_seconds=3600)
    intense = calculate_workout_energy(intensity="high", weight_kg=70, duration_seconds=3600)

    assert intense.gross_calories > calm.gross_calories
    assert intense.credited_calories > calm.credited_calories


def test_first_version_asks_only_the_three_agreed_workout_intensities():
    assert WORKOUT_INTENSITY_LABELS == {
        "light": "🙂 Спокойно",
        "moderate": "💪 Обычно",
        "high": "🔥 Интенсивно",
    }


def test_main_activity_navigation_uses_new_russian_process_names():
    assert TRAINING_BUTTON_TEXT == "🏃 Активность"
    labels = [button.text for row in training_menu.keyboard for button in row]
    assert labels[:3] == [
        "⏱ Активность по времени",
        "🏋️ Тренировка",
        "📅 Календарь активности",
    ]
    assert "⚡ Быстрое упражнение" not in labels


def test_workout_button_is_not_handled_as_activity_section_entry():
    assert WORKOUT_BUTTON_TEXT == "🏋️ Тренировка"
    assert WORKOUT_BUTTON_TEXT not in ACTIVITY_BUTTON_ALIASES


def test_start_workout_creates_draft_and_opens_categories(activity_store):
    message = SimpleNamespace(
        from_user=SimpleNamespace(id=1), bot=SimpleNamespace(),
        answer=AsyncMock(),
    )
    state = SimpleNamespace(clear=AsyncMock(), update_data=AsyncMock())
    with patch("handlers.activity_tracking.WeightRepository.get_last_weight", return_value=70):
        asyncio.run(start_workout(message, state))

    draft = ActivityRepository.get_workout_draft("1")
    assert draft is not None
    assert draft.weight_kg_snapshot == 70
    assert "Выбери категорию упражнения" in message.answer.await_args.args[0]
    keyboard = message.answer.await_args.kwargs["reply_markup"]
    assert keyboard.inline_keyboard[-1][0].callback_data == "act:workout_start_back"
    assert all(len(button.callback_data.encode("utf-8")) <= 64 for row in keyboard.inline_keyboard for button in row)


@pytest.mark.parametrize("previous_state", [None, ActivityTrackingStates.entering_timed_duration])
def test_workout_reply_button_opens_workout_even_with_unfinished_input(activity_store, previous_state):
    async def scenario():
        bot = Bot("12345:test-token")
        storage = MemoryStorage()
        state = FSMContext(storage, StorageKey(bot.id, 1, 1))
        await state.set_state(previous_state)
        await state.update_data(activity_code="running", entry_date=date.today().isoformat())
        message = Message.model_validate({
            "message_id": 1, "date": 0, "chat": {"id": 1, "type": "private"},
            "from": {"id": 1, "is_bot": False, "first_name": "Test"},
            "text": WORKOUT_BUTTON_TEXT,
        }, context={"bot": bot})
        with patch.object(Message, "answer", new_callable=AsyncMock) as answer, patch(
            "handlers.activity_tracking.WeightRepository.get_last_weight", return_value=70,
        ):
            await activity_tracking.router.propagate_event(
                "message", message, state=state, raw_state=await state.get_state(),
            )
        assert ActivityRepository.get_workout_draft("1") is not None
        assert "Выбери категорию упражнения" in answer.await_args.args[0]
        assert await state.get_state() is None
        await storage.close()
        await bot.session.close()

    asyncio.run(scenario())


def test_workout_opens_if_service_keyboard_message_is_rejected(activity_store):
    message = SimpleNamespace(
        from_user=SimpleNamespace(id=1), bot=SimpleNamespace(),
        answer=AsyncMock(side_effect=[
            TelegramBadRequest(method=SendMessage(chat_id=1, text="test"), message="Bad Request"),
            None,
        ]),
    )
    state = SimpleNamespace(clear=AsyncMock(), update_data=AsyncMock())
    with patch("handlers.activity_tracking.WeightRepository.get_last_weight", return_value=70):
        asyncio.run(start_workout(message, state))
    assert "Выбери категорию упражнения" in message.answer.await_args.args[0]
    assert ActivityRepository.get_workout_draft("1") is not None


def test_reentering_empty_workout_reuses_draft_and_back_restores_activity(activity_store):
    message = SimpleNamespace(from_user=SimpleNamespace(id=1), bot=SimpleNamespace(), answer=AsyncMock())
    state = SimpleNamespace(clear=AsyncMock(), update_data=AsyncMock())
    with patch("handlers.activity_tracking.WeightRepository.get_last_weight", return_value=70):
        asyncio.run(start_workout(message, state))
        draft_id = ActivityRepository.get_workout_draft("1").id
        asyncio.run(start_workout(message, state))
    assert ActivityRepository.get_workout_draft("1").id == draft_id
    callback = SimpleNamespace(from_user=message.from_user, message=message, answer=AsyncMock())
    with patch("handlers.activity_tracking._replace_with_activity_main", new_callable=AsyncMock) as show_main:
        asyncio.run(activity_tracking.leave_new_workout(callback, state))
    assert ActivityRepository.get_workout_draft("1") is None
    show_main.assert_awaited_once_with(message, "1", date.today())


def test_timed_and_exercise_search_find_russian_names():
    assert [item.code for item in _timed_search_matches("бокс")][:3] == [
        "boxing", "kickboxing", "muay_thai",
    ]
    assert "barbell_bench_press" in {item.code for item in _exercise_search_matches("жим штанги")}
    category_labels = [button.text for row in _exercise_categories_keyboard().inline_keyboard for button in row]
    assert category_labels[0] == "🔎 Поиск упражнения"


def test_new_workout_back_returns_to_activity_and_keeps_origin_through_categories():
    categories = _exercise_categories_keyboard(
        back_target=WORKOUT_BACK_TO_ACTIVITY,
    )
    assert categories.inline_keyboard[-1][0].text == "⬅️ В раздел активности"
    assert categories.inline_keyboard[-1][0].callback_data == "act:workout_start_back"
    assert categories.inline_keyboard[1][0].callback_data.endswith(":activity")

    exercises = _exercise_list_keyboard(
        "workout", "bodyweight", 0, back_target=WORKOUT_BACK_TO_ACTIVITY,
    )
    assert exercises.inline_keyboard[-1][0].callback_data == (
        "act:ecategories:workout:activity"
    )


def test_adding_exercise_to_existing_workout_returns_to_draft():
    categories = _exercise_categories_keyboard()
    assert categories.inline_keyboard[-1][0].text == "⬅️ К тренировке"
    assert categories.inline_keyboard[-1][0].callback_data == "act:workout_draft"


def test_workout_repetitions_restore_previous_large_reply_keyboard():
    config = EXERCISE_BY_CODE["pushups"]
    message = SimpleNamespace(
        bot=SimpleNamespace(menu_stack=[]),
        answer=AsyncMock(),
    )
    state = SimpleNamespace(set_state=AsyncMock())

    asyncio.run(_show_repetitions_input(message, state, config))

    call = message.answer.await_args
    assert "Выбери количество повторений" in call.args[0]
    assert call.kwargs["reply_markup"] is count_menu
    assert [[button.text for button in row] for row in count_menu.keyboard[:2]] == [
        ["1", "2", "3", "4", "5"],
        ["6", "7", "8", "9", "10"],
    ]


def test_saved_set_restores_previous_confirmation_and_reply_actions(activity_store):
    session = ActivityRepository.create_workout_session(
        user_id="1", entry_date=date.today(), weight_kg=70, weight_source="profile",
    )
    config = EXERCISE_BY_CODE["pushups"]
    exercise = ActivityRepository.add_session_exercise(
        session_id=session.id,
        user_id="1",
        exercise_code=config.code,
        exercise_name=config.name,
        measurement_type=config.measurement_type,
        load_input_mode=config.load_input_mode,
        tempo_seconds_per_rep=config.tempo_seconds_per_rep,
    )
    ActivityRepository.add_workout_set(
        session_id=session.id,
        session_exercise_id=exercise.id,
        user_id="1",
        repetitions=15,
    )

    text = _legacy_workout_actions_text(session, "1")
    assert "✅ Записал! 👍" in text
    assert "🏋️ <b>Отжимания</b>" in text
    assert "🔁 15 раз" in text
    assert "Хотите внести еще подход?" in text

    message = SimpleNamespace(
        bot=SimpleNamespace(menu_stack=[]),
        answer=AsyncMock(),
    )
    asyncio.run(_show_legacy_workout_actions(message, "1"))
    assert message.answer.await_args.kwargs["reply_markup"] is add_another_set_menu


def test_repetition_save_uses_previous_confirmation_in_active_flow(activity_store):
    session = ActivityRepository.create_workout_session(
        user_id="1", entry_date=date.today(), weight_kg=70, weight_source="profile",
    )
    config = EXERCISE_BY_CODE["pushups"]
    exercise = ActivityRepository.add_session_exercise(
        session_id=session.id,
        user_id="1",
        exercise_code=config.code,
        exercise_name=config.name,
        measurement_type=config.measurement_type,
        load_input_mode=config.load_input_mode,
        tempo_seconds_per_rep=config.tempo_seconds_per_rep,
    )
    state = SimpleNamespace(
        get_data=AsyncMock(return_value={
            "session_id": session.id,
            "session_exercise_id": exercise.id,
            "exercise_code": config.code,
            "load_kg": None,
            "load_kind": None,
        }),
        clear=AsyncMock(),
    )
    message = SimpleNamespace(
        text="15",
        from_user=SimpleNamespace(id=1),
        bot=SimpleNamespace(menu_stack=[]),
        answer=AsyncMock(),
    )

    asyncio.run(receive_set_repetitions(message, state))

    state.clear.assert_awaited_once()
    assert ActivityRepository.get_session_sets(session.id, "1")[0].repetitions == 15
    call = message.answer.await_args
    assert "✅ Записал! 👍" in call.args[0]
    assert call.kwargs["reply_markup"] is add_another_set_menu

    asyncio.run(start_workout(message, state))
    assert ActivityRepository.get_workout_draft("1").id == session.id
    saved_sets = ActivityRepository.get_session_sets(session.id, "1")
    assert len(saved_sets) == 1
    assert saved_sets[0].repetitions == 15
    assert "Продолжаем незавершённую тренировку" in message.answer.await_args.args[0]
    assert message.answer.await_args.kwargs["reply_markup"] is add_another_set_menu


def test_back_from_activity_calendar_renders_activity_overview():
    from handlers import common
    from utils.keyboards import calendar_back_menu, main_menu

    message = SimpleNamespace(
        from_user=SimpleNamespace(id=12345),
        bot=SimpleNamespace(menu_stack=[main_menu, training_menu, calendar_back_menu]),
        answer=AsyncMock(),
    )
    state = SimpleNamespace(clear=AsyncMock())

    with patch(
        "handlers.activity_tracking.show_activity_main", new=AsyncMock(),
    ) as show_activity:
        asyncio.run(common.go_back(message, state))

    state.clear.assert_awaited_once()
    show_activity.assert_awaited_once_with(message, "12345")
    message.answer.assert_not_awaited()
    assert message.bot.menu_stack == [main_menu, training_menu]


def test_timed_catalog_is_russian_and_uses_stable_unique_codes():
    assert len(TIMED_ACTIVITIES) >= 100
    assert len({item.code for item in TIMED_ACTIVITIES}) == len(TIMED_ACTIVITIES)
    assert TIMED_ACTIVITY_BY_CODE["hiking"].name == "Пеший поход"
    assert all("outdoor" not in item.name.casefold() for item in TIMED_ACTIVITIES)
    assert all(item.code.isascii() for item in TIMED_ACTIVITIES)


def test_database_has_separate_activity_and_exercise_category_tables(activity_store):
    assert "activity_categories" in Base.metadata.tables
    assert "exercise_categories" in Base.metadata.tables
    assert "kind" not in Base.metadata.tables["activity_categories"].columns
    assert ActivityRepository.seed_catalog() is None


def test_timed_activity_can_have_one_or_three_intensity_levels():
    assert TIMED_ACTIVITY_BY_CODE["walking_easy"].intensity_mets == {"moderate": 2.5}
    assert list(TIMED_ACTIVITY_BY_CODE["running_general"].intensity_mets) == [
        "light", "moderate", "high",
    ]


def test_generated_callbacks_use_codes_and_fit_telegram_limit():
    callbacks = [f"act:tpick:{item.code}" for item in TIMED_ACTIVITIES]
    callbacks.extend(f"act:epick:workout:{item.code}" for item in EXERCISES)

    assert all(item.code not in item.name for item in TIMED_ACTIVITIES)
    assert all(len(value.encode("utf-8")) <= 64 for value in callbacks)


def test_exercise_catalog_removes_incorrect_deadlift_and_abstract_abs():
    names = {item.name for item in EXERCISES}

    assert "Становая тяга без утяжелителя" not in names
    assert "Румынская тяга без утяжелителя" not in names
    assert "Пресс" not in names
    assert EXERCISE_BY_CODE["deadlift"].name == "Становая тяга"
    assert EXERCISE_BY_CODE["deadlift"].category_code == "free_weights"
    assert EXERCISE_BY_CODE["pullups"].load_input_mode == "optional"
    assert EXERCISE_BY_CODE["farmers_walk"].measurement_type == "load_duration_distance"


def test_exercise_measurements_cover_bodyweight_free_weight_and_time():
    assert EXERCISE_BY_CODE["pushups"].measurement_type == "repetitions"
    assert EXERCISE_BY_CODE["barbell_bench_press"].measurement_type == "repetitions_load"
    assert EXERCISE_BY_CODE["barbell_bench_press"].load_input_mode == "total"
    assert EXERCISE_BY_CODE["dumbbell_bench_press"].load_input_mode == "per_item"
    assert EXERCISE_BY_CODE["plank"].measurement_type == "duration"


def test_weight_prompts_preserve_equipment_specific_input_rules():
    assert "включая гриф" in _load_prompt(EXERCISE_BY_CODE["barbell_bench_press"])
    assert "одной гантели" in _load_prompt(EXERCISE_BY_CODE["dumbbell_bench_press"])
    assert "стека" in _load_prompt(EXERCISE_BY_CODE["leg_press"])


def test_activity_validation_rejects_non_finite_and_unrealistic_values():
    with pytest.raises(ActivityValidationError):
        calculate_met_energy(met=5, weight_kg=float("nan"), duration_minutes=30)
    with pytest.raises(ActivityValidationError):
        calculate_steps_energy(steps=200001, weight_kg=70)
    with pytest.raises(ActivityValidationError):
        calculate_timed_activity_energy(
            activity_code="running_general", intensity="moderate",
            weight_kg=70, duration_minutes=0,
        )


def test_steps_overlap_is_subtracted_from_daily_summary():
    walking = SimpleNamespace(
        activity_code="walking_brisk",
        duration_minutes=30,
        gross_calories=120,
        credited_calories=90,
    )
    steps = SimpleNamespace(steps=8000, gross_calories=320, credited_calories=320)

    summary = summarize_daily_activity_energy(
        timed_entries=[walking], workout_sessions=[], daily_steps=steps,
    )

    assert summary.overlapping_steps == 3300
    assert summary.residual_steps == 4700
    assert summary.steps_gross_calories == pytest.approx(188)
    assert summary.gross_calories == pytest.approx(308)


def test_estimated_workout_duration_uses_tempo_and_rest():
    sets = [
        SimpleNamespace(repetitions=10, duration_seconds=None, exercise_code="pushups", session_exercise_id=1),
        SimpleNamespace(repetitions=10, duration_seconds=None, exercise_code="pushups", session_exercise_id=1),
        SimpleNamespace(repetitions=8, duration_seconds=None, exercise_code="pushups", session_exercise_id=1),
    ]

    assert estimate_workout_duration_seconds(sets) == 204


def test_estimated_workout_duration_does_not_count_set_rest_across_exercises_twice():
    sets = [
        SimpleNamespace(repetitions=10, duration_seconds=None, distance_meters=None, exercise_code="pushups", session_exercise_id=1),
        SimpleNamespace(repetitions=10, duration_seconds=None, distance_meters=None, exercise_code="pushups", session_exercise_id=1),
        SimpleNamespace(repetitions=10, duration_seconds=None, distance_meters=None, exercise_code="bodyweight_squats", session_exercise_id=2),
    ]

    # 90 сек работы + 60 сек между подходами + 90 сек на смену упражнения.
    assert estimate_workout_duration_seconds(sets) == 240


def test_repository_preserves_snapshots_and_catalog_seeding_is_idempotent(activity_store):
    ActivityRepository.seed_catalog()
    energy = calculate_timed_activity_energy(
        activity_code="swimming_general", intensity="moderate",
        weight_kg=73, duration_minutes=35,
    )
    row = ActivityRepository.save_timed_activity(
        user_id="1", activity_code="swimming_general", activity_name="Плавание",
        entry_date=date(2026, 8, 23), duration_minutes=35, intensity="moderate",
        met_value=energy.met_value, weight_kg=73, weight_source="profile",
        gross_calories=energy.gross_calories, credited_calories=energy.credited_calories,
    )

    loaded = ActivityRepository.get_timed_activity(row.id, "1")
    assert loaded.activity_code == "swimming_general"
    assert loaded.activity_name_snapshot == "Плавание"
    assert loaded.weight_kg_snapshot == 73
    assert loaded.met_value == 6.0
    assert loaded.calculation_version == "met-net-v1"

    # Последующее изменение веса профиля влияет только на новые расчёты.
    recalculated = calculate_timed_activity_energy(
        activity_code="swimming_general", intensity="moderate",
        weight_kg=90, duration_minutes=35,
    )
    unchanged = ActivityRepository.get_timed_activity(row.id, "1")
    assert unchanged.weight_kg_snapshot == 73
    assert unchanged.gross_calories == pytest.approx(energy.gross_calories)
    assert recalculated.gross_calories != pytest.approx(unchanged.gross_calories)


def test_recent_timed_activities_are_unique_and_crud_uses_snapshots(activity_store):
    def save(code: str, minutes: float):
        config = TIMED_ACTIVITY_BY_CODE[code]
        intensity = next(iter(config.intensity_mets))
        energy = calculate_timed_activity_energy(
            activity_code=code, intensity=intensity, weight_kg=70, duration_minutes=minutes,
        )
        return ActivityRepository.save_timed_activity(
            user_id="1", activity_code=code, activity_name=config.name,
            entry_date=date(2026, 8, 23), duration_minutes=minutes, intensity=intensity,
            met_value=energy.met_value, weight_kg=70, weight_source="profile",
            gross_calories=energy.gross_calories, credited_calories=energy.credited_calories,
        )

    first = save("walking_easy", 20)
    save("swimming_general", 30)
    save("walking_easy", 25)
    assert ActivityRepository.get_recent_timed_activity_codes("1")[:2] == [
        "walking_easy", "swimming_general",
    ]

    updated_energy = calculate_timed_activity_energy(
        activity_code="walking_easy", intensity="moderate", weight_kg=70, duration_minutes=40,
    )
    assert ActivityRepository.update_timed_activity(
        entry_id=first.id, user_id="1", duration_minutes=40, intensity="moderate",
        met_value=updated_energy.met_value, gross_calories=updated_energy.gross_calories,
        credited_calories=updated_energy.credited_calories,
    )
    assert ActivityRepository.get_timed_activity(first.id, "1").duration_minutes == 40
    assert ActivityRepository.delete_timed_activity(first.id, "1")
    assert ActivityRepository.get_timed_activity(first.id, "1") is None


def test_today_overview_restores_old_inline_actions_without_day_arrows(activity_store):
    config = TIMED_ACTIVITY_BY_CODE["boxing"]
    energy = calculate_timed_activity_energy(
        activity_code=config.code, intensity="moderate", weight_kg=70, duration_minutes=30,
    )
    ActivityRepository.save_timed_activity(
        user_id="1", activity_code=config.code, activity_name=config.name,
        entry_date=date.today(), duration_minutes=30, intensity="moderate",
        met_value=energy.met_value, weight_kg=70, weight_source="profile",
        gross_calories=energy.gross_calories, credited_calories=energy.credited_calories,
    )

    text, keyboard = format_activity_overview("1", date.today())
    labels = [button.text for row in keyboard.inline_keyboard for button in row]
    callbacks = [button.callback_data for row in keyboard.inline_keyboard for button in row]

    assert "Бокс — 30 мин" in text
    assert labels == ["✏️ Редактировать активность", "👣 Добавить шаги"]
    assert all("prev" not in callback and "next" not in callback for callback in callbacks)


def test_manual_workout_uses_estimated_duration_and_session_level_calories(activity_store):
    workout = ActivityRepository.create_workout_session(
        user_id="1", entry_date=date(2026, 8, 23), weight_kg=70,
        weight_source="profile",
    )
    config = EXERCISE_BY_CODE["pushups"]
    exercise = ActivityRepository.add_session_exercise(
        session_id=workout.id, user_id="1", exercise_code=config.code,
        exercise_name=config.name, measurement_type=config.measurement_type,
        load_input_mode=config.load_input_mode,
        tempo_seconds_per_rep=config.tempo_seconds_per_rep,
    )
    for repetitions in (10, 10, 8):
        ActivityRepository.add_workout_set(
            session_id=workout.id, session_exercise_id=exercise.id,
            user_id="1", repetitions=repetitions,
        )
    duration_seconds = estimate_workout_duration_seconds(
        ActivityRepository.get_session_sets(workout.id, "1")
    )
    assert duration_seconds == 204
    energy = calculate_workout_energy(
        intensity="moderate", weight_kg=workout.weight_kg_snapshot,
        duration_seconds=duration_seconds,
    )
    completed = ActivityRepository.finish_workout(
        session_id=workout.id, user_id="1", intensity="moderate",
        duration_seconds=duration_seconds,
        met_value=energy.met_value, gross_calories=energy.gross_calories,
        credited_calories=energy.credited_calories,
    )
    assert completed.status == "completed"
    assert completed.duration_source == "estimated"
    assert completed.duration_seconds == 204
    assert completed.exercise_count == 1
    assert completed.set_count == 3
    assert completed.training_volume_kg == 0


def test_repository_keeps_individual_sets_and_repeats_previous(activity_store):
    workout = ActivityRepository.create_workout_session(
        user_id="1", entry_date=date.today(), weight_kg=70,
        weight_source="profile",
    )
    config = EXERCISE_BY_CODE["barbell_bench_press"]
    exercise = ActivityRepository.add_session_exercise(
        session_id=workout.id, user_id="1", exercise_code=config.code,
        exercise_name=config.name, measurement_type=config.measurement_type,
        load_input_mode=config.load_input_mode,
        tempo_seconds_per_rep=config.tempo_seconds_per_rep,
    )
    first = ActivityRepository.add_workout_set(
        session_id=workout.id, session_exercise_id=exercise.id,
        user_id="1", repetitions=10, load_kg=60,
    )
    repeated = ActivityRepository.repeat_last_set(workout.id, "1")
    sets = ActivityRepository.get_session_sets(workout.id, "1")

    assert first.position == 1
    assert repeated.position == 2
    assert [(item.repetitions, item.load_kg) for item in sets] == [(10, 60), (10, 60)]

    duration_seconds = estimate_workout_duration_seconds(sets)
    energy = calculate_workout_energy(
        intensity="moderate", weight_kg=70, duration_seconds=duration_seconds,
    )
    completed = ActivityRepository.finish_workout(
        session_id=workout.id, user_id="1", intensity="moderate",
        duration_seconds=duration_seconds,
        met_value=energy.met_value, gross_calories=energy.gross_calories,
        credited_calories=energy.credited_calories,
    )
    assert (completed.exercise_count, completed.set_count, completed.training_volume_kg) == (1, 2, 1200)

    assert ActivityRepository.update_workout_set(
        set_id=repeated.id, user_id="1", repetitions=8, load_kg=62.5, update_load=True,
    )
    changed = ActivityRepository.get_workout_set(repeated.id, "1")
    assert (changed.repetitions, changed.load_kg) == (8, 62.5)
    assert ActivityRepository.get_workout_session(workout.id, "1").training_volume_kg == 1100
    assert ActivityRepository.delete_workout_set(first.id, "1")
    assert ActivityRepository.get_workout_set(first.id, "1") is None
    refreshed = ActivityRepository.get_workout_session(workout.id, "1")
    assert (refreshed.set_count, refreshed.training_volume_kg) == (1, 500)


def test_cancelled_set_input_does_not_leave_empty_exercise_in_draft(activity_store):
    workout = ActivityRepository.create_workout_session(
        user_id="1", entry_date=date.today(), weight_kg=70, weight_source="profile",
    )
    config = EXERCISE_BY_CODE["pushups"]
    ActivityRepository.add_session_exercise(
        session_id=workout.id, user_id="1", exercise_code=config.code,
        exercise_name=config.name, measurement_type=config.measurement_type,
        load_input_mode=config.load_input_mode,
        tempo_seconds_per_rep=config.tempo_seconds_per_rep,
    )

    assert ActivityRepository.remove_empty_session_exercises(workout.id, "1") == 1
    assert ActivityRepository.get_session_exercises(workout.id, "1") == []


def test_set_formatter_keeps_actual_units_only():
    assert _format_set(SimpleNamespace(
        repetitions=10, duration_seconds=None, distance_meters=None,
        load_kg=20, load_kind="working",
    ), "per_item") == "10 раз, 20 кг на снаряд"
    assert _format_set(SimpleNamespace(
        repetitions=None, duration_seconds=90, distance_meters=None,
        load_kg=None, load_kind=None,
    )) == "1 мин 30 сек"
    assert _format_set(SimpleNamespace(
        repetitions=None, duration_seconds=None, distance_meters=50,
        load_kg=24, load_kind="working",
    )) == "50 м, 24 кг"


def test_only_one_open_workout_is_allowed(activity_store):
    created = ActivityRepository.create_workout_session(
        user_id="1", entry_date=date.today(), weight_kg=70,
        weight_source="profile",
    )
    reopened = ActivityRepository.get_workout_draft("1")
    assert reopened.id == created.id
    assert reopened.status == "draft"

    with pytest.raises(repository_module.WorkoutDraftExistsError):
        ActivityRepository.create_workout_session(
            user_id="1", entry_date=date.today(), weight_kg=70,
            weight_source="profile",
        )
