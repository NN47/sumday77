"""Защита inline-шагов мастера создания добавки от текстовых действий."""

import re
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.fsm.context import FSMContext
from aiogram.types import Message, ReplyKeyboardRemove, TelegramObject

from states.user_states import SupplementStates


CREATION_INLINE_ONLY_STATES = {
    SupplementStates.selecting_catalog_item.state,
    SupplementStates.selecting_days.state,
    SupplementStates.choosing_duration.state,
}


class SupplementCreationMessageGuard(BaseMiddleware):
    """Блокирует reply-действия внутри inline-only мастера создания."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: Message,
        data: dict[str, Any],
    ) -> Any:
        state: FSMContext | None = data.get("state")
        current_state = await state.get_state() if state is not None else None
        state_data = await state.get_data() if state is not None else {}
        is_creation = state_data.get("supplement_id") is None
        is_notification_step = (
            current_state == SupplementStates.choosing_notifications.state
        )
        is_creation_inline_step = (
            is_creation and current_state in CREATION_INLINE_ONLY_STATES
        )
        is_creation_time_step = (
            is_creation and current_state == SupplementStates.entering_time.state
        )
        text = (event.text or "").strip()
        is_manual_time = bool(
            re.fullmatch(r"(?:\d{1,4}|(?:[01]\d|2[0-3]):[0-5]\d)", text)
        )

        if is_creation_time_step and is_manual_time:
            return await handler(event, data)

        if is_notification_step or is_creation_inline_step or is_creation_time_step:
            await event.answer(
                "На этом шаге используй кнопки под сообщением бота.",
                reply_markup=ReplyKeyboardRemove(),
            )
            return None

        return await handler(event, data)
