"""Reject personal data before it reaches user-facing business handlers."""
from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import Message, TelegramObject

from utils.sensitive_text import SensitiveDataType, check_sensitive_personal_data

logger = logging.getLogger(__name__)

SENSITIVE_INPUT_REJECTED_TEXT = (
    "⚠️ Сообщение не обработано: похоже, оно содержит личные или конфиденциальные данные.\n\n"
    "Удалите ФИО, телефон, email, адрес, реквизиты документов, банковские данные, "
    "пароли и коды доступа, затем отправьте сообщение ещё раз."
)


class SensitiveInputMiddleware(BaseMiddleware):
    """Apply one local personal-data check to every incoming message and caption."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        if not isinstance(event, Message) or event.from_user is None:
            return await handler(event, data)

        reason: SensitiveDataType | None = None
        message_text = event.text or event.caption or ""
        if message_text:
            check = check_sensitive_personal_data(message_text)
            if check.is_sensitive:
                reason = check.reason

        # Telegram contact and geolocation objects are direct structured personal
        # data and are not required by any Sumday77 scenario.
        if reason is None and event.contact is not None:
            reason = SensitiveDataType.PHONE
        if reason is None and (event.location is not None or event.venue is not None):
            reason = SensitiveDataType.ADDRESS

        if reason is None:
            return await handler(event, data)

        logger.info(
            "Sensitive input rejected reason=%s message_chars=%s",
            reason.value,
            len(message_text),
        )
        await event.answer(SENSITIVE_INPUT_REJECTED_TEXT)
        return None
