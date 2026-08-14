"""Middleware for mandatory KBJU onboarding."""
from __future__ import annotations

from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, ReplyKeyboardRemove, TelegramObject

from database.repositories import MealRepository, UserRepository
from config import ADMIN_ID
from handlers.kbju_test import (
    UNDERAGE_MESSAGE,
    restart_required_kbju_test,
    start_age_confirmation,
)
from states.user_states import AgeGateStates, KbjuTestStates
from utils.keyboards import kbju_age_range_inline


class OnboardingMiddleware(BaseMiddleware):
    """Centrally enforce the 18+ gate before the existing KBJU onboarding."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        if isinstance(event, Message):
            if await self._is_allowed_message(event, data):
                return await handler(event, data)

            state = data.get("state")
            if state is not None and event.from_user is not None:
                await self._redirect_message(event, state)
            return None

        if isinstance(event, CallbackQuery):
            if await self._is_allowed_callback(event, data):
                return await handler(event, data)

            state = data.get("state")
            if state is not None and event.message is not None and event.from_user is not None:
                await self._redirect_callback(event, state)
            return None

        return await handler(event, data)

    async def _is_allowed_message(self, message: Message, data: dict[str, Any]) -> bool:
        if message.from_user is None:
            return True

        text = (message.text or "").strip()
        if text.startswith("/admin") and message.from_user.id == ADMIN_ID:
            return True

        if text.startswith("/start"):
            return True

        user_id = str(message.from_user.id)
        age_verified = UserRepository.get_age_verification(user_id)

        state = data.get("state")
        current_state = None
        if state is not None:
            current_state = await state.get_state()

        if age_verified is not True:
            # Free-form text and stale reply buttons must not escape into other
            # routers while an age decision is pending. /start was allowed above.
            return False

        if MealRepository.get_kbju_settings(user_id):
            return True

        if current_state and current_state.startswith(f"{KbjuTestStates.__name__}:"):
            return True
        return False

    async def _is_allowed_callback(self, callback: CallbackQuery, data: dict[str, Any]) -> bool:
        if callback.from_user is None:
            return True

        user_id = str(callback.from_user.id)
        age_verified = UserRepository.get_age_verification(user_id)

        state = data.get("state")
        current_state = None
        if state is not None:
            current_state = await state.get_state()

        if age_verified is not True:
            return self._is_allowed_unverified_callback(
                current_state,
                age_verified,
                callback.data or "",
            )

        if MealRepository.get_kbju_settings(user_id):
            return True

        if current_state and current_state.startswith(f"{KbjuTestStates.__name__}:"):
            return True

        return False

    @staticmethod
    def _is_allowed_unverified_callback(
        current_state: str | None,
        age_verified: bool | None,
        callback_data: str,
    ) -> bool:
        if current_state == AgeGateStates.confirming_age.state:
            return callback_data.startswith("age_gate:")
        if age_verified is None:
            if current_state == KbjuTestStates.entering_gender.state:
                return callback_data.startswith("kbju_gender:")
            if current_state == KbjuTestStates.entering_age.state:
                return callback_data.startswith("kbju_age:") or callback_data == "kbju_back:gender"
        return False

    async def _redirect_message(self, message: Message, state) -> None:
        user_id = str(message.from_user.id)
        age_verified = UserRepository.get_age_verification(user_id)
        if age_verified is False:
            await state.clear()
            await message.answer(UNDERAGE_MESSAGE, reply_markup=ReplyKeyboardRemove())
            return
        if age_verified is not True:
            if MealRepository.get_kbju_settings(user_id):
                await start_age_confirmation(message, state)
            elif await state.get_state() == KbjuTestStates.entering_age.state:
                await message.answer(
                    "Выбери возрастную группу кнопкой ниже 👇",
                    reply_markup=kbju_age_range_inline,
                )
            else:
                await restart_required_kbju_test(message, state)
            return
        await restart_required_kbju_test(message, state)

    async def _redirect_callback(self, callback: CallbackQuery, state) -> None:
        user_id = str(callback.from_user.id)
        age_verified = UserRepository.get_age_verification(user_id)
        if age_verified is False:
            await callback.answer(UNDERAGE_MESSAGE, show_alert=True)
            await state.clear()
            return

        if age_verified is not True:
            await callback.answer("Сначала подтверди, что тебе уже исполнилось 18 лет.")
            if MealRepository.get_kbju_settings(user_id):
                await start_age_confirmation(callback.message, state)
            else:
                await restart_required_kbju_test(callback.message, state)
            return

        await callback.answer(
            "Сначала пройди стартовый тест КБЖУ, чтобы открыть остальные разделы.",
            show_alert=False,
        )
        await restart_required_kbju_test(callback.message, state)
