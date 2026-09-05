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
from middlewares.user_activity import UserActivityMiddleware
from states.user_states import AccountDeletionStates, KbjuTestStates, LegalStates
from utils.legal_documents import LEGAL_DOCUMENTS, LEGAL_VERSION, SUPPORT_CONTACT
from user_operation_guard import user_operation_guard


@pytest.fixture
def legal_db(monkeypatch):
    user_operation_guard.reset_for_testing()
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
    user_operation_guard.reset_for_testing()
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


async def current_button(state, prefix):
    keyboard = legal.gate_keyboard(await state.get_data())
    return next(button.callback_data for row in keyboard.inline_keyboard for button in row
                if (button.callback_data or "").startswith(prefix))


async def select_both(state):
    for key in ("terms", "privacy"):
        callback = fake_callback(await current_button(state, f"legal:toggle:{key}:"))
        await legal.toggle_legal_choice(callback, state)
    return await current_button(state, "legal:accept:")


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
        callback = fake_callback(await select_both(state))
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
        data = await select_both(state)
        if case == "old_version":
            data = data.replace(LEGAL_VERSION, "old")
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
        terms = LEGAL_DOCUMENTS["terms"].read()
        assert "любые поля ввода Бота" in terms and "номера паспортов" in terms
        assert "нецензурную лексику" in terms and "призывы к нарушению законов РФ" in terms
        assert "угрозой жизни, здоровью или психологическому благополучию" in terms
        assert "Бот не является сервисом психологической или экстренной помощи" in terms
    asyncio.run(scenario())


def test_document_back_restores_gate_without_acceptance(legal_db):
    async def scenario():
        state = fsm()
        await legal.back_to_legal_origin(fake_callback("legal:back:gate"), state)
        assert await state.get_state() == LegalStates.reviewing.state
        assert not UserRepository.has_current_legal_acceptance("101")
    asyncio.run(scenario())


def test_checkboxes_are_independent_and_only_final_button_records_acceptance(legal_db):
    async def scenario():
        state = fsm()
        message = fake_message()
        await legal.show_legal_gate(message, state)
        keyboard = message.answer.await_args.kwargs["reply_markup"]
        assert [b.text for row in keyboard.inline_keyboard[:2] for b in row] == [
            "📄 Соглашение", "☐ Принимаю", "🔒 Политика", "☐ Ознакомлен",
        ]
        assert not any(b.text == "Принять условия" for row in keyboard.inline_keyboard for b in row)
        accept_data = await select_both(state)
        assert not UserRepository.has_current_legal_acceptance("101")
        _, provider = legal_db
        with provider() as session:
            assert session.query(User).count() == 0

        await legal.toggle_legal_choice(fake_callback(await current_button(state, "legal:toggle:terms:")), state)
        keyboard = legal.gate_keyboard(await state.get_data())
        assert not any(b.text == "Принять условия" for row in keyboard.inline_keyboard for b in row)
        with patch("handlers.start.continue_start", new=AsyncMock()) as resume:
            await legal.accept_terms(fake_callback(accept_data), state)
            # Even a fabricated current button must not bypass an unchecked box.
            data = await state.get_data()
            await legal.accept_terms(fake_callback(
                f"legal:accept:{LEGAL_VERSION}:{data['legal_nonce']}:{data['legal_revision']}"
            ), state)
            resume.assert_not_awaited()
        assert not UserRepository.has_current_legal_acceptance("101")
        assert (await state.get_data())["legal_choices"] == {"terms": False, "privacy": True}
        assert all(len(b.callback_data.encode()) <= 64 for row in keyboard.inline_keyboard for b in row if b.callback_data)
    asyncio.run(scenario())


def test_reading_and_pagination_preserve_selection_without_auto_checking(legal_db):
    async def scenario():
        state = fsm()
        await legal.show_legal_gate(fake_message(text="/start recommendations"), state)
        await legal.toggle_legal_choice(fake_callback(await current_button(state, "legal:toggle:terms:")), state)
        before = await state.get_data()
        for key in ("terms", "privacy"):
            for page in (0, 1):
                await legal.document_page(fake_callback(f"legal:doc:{key}:gate:{page}"))
            await legal.back_to_legal_origin(fake_callback("legal:back:gate"), state)
            assert await state.get_data() == before
        assert not UserRepository.has_current_legal_acceptance("101")
    asyncio.run(scenario())


def test_old_single_accept_button_opens_checkboxes_without_accepting(legal_db):
    async def scenario():
        state = fsm()
        # MemoryStorage can be empty after a restart with an old message still visible.
        callback = fake_callback(f"legal:accept:{LEGAL_VERSION}")
        with patch("handlers.start.continue_start", new=AsyncMock()) as resume:
            await legal.accept_terms(callback, state)
            resume.assert_not_awaited()
        assert await state.get_state() == LegalStates.reviewing.state
        assert (await state.get_data())["legal_choices"] == {"terms": False, "privacy": False}
        assert not UserRepository.has_current_legal_acceptance("101")
        callback.message.edit_text.assert_awaited_once()
    asyncio.run(scenario())


