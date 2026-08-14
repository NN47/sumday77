import asyncio
from contextlib import contextmanager
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from aiogram.types import CallbackQuery, Chat, Message, User as TelegramUser
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database.models import Base, KbjuSettings, Meal, User
from database.repositories.user_repository import UserRepository
from handlers import kbju_test
from handlers.start import start
from middlewares.onboarding import OnboardingMiddleware
from services.notification_scheduler import NotificationScheduler
from states.user_states import AgeGateStates, KbjuTestStates


class DummyState:
    def __init__(self, current_state=None, data=None):
        self.current_state = current_state
        self.data = dict(data or {})
        self.clear_count = 0

    async def clear(self):
        self.current_state = None
        self.data.clear()
        self.clear_count += 1

    async def set_state(self, state):
        self.current_state = state.state if hasattr(state, "state") else state

    async def get_state(self):
        return self.current_state

    async def update_data(self, **kwargs):
        self.data.update(kwargs)

    async def get_data(self):
        return dict(self.data)


def _message(user_id: int, text: str) -> Message:
    return Message(
        message_id=1,
        date=datetime.now(timezone.utc),
        chat=Chat(id=user_id, type="private"),
        from_user=TelegramUser(id=user_id, is_bot=False, first_name="Test"),
        text=text,
    )


def _callback(user_id: int, data: str) -> CallbackQuery:
    return CallbackQuery(
        id=f"callback-{user_id}",
        from_user=TelegramUser(id=user_id, is_bot=False, first_name="Test"),
        chat_instance=f"chat-{user_id}",
        data=data,
        message=_message(user_id, "old inline message"),
    )


def _handler_callback(user_id: int, data: str):
    message = SimpleNamespace(
        text=None,
        answer=AsyncMock(),
        bot=SimpleNamespace(menu_stack=[]),
    )
    return SimpleNamespace(
        data=data,
        from_user=SimpleNamespace(id=user_id),
        message=message,
        answer=AsyncMock(),
    )


def test_new_underage_user_stops_onboarding_without_kbju_calculation():
    state = DummyState(
        KbjuTestStates.entering_age.state,
        {"gender": "male", "required_onboarding": True},
    )
    callback = _handler_callback(101, "kbju_age:under_18")

    with (
        patch.object(kbju_test.UserRepository, "set_age_verification") as set_age,
        patch.object(kbju_test, "calculate_nutrition_profile") as calculate,
        patch.object(kbju_test.MealRepository, "save_kbju_settings") as save_kbju,
    ):
        asyncio.run(kbju_test.handle_kbju_test_age_callback(callback, state))

    set_age.assert_called_once_with("101", False)
    calculate.assert_not_called()
    save_kbju.assert_not_called()
    assert state.current_state is None
    assert state.data == {}
    assert kbju_test.UNDERAGE_MESSAGE in callback.message.answer.await_args.args[0]


def test_adult_user_continues_existing_kbju_onboarding():
    state = DummyState(KbjuTestStates.entering_age.state, {"gender": "female"})
    callback = _handler_callback(102, "kbju_age:18_24")

    with patch.object(kbju_test.UserRepository, "set_age_verification") as set_age:
        asyncio.run(kbju_test.handle_kbju_test_age_callback(callback, state))

    set_age.assert_called_once_with("102", True)
    assert state.current_state == KbjuTestStates.entering_height.state
    assert state.data["age"] == 21
    assert state.data["age_range"] == "18-24"


def test_start_command_remains_available_for_underage_user():
    middleware = OnboardingMiddleware()
    message = _message(103, "/start")
    state = DummyState()
    handler = AsyncMock(return_value="started")

    with patch.object(UserRepository, "get_age_verification", return_value=False):
        result = asyncio.run(middleware(handler, message, {"state": state}))

    assert result == "started"
    handler.assert_awaited_once()


