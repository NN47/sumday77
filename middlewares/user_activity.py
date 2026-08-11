"""Middleware обновления активности пользователя."""
from __future__ import annotations

from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject

from database.repositories import UserRepository
from user_operation_guard import UserOperationBlocked, user_operation_guard


class UserActivityMiddleware(BaseMiddleware):
    """При любом апдейте создаёт/обновляет пользователя."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        user_id = None
        if isinstance(event, Message) and event.from_user:
            user_id = str(event.from_user.id)
        elif isinstance(event, CallbackQuery) and event.from_user:
            user_id = str(event.from_user.id)

        if not user_id:
            return await handler(event, data)

        allow_reactivate = False
        if isinstance(event, Message):
            message_text = (event.text or "").strip()
            command = message_text.split(maxsplit=1)[0].casefold() if message_text else ""
            allow_reactivate = command == "/start" or command.startswith("/start@")

        try:
            async with user_operation_guard.operation(
                user_id,
                allow_reactivate=allow_reactivate,
            ):
                UserRepository.touch_user(user_id)
                return await handler(event, data)
        except UserOperationBlocked:
            return None
