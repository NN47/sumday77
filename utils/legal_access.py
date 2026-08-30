"""Public document/deletion routes shared by the legal and age gates."""
from aiogram.types import CallbackQuery, Message
from states.user_states import AccountDeletionStates
from utils.legal_documents import PRIVACY_BUTTON, TERMS_BUTTON
from utils.pagination import PAGINATION_NOOP_CALLBACK

DELETE_ACCOUNT_BUTTON = "🗑 Удалить аккаунт"


async def is_public_legal_event(event, state) -> bool:
    if isinstance(event, CallbackQuery):
        return bool(event.data and (event.data.startswith("legal:") or event.data == PAGINATION_NOOP_CALLBACK))
    if not isinstance(event, Message):
        return False
    if event.text in {TERMS_BUTTON, PRIVACY_BUTTON, DELETE_ACCOUNT_BUTTON}:
        return True
    command = (event.text or "").split(maxsplit=1)
    if command and command[0].split("@")[0] == "/start":
        return False
    current_state = await state.get_state() if state else None
    return current_state in {
        AccountDeletionStates.waiting_for_button_confirmation.state,
        AccountDeletionStates.waiting_for_text_confirmation.state,
    }
