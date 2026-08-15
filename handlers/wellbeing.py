"""Обработчики раздела дневных заметок."""
import logging
from datetime import date

from aiogram import Router
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton

from database.repositories.note_repository import NoteRepository
from database.repositories.analytics_repository import AnalyticsRepository
from states.user_states import WellbeingStates
from utils.calendar_utils import build_notes_calendar_keyboard, show_calendar_back_button
from utils.keyboards import (
    WELLBEING_AND_PROCEDURES_BUTTON_TEXT,
    LEGACY_WELLBEING_AND_PROCEDURES_BUTTON_TEXT,
    MAIN_MENU_BUTTON_ALIASES,
    main_menu,
    notes_main_menu,
    notes_rating_menu,
)
from utils.note_factors import (
    NOTE_FACTOR_CALLBACK_PREFIX,
    NOTE_FACTOR_LABELS,
    NOTE_FACTOR_OPTIONS,
    normalize_note_rating,
    parse_note_factor_callback,
    sanitize_note_factors,
)

logger = logging.getLogger(__name__)
router = Router()

RATING_LABELS = {
    5: "😁 Отлично",
    4: "🙂 Нормально",
    3: "😐 Средне",
    2: "😞 Плохо",
    1: "😫 Очень тяжело",
}

RATING_TEXT_TO_VALUE = {text: value for value, text in RATING_LABELS.items()}

FACTORS_PROMPT = (
    "Что повлияло на твой день? Можно выбрать несколько факторов.\n\n"
    "Когда закончишь, нажми «💾 Сохранить»."
)


@router.message(
    StateFilter(None),
    lambda m: m.text in {WELLBEING_AND_PROCEDURES_BUTTON_TEXT, LEGACY_WELLBEING_AND_PROCEDURES_BUTTON_TEXT}
)
async def open_notes_section(message: Message, state: FSMContext):
    """Открывает раздел заметок за текущий день."""
    await state.clear()
    AnalyticsRepository.track_event(str(message.from_user.id), "open_notes", section="notes")
    await show_notes_day(message, str(message.from_user.id), date.today())


async def show_notes_day(message: Message, user_id: str, target_date: date):
    """Показывает карточку заметки за дату."""
    note = NoteRepository.get_note_for_date(user_id, target_date)

    if note:
        factors = [_format_factor_label(f) for f in sanitize_note_factors(note.factors)]
        factors_short = " ".join(label.split()[0] for label in factors) or "—"
        text = (
            "📝 Заметки дня\n\n"
            f"Оценка:\n{RATING_LABELS.get(note.day_rating, '—')}\n\n"
            f"Факторы:\n{factors_short}"
        )
        keyboard = notes_main_menu
    else:
        text = (
            "📝 Заметки\n\n"
            "Отметь, как прошёл день и какие факторы могли повлиять на питание, "
            "активность и общее состояние.\n\n"
            "Выбранные отметки будут учтены при анализе дня."
        )
        keyboard = notes_main_menu

    await message.answer(text, reply_markup=keyboard)


def build_factors_keyboard(selected: list[str]) -> InlineKeyboardMarkup:
    selected = sanitize_note_factors(selected)
    rows = []
    for factor_key, label in NOTE_FACTOR_OPTIONS:
        prefix = "✅ " if factor_key in selected else ""
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"{prefix}{label}",
                    callback_data=f"{NOTE_FACTOR_CALLBACK_PREFIX}{factor_key}",
                )
            ]
        )
    if selected:
        rows.append([InlineKeyboardButton(text="💾 Сохранить", callback_data="save_note_factors")])
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="back_to_rating")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _format_factor_label(factor_key: str) -> str:
    """Возвращает подпись разрешённого структурированного фактора."""
    return NOTE_FACTOR_LABELS[factor_key]


async def start_note_flow(message: Message, state: FSMContext, target_date: date, user_id: str | None = None):
    """Запускает сценарий добавления/редактирования заметки."""
    user_id = user_id or str(message.from_user.id)
    note = NoteRepository.get_note_for_date(user_id, target_date)

    await state.clear()
    await state.set_state(WellbeingStates.note_rating)
    await state.update_data(
        note_user_id=user_id,
        note_date=target_date.isoformat(),
        day_rating=note.day_rating if note else None,
        factors=sanitize_note_factors(note.factors) if note else [],
    )

    await message.answer(
        "📝 Как прошёл твой день?\n\nВыбери вариант:",
        reply_markup=notes_rating_menu,
    )


