"""Workout entry must work after upgrading an existing database."""
import asyncio
from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import database.session as session_module
from database.repositories import ActivityRepository
from handlers import activity_tracking
from utils.activity_catalog import EXERCISE_BY_CODE


@pytest.fixture(params=["timer", "manual", "fresh"])
def upgraded_workout_db(request, monkeypatch):
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )
    if request.param != "fresh":
        # Historical DDL: Python-side defaults did not create SQL defaults.
        legacy_columns = (
            "session_kind VARCHAR(24) NOT NULL, started_at DATETIME, ended_at DATETIME, "
            "paused_at DATETIME, paused_seconds INTEGER NOT NULL, "
            if request.param == "timer" else ""
        )
        with engine.begin() as connection:
            connection.execute(text(
                "CREATE TABLE workout_sessions ("
                "id INTEGER PRIMARY KEY, user_id VARCHAR NOT NULL, entry_date DATE NOT NULL, "
                "status VARCHAR(24) NOT NULL, " + legacy_columns +
                "duration_seconds INTEGER, duration_source VARCHAR(24) NOT NULL, "
                "intensity VARCHAR(16), met_value FLOAT, exercise_count INTEGER NOT NULL, "
                "set_count INTEGER NOT NULL, training_volume_kg FLOAT NOT NULL, "
                "weight_kg_snapshot FLOAT NOT NULL, weight_source VARCHAR(24) NOT NULL, "
                "gross_calories FLOAT NOT NULL, credited_calories FLOAT NOT NULL, "
                "calculation_version VARCHAR(32) NOT NULL, "
                "created_at DATETIME NOT NULL, updated_at DATETIME NOT NULL)"
            ))
    monkeypatch.setattr(session_module, "engine", engine)
    monkeypatch.setattr(session_module, "SessionLocal", sessionmaker(bind=engine, expire_on_commit=False))
    session_module.init_db()
    session_module.init_db()
    yield engine
    engine.dispose()


def test_workout_button_opens_after_database_upgrade_and_back_discards_only_empty_draft(upgraded_workout_db):
    async def scenario():
        message = SimpleNamespace(
            from_user=SimpleNamespace(id=42), bot=SimpleNamespace(), answer=AsyncMock(),
        )
        state = SimpleNamespace(clear=AsyncMock(), update_data=AsyncMock())
        await activity_tracking.start_workout(message, state)
        draft = ActivityRepository.get_workout_draft("42")
        assert draft is not None
        assert "Выбери категорию упражнения" in message.answer.await_args.args[0]

        await activity_tracking.start_workout(message, state)
        assert ActivityRepository.get_workout_draft("42").id == draft.id
        callback = SimpleNamespace(from_user=message.from_user, message=message, answer=AsyncMock())
        await activity_tracking.leave_new_workout(callback, state)
        assert ActivityRepository.get_workout_draft("42") is None
        assert message.answer.await_args.kwargs["reply_markup"] is activity_tracking.training_menu

    asyncio.run(scenario())


def test_migration_preserves_history_sets_and_resumes_old_unfinished_workouts(upgraded_workout_db):
    statuses = ("active", "paused", "awaiting_intensity", "completed", "cancelled", "draft")
    config = EXERCISE_BY_CODE["barbell_bench_press"]
    saved_sets = {}
    for user_number, status in enumerate(statuses, start=100):
        user_id = str(user_number)
        workout = ActivityRepository.create_workout_session(
            user_id=user_id, entry_date=date(2026, 8, 22), weight_kg=70, weight_source="profile",
        )
        exercise = ActivityRepository.add_session_exercise(
            session_id=workout.id, user_id=user_id, exercise_code=config.code,
            exercise_name=config.name, measurement_type=config.measurement_type,
            load_input_mode=config.load_input_mode, tempo_seconds_per_rep=config.tempo_seconds_per_rep,
        )
        saved_sets[user_id] = ActivityRepository.add_workout_set(
            session_id=workout.id, session_exercise_id=exercise.id, user_id=user_id,
            repetitions=10, load_kg=60, load_kind="working",
        ).id
        with upgraded_workout_db.begin() as connection:
            connection.execute(text(
                "UPDATE workout_sessions SET status = :status, session_kind = 'quick', "
                "paused_seconds = 90, duration_seconds = 900, duration_source = 'timer', "
                "gross_calories = 150, credited_calories = 120 WHERE id = :id"
            ), {"status": status, "id": workout.id})

    def snapshot():
        with upgraded_workout_db.connect() as connection:
            return {
                table: [dict(row) for row in connection.execute(text(f"SELECT * FROM {table} ORDER BY id")).mappings()]
                for table in ("workout_sessions", "workout_session_exercises", "workout_sets")
            }

    expected = snapshot()
    for row in expected["workout_sessions"]:
        if row["status"] in {"active", "paused", "awaiting_intensity"}:
            row["status"] = "draft"
    session_module.init_db()
    session_module.init_db()
    assert snapshot() == expected

    async def resume():
        for user_id in ("100", "101", "102", "105"):
            message = SimpleNamespace(
                from_user=SimpleNamespace(id=int(user_id)), bot=SimpleNamespace(), answer=AsyncMock(),
            )
            state = SimpleNamespace(clear=AsyncMock(), update_data=AsyncMock())
            await activity_tracking.start_workout(message, state)
            assert "Продолжаем незавершённую тренировку" in message.answer.await_args.args[0]
            draft = ActivityRepository.get_workout_draft(user_id)
            assert ActivityRepository.get_session_sets(draft.id, user_id)[0].id == saved_sets[user_id]

    asyncio.run(resume())
    completed = ActivityRepository.get_workout_sessions_for_day("103", date(2026, 8, 22))[0]
    report = activity_tracking._workout_detail_text(completed, "103")
    assert "15 мин" in report
    assert "150 ккал" in report
    assert "10 раз" in report and "60 кг" in report
    assert ActivityRepository.update_workout_set(set_id=saved_sets["103"], user_id="103", repetitions=12)
    assert ActivityRepository.get_workout_set(saved_sets["103"], "103").repetitions == 12
    assert ActivityRepository.get_workout_draft("103") is None
    assert ActivityRepository.get_workout_draft("104") is None
