import asyncio
import os
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

os.environ.setdefault("API_TOKEN", "test-token")

from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage
from database.models import Base, User
from handlers.settings import (
    delete_account_confirm,
    delete_account_start,
    delete_account_text_confirm,
)
from services.notification_scheduler import NotificationScheduler
from user_operation_guard import (
    UserOperationBlocked,
    UserWriteBlocked,
    user_operation_guard,
)
from states.user_states import AccountDeletionStates, WellbeingStates


def make_message(user_id: str, bot, text: str):
    return SimpleNamespace(
        from_user=SimpleNamespace(id=int(user_id)),
        bot=bot,
        text=text,
        answer=AsyncMock(),
    )


def make_fsm_context(
    storage: MemoryStorage,
    user_id: str,
    *,
    chat_id: int | None = None,
) -> FSMContext:
    numeric_user_id = int(user_id)
    return FSMContext(
        storage=storage,
        key=StorageKey(
            bot_id=777,
            chat_id=chat_id if chat_id is not None else numeric_user_id,
            user_id=numeric_user_id,
        ),
    )


class AccountDeletionProcessSafetyTests(unittest.TestCase):
    user_a = "1001"
    user_b = "2002"

    def setUp(self):
        user_operation_guard.reset_for_testing()
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine, expire_on_commit=False)

    def tearDown(self):
        user_operation_guard.reset_for_testing()
        self.engine.dispose()

    def test_confirmation_fsm_is_isolated_between_users(self):
        async def scenario():
            bot = SimpleNamespace()
            storage = MemoryStorage()
            state_a = make_fsm_context(storage, self.user_a)
            state_b = make_fsm_context(storage, self.user_b)
            await state_b.set_state(WellbeingStates.note_text)
            await state_b.update_data(private_draft="B")
            message_a = make_message(self.user_a, bot, "🗑 Удалить аккаунт")
            message_b = make_message(self.user_b, bot, "🗑 Удалить аккаунт")

            async with user_operation_guard.operation(self.user_a):
                await delete_account_start(message_a, state_a)

            self.assertEqual(
                await state_a.get_state(),
                AccountDeletionStates.waiting_for_button_confirmation.state,
            )
            self.assertEqual(await state_b.get_state(), WellbeingStates.note_text.state)
            self.assertEqual(await state_b.get_data(), {"private_draft": "B"})

            async with user_operation_guard.operation(self.user_b):
                await delete_account_start(message_b, state_b)

            async with user_operation_guard.operation(self.user_a):
                await delete_account_confirm(message_a, state_a)

            self.assertEqual(
                await state_a.get_state(),
                AccountDeletionStates.waiting_for_text_confirmation.state,
            )
            self.assertEqual(
                await state_b.get_state(),
                AccountDeletionStates.waiting_for_button_confirmation.state,
            )
            self.assertFalse(hasattr(bot, "expecting_account_deletion_confirm"))
            self.assertFalse(hasattr(bot, "expecting_account_deletion_text_confirm"))

        asyncio.run(scenario())

    def test_success_clears_only_deleted_users_fsm_and_caches(self):
        async def scenario():
            bot = SimpleNamespace(
                last_meal_ids={self.user_a: 11, self.user_b: 22},
                selected_food_diary_dates={self.user_a: "2026-08-10", self.user_b: "2026-08-11"},
                food_diary_message_ids={self.user_a: {"x": 1}, self.user_b: {"x": 2}},
                meal_comment_in_progress={(self.user_a, 11), (self.user_b, 22)},
            )
            scheduler = NotificationScheduler(bot)
            scheduler.sent_notifications_today = {
                f"{self.user_a}_11_08:00_2026-08-11",
                f"{self.user_b}_22_08:00_2026-08-11",
            }
            bot.sumday77_notification_scheduler = scheduler

            storage = MemoryStorage()
            state_a = make_fsm_context(storage, self.user_a)
            secondary_state_a = make_fsm_context(storage, self.user_a, chat_id=9001)
            state_b = make_fsm_context(storage, self.user_b)
            await state_a.set_state(AccountDeletionStates.waiting_for_text_confirmation)
            await state_a.update_data(confirmation="A")
            await secondary_state_a.set_state(WellbeingStates.note_text)
            await secondary_state_a.update_data(private_draft="A-secondary")
            await state_b.set_state(WellbeingStates.note_text)
            await state_b.update_data(private_draft="B")
            message_a = make_message(
                self.user_a,
                bot,
                "Я удаляю аккаунт Sumday77",
            )

            with patch("handlers.settings.delete_user_account", return_value=True):
                async with user_operation_guard.operation(self.user_a):
                    await delete_account_text_confirm(message_a, state_a)

            self.assertFalse(
                any(str(key.user_id) == self.user_a for key in storage.storage)
            )
            self.assertEqual(await state_b.get_state(), WellbeingStates.note_text.state)
            self.assertEqual(await state_b.get_data(), {"private_draft": "B"})

            self.assertEqual(bot.last_meal_ids, {self.user_b: 22})
            self.assertEqual(bot.selected_food_diary_dates, {self.user_b: "2026-08-11"})
            self.assertEqual(bot.food_diary_message_ids, {self.user_b: {"x": 2}})
            self.assertEqual(bot.meal_comment_in_progress, {(self.user_b, 22)})
            self.assertEqual(
                scheduler.sent_notifications_today,
                {f"{self.user_b}_22_08:00_2026-08-11"},
            )
            self.assertEqual(user_operation_guard.status(self.user_a), "deleted")

        asyncio.run(scenario())

    def test_other_user_runs_while_deletion_blocks_new_target_operation(self):
        async def scenario():
            deletion_started = asyncio.Event()
            finish_deletion = asyncio.Event()
            target_created = []
            other_user_completed = asyncio.Event()

            async def delete_a():
                async with user_operation_guard.operation(self.user_a):
                    await user_operation_guard.begin_deletion(self.user_a)
                    deletion_started.set()
                    await finish_deletion.wait()
                    user_operation_guard.complete_deletion(self.user_a)

            async def create_for_a():
                await deletion_started.wait()
                try:
                    async with user_operation_guard.operation(self.user_a):
                        target_created.append(True)
                        with self.Session() as session:
                            session.add(User(user_id=self.user_a))
                            session.commit()
                except UserOperationBlocked:
                    return

            async def run_for_b():
                await deletion_started.wait()
                async with user_operation_guard.operation(self.user_b):
                    with self.Session() as session:
                        session.add(User(user_id=self.user_b))
                        session.commit()
                    other_user_completed.set()

            deletion_task = asyncio.create_task(delete_a())
            blocked_task = asyncio.create_task(create_for_a())
            other_task = asyncio.create_task(run_for_b())

            await asyncio.wait_for(other_user_completed.wait(), timeout=1)
            self.assertEqual(target_created, [])
            finish_deletion.set()
            await asyncio.gather(deletion_task, blocked_task, other_task)

            with self.Session() as session:
                self.assertIsNone(session.query(User).filter_by(user_id=self.user_a).first())
                self.assertIsNotNone(session.query(User).filter_by(user_id=self.user_b).first())

        asyncio.run(scenario())

    def test_stale_async_operation_cannot_restore_deleted_account(self):
        async def scenario():
            with self.Session() as session:
                session.add(User(user_id=self.user_a))
                session.commit()

            release_stale_task = asyncio.Event()

            async with user_operation_guard.operation(self.user_a):
                async def stale_write():
                    await release_stale_task.wait()
                    with self.Session() as session:
                        session.add(User(user_id=self.user_a))
                        session.commit()

                stale_task = asyncio.create_task(stale_write())

            async with user_operation_guard.operation(self.user_a):
                await user_operation_guard.begin_deletion(self.user_a)
                with self.Session() as session:
                    session.query(User).filter_by(user_id=self.user_a).delete()
                    session.commit()
                user_operation_guard.complete_deletion(self.user_a)

            release_stale_task.set()
            with self.assertRaises(UserWriteBlocked):
                await stale_task

            with self.Session() as session:
                self.assertIsNone(session.query(User).filter_by(user_id=self.user_a).first())

        asyncio.run(scenario())

    def test_database_rollback_preserves_fsm_and_caches(self):
        async def scenario():
            bot = SimpleNamespace(
                last_meal_ids={self.user_a: 11, self.user_b: 22},
                selected_food_diary_dates={self.user_a: "2026-08-10"},
                food_diary_message_ids={self.user_a: {"x": 1}},
                meal_comment_in_progress={(self.user_a, 11)},
            )
            storage = MemoryStorage()
            state_a = make_fsm_context(storage, self.user_a)
            await state_a.set_state(AccountDeletionStates.waiting_for_text_confirmation)
            await state_a.update_data(confirmation="A")
            message_a = make_message(
                self.user_a,
                bot,
                "Я удаляю аккаунт Sumday77",
            )

            with patch("handlers.settings.delete_user_account", return_value=False):
                async with user_operation_guard.operation(self.user_a):
                    await delete_account_text_confirm(message_a, state_a)

            self.assertEqual(
                await state_a.get_state(),
                AccountDeletionStates.waiting_for_text_confirmation.state,
            )
            self.assertEqual(await state_a.get_data(), {"confirmation": "A"})
            self.assertEqual(bot.last_meal_ids, {self.user_a: 11, self.user_b: 22})
            self.assertEqual(bot.selected_food_diary_dates, {self.user_a: "2026-08-10"})
            self.assertEqual(bot.food_diary_message_ids, {self.user_a: {"x": 1}})
            self.assertEqual(bot.meal_comment_in_progress, {(self.user_a, 11)})
            self.assertEqual(user_operation_guard.status(self.user_a), "active")

            async with user_operation_guard.operation(self.user_a):
                pass

        asyncio.run(scenario())


if __name__ == "__main__":
    unittest.main()
