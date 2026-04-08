"""Middleware for mandatory KBJU onboarding."""
from __future__ import annotations

from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject

from database.repositories import MealRepository
from config import ADMIN_ID
from handlers.kbju_test import restart_required_kbju_test
from states.user_states import KbjuTestStates


class OnboardingMiddleware(BaseMiddleware):
    """Blocks access until the user completes the initial KBJU test."""

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
            if state is not None:
                await restart_required_kbju_test(event, state)
            return None

        if isinstance(event, CallbackQuery):
            if await self._is_allowed_callback(event, data):
                return await handler(event, data)

            await event.answer(
                "Сначала пройди стартовый тест КБЖУ, чтобы открыть остальные разделы.",
                show_alert=False,
            )
            state = data.get("state")
            if state is not None and event.message is not None:
                await restart_required_kbju_test(event.message, state)
            return None

        return await handler(event, data)

    async def _is_allowed_message(self, message: Message, data: dict[str, Any]) -> bool:
        if message.from_user is None:
            return True

        if MealRepository.get_kbju_settings(str(message.from_user.id)):
            return True

        state = data.get("state")
        if state is not None:
            current_state = await state.get_state()
            if current_state and current_state.startswith(f"{KbjuTestStates.__name__}:"):
                return True

        text = (message.text or "").strip()
        if text.startswith("/admin") and message.from_user.id == ADMIN_ID:
            return True
        return text.startswith("/start")

    async def _is_allowed_callback(self, callback: CallbackQuery, data: dict[str, Any]) -> bool:
        if callback.from_user is None:
            return True

        if MealRepository.get_kbju_settings(str(callback.from_user.id)):
            return True

        state = data.get("state")
        if state is not None:
            current_state = await state.get_state()
            if current_state and current_state.startswith(f"{KbjuTestStates.__name__}:"):
                return True

        return False