@router.callback_query(lambda c: c.data == "edit_note")
async def edit_or_add_note(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await start_note_flow(callback.message, state, date.today(), user_id=str(callback.from_user.id))


@router.message(StateFilter(None), lambda m: m.text in {"➕ Добавить запись", "✏️ Изменить"})
async def edit_or_add_note_message(message: Message, state: FSMContext):
    await start_note_flow(message, state, date.today(), user_id=str(message.from_user.id))


@router.callback_query(lambda c: c.data.startswith("note_cal_edit:"))
async def edit_note_from_calendar(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    target_date = date.fromisoformat(callback.data.split(":")[1])
    await start_note_flow(callback.message, state, target_date, user_id=str(callback.from_user.id))


@router.callback_query(WellbeingStates.note_rating, lambda c: c.data.startswith("note_rate_"))
async def select_rating(callback: CallbackQuery, state: FSMContext):
    rating = normalize_note_rating(callback.data.rsplit("_", 1)[-1])
    if rating is None:
        await callback.answer("Недоступная оценка", show_alert=True)
        return
    await callback.answer()
    data = await state.get_data()
    factors = sanitize_note_factors(data.get("factors", []))
    await state.update_data(day_rating=rating, factors=factors)
    await state.set_state(WellbeingStates.note_factors)
    await callback.message.answer(
        FACTORS_PROMPT,
        reply_markup=build_factors_keyboard(factors),
    )


@router.message(WellbeingStates.note_rating, lambda m: m.text in RATING_TEXT_TO_VALUE)
async def select_rating_message(message: Message, state: FSMContext):
    rating = RATING_TEXT_TO_VALUE[message.text]
    data = await state.get_data()
    factors = sanitize_note_factors(data.get("factors", []))
    await state.update_data(day_rating=rating, factors=factors)
    await state.set_state(WellbeingStates.note_factors)
    await message.answer(
        FACTORS_PROMPT,
        reply_markup=build_factors_keyboard(factors),
    )


@router.callback_query(
    WellbeingStates.note_factors,
    lambda c: bool(c.data and c.data.startswith(NOTE_FACTOR_CALLBACK_PREFIX)),
)
async def toggle_factor(callback: CallbackQuery, state: FSMContext):
    factor = parse_note_factor_callback(callback.data)
    if factor is None:
        await callback.answer("Недоступный фактор", show_alert=True)
        return
    await callback.answer()
    data = await state.get_data()
    selected = sanitize_note_factors(data.get("factors", []))
    if factor in selected:
        selected.remove(factor)
    else:
        selected.append(factor)
    await state.update_data(factors=selected)
    await callback.message.edit_text(
        FACTORS_PROMPT,
        reply_markup=build_factors_keyboard(selected),
    )


@router.message(
    StateFilter(WellbeingStates.note_rating, WellbeingStates.note_factors),
    lambda m: (m.text or "").strip() in MAIN_MENU_BUTTON_ALIASES,
)
async def cancel_note_flow_to_main_menu(message: Message, state: FSMContext):
    """Прерывает создание заметки и очищает временные данные."""
    await state.clear()
    await message.answer("Возврат в меню.", reply_markup=main_menu)


@router.message(WellbeingStates.note_rating, lambda m: m.text == "⬅️ Назад")
async def note_rating_back(message: Message, state: FSMContext):
    await state.clear()
    await show_notes_day(message, str(message.from_user.id), date.today())


async def persist_note(message: Message, state: FSMContext, user_id: str | None = None):
    data = await state.get_data()
    user_id = user_id or data.get("note_user_id") or str(message.from_user.id)
    note_date = date.fromisoformat(data.get("note_date", date.today().isoformat()))
    day_rating = normalize_note_rating(data.get("day_rating")) or 3
    factors = sanitize_note_factors(data.get("factors", []))

    note = NoteRepository.upsert_note(
        user_id=user_id,
        entry_date=note_date,
        day_rating=day_rating,
        factors=factors,
    )
    await state.clear()

    factors_text = "\n".join(
        _format_factor_label(f) for f in sanitize_note_factors(note.factors)
    ) or "—"
    msg = (
        "📝 Заметка сохранена\n\n"
        f"{note.date.strftime('%d.%m.%Y')}:\n\n"
        f"{RATING_LABELS.get(note.day_rating, '—')}\n\n"
        f"Факторы:\n{factors_text}"
    )
    await message.answer(msg, reply_markup=notes_main_menu)


@router.callback_query(WellbeingStates.note_factors, lambda c: c.data == "save_note_factors")
async def finalize_note(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    if not sanitize_note_factors(data.get("factors", [])):
        await callback.answer("Выбери хотя бы один фактор", show_alert=True)
        return
    await callback.answer()
    await persist_note(callback.message, state, user_id=str(callback.from_user.id))


@router.callback_query(WellbeingStates.note_factors, lambda c: c.data == "back_to_rating")
async def back_to_rating(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.set_state(WellbeingStates.note_rating)
    await callback.message.answer("📝 Как прошёл твой день?\n\nВыбери вариант:", reply_markup=notes_rating_menu)


@router.callback_query(lambda c: c.data == "delete_note")
async def ask_delete_note(callback: CallbackQuery):
    await callback.answer()
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="✅ Да", callback_data="confirm_delete")],
            [InlineKeyboardButton(text="❌ Нет", callback_data="cancel_delete")],
        ]
    )
    await callback.message.answer("Удалить заметку?", reply_markup=keyboard)


@router.message(lambda m: m.text == "🗑 Удалить")
async def ask_delete_note_message(message: Message):
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="✅ Да", callback_data="confirm_delete")],
            [InlineKeyboardButton(text="❌ Нет", callback_data="cancel_delete")],
        ]
    )
    await message.answer("Удалить заметку?", reply_markup=keyboard)


