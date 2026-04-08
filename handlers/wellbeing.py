"""Обработчики раздела дневных заметок."""
import logging
from datetime import date

from aiogram import Router
from aiogram.fsm.context import FSMContext
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton

from database.repositories.note_repository import NoteRepository
from states.user_states import WellbeingStates
from utils.calendar_utils import build_notes_calendar_keyboard
from utils.keyboards import (
    WELLBEING_AND_PROCEDURES_BUTTON_TEXT,
    LEGACY_WELLBEING_AND_PROCEDURES_BUTTON_TEXT,
    main_menu,
    notes_main_menu,
    notes_rating_menu,
    notes_text_menu,
    build_notes_factors_menu,
)

logger = logging.getLogger(__name__)
router = Router()

RATING_LABELS = {
    5: "😄 Отлично",
    4: "🙂 Нормально",
    3: "😐 Средне",
    2: "😞 Плохо",
    1: "😫 Очень тяжёлый",
}

FACTOR_LABELS = {
    "energy": "💪 Много энергии",
    "tired": "😴 Усталость",
    "headache": "🤕 Головная боль",
    "bad_sleep": "💤 Плохой сон",
    "stress": "😈 Стресс",
    "overeating": "🍔 Переедание",
    "medicine": "💊 Лекарства",
    "workout": "🏋️ Тренировка",
    "productive": "🔥 Продуктивный день",
    "rest": "🍿 Отдых",
    "good_mood": "🙏 Хорошее настроение",
}

FACTOR_CALLBACK_TO_KEY = {
    "toggle_factor_energy": "energy",
    "toggle_factor_tired": "tired",
    "toggle_factor_headache": "headache",
    "toggle_factor_sleep": "bad_sleep",
    "toggle_factor_stress": "stress",
    "toggle_factor_overeat": "overeating",
    "toggle_factor_medicine": "medicine",
    "toggle_factor_workout": "workout",
    "toggle_factor_productive": "productive",
    "toggle_factor_rest": "rest",
    "toggle_factor_goodmood": "good_mood",
}

RATING_TEXT_TO_VALUE = {text: value for value, text in RATING_LABELS.items()}


@router.message(
    lambda m: m.text in {WELLBEING_AND_PROCEDURES_BUTTON_TEXT, LEGACY_WELLBEING_AND_PROCEDURES_BUTTON_TEXT}
)
async def open_notes_section(message: Message, state: FSMContext):
    """Открывает раздел заметок за текущий день."""
    await state.clear()
    await show_notes_day(message, str(message.from_user.id), date.today())


async def show_notes_day(message: Message, user_id: str, target_date: date):
    """Показывает карточку заметки за дату."""
    note = NoteRepository.get_note_for_date(user_id, target_date)

    if note:
        factors = [_format_factor_label(f) for f in note.factors]
        factors_short = " ".join(label.split()[0] for label in factors) or "—"
        text = (
            "📝 Заметки дня\n\n"
            f"Оценка:\n{RATING_LABELS.get(note.day_rating, '—')}\n\n"
            f"Факторы:\n{factors_short}"
        )
        if note.text:
            text += f"\n\nКомментарий:\n{note.text}"
        keyboard = notes_main_menu
    else:
        text = (
            "📝 Заметки дня\n\n"
            "Здесь ты можешь коротко отметить самочувствие, важные события и настроение за день 💛\n\n"
            "Эти заметки учитываются в ИИ-анализе, чтобы рекомендации были точнее.\n\n"
            "📝 Как прошёл день?"
        )
        keyboard = notes_main_menu

    await message.answer(text, reply_markup=keyboard)


def build_factors_keyboard(selected: list[str]) -> InlineKeyboardMarkup:
    rows = []
    for callback_name, factor_key in FACTOR_CALLBACK_TO_KEY.items():
        label = FACTOR_LABELS[factor_key]
        prefix = "✅ " if factor_key in selected else ""
        rows.append([InlineKeyboardButton(text=f"{prefix}{label}", callback_data=callback_name)])
    rows.append([InlineKeyboardButton(text="✅ Продолжить", callback_data="done_factors")])
    rows.append([InlineKeyboardButton(text="⏭ Пропустить", callback_data="skip_factors")])
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="back_to_rating")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _format_factor_label(factor_key: str) -> str:
    """Возвращает подпись фактора, включая пользовательские варианты."""
    return FACTOR_LABELS.get(factor_key, f"✍️ {factor_key}")


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
        factors=note.factors if note else [],
        note_text=note.text or "" if note else "",
    )

    await message.answer(
        "📝 Как прошёл день?\n\nВыбери вариант:",
        reply_markup=notes_rating_menu,
    )


