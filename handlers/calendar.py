"""Обработчики для общего календаря."""
import logging
from datetime import date
from typing import Optional
from aiogram import Router
from aiogram.types import Message, CallbackQuery
from utils.calendar_utils import build_workout_calendar_keyboard
from handlers.workouts import show_day_workouts
from handlers.workouts import show_day_workouts

logger = logging.getLogger(__name__)

router = Router()


@router.message(lambda m: m.text == "📆 Календарь")
async def calendar_view(message: Message):
    """Показывает общий календарь тренировок."""
    user_id = str(message.from_user.id)
    logger.info(f"User {user_id} opened general calendar")
    await show_calendar(message, user_id)


async def show_calendar(message: Message, user_id: str, year: Optional[int] = None, month: Optional[int] = None):
    """Показывает календарь тренировок."""
    today = date.today()
    year = year or today.year
    month = month or today.month
    keyboard = build_workout_calendar_keyboard(user_id, year, month)
    await message.answer(
        "📆 Выбери день, чтобы посмотреть, изменить или удалить тренировку:",
        reply_markup=keyboard,
    )


@router.callback_query(lambda c: c.data.startswith("cal_day:"))
async def select_calendar_day(callback: CallbackQuery):
    """Выбор дня в общем календаре."""
    await callback.answer()
    parts = callback.data.split(":")
    target_date = date.fromisoformat(parts[1])
    user_id = str(callback.from_user.id)
    await show_day_workouts(callback.message, user_id, target_date)


def register_calendar_handlers(dp):
    """Регистрирует обработчики календаря."""
    dp.include_router(router)