def test_underage_user_can_recheck_age_via_start_and_resume_onboarding():
    persisted_user = SimpleNamespace(age_verified=False)
    query = Mock()
    query.filter.return_value.first.return_value = persisted_user
    session = Mock()
    session.query.return_value = query

    @contextmanager
    def session_provider():
        yield session

    state = DummyState(KbjuTestStates.entering_height.state, {"old": "draft"})
    start_message = SimpleNamespace(
        from_user=SimpleNamespace(id=1031),
        text="/start",
        bot=SimpleNamespace(menu_stack=[]),
        answer=AsyncMock(),
    )

    with (
        patch("handlers.start.get_db_session", session_provider),
        patch("handlers.start.AnalyticsRepository.track_event"),
    ):
        asyncio.run(start(start_message, state))

    assert state.current_state == AgeGateStates.confirming_age.state
    assert state.data == {}
    assert start_message.answer.await_args.kwargs["reply_markup"] is kbju_test.age_gate_inline

    callback = _handler_callback(1031, "age_gate:18_24")

    def persist_age(_user_id, is_adult):
        persisted_user.age_verified = is_adult

    with (
        patch.object(
            kbju_test.UserRepository,
            "set_age_verification",
            side_effect=persist_age,
        ),
        patch("handlers.start.has_completed_kbju_test", return_value=False),
    ):
        asyncio.run(kbju_test.handle_existing_user_age_confirmation(callback, state))

    assert persisted_user.age_verified is True
    assert state.current_state == KbjuTestStates.entering_gender.state
    assert state.data == {"required_onboarding": True}


def test_manual_command_cannot_bypass_underage_gate():
    middleware = OnboardingMiddleware()
    message = _message(104, "/meal")
    state = DummyState()
    handler = AsyncMock()

    with (
        patch.object(UserRepository, "get_age_verification", return_value=False),
        patch.object(middleware, "_redirect_message", new=AsyncMock()) as redirect,
    ):
        asyncio.run(middleware(handler, message, {"state": state}))

    handler.assert_not_awaited()
    redirect.assert_awaited_once_with(message, state)


def test_old_callback_cannot_bypass_unverified_gate():
    middleware = OnboardingMiddleware()
    callback = _callback(105, "evening_analysis_start:2026-08-14")
    state = DummyState()
    handler = AsyncMock()

    with (
        patch.object(UserRepository, "get_age_verification", return_value=None),
        patch.object(middleware, "_redirect_callback", new=AsyncMock()) as redirect,
    ):
        asyncio.run(middleware(handler, callback, {"state": state}))

    handler.assert_not_awaited()
    redirect.assert_awaited_once_with(callback, state)


def test_underage_user_does_not_affect_verified_user():
    middleware = OnboardingMiddleware()
    state_a = DummyState()
    state_b = DummyState()
    message_a = _message(201, "🍱 Дневник питания")
    message_b = _message(202, "🍱 Дневник питания")

    def age_status(user_id):
        return {"201": False, "202": True}[str(user_id)]

    with (
        patch.object(UserRepository, "get_age_verification", side_effect=age_status),
        patch.object(kbju_test.MealRepository, "get_kbju_settings", return_value=object()),
        patch("middlewares.onboarding.MealRepository.get_kbju_settings", return_value=object()),
    ):
        allowed_a = asyncio.run(middleware._is_allowed_message(message_a, {"state": state_a}))
        allowed_b = asyncio.run(middleware._is_allowed_message(message_b, {"state": state_b}))

    assert allowed_a is False
    assert allowed_b is True


def test_existing_unverified_user_gets_one_time_gate():
    user = SimpleNamespace(age_verified=None)
    query = Mock()
    query.filter.return_value.first.return_value = user
    session = Mock()
    session.query.return_value = query

    @contextmanager
    def session_provider():
        yield session

    message = _message(301, "/start")
    state = DummyState()

    with (
        patch("handlers.start.get_db_session", session_provider),
        patch("handlers.start.AnalyticsRepository.track_event"),
        patch("handlers.start.has_completed_kbju_test", return_value=True),
        patch("handlers.start.start_age_confirmation", new=AsyncMock()) as age_gate,
        patch("handlers.start.show_verified_start", new=AsyncMock()) as verified_start,
    ):
        asyncio.run(start(message, state))

    age_gate.assert_awaited_once_with(message, state)
    verified_start.assert_not_awaited()


