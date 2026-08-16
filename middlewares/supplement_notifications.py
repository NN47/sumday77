"""Защита inline-шага настройки уведомлений добавки от текстовых действий."""

from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.fsm.context import FSMContext
from aiogram.types import Message, ReplyKeyboardRemove, TelegramObject

from states.user_states import SupplementStates


class SupplementNotificationMessageGuard(BaseMiddleware):
    """Не пропускает сообщения в handlers во время inline-only шага."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: Message,
        data: dict[str, Any],
    ) -> Any:
        state: FSMContext | None = data.get("state")
        if (
            state is not None
            and await state.get_state()
            == SupplementStates.choosing_notifications.state
        ):
            await event.answer(
                "На этом шаге используй кнопки под сообщением настройки уведомлений.",
                reply_markup=ReplyKeyboardRemove(),
            )
            return None

        return await handler(event, data)
