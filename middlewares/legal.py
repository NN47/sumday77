"""Require current offer acceptance before user-facing business scenarios."""
from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message
from config import ADMIN_ID
from database.repositories import UserRepository
from utils.legal_access import is_public_legal_event


class LegalAcceptanceMiddleware(BaseMiddleware):
    async def __call__(self, handler, event, data):
        if not isinstance(event, (Message, CallbackQuery)) or event.from_user is None:
            return await handler(event, data)
        if isinstance(event, Message) and event.from_user.id == ADMIN_ID and (event.text or "").startswith("/admin"):
            return await handler(event, data)
        state = data.get("state")
        if await is_public_legal_event(event, state):
            return await handler(event, data)
        if UserRepository.has_current_legal_acceptance(str(event.from_user.id)):
            return await handler(event, data)

        from handlers.legal import show_legal_gate
        if isinstance(event, CallbackQuery):
            await event.answer("Сначала ознакомься с документами и прими условия.")
            message = event.message
        else:
            message = event
        if message is not None and state is not None:
            await show_legal_gate(message, state)
        return None