def test_existing_user_data_survives_age_confirmation(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)

    @contextmanager
    def session_provider():
        session = Session()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    with session_provider() as session:
        session.add(User(user_id="401", age_verified=None))
        session.add(Meal(user_id="401", description="existing diary entry"))

    monkeypatch.setattr("database.repositories.user_repository.get_db_session", session_provider)
    UserRepository.set_age_verification("401", True)

    with session_provider() as session:
        assert session.query(User).filter_by(user_id="401").one().age_verified is True
        assert session.query(Meal).filter_by(user_id="401").count() == 1

    engine.dispose()


def test_verified_user_is_not_prompted_on_repeated_start():
    user = SimpleNamespace(age_verified=True)
    query = Mock()
    query.filter.return_value.first.return_value = user
    session = Mock()
    session.query.return_value = query

    @contextmanager
    def session_provider():
        yield session

    state = DummyState()
    first = _message(501, "/start")
    second = _message(501, "/start")

    with (
        patch("handlers.start.get_db_session", session_provider),
        patch("handlers.start.AnalyticsRepository.track_event"),
        patch("handlers.start.start_age_confirmation", new=AsyncMock()) as age_gate,
        patch("handlers.start.show_verified_start", new=AsyncMock()) as verified_start,
    ):
        asyncio.run(start(first, state))
        asyncio.run(start(second, state))

    age_gate.assert_not_awaited()
    assert verified_start.await_count == 2


def test_age_gate_state_only_allows_age_gate_callbacks():
    middleware = OnboardingMiddleware()
    state = DummyState(AgeGateStates.confirming_age.state)
    age_callback = _callback(601, "age_gate:25_29")
    stale_callback = _callback(601, "evening_analysis_start:2026-08-14")

    with patch.object(UserRepository, "get_age_verification", return_value=None):
        assert asyncio.run(middleware._is_allowed_callback(age_callback, {"state": state})) is True
        assert asyncio.run(middleware._is_allowed_callback(stale_callback, {"state": state})) is False


def test_existing_adult_confirmation_uses_callback_user_not_bot_sender():
    state = DummyState(AgeGateStates.confirming_age.state)
    callback = _handler_callback(701, "age_gate:25_29")

    with (
        patch.object(kbju_test.UserRepository, "set_age_verification") as set_age,
        patch("handlers.start.show_verified_start", new=AsyncMock()) as verified_start,
    ):
        asyncio.run(kbju_test.handle_existing_user_age_confirmation(callback, state))

    set_age.assert_called_once_with("701", True)
    assert state.current_state is None
    verified_start.assert_awaited_once_with(callback.message, state, user_id="701")


def test_scheduled_notifications_only_target_verified_adults():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)

    @contextmanager
    def session_provider():
        session = Session()
        try:
            yield session
            session.commit()
        finally:
            session.close()

    with session_provider() as session:
        session.add_all(
            [
                User(user_id="801", age_verified=True),
                User(user_id="802", age_verified=False),
                KbjuSettings(user_id="801", calories=2000, protein=100, fat=70, carbs=200),
                KbjuSettings(user_id="802", calories=2000, protein=100, fat=70, carbs=200),
            ]
        )

    scheduler = NotificationScheduler(SimpleNamespace(send_message=AsyncMock()))
    scheduler.send_notification = AsyncMock(return_value=True)
    with patch("services.notification_scheduler.get_db_session", session_provider):
        asyncio.run(scheduler.send_meal_notifications("обед", "reminder"))

    scheduler.send_notification.assert_awaited_once_with("801", "reminder")
    engine.dispose()


def test_notification_send_rechecks_age_before_delivery():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)

    @contextmanager
    def session_provider():
        session = Session()
        try:
            yield session
            session.commit()
        finally:
            session.close()

    with session_provider() as session:
        session.add_all(
            [
                User(user_id="901", age_verified=True),
                User(user_id="902", age_verified=False),
            ]
        )

    bot = SimpleNamespace(send_message=AsyncMock())
    scheduler = NotificationScheduler(bot)
    with patch("services.notification_scheduler.get_db_session", session_provider):
        assert asyncio.run(scheduler.send_notification("901", "adult reminder")) is True
        assert asyncio.run(scheduler.send_notification("902", "minor reminder")) is False

    bot.send_message.assert_awaited_once()
    assert bot.send_message.await_args.kwargs["chat_id"] == "901"
    engine.dispose()
