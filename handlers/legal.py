"""Public document reader and explicit, versioned offer acceptance."""
from secrets import token_hex

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

# Only presentation labels live here; document contents and versions stay unchanged.
LEGAL_CHOICES = {
    "terms": ("📄 Соглашение", "Принимаю"),
    "privacy": ("🔒 Политика", "Ознакомлен"),
}


def _choice_suffix(data: dict) -> str:
    return f"{data['legal_nonce']}:{data['legal_revision']}"


def _all_selected(data: dict) -> bool:
    choices = data.get("legal_choices", {})
    return all(choices.get(key) is True for key in LEGAL_CHOICES)


def document_buttons(origin: str) -> list[list[InlineKeyboardButton]]:
    return [[InlineKeyboardButton(text=doc.title, callback_data=f"legal:doc:{key}:{origin}:0")]
            for key, doc in LEGAL_DOCUMENTS.items()]


def gate_keyboard(data: dict | None = None, *, can_accept: bool = True) -> InlineKeyboardMarkup:
    rows = []
    if can_accept:
        suffix = _choice_suffix(data)
        for key, (title, label) in LEGAL_CHOICES.items():
            selected = data.get("legal_choices", {}).get(key, False)
            rows.append([
                InlineKeyboardButton(text=title, callback_data=f"legal:doc:{key}:gate:0"),
                InlineKeyboardButton(text=f"{'✅' if selected else '☐'} {label}",
                                     callback_data=f"legal:toggle:{key}:{suffix}"),
            ])
        if _all_selected(data):
            # Keep the exact button name used by the current agreement.
            rows.append([InlineKeyboardButton(text="Принять условия",
                         callback_data=f"legal:accept:{LEGAL_VERSION}:{suffix}")])
        rows.append([InlineKeyboardButton(text="Не принимаю", callback_data=f"legal:decline:{suffix}")])
    else:
        rows = document_buttons("gate")
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
    is_start = command.split("@")[0] == "/start"
    if is_start:
        start_payload = argument.strip()
    if (is_start or await state.get_state() != LegalStates.reviewing.state
            or previous.get("legal_version") != LEGAL_VERSION or not previous.get("legal_nonce")):
        await state.clear()
        await state.set_state(LegalStates.reviewing)
        await state.update_data(legal_version=LEGAL_VERSION, legal_start_payload=start_payload,
                                legal_choices={key: False for key in LEGAL_CHOICES},
                                legal_nonce=token_hex(4), legal_revision=0)
    data = await state.get_data()
    if not edit:
        await message.answer("📄 Условия использования Sumday77", reply_markup=ReplyKeyboardRemove())
    await _render(
        message,
        "📄 Перед началом работы\n\n"
        "Открой документы слева и отдельно отметь оба пункта справа:\n"
        "• принимаю пользовательское соглашение;\n"
        "• ознакомлен с политикой обработки данных.\n\n"
        "Кнопка «Принять условия» появится после двух отметок. "
        "До её нажатия галочки можно снять; подтверждения сохранятся только после нажатия кнопки.\n\n"
        "Без принятия условий тест и основные разделы недоступны. "
        "Документы, поддержка и удаление аккаунта остаются доступны.",
        gate_keyboard(data), edit=edit,
    )


async def _current_choice_data(callback: CallbackQuery, state: FSMContext, suffix: str) -> dict | None:
    """Reject queued double taps and buttons from previous screens/sessions."""
    data = await state.get_data()
    if (await state.get_state() != LegalStates.reviewing.state
            or data.get("legal_version") != LEGAL_VERSION
            or not data.get("legal_nonce") or suffix != _choice_suffix(data)):
        await callback.answer("Эта кнопка устарела. Используй последний экран условий или /start.", show_alert=True)
        return None
    return data


@router.callback_query(F.data.startswith("legal:toggle:"))
async def toggle_legal_choice(callback: CallbackQuery, state: FSMContext):
    parts = (callback.data or "").split(":")
    if len(parts) != 5 or parts[2] not in LEGAL_CHOICES:
        await callback.answer("Пункт не найден.", show_alert=True)
        return
    data = await _current_choice_data(callback, state, ":".join(parts[3:]))
    if data is None:
        return
    choices = dict(data["legal_choices"])
    choices[parts[2]] = not choices[parts[2]]
    data = await state.update_data(legal_choices=choices, legal_revision=data["legal_revision"] + 1)
    await callback.message.edit_reply_markup(reply_markup=gate_keyboard(data))
    await callback.answer("Отметка выбрана" if choices[parts[2]] else "Отметка снята")


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
    if UserRepository.has_current_legal_acceptance(user_id):
        if callback.data == "legal:back:settings":
            from handlers.settings import settings
            await callback.message.edit_reply_markup(reply_markup=None)
            await settings(callback.message, state)
        else:
            await _render(callback.message, "✅ Условия уже приняты. Документы доступны в настройках.", None, edit=True)
        return
    await show_legal_gate(callback.message, state, edit=True)


@router.callback_query(F.data == "legal:decline")
@router.callback_query(F.data.startswith("legal:decline:"))
async def decline_terms(callback: CallbackQuery, state: FSMContext):
    if UserRepository.has_current_legal_acceptance(str(callback.from_user.id)):
        await callback.answer("Условия уже приняты. Для прекращения использования доступно удаление аккаунта.", show_alert=True)
        return
    if callback.data != "legal:decline" and await _current_choice_data(
        callback, state, callback.data.removeprefix("legal:decline:")
    ) is None:
        return
    await callback.answer()
    await state.clear()
    await _render(callback.message,
                  "Условия не приняты. Тест не запущен.\n\n"
                  "Можно прочитать документы, обратиться в поддержку или удалить аккаунт.",
                  gate_keyboard(can_accept=False), edit=True)


@router.callback_query(F.data.startswith("legal:accept:"))
async def accept_terms(callback: CallbackQuery, state: FSMContext):
    user_id = str(callback.from_user.id)
    if UserRepository.has_current_legal_acceptance(user_id):
        await callback.answer("Условия уже приняты.")
        return
    parts = (callback.data or "").split(":")
    if len(parts) != 5 or parts[2] != LEGAL_VERSION:
        # Previously sent one-button prompts must not bypass the checkboxes.
        await callback.answer("Откроется экран с двумя отметками.")
        await show_legal_gate(callback.message, state, edit=True)
        return
    data = await _current_choice_data(callback, state, ":".join(parts[3:]))
    if data is None:
        return
    if not _all_selected(data):
        await callback.answer("Сначала отметь оба пункта.", show_alert=True)
        return
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
    await delete_account_start(callback.message, state, return_to_legal=(
        not UserRepository.has_current_legal_acceptance(str(callback.from_user.id))
    ))


@router.callback_query(F.data == PAGINATION_NOOP_CALLBACK)
async def pagination_noop(callback: CallbackQuery):
    await callback.answer()


def register_legal_handlers(dp):
    # Deletion FSM must consume its input before generic main-menu handlers.
    from handlers.settings import account_deletion_router
    dp.include_router(account_deletion_router)
    dp.include_router(router)
