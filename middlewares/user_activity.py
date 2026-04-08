"""Middleware обновления активности пользователя."""
from __future__ import annotations

from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject

from database.repositories import UserRepository


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

        if user_id:
            UserRepository.touch_user(user_id)

        return await handler(event, data)
