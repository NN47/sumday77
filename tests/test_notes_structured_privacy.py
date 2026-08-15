import asyncio
import json
import os
from contextlib import contextmanager
from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

os.environ.setdefault("API_TOKEN", "test-token")

from database.models import Base, NoteEntry
from database.repositories.note_repository import NoteRepository
from handlers import wellbeing
from services.extended_activity_analysis_service import AnalysisPeriod, ExtendedActivityAnalysisService
from states.user_states import WellbeingStates
from utils.note_factors import (
    ALLOWED_NOTE_FACTORS,
    NOTE_FACTOR_LABELS,
    NOTE_FACTOR_OPTIONS,
    parse_note_factor_callback,
)


PRIVATE_MARKER = "PRIVATE_NOTE_TEXT_12345"


class DummyState:
    def __init__(self, data=None):
        self.data = dict(data or {})
        self.current_state = None

    async def clear(self):
        self.data.clear()
        self.current_state = None

    async def set_state(self, state):
        self.current_state = state.state if hasattr(state, "state") else state

    async def get_state(self):
        return self.current_state

    async def update_data(self, **kwargs):
        self.data.update(kwargs)

    async def get_data(self):
        return dict(self.data)


def make_message(*, text=None, user_id=123):
    return SimpleNamespace(
        text=text,
        from_user=SimpleNamespace(id=user_id),
        answer=AsyncMock(),
    )


def make_callback(data: str, *, user_id=123):
    return SimpleNamespace(
        data=data,
        from_user=SimpleNamespace(id=user_id),
        answer=AsyncMock(),
        message=SimpleNamespace(answer=AsyncMock(), edit_text=AsyncMock()),
    )


def test_rating_and_factor_multiselect_flow_uses_only_inline_buttons():
    state = DummyState({"note_user_id": "123", "note_date": "2026-08-15"})
    rating_message = make_message(text="🙂 Нормально")

    asyncio.run(wellbeing.select_rating_message(rating_message, state))

    assert state.current_state == WellbeingStates.note_factors.state
    assert state.data["day_rating"] == 4
    keyboard = rating_message.answer.await_args.kwargs["reply_markup"]
    button_texts = [button.text for row in keyboard.inline_keyboard for button in row]
    assert "✍️ Свой вариант" not in button_texts
    assert "💾 Сохранить" not in button_texts

    first_factor_callback = make_callback("note_factor:tired")
    asyncio.run(wellbeing.toggle_factor(first_factor_callback, state))
    selected_keyboard = first_factor_callback.message.edit_text.await_args.kwargs["reply_markup"]
    assert any(
        button.text == "💾 Сохранить"
        for row in selected_keyboard.inline_keyboard
        for button in row
    )
    asyncio.run(wellbeing.toggle_factor(make_callback("note_factor:workout"), state))
    assert state.data["factors"] == ["tired", "workout"]

    asyncio.run(wellbeing.toggle_factor(make_callback("note_factor:tired"), state))
    assert state.data["factors"] == ["workout"]


def test_save_persists_only_whitelisted_factors_and_has_no_text_step():
    state = DummyState(
        {
            "note_user_id": "123",
            "note_date": "2026-08-15",
            "day_rating": 5,
            "factors": ["energy", "headache", "custom factor", "energy"],
            "note_text": PRIVATE_MARKER,
        }
    )
    callback = make_callback("save_note_factors")
    saved = SimpleNamespace(
        date=date(2026, 8, 15),
        day_rating=5,
        factors=["energy"],
        text=PRIVATE_MARKER,
    )

    with patch.object(wellbeing.NoteRepository, "upsert_note", return_value=saved) as upsert:
        asyncio.run(wellbeing.finalize_note(callback, state))

    upsert.assert_called_once_with(
        user_id="123",
        entry_date=date(2026, 8, 15),
        day_rating=5,
        factors=["energy"],
    )
    assert state.current_state is None
    assert PRIVATE_MARKER not in callback.message.answer.await_args.args[0]
    assert not hasattr(WellbeingStates, "note_text")


def test_invalid_and_legacy_factor_callbacks_cannot_change_or_save_state():
    state = DummyState(
        {
            "note_user_id": "123",
            "note_date": "2026-08-15",
            "day_rating": 4,
            "factors": ["workout"],
        }
    )
    invalid = make_callback("note_factor:PRIVATE_CUSTOM_FACTOR")

    asyncio.run(wellbeing.toggle_factor(invalid, state))

    assert state.data["factors"] == ["workout"]
    invalid.answer.assert_awaited_once_with("Недоступный фактор", show_alert=True)
    assert parse_note_factor_callback("toggle_factor_headache") is None
    assert parse_note_factor_callback("done_factors") is None
    assert parse_note_factor_callback("save_note") is None


def test_forged_save_callback_without_factor_does_not_persist_note():
    state = DummyState(
        {
            "note_user_id": "123",
            "note_date": "2026-08-15",
            "day_rating": 4,
            "factors": ["custom factor"],
        }
    )
    callback = make_callback("save_note_factors")

    with patch.object(wellbeing.NoteRepository, "upsert_note") as upsert:
        asyncio.run(wellbeing.finalize_note(callback, state))

    upsert.assert_not_called()
    callback.answer.assert_awaited_once_with("Выбери хотя бы один фактор", show_alert=True)


