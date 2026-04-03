"""Обработчики для процедур."""
import logging
from datetime import date
from typing import Optional
from aiogram import Router
from aiogram.types import Message, CallbackQuery
from aiogram.fsm.context import FSMContext
from utils.keyboards import push_menu_stack, main_menu_button
from database.repositories import ProcedureRepository
from states.user_states import ProcedureStates
from utils.calendar_utils import (
    build_procedure_calendar_keyboard,
    build_procedure_day_actions_keyboard,
)

logger = logging.getLogger(__name__)

router = Router()

# Меню для процедур
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton

procedures_menu = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="➕ Добавить процедуру")],
        [KeyboardButton(text="📊 Сегодня")],
        [KeyboardButton(text="📆 Календарь процедур")],
        [KeyboardButton(text="⬅️ Назад"), main_menu_button],
    ],
    resize_keyboard=True,
)


@router.message(lambda m: m.text == "💆 Процедуры")
async def procedures(message: Message):
    """Показывает меню процедур."""
    user_id = str(message.from_user.id)
    logger.info(f"User {user_id} opened procedures menu")
    
    intro_text = (
        "💆 Раздел «Процедуры»\n\n"
        "Здесь ты можешь отслеживать любые процедуры для здоровья и красоты:\n"
        "• Контрастный душ\n"
        "• Баня и сауна\n"
        "• СПА-процедуры\n"
        "• Косметические процедуры\n"
        "• Массаж\n"
        "• И любые другие процедуры для ухода за собой\n\n"
        "Все записи сохраняются в календарь, чтобы ты видел свою активность."
    )
    
    push_menu_stack(message.bot, procedures_menu)
    await message.answer(intro_text, reply_markup=procedures_menu)


@router.message(lambda m: m.text == "➕ Добавить процедуру")
async def add_procedure(message: Message, state: FSMContext):
    """Начинает процесс добавления процедуры."""
    user_id = str(message.from_user.id)
    logger.info(f"User {user_id} started adding procedure")
    await start_add_procedure(message, state)


@router.callback_query(lambda c: c.data == "quick_procedure")
async def quick_add_procedure_cb(callback: CallbackQuery, state: FSMContext):
    """Быстрое добавление процедуры через inline-кнопку."""
    await callback.answer()
    await start_add_procedure(callback.message, state)


async def start_add_procedure(message: Message, state: FSMContext, *, entry_date: Optional[date] = None):
    """Запускает процесс добавления процедуры."""
    await state.update_data(entry_date=(entry_date or date.today()).isoformat())
    await state.set_state(ProcedureStates.entering_name)
    
    push_menu_stack(message.bot, procedures_menu)
    await message.answer(
        "💆 Добавление процедуры\n\n"
        "Напиши название процедуры (например: контрастный душ, баня, массаж, маска для лица и т.д.)\n\n"
        "Можешь добавить заметки через запятую после названия.",
        reply_markup=procedures_menu,
    )


@router.message(ProcedureStates.entering_name)
async def process_procedure_name(message: Message, state: FSMContext):
    """Обрабатывает ввод названия процедуры."""
    user_id = str(message.from_user.id)
    text = message.text.strip()
    
    if not text:
        await message.answer("Напиши название процедуры, пожалуйста 🙏")
        return
    
    # Разделяем название и заметки (если есть запятая)
    parts = text.split(",", 1)
    name = parts[0].strip()
    notes = parts[1].strip() if len(parts) > 1 else None
    
    data = await state.get_data()
    entry_date_str = data.get("entry_date", date.today().isoformat())
    
    if isinstance(entry_date_str, str):
        try:
            entry_date = date.fromisoformat(entry_date_str)
        except ValueError:
            entry_date = date.today()
    else:
        entry_date = date.today()
    
    procedure_id = ProcedureRepository.save_procedure(user_id, name, entry_date, notes)
    
    if procedure_id:
        await state.clear()
        push_menu_stack(message.bot, procedures_menu)
        
        result_text = f"✅ Добавил процедуру: {name}"
        if notes:
            result_text += f"\n📝 Заметки: {notes}"
        
        await message.answer(result_text, reply_markup=procedures_menu)
    else:
        await message.answer("❌ Не удалось сохранить процедуру. Попробуйте позже.")
        await state.clear()


@router.message(lambda m: m.text == "📊 Сегодня")
async def procedures_today(message: Message):
    """Показывает процедуры за сегодня."""
    user_id = str(message.from_user.id)
    today = date.today()
    procedures_list = ProcedureRepository.get_procedures_for_day(user_id, today)
    
    if not procedures_list:
        push_menu_stack(message.bot, procedures_menu)
        await message.answer(
            "💆 Сегодня процедур пока нет.\n\nДобавь первую процедуру через кнопку «➕ Добавить процедуру»",
            reply_markup=procedures_menu,
        )
        return
    
    lines = [f"💆 Процедуры за {today.strftime('%d.%m.%Y')}:\n"]
    for i, proc in enumerate(procedures_list, 1):
        notes_text = f" ({proc.notes})" if proc.notes else ""
        lines.append(f"{i}. {proc.name}{notes_text}")
    
    push_menu_stack(message.bot, procedures_menu)
    await message.answer("\n".join(lines), reply_markup=procedures_menu)


