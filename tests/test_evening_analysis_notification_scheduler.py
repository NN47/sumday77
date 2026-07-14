import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from contextlib import contextmanager
from datetime import date, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from zoneinfo import ZoneInfo

os.environ.setdefault("API_TOKEN", "test-token")

from database.models import EveningAnalysisNotificationState, User
from services.notification_scheduler import EVENING_ANALYSIS_MAIN_TEXT, NotificationScheduler


class FakeQuery:
    def __init__(self, session, kind):
        self.session = session
        self.kind = kind

    def join(self, *args, **kwargs):
        return self

    def filter(self, *args, **kwargs):
        return self

    def all(self):
        if self.kind == "users":
            return self.session.users
        return []

    def first(self):
        if self.kind == "state":
            user = self.session.users[0]
            return self.session.states.get(user.user_id)
        if self.kind == "analysis":
            return (1,) if self.session.generated_exists else None
        return None


class FakeSession:
    def __init__(self, users, states=None, generated_exists=False):
        self.users = users
        self.states = states or {}
        self.generated_exists = generated_exists

    def query(self, *entities):
        entity = entities[0]
        if entity is User:
            return FakeQuery(self, "users")
        if entity is EveningAnalysisNotificationState:
            return FakeQuery(self, "state")
        return FakeQuery(self, "analysis")

    def add(self, state):
        self.states[state.user_id] = state

    def flush(self):
        pass


@contextmanager
def fake_db_session(session):
    yield session


def test_evening_analysis_notification_is_sent_after_2222_if_exact_minute_was_missed():
    user = SimpleNamespace(user_id="12345", timezone="Europe/Moscow")
    session = FakeSession([user])
    bot = SimpleNamespace(send_message=AsyncMock())
    scheduler = NotificationScheduler(bot)
    fixed_now = datetime(2026, 4, 8, 22, 23, tzinfo=ZoneInfo("Europe/Moscow"))

    class FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed_now.astimezone(tz) if tz else fixed_now.replace(tzinfo=None)

        @classmethod
        def utcnow(cls):
            return fixed_now.astimezone(ZoneInfo("UTC")).replace(tzinfo=None)

    with (
        patch("services.notification_scheduler.datetime", FixedDateTime),
        patch("services.notification_scheduler.get_db_session", return_value=fake_db_session(session)),
        patch(
            "services.notification_scheduler.EveningAnalysisNotificationRepository.mark_evening_notification_sent"
        ) as mark_sent,
    ):
        asyncio.run(scheduler.check_and_send_evening_analysis_notifications())

    bot.send_message.assert_awaited_once()
    assert bot.send_message.await_args.kwargs["chat_id"] == "12345"
    assert bot.send_message.await_args.kwargs["text"] == EVENING_ANALYSIS_MAIN_TEXT
    mark_sent.assert_called_once_with("12345", date(2026, 4, 8))

def test_evening_analysis_notification_is_not_sent_before_2222():
    user = SimpleNamespace(user_id="12345", timezone="Europe/Moscow")
    session = FakeSession([user])
    bot = SimpleNamespace(send_message=AsyncMock())
    scheduler = NotificationScheduler(bot)
    fixed_now = datetime(2026, 4, 8, 22, 21, tzinfo=ZoneInfo("Europe/Moscow"))

    class FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed_now.astimezone(tz) if tz else fixed_now.replace(tzinfo=None)

        @classmethod
        def utcnow(cls):
            return fixed_now.astimezone(ZoneInfo("UTC")).replace(tzinfo=None)

    with (
        patch("services.notification_scheduler.datetime", FixedDateTime),
        patch("services.notification_scheduler.get_db_session", return_value=fake_db_session(session)),
        patch(
            "services.notification_scheduler.EveningAnalysisNotificationRepository.mark_evening_notification_sent"
        ) as mark_sent,
    ):
        asyncio.run(scheduler.check_and_send_evening_analysis_notifications())

    bot.send_message.assert_not_awaited()
    mark_sent.assert_not_called()


def test_evening_analysis_notification_uses_app_timezone_not_stale_user_timezone():
    user = SimpleNamespace(user_id="12345", timezone="UTC")
    session = FakeSession([user])
    bot = SimpleNamespace(send_message=AsyncMock())
    scheduler = NotificationScheduler(bot)
    fixed_now = datetime(2026, 4, 8, 22, 23, tzinfo=ZoneInfo("Europe/Moscow"))

    class FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed_now.astimezone(tz) if tz else fixed_now.replace(tzinfo=None)

        @classmethod
        def utcnow(cls):
            return fixed_now.astimezone(ZoneInfo("UTC")).replace(tzinfo=None)

    with (
        patch("services.notification_scheduler.datetime", FixedDateTime),
        patch("services.notification_scheduler.get_db_session", return_value=fake_db_session(session)),
        patch(
            "services.notification_scheduler.EveningAnalysisNotificationRepository.mark_evening_notification_sent"
        ) as mark_sent,
    ):
        asyncio.run(scheduler.check_and_send_evening_analysis_notifications())

    bot.send_message.assert_awaited_once()
    assert bot.send_message.await_args.kwargs["chat_id"] == "12345"
    assert bot.send_message.await_args.kwargs["text"] == EVENING_ANALYSIS_MAIN_TEXT
    mark_sent.assert_called_once_with("12345", date(2026, 4, 8))


