"""Legal gate, migration and privacy regressions, without external API calls."""
import asyncio
from contextlib import contextmanager
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from aiogram import Bot, Dispatcher, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import CallbackQuery, Chat, Message, Update, User as TelegramUser
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from database.account_deletion import delete_user_account
from database.legal_migration import migrate_legal_metadata
from database.models import Base, SupportMessage, User
from database.repositories import UserRepository
from handlers import legal, settings
from middlewares.legal import LegalAcceptanceMiddleware
from middlewares.onboarding import OnboardingMiddleware
from states.user_states import AccountDeletionStates, KbjuTestStates, LegalStates
from utils.legal_documents import LEGAL_DOCUMENTS, LEGAL_VERSION, SUPPORT_CONTACT


@pytest.fixture
def legal_db(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)

    @contextmanager
    def session_provider():
        with factory() as session:
            try:
                yield session
                session.commit()
            except Exception:
                session.rollback()
                raise

    monkeypatch.setattr("database.repositories.user_repository.get_db_session", session_provider)
    monkeypatch.setattr("database.repositories.support_repository.get_db_session", session_provider)
    yield engine, session_provider
    engine.dispose()


def fake_message(user_id=101, text="/start"):
    return SimpleNamespace(
        from_user=SimpleNamespace(id=user_id), text=text, bot=SimpleNamespace(),
        answer=AsyncMock(), edit_text=AsyncMock(), edit_reply_markup=AsyncMock(),
    )


def fake_callback(data, user_id=101):
    return SimpleNamespace(data=data, from_user=SimpleNamespace(id=user_id),
                           message=fake_message(user_id=999, text="Document"), answer=AsyncMock())


def fsm(user_id=101):
    return FSMContext(storage=MemoryStorage(), key=StorageKey(bot_id=999, chat_id=user_id, user_id=user_id))


def test_legacy_migration_clears_profile_metadata_without_touching_messages_or_diary():
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE users (id INTEGER PRIMARY KEY, user_id VARCHAR, age_verified BOOLEAN)"))
        conn.execute(text("INSERT INTO users VALUES (1, '101', 1)"))
        conn.execute(text("CREATE TABLE support_messages (id INTEGER PRIMARY KEY, user_id VARCHAR, username VARCHAR, full_name VARCHAR, message_text TEXT)"))
        conn.execute(text("INSERT INTO support_messages VALUES (1, '101', 'old_name', 'Old Full Name', 'Help')"))
        conn.execute(text("CREATE TABLE meals (id INTEGER PRIMARY KEY, calories FLOAT)"))
        conn.execute(text("INSERT INTO meals VALUES (1, 123)"))
    migrate_legal_metadata(engine)
    migrate_legal_metadata(engine)
    with engine.connect() as conn:
        user = conn.execute(text("SELECT * FROM users")).mappings().one()
        support = conn.execute(text("SELECT * FROM support_messages")).mappings().one()
        assert user["age_verified"] == 1
        assert user["accepted_terms_version"] is None
        assert user["terms_accepted_at"] is None
        assert support["username"] is None and support["full_name"] is None
        assert support["message_text"] == "Help" and support["user_id"] == "101"
        assert conn.execute(text("SELECT calories FROM meals")).scalar_one() == 123
    engine.dispose()


def test_acceptance_is_persisted_idempotent_and_deleted_with_account(legal_db):
    engine, provider = legal_db
    assert {"username", "full_name"}.isdisjoint(c["name"] for c in inspect(engine).get_columns("support_messages"))
    assert not UserRepository.has_current_legal_acceptance("101")
    UserRepository.accept_legal_documents("101")
    with provider() as session:
        first = session.query(User).filter_by(user_id="101").one().terms_accepted_at
    UserRepository.accept_legal_documents("101")
    assert UserRepository.has_current_legal_acceptance("101")
    assert not UserRepository.has_current_legal_acceptance("202")
    with provider() as session:
        user = session.query(User).filter_by(user_id="101").one()
        assert user.terms_accepted_at == first
        assert user.accepted_terms_version == LEGAL_VERSION
        assert user.acknowledged_privacy_version == LEGAL_VERSION
        assert user.privacy_acknowledged_at is not None
        assert user.age_verified is None
    assert delete_user_account("101", session_provider=provider)
    assert not UserRepository.has_current_legal_acceptance("101")