@router.message(lambda m: m.text == "📆 Календарь процедур")
async def procedures_calendar(message: Message):
    """Показывает календарь процедур."""
    user_id = str(message.from_user.id)
    logger.info(f"User {user_id} opened procedures calendar")
    today = date.today()
    await show_procedures_calendar(message, user_id, today.year, today.month)


async def show_procedures_calendar(message: Message, user_id: str, year: int, month: int):
    """Показывает календарь процедур."""
    keyboard = build_procedure_calendar_keyboard(user_id, year, month)
    await message.answer(
        "📆 Календарь процедур\n\nВыбери день, чтобы посмотреть или добавить процедуру:",
        reply_markup=keyboard,
    )


@router.callback_query(lambda c: c.data.startswith("proc_cal_nav:"))
async def navigate_procedures_calendar(callback: CallbackQuery):
    """Навигация по календарю процедур."""
    await callback.answer()
    parts = callback.data.split(":")
    year, month = map(int, parts[1].split("-"))
    user_id = str(callback.from_user.id)
    await show_procedures_calendar(callback.message, user_id, year, month)


@router.callback_query(lambda c: c.data.startswith("proc_cal_back:"))
async def back_to_procedures_calendar(callback: CallbackQuery):
    """Возврат к календарю процедур."""
    await callback.answer()
    parts = callback.data.split(":")
    year, month = map(int, parts[1].split("-"))
    user_id = str(callback.from_user.id)
    await show_procedures_calendar(callback.message, user_id, year, month)


@router.callback_query(lambda c: c.data.startswith("proc_cal_day:"))
async def select_procedure_calendar_day(callback: CallbackQuery):
    """Выбор дня в календаре процедур."""
    await callback.answer()
    parts = callback.data.split(":")
    # Формат: proc_cal_day:YYYY-MM-DD
    date_str = parts[1]
    try:
        # Пробуем распарсить как YYYY-MM-DD
        from datetime import datetime
        target_date = datetime.strptime(date_str, "%Y-%m-%d").date()
    except ValueError:
        # Если не получилось, пробуем ISO формат
        target_date = date.fromisoformat(date_str)
    user_id = str(callback.from_user.id)
    await show_procedure_day(callback.message, user_id, target_date)


async def show_procedure_day(message: Message, user_id: str, target_date: date):
    """Показывает процедуры за день."""
    procedures_list = ProcedureRepository.get_procedures_for_day(user_id, target_date)

    if not procedures_list:
        await message.answer(
            f"💆 {target_date.strftime('%d.%m.%Y')}\n\nПроцедур в этот день не было.",
            reply_markup=build_procedure_day_actions_keyboard([], target_date),
        )
        return

    lines = [f"💆 Процедуры за {target_date.strftime('%d.%m.%Y')}:\n"]
    for i, proc in enumerate(procedures_list, 1):
        notes_text = f" ({proc.notes})" if proc.notes else ""
        lines.append(f"{i}. {proc.name}{notes_text}")

    await message.answer(
        "\n".join(lines),
        reply_markup=build_procedure_day_actions_keyboard(procedures_list, target_date),
    )


@router.callback_query(lambda c: c.data.startswith("proc_cal_add:"))
async def add_procedure_from_calendar(callback: CallbackQuery, state: FSMContext):
    """Добавляет процедуру из календаря."""
    await callback.answer()
    parts = callback.data.split(":")
    target_date = date.fromisoformat(parts[1])
    await start_add_procedure(callback.message, state, entry_date=target_date)


@router.callback_query(lambda c: c.data.startswith("proc_cal_del:"))
async def delete_procedure_from_calendar(callback: CallbackQuery):
    """Удаляет процедуру из календаря."""
    await callback.answer()
    parts = callback.data.split(":")
    target_date = date.fromisoformat(parts[1])
    procedure_id = int(parts[2])
    user_id = str(callback.from_user.id)

    success = ProcedureRepository.delete_procedure(user_id, procedure_id)
    if success:
        await callback.message.answer("✅ Процедура удалена.")
    else:
        await callback.message.answer("❌ Не удалось удалить процедуру.")

    await show_procedure_day(callback.message, user_id, target_date)


def register_procedure_handlers(dp):
    """Регистрирует обработчики процедур."""
    dp.include_router(router)