@router.callback_query(lambda c: c.data == "edit_note")
async def edit_or_add_note(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await start_note_flow(callback.message, state, date.today(), user_id=str(callback.from_user.id))


@router.message(lambda m: m.text in {"➕ Добавить запись", "✏️ Изменить"})
async def edit_or_add_note_message(message: Message, state: FSMContext):
    await start_note_flow(message, state, date.today(), user_id=str(message.from_user.id))


@router.callback_query(lambda c: c.data.startswith("note_cal_edit:"))
async def edit_note_from_calendar(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    target_date = date.fromisoformat(callback.data.split(":")[1])
    await start_note_flow(callback.message, state, target_date, user_id=str(callback.from_user.id))


@router.callback_query(lambda c: c.data.startswith("note_rate_"))
async def select_rating(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    rating = int(callback.data.rsplit("_", 1)[1])
    data = await state.get_data()
    await state.update_data(day_rating=rating, factors=data.get("factors", []))
    await state.set_state(WellbeingStates.note_factors)
    await callback.message.answer(
        "Что повлияло на день? (можно несколько)\nМожно выбрать из кнопок или вписать свой вариант текстом.",
        reply_markup=build_notes_factors_menu(_build_factor_labels(data.get("factors", []))),
    )


@router.message(WellbeingStates.note_rating, lambda m: m.text in RATING_TEXT_TO_VALUE)
async def select_rating_message(message: Message, state: FSMContext):
    rating = RATING_TEXT_TO_VALUE[message.text]
    data = await state.get_data()
    await state.update_data(day_rating=rating, factors=data.get("factors", []))
    await state.set_state(WellbeingStates.note_factors)
    await message.answer(
        "Что повлияло на день? (можно несколько)\nМожно выбрать из кнопок или вписать свой вариант текстом.",
        reply_markup=build_notes_factors_menu(_build_factor_labels(data.get("factors", []))),
    )


@router.callback_query(lambda c: c.data in FACTOR_CALLBACK_TO_KEY)
async def toggle_factor(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    factor = FACTOR_CALLBACK_TO_KEY[callback.data]
    data = await state.get_data()
    selected = list(data.get("factors", []))
    if factor in selected:
        selected.remove(factor)
    else:
        selected.append(factor)
    await state.update_data(factors=selected)
    await callback.message.answer(
        "Что повлияло на день? (можно несколько)\nМожно выбрать из кнопок или вписать свой вариант текстом.",
        reply_markup=build_factors_keyboard(selected),
    )


def _build_factor_labels(selected: list[str]) -> list[str]:
    labels = []
    for factor_key in FACTOR_CALLBACK_TO_KEY.values():
        prefix = "✅ " if factor_key in selected else ""
        labels.append(f"{prefix}{FACTOR_LABELS[factor_key]}")
    custom_factors = [factor for factor in selected if factor not in FACTOR_LABELS]
    for custom_factor in custom_factors:
        labels.append(f"✅ ✍️ {custom_factor}")
    return labels


@router.message(WellbeingStates.note_factors)
async def toggle_factor_message(message: Message, state: FSMContext):
    text = (message.text or "").strip()
    if text == "✅ Продолжить":
        await state.set_state(WellbeingStates.note_text)
        await message.answer(
            "✏️ Добавь заметку (необязательно)\n\nНапример:\nСегодня было тяжело держать питание.",
            reply_markup=notes_text_menu,
        )
        return
    if text == "⏭ Пропустить":
        await state.update_data(factors=[])
        await state.set_state(WellbeingStates.note_text)
        await message.answer(
            "✏️ Добавь заметку (необязательно)\n\nНапример:\nСегодня было тяжело держать питание.",
            reply_markup=notes_text_menu,
        )
        return
    if text == "⬅️ Назад":
        await state.set_state(WellbeingStates.note_rating)
        await message.answer("📝 Как прошёл день?\n\nВыбери вариант:", reply_markup=notes_rating_menu)
        return
    if text == "✍️ Свой вариант":
        await message.answer("Напиши свой фактор одним сообщением, и я добавлю его в список.")
        return

    clean_label = text.removeprefix("✅ ").strip()
    label_to_key = {label: key for key, label in FACTOR_LABELS.items()}
    factor = label_to_key.get(clean_label)
    if not factor:
        factor = clean_label.removeprefix("✍️ ").strip()
        if not factor:
            return
        if len(factor) > 80:
            await message.answer("Слишком длинно. Напиши фактор короче (до 80 символов).")
            return

    data = await state.get_data()
    selected = list(data.get("factors", []))
    if factor in selected:
        selected.remove(factor)
    else:
        selected.append(factor)
    await state.update_data(factors=selected)
    await message.answer(
        "Что повлияло на день? (можно несколько)\nМожно выбрать из кнопок или вписать свой вариант текстом.",
        reply_markup=build_notes_factors_menu(_build_factor_labels(selected)),
    )


@router.message(WellbeingStates.note_rating, lambda m: m.text == "⬅️ Назад")
async def note_rating_back(message: Message, state: FSMContext):
    await state.clear()
    await show_notes_day(message, str(message.from_user.id), date.today())


@router.callback_query(lambda c: c.data in {"done_factors", "skip_factors"})
async def done_factors_step(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    if callback.data == "skip_factors":
        await state.update_data(factors=[])
    await state.set_state(WellbeingStates.note_text)
    await callback.message.answer(
        "✏️ Добавь заметку (необязательно)\n\nНапример:\nСегодня было тяжело держать питание.",
        reply_markup=notes_text_menu,
    )


@router.message(WellbeingStates.note_text, lambda m: m.text not in {"💾 Сохранить", "⏭ Пропустить", "⬅️ Назад"})
async def capture_note_text(message: Message, state: FSMContext):
    text = (message.text or "").strip()
    if len(text) > 500:
        await message.answer("Лимит заметки — 500 символов.")
        return
    await state.update_data(note_text=text)
    await message.answer("Текст обновлён. Нажми 💾 Сохранить или ⏭ Пропустить.", reply_markup=notes_text_menu)


async def persist_note(message: Message, state: FSMContext, user_id: str | None = None):
    data = await state.get_data()
    user_id = user_id or data.get("note_user_id") or str(message.from_user.id)
    note_date = date.fromisoformat(data.get("note_date", date.today().isoformat()))
    day_rating = int(data.get("day_rating") or 3)
    factors = data.get("factors", [])
    note_text = (data.get("note_text") or "").strip()[:500] or None

    note = NoteRepository.upsert_note(
        user_id=user_id,
        entry_date=note_date,
        day_rating=day_rating,
        factors=factors,
        text=note_text,
    )
    await state.clear()

    factors_text = "\n".join(_format_factor_label(f) for f in note.factors) or "—"
    msg = (
        "📝 Заметка сохранена\n\n"
        f"{note.date.strftime('%d.%m.%Y')}:\n\n"
        f"{RATING_LABELS.get(note.day_rating, '—')}\n\n"
        f"Факторы:\n{factors_text}"
    )
    if note.text:
        msg += f"\n\nКомментарий:\n{note.text}"

    await message.answer(msg, reply_markup=notes_main_menu)


@router.callback_query(lambda c: c.data in {"save_note", "skip_note"})
async def finalize_note(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    if callback.data == "skip_note":
        await state.update_data(note_text="")
    await persist_note(callback.message, state, user_id=str(callback.from_user.id))


@router.message(WellbeingStates.note_text, lambda m: m.text in {"💾 Сохранить", "⏭ Пропустить"})
async def finalize_note_message(message: Message, state: FSMContext):
    if message.text == "⏭ Пропустить":
        await state.update_data(note_text="")
    await persist_note(message, state, user_id=str(message.from_user.id))


@router.callback_query(lambda c: c.data == "back_to_rating")
async def back_to_rating(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.set_state(WellbeingStates.note_rating)
    await callback.message.answer("📝 Как прошёл день?\n\nВыбери вариант:", reply_markup=notes_rating_menu)


@router.callback_query(lambda c: c.data == "back_to_factors")
async def back_to_factors(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    data = await state.get_data()
    await state.set_state(WellbeingStates.note_factors)
    await callback.message.answer(
        "Что повлияло на день? (можно несколько)\nМожно выбрать из кнопок или вписать свой вариант текстом.",
        reply_markup=build_notes_factors_menu(_build_factor_labels(data.get("factors", []))),
    )


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
    await show_notes_calendar(callback.message, user_id, today.year, today.month)


@router.message(lambda m: m.text == "📅 Календарь")
async def open_notes_calendar_message(message: Message):
    today = date.today()
    user_id = str(message.from_user.id)
    await show_notes_calendar(message, user_id, today.year, today.month)


@router.message(WellbeingStates.note_text, lambda m: m.text == "⬅️ Назад")
async def note_text_back(message: Message, state: FSMContext):
    data = await state.get_data()
    await state.set_state(WellbeingStates.note_factors)
    await message.answer(
        "Что повлияло на день? (можно несколько)\nМожно выбрать из кнопок или вписать свой вариант текстом.",
        reply_markup=build_notes_factors_menu(_build_factor_labels(data.get("factors", []))),
    )


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

    factors_text = "\n".join(_format_factor_label(f) for f in note.factors) or "—"
    text = f"📝 {target_date.strftime('%d.%m')}\n\n{RATING_LABELS.get(note.day_rating, '—')}\n\nФакторы:\n{factors_text}"
    if note.text:
        text += f"\n\nКомментарий:\n{note.text}"

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