def test_old_document_version_requires_acceptance_again(legal_db):
    _, provider = legal_db
    UserRepository.accept_legal_documents("101")
    with provider() as session:
        session.query(User).filter_by(user_id="101").one().acknowledged_privacy_version = "old"
    assert not UserRepository.has_current_legal_acceptance("101")


def test_accept_callback_uses_actual_actor_and_preserves_start_payload(legal_db):
    async def scenario():
        state = fsm()
        await legal.show_legal_gate(fake_message(text="/start recommendations"), state)
        callback = fake_callback(f"legal:accept:{LEGAL_VERSION}")
        with patch("handlers.start.continue_start", new=AsyncMock()) as resume:
            await legal.accept_terms(callback, state)
        assert UserRepository.has_current_legal_acceptance("101")
        assert not UserRepository.has_current_legal_acceptance("999")
        assert resume.await_args.kwargs["user_id"] == "101"
        assert resume.await_args.kwargs["start_payload"] == "recommendations"
        assert resume.await_args.kwargs["age_verified"] is None
        assert await state.get_state() is None
    asyncio.run(scenario())


@pytest.mark.parametrize("case", ["old_version", "wrong_state", "declined"])
def test_stale_or_declined_accept_buttons_do_not_write_consent(legal_db, case):
    async def scenario():
        state = fsm()
        await legal.show_legal_gate(fake_message(), state)
        data = f"legal:accept:{LEGAL_VERSION}"
        if case == "old_version":
            data = "legal:accept:old"
        elif case == "wrong_state":
            await state.set_state(KbjuTestStates.entering_weight)
        else:
            await legal.decline_terms(fake_callback("legal:decline"), state)
        with patch("handlers.start.continue_start", new=AsyncMock()) as resume:
            await legal.accept_terms(fake_callback(data), state)
            resume.assert_not_awaited()
        assert not UserRepository.has_current_legal_acceptance("101")
    asyncio.run(scenario())


def test_documents_are_readable_before_acceptance_and_fit_telegram(legal_db):
    async def scenario():
        for key, document in LEGAL_DOCUMENTS.items():
            message = fake_message()
            for page in (0, 1, 999):
                await legal.show_document(message, key, origin="gate", page=page)
                rendered = message.answer.await_args.args[0]
                assert len(rendered.encode("utf-16-le")) // 2 < 4096
                assert "{support_contact}" not in rendered and "{updated_date}" not in rendered
                keyboard = message.answer.await_args.kwargs["reply_markup"]
                assert sum(b.text == "⬅️ Назад" for row in keyboard.inline_keyboard for b in row) == 1
            assert SUPPORT_CONTACT in document.read()
        assert not UserRepository.has_current_legal_acceptance("101")
        policy = LEGAL_DOCUMENTS["privacy"].read()
        assert "Google Gemini" in policy
        assert "/delete_account" not in policy and "24 час" not in policy
        assert "оперативной памяти" not in policy and "не используется для её обучения" not in policy
    asyncio.run(scenario())


def test_document_back_restores_gate_without_acceptance(legal_db):
    async def scenario():
        state = fsm()
        await legal.back_to_legal_origin(fake_callback("legal:back:gate"), state)
        assert await state.get_state() == LegalStates.reviewing.state
        assert not UserRepository.has_current_legal_acceptance("101")
    asyncio.run(scenario())


def test_stale_decline_does_not_clear_an_accepted_users_current_scenario(legal_db):
    async def scenario():
        UserRepository.accept_legal_documents("101")
        state = fsm()
        await state.set_state(KbjuTestStates.entering_weight)
        callback = fake_callback("legal:decline")
        await legal.decline_terms(callback, state)
        assert UserRepository.has_current_legal_acceptance("101")
        assert await state.get_state() == KbjuTestStates.entering_weight.state
        callback.message.edit_text.assert_not_awaited()
    asyncio.run(scenario())