@router.callback_query(lambda c: c.data.startswith("note_cal_del:"))
async def ask_delete_note_from_calendar(callback: CallbackQuery):
    await callback.answer()
    target_date = callback.data.split(":")[1]
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="✅ Да", callback_data=f"confirm_delete:{target_date}")],
            [InlineKeyboardButton(text="❌ Нет", callback_data=f"cancel_delete:{target_date}")],
        ]
    )
    await callback.message.answer("Удалить заметку?", reply_markup=keyboard)


@router.callback_query(lambda c: c.data.startswith("confirm_delete"))
async def confirm_delete(callback: CallbackQuery):
    await callback.answer()
    parts = callback.data.split(":")
    target_date = date.fromisoformat(parts[1]) if len(parts) > 1 else date.today()
    user_id = str(callback.from_user.id)
    NoteRepository.delete_note_for_date(user_id, target_date)
    await callback.message.answer("📝 Заметка удалена.")
    if len(parts) > 1:
        await show_note_calendar_day(callback.message, user_id, target_date)
    else:
        await show_notes_day(callback.message, user_id, date.today())


@router.callback_query(lambda c: c.data.startswith("cancel_delete"))
async def cancel_delete(callback: CallbackQuery):
    await callback.answer()
    parts = callback.data.split(":")
    user_id = str(callback.from_user.id)
    if len(parts) > 1:
        await show_note_calendar_day(callback.message, user_id, date.fromisoformat(parts[1]))
    else:
        await show_notes_day(callback.message, user_id, date.today())


@router.callback_query(lambda c: c.data == "calendar_open")
async def open_notes_calendar(callback: CallbackQuery):
    await callback.answer()
    today = date.today()
    user_id = str(callback.from_user.id)
    await show_calendar_back_button(callback.message)
    await show_notes_calendar(callback.message, user_id, today.year, today.month)


@router.message(lambda m: m.text == "📅 Календарь")
async def open_notes_calendar_message(message: Message):
    today = date.today()
    user_id = str(message.from_user.id)
    await show_calendar_back_button(message)
    await show_notes_calendar(message, user_id, today.year, today.month)


async def show_notes_calendar(message: Message, user_id: str, year: int, month: int):
    keyboard = build_notes_calendar_keyboard(user_id, year, month)
    await message.answer("📅 Календарь заметок", reply_markup=keyboard)


@router.callback_query(lambda c: c.data.startswith("note_cal_nav:"))
async def navigate_notes_calendar(callback: CallbackQuery):
    await callback.answer()
    year, month = map(int, callback.data.split(":")[1].split("-"))
    await show_notes_calendar(callback.message, str(callback.from_user.id), year, month)


@router.callback_query(lambda c: c.data.startswith("note_cal_day:"))
async def select_notes_calendar_day(callback: CallbackQuery):
    await callback.answer()
    target_date = date.fromisoformat(callback.data.split(":")[1])
    user_id = str(callback.from_user.id)
    await show_note_calendar_day(callback.message, user_id, target_date)


@router.callback_query(lambda c: c.data.startswith("note_cal_back:"))
async def back_to_note_calendar(callback: CallbackQuery):
    await callback.answer()
    year, month = map(int, callback.data.split(":")[1].split("-"))
    await show_notes_calendar(callback.message, str(callback.from_user.id), year, month)


async def show_note_calendar_day(message: Message, user_id: str, target_date: date):
    note = NoteRepository.get_note_for_date(user_id, target_date)
    if not note:
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="➕ Добавить запись", callback_data=f"note_cal_edit:{target_date.isoformat()}")],
                [InlineKeyboardButton(text="⬅️ Назад к календарю", callback_data=f"note_cal_back:{target_date.year}-{target_date.month:02d}")],
            ]
        )
        await message.answer(f"📝 {target_date.strftime('%d.%m')}\n\nЗаписи нет.", reply_markup=keyboard)
        return

    factors_text = "\n".join(
        _format_factor_label(f) for f in sanitize_note_factors(note.factors)
    ) or "—"
    text = f"📝 {target_date.strftime('%d.%m')}\n\n{RATING_LABELS.get(note.day_rating, '—')}\n\nФакторы:\n{factors_text}"

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="✏️ Изменить", callback_data=f"note_cal_edit:{target_date.isoformat()}")],
            [InlineKeyboardButton(text="🗑 Удалить", callback_data=f"note_cal_del:{target_date.isoformat()}")],
            [InlineKeyboardButton(text="⬅️ Назад к календарю", callback_data=f"note_cal_back:{target_date.year}-{target_date.month:02d}")],
        ]
    )
    await message.answer(text, reply_markup=keyboard)


@router.callback_query(lambda c: c.data == "notes_back")
async def notes_back(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.clear()
    await callback.message.answer("Возврат в меню.", reply_markup=main_menu)


def register_wellbeing_handlers(dp):
    """Регистрирует обработчики заметок."""
    dp.include_router(router)
