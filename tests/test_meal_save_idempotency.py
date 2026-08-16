import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import date
import os
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest
from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import sessionmaker

os.environ.setdefault("API_TOKEN", "test-token")
os.environ.setdefault("GEMINI_API_KEY", "test-gemini-key")

from database.models import Base, Meal
from database.repositories.analytics_repository import AnalyticsRepository
from database.repositories.meal_repository import (
    MealRepository,
    MealSaveStatus,
)
import database.repositories.meal_repository as meal_repository_module
from handlers import meals


def _token(character: str) -> str:
    return character * meals.MEAL_SAVE_TOKEN_LENGTH


@pytest.fixture
def meal_db(tmp_path, monkeypatch):
    db_path = tmp_path / "meal-idempotency.db"
    engine = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    test_session = sessionmaker(bind=engine, expire_on_commit=False)

    @contextmanager
    def get_test_db_session():
        session = test_session()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    monkeypatch.setattr(meal_repository_module, "get_db_session", get_test_db_session)
    analytics = Mock()
    monkeypatch.setattr(AnalyticsRepository, "track_event", analytics)

    yield SimpleNamespace(engine=engine, Session=test_session, analytics=analytics)

    engine.dispose()


def _save(save_token: str, *, raw_query: str = "Банан 120 г"):
    return MealRepository.save_meal_idempotent(
        save_token=save_token,
        user_id="12345",
        raw_query=raw_query,
        calories=107,
        protein=1.3,
        fat=0.4,
        carbs=27,
        entry_date=date(2026, 8, 16),
        products_json="[]",
        meal_type="snack",
    )


def _meal_count(meal_db) -> int:
    with meal_db.Session() as session:
        return session.query(Meal).count()


def _concurrent_saves(save_token: str, workers: int):
    import threading

    barrier = threading.Barrier(workers)

    def worker():
        barrier.wait()
        return _save(save_token)

    with ThreadPoolExecutor(max_workers=workers) as executor:
        return list(executor.map(lambda _index: worker(), range(workers)))


def test_sequential_double_save_creates_one_meal(meal_db):
    first = _save(_token("A"))
    second = _save(_token("A"))

    assert [first.status, second.status] == [
        MealSaveStatus.SAVED,
        MealSaveStatus.ALREADY_SAVED,
    ]
    assert first.meal.id == second.meal.id
    assert _meal_count(meal_db) == 1


@pytest.mark.parametrize("workers", [2, 5])
def test_concurrent_same_draft_creates_one_meal(meal_db, workers):
    results = _concurrent_saves(_token("B"), workers)

    assert [result.status for result in results].count(MealSaveStatus.SAVED) == 1
    assert [result.status for result in results].count(MealSaveStatus.ALREADY_SAVED) == workers - 1
    assert len({result.meal.id for result in results}) == 1
    assert _meal_count(meal_db) == 1
    assert meal_db.analytics.call_count == 1


def test_identical_food_in_different_drafts_creates_two_meals(meal_db):
    first = _save(_token("C"), raw_query="Кофе")
    second = _save(_token("D"), raw_query="Кофе")

    assert first.status is MealSaveStatus.SAVED
    assert second.status is MealSaveStatus.SAVED
    assert first.meal.id != second.meal.id
    assert _meal_count(meal_db) == 2


def test_nullable_tokens_do_not_block_legacy_or_non_preview_meals(meal_db):
    kwargs = dict(
        user_id="12345",
        raw_query="Кофе",
        calories=5,
        protein=0,
        fat=0,
        carbs=1,
        entry_date=date(2026, 8, 16),
    )

    MealRepository.save_meal(**kwargs)
    MealRepository.save_meal(**kwargs)

    assert _meal_count(meal_db) == 2


