"""Функции форматирования для тренировок."""
import logging
from datetime import date
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from database.models import Workout

logger = logging.getLogger(__name__)


def build_day_actions_keyboard(workouts: list[Workout], target_date: date) -> InlineKeyboardMarkup:
    """Строит клавиатуру с действиями для тренировок за день."""
    rows: list[list[InlineKeyboardButton]] = []
    
    for w in workouts:
        label = f"{w.exercise} ({w.count})"
        rows.append(
            [
            InlineKeyboardButton(
                    text=f"✏️ {label}",
                    callback_data=f"wrk_edit:{w.id}:{target_date.isoformat()}",
            ),
            InlineKeyboardButton(
                    text=f"🗑 {label}",
                    callback_data=f"wrk_del:{w.id}:{target_date.isoformat()}",
            ),
            ]
        )
    
    rows.append(
        [
        InlineKeyboardButton(
            text="➕ Добавить тренировку",
            callback_data=f"wrk_add:{target_date.isoformat()}",
            )
        ]
    )
    
    rows.append(
        [
        InlineKeyboardButton(
            text="⬅️ Назад к календарю",
            callback_data=f"cal_back:{target_date.year}-{target_date.month:02d}",
            )
        ]
    )
    
    return InlineKeyboardMarkup(inline_keyboard=rows)
