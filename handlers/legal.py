"""Public document reader and explicit, versioned offer acceptance."""
from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message, ReplyKeyboardRemove

from database.repositories import UserRepository
from states.user_states import LegalStates
from utils.legal_documents import LEGAL_DOCUMENTS, LEGAL_VERSION, PRIVACY_BUTTON, SUPPORT_URL, TERMS_BUTTON
from utils.pagination import PAGINATION_NOOP_CALLBACK, build_pagination_keyboard, clamp_page
from utils.telegram_text import split_telegram_message

router = Router(name="legal")


def document_buttons(origin: str) -> list[list[InlineKeyboardButton]]:
    return [[InlineKeyboardButton(text=doc.title, callback_data=f"legal:doc:{key}:{origin}:0")]
            for key, doc in LEGAL_DOCUMENTS.items()]


def gate_keyboard(*, can_accept: bool = True) -> InlineKeyboardMarkup:
    rows = document_buttons("gate")
    if can_accept:
        rows.append([InlineKeyboardButton(text="Принять условия", callback_data=f"legal:accept:{LEGAL_VERSION}")])
        rows.append([InlineKeyboardButton(text="Не принимаю", callback_data="legal:decline")])
    else:
        rows.append([InlineKeyboardButton(text="Вернуться к условиям", callback_data="legal:home")])
    rows.append([InlineKeyboardButton(text="💬 Поддержка", url=SUPPORT_URL)])
    rows.append([InlineKeyboardButton(text="🗑 Удалить аккаунт", callback_data="legal:delete")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def _render(message: Message, text: str, keyboard, *, edit: bool) -> None:
    if edit:
        try:
            await message.edit_text(text, reply_markup=keyboard, parse_mode=None)
            return
        except TelegramBadRequest as exc:
            if "message is not modified" in str(exc).lower():
                return
            raise
    await message.answer(text, reply_markup=keyboard, parse_mode=None)


async def show_legal_gate(message: Message, state: FSMContext, *, edit: bool = False) -> None:
    previous = await state.get_data()
    start_payload = previous.get("legal_start_payload", "")
    text = message.text or ""
    command, _, argument = text.partition(" ")
    if command.split("@")[0] == "/start":
        start_payload = argument.strip()
    await state.clear()
    await state.set_state(LegalStates.reviewing)
    await state.update_data(legal_version=LEGAL_VERSION, legal_start_payload=start_payload)
    if not edit:
        await message.answer("📄 Условия использования Sumday77", reply_markup=ReplyKeyboardRemove())
    await _render(
        message,
        "Перед началом работы ознакомься с документами ниже.\n\n"
        "Нажимая «Принять условия», ты принимаешь пользовательское соглашение "
        "и подтверждаешь ознакомление с политикой обработки данных.\n\n"
        "Без принятия условий тест и основные разделы недоступны. "
        "Документы, поддержка и удаление аккаунта остаются доступны.",
        gate_keyboard(), edit=edit,
    )


async def show_document(message: Message, key: str, *, origin: str = "settings", page: int = 0, edit: bool = False) -> None:
    document = LEGAL_DOCUMENTS[key]
    pages = split_telegram_message(document.read(), limit=3300)
    page = clamp_page(page, len(pages))
    keyboard = build_pagination_keyboard(
        page, len(pages), f"legal:doc:{key}:{origin}",
        extra_rows=[[InlineKeyboardButton(text="⬅️ Назад", callback_data=f"legal:back:{origin}")]],
        pagination_first=True,
    )
    await _render(message, pages[page], keyboard, edit=edit)


@router.message(F.text == TERMS_BUTTON)
async def terms_document(message: Message):
    await show_document(message, "terms")


@router.message(F.text == PRIVACY_BUTTON)
async def privacy_document(message: Message):
    await show_document(message, "privacy")


@router.callback_query(F.data.startswith("legal:doc:"))
async def document_page(callback: CallbackQuery):
    parts = (callback.data or "").split(":")
    if len(parts) != 5 or parts[2] not in LEGAL_DOCUMENTS or parts[3] not in {"gate", "settings"}:
        await callback.answer("Документ не найден.", show_alert=True)
        return
    try:
        page = int(parts[4])
    except ValueError:
        await callback.answer("Страница не найдена.", show_alert=True)
        return
    await callback.answer()
    await show_document(callback.message, parts[2], origin=parts[3], page=page, edit=True)


@router.callback_query(F.data.in_({"legal:home", "legal:back:gate", "legal:back:settings"}))
async def back_to_legal_origin(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    user_id = str(callback.from_user.id)
    if callback.data == "legal:back:settings" and UserRepository.has_current_legal_acceptance(user_id):
        from handlers.settings import settings
        await callback.message.edit_reply_markup(reply_markup=None)
        await settings(callback.message, state)
        return
    await show_legal_gate(callback.message, state, edit=True)


@router.callback_query(F.data == "legal:decline")
async def decline_terms(callback: CallbackQuery, state: FSMContext):
    if UserRepository.has_current_legal_acceptance(str(callback.from_user.id)):
        await callback.answer("Условия уже приняты. Для прекращения использования доступно удаление аккаунта.", show_alert=True)
        return
    await callback.answer()
    await state.clear()
    await _render(callback.message,
                  "Условия не приняты. Тест не запущен.\n\n"
                  "Можно прочитать документы, обратиться в поддержку или удалить аккаунт.",
                  gate_keyboard(can_accept=False), edit=True)


@router.callback_query(F.data.startswith("legal:accept:"))
async def accept_terms(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    if (callback.data != f"legal:accept:{LEGAL_VERSION}"
            or await state.get_state() != LegalStates.reviewing.state
            or data.get("legal_version") != LEGAL_VERSION):
        await callback.answer("Открой актуальные условия и подтверди их принятие.", show_alert=True)
        return
    user_id = str(callback.from_user.id)
    UserRepository.accept_legal_documents(user_id)
    await callback.answer("Условия приняты")
    await _render(callback.message, "✅ Условия приняты. Ознакомление с политикой сохранено.", None, edit=True)
    await state.clear()
    from handlers.start import continue_start
    await continue_start(callback.message, state, user_id=user_id,
                         age_verified=UserRepository.get_age_verification(user_id),
                         start_payload=data.get("legal_start_payload", ""))


@router.callback_query(F.data == "legal:delete")
async def delete_from_legal_gate(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await callback.message.edit_reply_markup(reply_markup=None)
    from handlers.settings import delete_account_start
    await delete_account_start(callback.message, state)


@router.callback_query(F.data == PAGINATION_NOOP_CALLBACK)
async def pagination_noop(callback: CallbackQuery):
    await callback.answer()


def register_legal_handlers(dp):
    # Deletion FSM must consume its input before generic main-menu handlers.
    from handlers.settings import account_deletion_router
    dp.include_router(account_deletion_router)
    dp.include_router(router)