def test_factor_whitelist_matches_required_non_medical_options():
    assert [label for _, label in NOTE_FACTOR_OPTIONS] == [
        "⚡ Много энергии",
        "😴 Усталость",
        "💤 Недосып",
        "😌 Хороший сон",
        "🍽 Голод",
        "🍔 Переедание",
        "🏋️ Тренировка",
        "🚶 Много активности",
        "🛋 Мало активности",
        "🧘 Отдых",
        "😵‍💫 Стресс",
        "😊 Хорошее настроение",
        "😔 Плохое настроение",
        "🔥 Продуктивный день",
    ]
    forbidden = {
        "headache",
        "body_pain",
        "medicine",
        "nausea",
        "pressure",
        "migraine",
        "pain",
        "illness",
    }
    assert ALLOWED_NOTE_FACTORS.isdisjoint(forbidden)
    assert not any(
        medical_word in label.lower()
        for label in NOTE_FACTOR_LABELS.values()
        for medical_word in ("боль", "лекар", "тошнот", "давлен", "мигрен", "болез")
    )


def test_repository_filters_factors_and_does_not_overwrite_legacy_text():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)

    with Session() as session:
        session.add(
            NoteEntry(
                user_id="123",
                date=date(2026, 8, 15),
                day_rating=3,
                factors_json=json.dumps(["headache", "legacy custom"]),
                text=PRIVATE_MARKER,
            )
        )
        session.commit()

    @contextmanager
    def session_provider():
        with Session() as session:
            yield session

    with patch("database.repositories.note_repository.get_db_session", session_provider):
        note = NoteRepository.upsert_note(
            "123",
            date(2026, 8, 15),
            4,
            ["tired", "headache", "custom factor", "workout", "tired"],
        )
        new_note = NoteRepository.upsert_note(
            "456",
            date(2026, 8, 15),
            5,
            ["energy", "medicine"],
        )

    assert note.factors == ["tired", "workout"]
    assert note.text == PRIVATE_MARKER
    assert new_note.factors == ["energy"]
    assert new_note.text is None
    engine.dispose()


def test_extended_ai_context_excludes_all_legacy_free_text_and_custom_factors():
    note = SimpleNamespace(
        day_rating=4,
        factors=["tired", "workout", "headache", "custom factor"],
        text=PRIVATE_MARKER,
    )
    legacy_wellbeing = SimpleNamespace(
        date=date(2026, 8, 15),
        entry_type="comment",
        mood=None,
        influence=None,
        difficulty=None,
        comment=PRIVATE_MARKER,
    )
    service = ExtendedActivityAnalysisService()
    target_date = date(2026, 8, 15)

    with (
        patch("services.extended_activity_analysis_service.MealRepository.get_kbju_settings", return_value=None),
        patch("services.extended_activity_analysis_service.MealRepository.get_meals_for_date", return_value=[]),
        patch("services.extended_activity_analysis_service.WaterRepository.get_daily_total", return_value=0),
        patch("services.extended_activity_analysis_service.WorkoutRepository.get_workouts_for_period", return_value=[]),
        patch("services.extended_activity_analysis_service.WeightRepository.get_weights_for_date_range", return_value=[]),
        patch("services.extended_activity_analysis_service.NoteRepository.get_note_for_date", return_value=note),
        patch("services.extended_activity_analysis_service.get_water_recommended", return_value=2000),
        patch(
            "database.repositories.WellbeingRepository.get_entries_for_period",
            return_value=[legacy_wellbeing],
        ) as wellbeing_reader,
    ):
        context = service.collect_period_context(
            "123",
            AnalysisPeriod(target_date, target_date, "за день"),
        )

    payload = json.dumps(context, ensure_ascii=False)
    assert context["notes"] == [{"rating": 4, "factors": ["tired", "workout"]}]
    assert "wellbeing" not in context
    assert PRIVATE_MARKER not in payload
    assert "headache" not in payload
    assert "custom factor" not in payload
    wellbeing_reader.assert_not_called()


def test_old_note_text_is_not_displayed_in_current_or_calendar_views():
    note = SimpleNamespace(
        date=date(2026, 8, 15),
        day_rating=4,
        factors=["tired", "headache", "custom factor"],
        text=PRIVATE_MARKER,
    )
    current_message = make_message()
    calendar_message = make_message()

    with patch.object(wellbeing.NoteRepository, "get_note_for_date", return_value=note):
        asyncio.run(wellbeing.show_notes_day(current_message, "123", date(2026, 8, 15)))
        asyncio.run(wellbeing.show_note_calendar_day(calendar_message, "123", date(2026, 8, 15)))

    current_text = current_message.answer.await_args.args[0]
    calendar_text = calendar_message.answer.await_args.args[0]
    assert PRIVATE_MARKER not in current_text
    assert PRIVATE_MARKER not in calendar_text
    assert "Комментарий" not in current_text
    assert "Комментарий" not in calendar_text
    assert "Головная боль" not in current_text
    assert "custom factor" not in calendar_text