def test_init_db_migrates_existing_meals_table_and_enforces_unique_token(tmp_path, monkeypatch):
    import database.session as database_session

    engine = create_engine(f"sqlite:///{tmp_path / 'legacy.db'}")
    with engine.begin() as connection:
        connection.execute(
            text(
                "CREATE TABLE meals ("
                "id INTEGER PRIMARY KEY, "
                "user_id VARCHAR NOT NULL, "
                "raw_query VARCHAR"
                ")"
            )
        )
    monkeypatch.setattr(database_session, "engine", engine)

    database_session.init_db()

    inspector = inspect(engine)
    columns = {column["name"]: column for column in inspector.get_columns("meals")}
    indexes = {index["name"]: index for index in inspector.get_indexes("meals")}
    assert columns["save_token"]["nullable"] is True
    assert indexes["uq_meals_save_token"]["unique"] == 1

    with engine.begin() as connection:
        connection.execute(text("INSERT INTO meals (user_id, save_token) VALUES ('1', NULL)"))
        connection.execute(text("INSERT INTO meals (user_id, save_token) VALUES ('2', NULL)"))
        connection.execute(text("INSERT INTO meals (user_id, save_token) VALUES ('3', 'same-token')"))
    with pytest.raises(IntegrityError):
        with engine.begin() as connection:
            connection.execute(text("INSERT INTO meals (user_id, save_token) VALUES ('4', 'same-token')"))

    engine.dispose()


def test_db_failure_before_commit_keeps_token_retryable(meal_db):
    def fail_meal_insert(_conn, _cursor, statement, parameters, _context, _executemany):
        if "insert into meals" in statement.lower():
            raise OperationalError(statement, parameters, Exception("forced test failure"))

    event.listen(meal_db.engine, "before_cursor_execute", fail_meal_insert)
    failed = _save(_token("E"))
    event.remove(meal_db.engine, "before_cursor_execute", fail_meal_insert)

    assert failed.status is MealSaveStatus.FAILED
    assert _meal_count(meal_db) == 0

    retry = _save(_token("E"))
    assert retry.status is MealSaveStatus.SAVED
    assert _meal_count(meal_db) == 1


def test_analytics_failure_after_commit_cannot_enable_duplicate(meal_db, monkeypatch):
    monkeypatch.setattr(
        AnalyticsRepository,
        "track_event",
        Mock(side_effect=RuntimeError("analytics unavailable")),
    )

    first = _save(_token("F"))
    second = _save(_token("F"))

    assert first.status is MealSaveStatus.SAVED
    assert second.status is MealSaveStatus.ALREADY_SAVED
    assert _meal_count(meal_db) == 1


class _ConcurrentState:
    def __init__(self, data: dict, concurrent_reads: int = 1):
        self.data = dict(data)
        self._concurrent_reads = concurrent_reads
        self._reads = 0
        self._all_read = asyncio.Event()

    async def get_data(self):
        snapshot = dict(self.data)
        if self._reads < self._concurrent_reads:
            self._reads += 1
            if self._reads == self._concurrent_reads:
                self._all_read.set()
            else:
                await self._all_read.wait()
        return snapshot

    async def update_data(self, **kwargs):
        self.data.update(kwargs)

    async def set_state(self, value):
        self.state = value


def _message(text: str | None = None):
    return SimpleNamespace(
        text=text,
        from_user=SimpleNamespace(id=12345),
        bot=SimpleNamespace(),
        answer=AsyncMock(),
        edit_reply_markup=AsyncMock(),
    )


def _text_draft(save_token: str) -> dict:
    return {
        "save_token": save_token,
        "raw_query": "Банан",
        "meal_type": "snack",
        "entry_date": "2026-08-16",
        "items": [
            {
                "name": "Банан",
                "grams": 120,
                "calories": 107,
                "protein_g": 1.3,
                "fat_total_g": 0.4,
                "carbohydrates_total_g": 27,
            }
        ],
    }


def _photo_draft(save_token: str) -> dict:
    return {
        "photo_save_token": save_token,
        "photo_analysis_raw_query": "[Анализ по фото]",
        "meal_type": "lunch",
        "entry_date": "2026-08-16",
        "photo_analysis_items": [
            {
                "name": "Рис",
                "grams": 180,
                "kcal": 230,
                "protein": 5,
                "fat": 1,
                "carbs": 50,
            }
        ],
    }


def test_two_concurrent_text_save_handlers_create_one_meal(meal_db):
    save_token = _token("G")
    state = _ConcurrentState({"ai_pending_meal": _text_draft(save_token)}, concurrent_reads=2)
    first_message = _message("✅ Сохранить")
    second_message = _message("✅ Сохранить")

    async def run_handlers():
        with patch("handlers.meals._keep_meal_entry_open_after_save", new=AsyncMock()):
            await asyncio.gather(
                meals.handle_ai_confirm(first_message, state),
                meals.handle_ai_confirm(second_message, state),
            )

    asyncio.run(run_handlers())

    assert _meal_count(meal_db) == 1
    assert state.data["ai_pending_meal"] is None