def test_support_does_not_even_read_telegram_profile_fields(legal_db):
    async def scenario():
        message = fake_message(text="Кнопка не работает")
        message.caption = None
        message.bot.send_message = AsyncMock()
        # The Telegram actor intentionally exposes only id; any profile read fails.
        with patch.object(settings.AnalyticsRepository, "track_event"):
            await settings.handle_support_message(message, fsm())
        assert "101" in message.bot.send_message.await_args.kwargs["text"]
        _, provider = legal_db
        with provider() as session:
            record = session.query(SupportMessage).one()
            assert record.user_id == "101" and record.message_text == "Кнопка не работает"
    asyncio.run(scenario())


def test_dispatcher_gate_age_and_deletion_cannot_be_bypassed(legal_db):
    async def scenario():
        dp = Dispatcher(storage=MemoryStorage())
        bot = Bot(token="999:test-token")
        dp.message.outer_middleware(LegalAcceptanceMiddleware())
        dp.callback_query.outer_middleware(LegalAcceptanceMiddleware())
        dp.message.outer_middleware(OnboardingMiddleware())
        dp.callback_query.outer_middleware(OnboardingMiddleware())
        legal.register_legal_handlers(dp)
        fallback = Router()
        business = AsyncMock()

        async def business_handler(event):
            await business(event)

        fallback.message.register(business_handler)
        fallback.callback_query.register(business_handler)
        dp.include_router(fallback)
        calls = []
        sequence = 0

        async def fake_api(method, **kwargs):
            calls.append(method)
            if method.__api_method__ in {"sendMessage", "editMessageText"}:
                return Message(message_id=500, date=datetime.now(timezone.utc),
                               chat=Chat(id=101, type="private"), text=method.text)
            return True

        async def send(content, *, callback=False):
            nonlocal sequence
            sequence += 1
            actor = TelegramUser(id=101, is_bot=False, first_name="Test")
            msg = Message(message_id=sequence, date=datetime.now(timezone.utc),
                          chat=Chat(id=101, type="private"), from_user=actor, text=content)
            event = Update(update_id=sequence, callback_query=CallbackQuery(
                id=str(sequence), from_user=actor, chat_instance="test", data=content,
                message=msg.model_copy(update={"from_user": TelegramUser(id=999, is_bot=True, first_name="Bot")}),
            )) if callback else Update(update_id=sequence, message=msg)
            await dp.feed_update(bot, event)

        state = dp.fsm.get_context(bot=bot, chat_id=101, user_id=101)
        with patch.object(Bot, "__call__", new=AsyncMock(side_effect=fake_api)), patch(
            "middlewares.onboarding.MealRepository.get_kbju_settings", return_value=None
        ), patch("handlers.start.has_completed_kbju_test", return_value=False):
            await send("/start")
            assert await state.get_state() == LegalStates.reviewing.state
            await send("kbju_gender:male", callback=True)
            business.assert_not_awaited()
            await send("legal:doc:privacy:gate:0", callback=True)
            assert not UserRepository.has_current_legal_acceptance("101")
            await send("legal:back:gate", callback=True)
            await send("legal:delete", callback=True)
            assert await state.get_state() == AccountDeletionStates.waiting_for_button_confirmation.state
            await send("🏠 Главное меню")
            business.assert_not_awaited()
            await send("Да, удалить аккаунт")
            assert await state.get_state() == AccountDeletionStates.waiting_for_text_confirmation.state
            await send("wrong confirmation")
            assert await state.get_state() == AccountDeletionStates.waiting_for_text_confirmation.state
            await send("❌ Отмена")
            assert await state.get_state() is None
            await send("/start")
            await send(f"legal:accept:{LEGAL_VERSION}", callback=True)
            assert UserRepository.has_current_legal_acceptance("101")
            assert await state.get_state() == KbjuTestStates.entering_gender.state
            # Acceptance does not bypass the age gate.
            await send("add_meal", callback=True)
            business.assert_not_awaited()
            UserRepository.set_age_verification("101", True)
            with patch("middlewares.onboarding.MealRepository.get_kbju_settings", return_value=object()):
                await send("🍱 Дневник питания")
                business.assert_awaited_once()
        await bot.session.close()
        await dp.storage.close()
    asyncio.run(scenario())