def test_evening_analysis_notification_is_retried_when_telegram_send_fails():
    user = SimpleNamespace(user_id="12345", timezone="Europe/Moscow")
    session = FakeSession([user])
    bot = SimpleNamespace(send_message=AsyncMock(side_effect=RuntimeError("telegram unavailable")))
    scheduler = NotificationScheduler(bot)
    fixed_now = datetime(2026, 4, 8, 22, 23, tzinfo=ZoneInfo("Europe/Moscow"))

    class FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed_now.astimezone(tz) if tz else fixed_now.replace(tzinfo=None)

        @classmethod
        def utcnow(cls):
            return fixed_now.astimezone(ZoneInfo("UTC")).replace(tzinfo=None)

    with (
        patch("services.notification_scheduler.datetime", FixedDateTime),
        patch("services.notification_scheduler.get_db_session", return_value=fake_db_session(session)),
        patch("services.notification_scheduler.log_app_error"),
        patch(
            "services.notification_scheduler.EveningAnalysisNotificationRepository.mark_evening_notification_sent"
        ) as mark_sent,
    ):
        asyncio.run(scheduler.check_and_send_evening_analysis_notifications())

    bot.send_message.assert_awaited_once()
    mark_sent.assert_not_called()


def test_evening_analysis_notification_is_deferred_after_recent_activity():
    fixed_now = datetime(2026, 4, 8, 22, 23, tzinfo=ZoneInfo("Europe/Moscow"))
    user = SimpleNamespace(
        user_id="12345",
        timezone="Europe/Moscow",
        last_seen_at=fixed_now.astimezone(ZoneInfo("UTC")).replace(tzinfo=None),
    )
    session = FakeSession([user])
    bot = SimpleNamespace(send_message=AsyncMock())
    scheduler = NotificationScheduler(bot)

    class FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed_now.astimezone(tz) if tz else fixed_now.replace(tzinfo=None)

        @classmethod
        def utcnow(cls):
            return fixed_now.astimezone(ZoneInfo("UTC")).replace(tzinfo=None)

    with (
        patch("services.notification_scheduler.datetime", FixedDateTime),
        patch("services.notification_scheduler.get_db_session", return_value=fake_db_session(session)),
        patch("services.notification_scheduler.random.randint", return_value=600),
        patch(
            "services.notification_scheduler.EveningAnalysisNotificationRepository.mark_evening_notification_sent"
        ) as mark_sent,
    ):
        asyncio.run(scheduler.check_and_send_evening_analysis_notifications())

    bot.send_message.assert_not_awaited()
    mark_sent.assert_not_called()
    state = session.states["12345"]
    assert state.remind_later_date == date(2026, 4, 8)
    assert state.reminder_due_at == FixedDateTime.utcnow() + __import__("datetime").timedelta(minutes=10)


def test_evening_analysis_notification_is_skipped_when_analysis_already_generated():
    user = SimpleNamespace(user_id="12345", timezone="Europe/Moscow")
    session = FakeSession([user], generated_exists=True)
    bot = SimpleNamespace(send_message=AsyncMock())
    scheduler = NotificationScheduler(bot)
    fixed_now = datetime(2026, 4, 8, 22, 23, tzinfo=ZoneInfo("Europe/Moscow"))

    class FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed_now.astimezone(tz) if tz else fixed_now.replace(tzinfo=None)

        @classmethod
        def utcnow(cls):
            return fixed_now.astimezone(ZoneInfo("UTC")).replace(tzinfo=None)

    with (
        patch("services.notification_scheduler.datetime", FixedDateTime),
        patch("services.notification_scheduler.get_db_session", return_value=fake_db_session(session)),
        patch(
            "services.notification_scheduler.EveningAnalysisNotificationRepository.mark_evening_notification_sent"
        ) as mark_sent,
    ):
        asyncio.run(scheduler.check_and_send_evening_analysis_notifications())

    bot.send_message.assert_not_awaited()
    mark_sent.assert_not_called()
    assert session.states["12345"].last_daily_analysis_date == date(2026, 4, 8)
    assert session.states["12345"].reminder_due_at is None


def test_evening_analysis_remind_later_keeps_start_button_and_schedules_reminder():
    from handlers import activity
    from services.notification_scheduler import build_evening_analysis_start_keyboard

    callback = SimpleNamespace(
        data="evening_analysis_remind:2026-04-08",
        from_user=SimpleNamespace(id=12345),
        message=SimpleNamespace(
            edit_reply_markup=AsyncMock(),
            answer=AsyncMock(),
        ),
        answer=AsyncMock(),
    )

    with patch(
        "handlers.activity.EveningAnalysisNotificationRepository.has_analysis_for_date",
        return_value=False,
    ), patch(
        "handlers.activity.EveningAnalysisNotificationRepository.schedule_reminder",
        return_value=1,
    ) as schedule_reminder:
        asyncio.run(activity.remind_evening_activity_analysis_later(callback))

    callback.message.edit_reply_markup.assert_awaited_once_with(
        reply_markup=build_evening_analysis_start_keyboard(date(2026, 4, 8))
    )
    callback.answer.assert_awaited_once_with("⏰ Хорошо, напомню позже.")
    callback.message.answer.assert_awaited_once_with("⏰ Хорошо, напомню позже.")
    assert schedule_reminder.call_args.args[0] == "12345"
    assert schedule_reminder.call_args.args[1] == date(2026, 4, 8)


def test_evening_analysis_start_keyboard_contains_only_start_button():
    from services.notification_scheduler import build_evening_analysis_start_keyboard

    keyboard = build_evening_analysis_start_keyboard(date(2026, 4, 8))

    assert len(keyboard.inline_keyboard) == 1
    assert keyboard.inline_keyboard[0][0].text == "✅ Запустить подробный AI-анализ"
    assert keyboard.inline_keyboard[0][0].callback_data == "evening_analysis_start:2026-04-08"