def test_db_failure_keeps_text_draft_for_safe_retry(meal_db):
    save_token = _token("L")
    draft = _text_draft(save_token)
    state = _ConcurrentState({"ai_pending_meal": draft})
    message = _message("✅ Сохранить")

    def fail_meal_insert(_conn, _cursor, statement, parameters, _context, _executemany):
        if "insert into meals" in statement.lower():
            raise OperationalError(statement, parameters, Exception("forced test failure"))

    event.listen(meal_db.engine, "before_cursor_execute", fail_meal_insert)
    failed = asyncio.run(meals._save_ai_meal_draft(message, state, user_id="12345"))
    event.remove(meal_db.engine, "before_cursor_execute", fail_meal_insert)

    assert failed.status is MealSaveStatus.FAILED
    assert state.data["ai_pending_meal"] == draft
    assert _meal_count(meal_db) == 0

    with patch("handlers.meals._keep_meal_entry_open_after_save", new=AsyncMock()):
        retried = asyncio.run(meals._save_ai_meal_draft(message, state, user_id="12345"))

    assert retried.status is MealSaveStatus.SAVED
    assert state.data["ai_pending_meal"] is None
    assert _meal_count(meal_db) == 1


def test_two_concurrent_photo_callback_handlers_create_one_meal(meal_db):
    save_token = _token("H")
    state = _ConcurrentState(_photo_draft(save_token), concurrent_reads=2)

    def callback():
        message = _message()
        return SimpleNamespace(
            data=f"save_photo_food_analysis:{save_token}",
            from_user=SimpleNamespace(id=12345),
            message=message,
            answer=AsyncMock(),
        )

    first_callback = callback()
    second_callback = callback()

    async def run_handlers():
        with patch("handlers.meals._keep_meal_entry_open_after_save", new=AsyncMock()):
            await asyncio.gather(
                meals.photo_analysis_save(first_callback, state),
                meals.photo_analysis_save(second_callback, state),
            )

    asyncio.run(run_handlers())

    assert _meal_count(meal_db) == 1
    assert state.data["photo_analysis_items"] is None
    first_callback.answer.assert_awaited_once()
    second_callback.answer.assert_awaited_once()


def test_stale_photo_callback_cannot_save_current_draft(meal_db):
    old_token = _token("I")
    current_token = _token("J")
    state = _ConcurrentState(_photo_draft(current_token))
    callback = SimpleNamespace(
        data=f"save_photo_food_analysis:{old_token}",
        from_user=SimpleNamespace(id=12345),
        message=_message(),
        answer=AsyncMock(),
    )

    asyncio.run(meals.photo_analysis_save(callback, state))

    assert _meal_count(meal_db) == 0
    callback.answer.assert_awaited_once_with(meals.STALE_MEAL_SAVE_TEXT, show_alert=True)


def test_old_unbound_callbacks_cannot_save_new_current_draft(meal_db):
    photo_callback = SimpleNamespace(
        data="save_photo_food_analysis",
        answer=AsyncMock(),
    )
    text_callback = SimpleNamespace(
        data="save_ai_meal_draft",
        answer=AsyncMock(),
    )

    asyncio.run(meals.reject_legacy_photo_meal_save_callback(photo_callback))
    asyncio.run(meals.reject_legacy_ai_meal_save_callback(text_callback))

    assert _meal_count(meal_db) == 0
    photo_callback.answer.assert_awaited_once_with(meals.STALE_MEAL_SAVE_TEXT, show_alert=True)
    text_callback.answer.assert_awaited_once_with(meals.STALE_MEAL_SAVE_TEXT, show_alert=True)


def test_photo_save_callback_is_bound_and_within_telegram_limit():
    save_token = _token("K")
    keyboard = meals._build_photo_analysis_confirm_menu(
        [{"name": "Банан"}],
        save_token,
    )
    callback_data = keyboard.inline_keyboard[-1][-1].callback_data

    assert callback_data == f"save_photo_food_analysis:{save_token}"
    assert len(callback_data.encode("utf-8")) <= 64