def test_restart_invalidates_previous_buttons_and_decline_clears_draft(legal_db):
    async def scenario():
        state = fsm()
        await legal.show_legal_gate(fake_message(), state)
        accept_data = await select_both(state)
        toggle_data = await current_button(state, "legal:toggle:terms:")
        await legal.show_legal_gate(fake_message(), state)
        new_data = await state.get_data()
        await legal.toggle_legal_choice(fake_callback(toggle_data), state)
        with patch("handlers.start.continue_start", new=AsyncMock()) as resume:
            await legal.accept_terms(fake_callback(accept_data), state)
            resume.assert_not_awaited()
        assert await state.get_data() == new_data
        assert not UserRepository.has_current_legal_acceptance("101")
        await legal.decline_terms(fake_callback(await current_button(state, "legal:decline:")), state)
        assert await state.get_state() is None
        assert await state.get_data() == {}
        await legal.back_to_legal_origin(fake_callback("legal:home"), state)
        assert (await state.get_data())["legal_choices"] == {"terms": False, "privacy": False}
    asyncio.run(scenario())


def test_repeat_accept_and_old_document_back_do_not_restart_onboarding(legal_db):
    async def scenario():
        state = fsm()
        await legal.show_legal_gate(fake_message(), state)
        callback = fake_callback(await select_both(state))
        with patch("handlers.start.continue_start", new=AsyncMock()) as resume:
            await legal.accept_terms(callback, state)
            await state.set_state(KbjuTestStates.entering_weight)
            _, provider = legal_db
            with provider() as session:
                accepted_at = session.query(User).one().terms_accepted_at
            await legal.accept_terms(callback, state)
            await legal.back_to_legal_origin(fake_callback("legal:back:gate"), state)
            resume.assert_awaited_once()
            with provider() as session:
                assert session.query(User).one().terms_accepted_at == accepted_at
        assert await state.get_state() == KbjuTestStates.entering_weight.state
    asyncio.run(scenario())


def test_settings_document_returns_to_settings_without_changing_acceptance(legal_db):
    async def scenario():
        UserRepository.accept_legal_documents("101")
        state = fsm()
        with patch.object(settings, "settings", new=AsyncMock()) as show_settings:
            await legal.back_to_legal_origin(fake_callback("legal:back:settings"), state)
            show_settings.assert_awaited_once()
        assert UserRepository.has_current_legal_acceptance("101")
    asyncio.run(scenario())


def test_cancel_deletion_from_old_gate_returns_accepted_user_to_settings(legal_db):
    async def scenario():
        UserRepository.accept_legal_documents("101")
        state = fsm()
        await state.set_state(KbjuTestStates.entering_weight)
        await legal.delete_from_legal_gate(fake_callback("legal:delete"), state)
        message = fake_message(text="❌ Отмена")
        await settings.delete_account_cancel(message, state)
        assert await state.get_state() is None
        assert message.answer.await_args.kwargs["reply_markup"] == settings.settings_menu
        assert UserRepository.has_current_legal_acceptance("101")
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
        dp.message.outer_middleware(UserActivityMiddleware())
        dp.callback_query.outer_middleware(UserActivityMiddleware())
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
            terms_choice = await current_button(state, "legal:toggle:terms:")
            # The real user-operation middleware serializes duplicate Telegram taps.
            await asyncio.gather(send(terms_choice, callback=True), send(terms_choice, callback=True))
            assert (await state.get_data())["legal_choices"] == {"terms": True, "privacy": False}
            await send("legal:delete", callback=True)
            assert await state.get_state() == AccountDeletionStates.waiting_for_button_confirmation.state
            await send("🏠 Главное меню")
            business.assert_not_awaited()
            await send("Да, удалить аккаунт")
            assert await state.get_state() == AccountDeletionStates.waiting_for_text_confirmation.state
            await send("wrong confirmation")
            assert await state.get_state() == AccountDeletionStates.waiting_for_text_confirmation.state
            await send("❌ Отмена")
            assert await state.get_state() == LegalStates.reviewing.state
            assert (await state.get_data())["legal_choices"] == {"terms": True, "privacy": False}
            await send("/start")
            for key in ("terms", "privacy"):
                await send(await current_button(state, f"legal:toggle:{key}:"), callback=True)
            await send(await current_button(state, "legal:accept:"), callback=True)
            assert UserRepository.has_current_legal_acceptance("101")
            assert await state.get_state() == KbjuTestStates.entering_gender.state
            # Acceptance does not bypass the age gate.
            await send("add_meal", callback=True)
            business.assert_not_awaited()
            UserRepository.set_age_verification("101", True)
            with patch("middlewares.onboarding.MealRepository.get_kbju_settings", return_value=object()):
                await send("🍱 Дневник питания")
                business.assert_awaited_once()
                await send("🗑 Удалить аккаунт")
                await send("Да, удалить аккаунт")
                _, provider = legal_db
                with patch("handlers.settings.delete_user_account",
                           side_effect=lambda user_id: delete_user_account(user_id, session_provider=provider)):
                    await send("Я удаляю аккаунт Sumday77")
                assert not UserRepository.has_current_legal_acceptance("101")
                await send("🍱 Дневник питания")
                business.assert_awaited_once()
                await send("/start")
                assert await state.get_state() == LegalStates.reviewing.state
                assert (await state.get_data())["legal_choices"] == {"terms": False, "privacy": False}
        await bot.session.close()
        await dp.storage.close()
    asyncio.run(scenario())
