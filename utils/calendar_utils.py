"""Утилиты для работы с календарями."""
import calendar
import logging
from datetime import date
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from config import MONTH_NAMES
from database.repositories import (
    WorkoutRepository,
    MealRepository,
    SupplementRepository,
    ProcedureRepository,
    WeightRepository,
    WaterRepository,
)
from database.repositories.note_repository import NoteRepository
from database.repositories.activity_analysis_repository import ActivityAnalysisRepository

logger = logging.getLogger(__name__)


def get_month_workout_days(user_id: str, year: int, month: int) -> set[int]:
    """Получает дни месяца, в которые были тренировки."""
    start_date = date(year, month, 1)
    if month == 12:
        end_date = date(year + 1, 1, 1)
    else:
        end_date = date(year, month + 1, 1)
    
    workouts = WorkoutRepository.get_workouts_for_period(user_id, start_date, end_date)
    return {w.date.day for w in workouts if w.date.month == month}


def get_month_meal_days(user_id: str, year: int, month: int) -> set[int]:
    """Получает дни месяца, в которые были приёмы пищи."""
    start_date = date(year, month, 1)
    if month == 12:
        end_date = date(year + 1, 1, 1)
    else:
        end_date = date(year, month + 1, 1)
    
    # TODO: Добавить метод в MealRepository для получения дней
    # Пока упрощённая версия
    days = set()
    for day in range(1, calendar.monthrange(year, month)[1] + 1):
        check_date = date(year, month, day)
        meals = MealRepository.get_meals_for_date(user_id, check_date)
        if meals:
            days.add(day)
    return days


def build_calendar_keyboard(
    user_id: str,
    year: int,
    month: int,
    callback_prefix: str = "cal",
    marker: str = "💪",
    get_days_func=None,
) -> InlineKeyboardMarkup:
    """
    Строит календарную клавиатуру.
    
    Args:
        user_id: ID пользователя
        year: Год
        month: Месяц
        callback_prefix: Префикс для callback_data
        marker: Маркер для дней с данными
        get_days_func: Функция для получения дней с данными
    """
    if get_days_func:
        marked_days = get_days_func(user_id, year, month)
    else:
        marked_days = set()
    
    keyboard: list[list[InlineKeyboardButton]] = []
    
    header = InlineKeyboardButton(text=f"{MONTH_NAMES[month]} {year}", callback_data="noop")
    keyboard.append([header])
    
    week_days = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
    keyboard.append([InlineKeyboardButton(text=d, callback_data="noop") for d in week_days])
    
    month_calendar = calendar.Calendar(firstweekday=0).monthdayscalendar(year, month)
    for week in month_calendar:
        row = []
        for day in week:
            if day == 0:
                row.append(InlineKeyboardButton(text=" ", callback_data="noop"))
            else:
                day_marker = marker if day in marked_days else ""
                row.append(
                    InlineKeyboardButton(
                        text=f"{day}{day_marker}",
                        callback_data=f"{callback_prefix}_day:{year}-{month:02d}-{day:02d}",
                    )
                )
        keyboard.append(row)
    
    prev_month = month - 1 or 12
    prev_year = year - 1 if month == 1 else year
    next_month = month % 12 + 1
    next_year = year + 1 if month == 12 else year
    
    keyboard.append(
        [
            InlineKeyboardButton(
                text="◀️", callback_data=f"{callback_prefix}_nav:{prev_year}-{prev_month:02d}"
            ),
            InlineKeyboardButton(text="Закрыть", callback_data="cal_close"),
            InlineKeyboardButton(
                text="▶️", callback_data=f"{callback_prefix}_nav:{next_year}-{next_month:02d}"
            ),
        ]
    )
    
    return InlineKeyboardMarkup(inline_keyboard=keyboard)


def build_workout_calendar_keyboard(user_id: str, year: int, month: int) -> InlineKeyboardMarkup:
    """Строит календарь тренировок."""
    return build_calendar_keyboard(
        user_id=user_id,
        year=year,
        month=month,
        callback_prefix="cal",
        marker="💪",
        get_days_func=get_month_workout_days,
    )


def build_kbju_calendar_keyboard(user_id: str, year: int, month: int) -> InlineKeyboardMarkup:
    """Строит календарь КБЖУ."""
    return build_calendar_keyboard(
        user_id=user_id,
        year=year,
        month=month,
        callback_prefix="meal_cal",
        marker="🍱",
        get_days_func=get_month_meal_days,
    )


def get_month_notes_days(user_id: str, year: int, month: int) -> set[int]:
    """Получает дни месяца, в которые есть дневные заметки."""
    return NoteRepository.get_month_note_days(user_id, year, month)


def build_notes_calendar_keyboard(user_id: str, year: int, month: int) -> InlineKeyboardMarkup:
    """Строит календарь заметок."""
    return build_calendar_keyboard(
        user_id=user_id,
        year=year,
        month=month,
        callback_prefix="note_cal",
        marker="📝",
        get_days_func=get_month_notes_days,
    )


def get_month_supplement_days(user_id: str, year: int, month: int) -> set[int]:
    """Получает дни месяца, в которые были приёмы добавок."""
    return SupplementRepository.get_history_days(user_id, year, month)


def build_supplement_calendar_keyboard(user_id: str, year: int, month: int) -> InlineKeyboardMarkup:
    """Строит клавиатуру календаря добавок."""
    return build_calendar_keyboard(
        user_id=user_id,
        year=year,
        month=month,
        callback_prefix="supcal",
        marker="💊",
        get_days_func=get_month_supplement_days,
    )


def build_supplement_day_actions_keyboard(entries: list[dict], target_date: date) -> InlineKeyboardMarkup:
    """Строит клавиатуру действий для дня в календаре добавок."""
    from aiogram.types import InlineKeyboardButton
    
    rows: list[list[InlineKeyboardButton]] = []
    
    for entry in entries:
        amount_text = f" — {entry['amount']}" if entry.get("amount") is not None else ""
        label = f"{entry['supplement_name']} ({entry['time_text']}{amount_text})"
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"✏️ {label}",
                    callback_data=(
                        f"supcal_edit:{target_date.isoformat()}:{entry['supplement_index']}:{entry['entry_index']}"
                    ),
                ),
                InlineKeyboardButton(
                    text=f"🗑 {label}",
                    callback_data=(
                        f"supcal_del:{target_date.isoformat()}:{entry['supplement_index']}:{entry['entry_index']}"
                    ),
                ),
            ]
        )
    
    rows.append(
        [
            InlineKeyboardButton(
                text="➕ Добавить ещё" if entries else "➕ Добавить приём",
                callback_data=f"supcal_add:{target_date.isoformat()}",
            ),
        ]
    )
    rows.append(
        [
            InlineKeyboardButton(
                text="⬅️ Назад к календарю",
                callback_data=f"supcal_back:{target_date.year}-{target_date.month:02d}",
            )
        ]
    )
    
    return InlineKeyboardMarkup(inline_keyboard=rows)


def get_month_procedure_days(user_id: str, year: int, month: int) -> set[int]:
    """Получает дни месяца, в которые были процедуры."""
    return ProcedureRepository.get_month_procedure_days(user_id, year, month)


def build_procedure_calendar_keyboard(user_id: str, year: int, month: int) -> InlineKeyboardMarkup:
    """Строит клавиатуру календаря процедур."""
    return build_calendar_keyboard(
        user_id=user_id,
        year=year,
        month=month,
        callback_prefix="proc_cal",
        marker="💆",
        get_days_func=get_month_procedure_days,
    )


def build_procedure_day_actions_keyboard(procedures, target_date: date) -> InlineKeyboardMarkup:
    """Строит клавиатуру действий для дня в календаре процедур."""
    from aiogram.types import InlineKeyboardButton

    rows: list[list[InlineKeyboardButton]] = []

    if procedures:
        for proc in procedures:
            rows.append(
                [
                    InlineKeyboardButton(
                        text=f"🗑 {proc.name}",
                        callback_data=f"proc_cal_del:{target_date.isoformat()}:{proc.id}",
                    )
                ]
            )

    rows.append(
        [
            InlineKeyboardButton(
                text="➕ Добавить процедуру",
                callback_data=f"proc_cal_add:{target_date.isoformat()}",
            ),
        ]
    )
    rows.append(
        [
            InlineKeyboardButton(
                text="⬅️ Назад к календарю",
                callback_data=f"proc_cal_back:{target_date.year}-{target_date.month:02d}",
            )
        ]
    )

    return InlineKeyboardMarkup(inline_keyboard=rows)


def get_month_water_days(user_id: str, year: int, month: int) -> set[int]:
    """Получает дни месяца, в которые была вода."""
    return WaterRepository.get_month_water_days(user_id, year, month)


def build_water_calendar_keyboard(user_id: str, year: int, month: int) -> InlineKeyboardMarkup:
    """Строит календарь воды."""
    return build_calendar_keyboard(
        user_id=user_id,
        year=year,
        month=month,
        callback_prefix="water_cal",
        marker="💧",
        get_days_func=get_month_water_days,
    )


def build_water_day_actions_keyboard(entries: list, target_date: date) -> InlineKeyboardMarkup:
    """Строит клавиатуру действий для дня в календаре воды."""
    from aiogram.types import InlineKeyboardButton

    rows: list[list[InlineKeyboardButton]] = []

    for entry in entries:
        time_text = entry.timestamp.strftime("%H:%M") if entry.timestamp else ""
        label = f"{entry.amount:.0f} мл {time_text}".strip()
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"🗑 {label}",
                    callback_data=f"water_cal_del:{target_date.isoformat()}:{entry.id}",
                )
            ]
        )

    rows.append(
        [
            InlineKeyboardButton(
                text="➕ Добавить воду",
                callback_data=f"water_cal_add:{target_date.isoformat()}",
            ),
        ]
    )
    rows.append(
        [
            InlineKeyboardButton(
                text="⬅️ Назад к календарю",
                callback_data=f"water_cal_back:{target_date.year}-{target_date.month:02d}",
            )
        ]
    )

    return InlineKeyboardMarkup(inline_keyboard=rows)


def get_month_weight_days(user_id: str, year: int, month: int) -> set[int]:
    """Получает дни месяца, в которые был записан вес."""
    return WeightRepository.get_month_weight_days(user_id, year, month)


def build_weight_calendar_keyboard(user_id: str, year: int, month: int) -> InlineKeyboardMarkup:
    """Строит клавиатуру календаря веса."""
    return build_calendar_keyboard(
        user_id=user_id,
        year=year,
        month=month,
        callback_prefix="weight_cal",
        marker="⚖️",
        get_days_func=get_month_weight_days,
    )


def build_weight_day_actions_keyboard(weight, target_date: date) -> InlineKeyboardMarkup:
    """Строит клавиатуру действий для дня в календаре веса."""
    from aiogram.types import InlineKeyboardButton
    
    rows: list[list[InlineKeyboardButton]] = []
    
    if weight:
        rows.append(
            [
                InlineKeyboardButton(
                    text="✏️ Редактировать",
                    callback_data=f"weight_cal_edit:{target_date.isoformat()}",
                ),
                InlineKeyboardButton(
                    text="🗑 Удалить",
                    callback_data=f"weight_cal_del:{target_date.isoformat()}",
                ),
            ]
        )
    
    rows.append(
        [
            InlineKeyboardButton(
                text="➕ Добавить вес" if not weight else "➕ Изменить вес",
                callback_data=f"weight_cal_add:{target_date.isoformat()}",
            ),
        ]
    )
    rows.append(
        [
            InlineKeyboardButton(
                text="⬅️ Назад к календарю",
                callback_data=f"weight_cal_back:{target_date.year}-{target_date.month:02d}",
            )
        ]
    )
    
    return InlineKeyboardMarkup(inline_keyboard=rows)


def get_month_measurement_days(user_id: str, year: int, month: int) -> set[int]:
    """Получает дни месяца, в которые были замеры."""
    return WeightRepository.get_month_measurement_days(user_id, year, month)


def build_measurement_calendar_keyboard(user_id: str, year: int, month: int) -> InlineKeyboardMarkup:
    """Строит клавиатуру календаря замеров."""
    return build_calendar_keyboard(
        user_id=user_id,
        year=year,
        month=month,
        callback_prefix="meas_cal",
        marker="📏",
        get_days_func=get_month_measurement_days,
    )


def build_measurement_day_actions_keyboard(measurement, target_date: date) -> InlineKeyboardMarkup:
    """Строит клавиатуру действий для дня в календаре замеров."""
    from aiogram.types import InlineKeyboardButton

    rows: list[list[InlineKeyboardButton]] = []

    if measurement:
        rows.append(
            [
                InlineKeyboardButton(
                    text="✏️ Редактировать",
                    callback_data=f"meas_cal_edit:{target_date.isoformat()}",
                ),
                InlineKeyboardButton(
                    text="🗑 Удалить",
                    callback_data=f"meas_cal_del:{target_date.isoformat()}",
                ),
            ]
        )

    rows.append(
        [
            InlineKeyboardButton(
                text="➕ Добавить замеры" if not measurement else "➕ Изменить замеры",
                callback_data=f"meas_cal_add:{target_date.isoformat()}",
            ),
        ]
    )
    rows.append(
        [
            InlineKeyboardButton(
                text="⬅️ Назад к календарю",
                callback_data=f"meas_cal_back:{target_date.year}-{target_date.month:02d}",
            )
        ]
    )

    return InlineKeyboardMarkup(inline_keyboard=rows)


def get_month_activity_analysis_days(user_id: str, year: int, month: int) -> set[int]:
    """Получает дни месяца, в которые есть сохранённые ИИ-анализы."""
    return ActivityAnalysisRepository.get_month_days(user_id, year, month)


def build_activity_analysis_calendar_keyboard(user_id: str, year: int, month: int) -> InlineKeyboardMarkup:
    """Строит календарь сохранённых ИИ-анализов деятельности."""
    return build_calendar_keyboard(
        user_id=user_id,
        year=year,
        month=month,
        callback_prefix="act_cal",
        marker="🤖",
        get_days_func=get_month_activity_analysis_days,
    )


def build_activity_analysis_day_actions_keyboard(entries: list, target_date: date) -> InlineKeyboardMarkup:
    """Строит клавиатуру действий для дня календаря ИИ-анализов."""
    rows: list[list[InlineKeyboardButton]] = []

    for entry in entries:
        source_label = "ИИ" if getattr(entry, "source", "manual") == "generated" else "ручной"
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"🗑 Удалить ({source_label}) #{entry.id}",
                    callback_data=f"act_cal_del:{target_date.isoformat()}:{entry.id}",
                )
            ]
        )

    rows.append(
        [
            InlineKeyboardButton(
                text="➕ Добавить анализ",
                callback_data=f"act_cal_add:{target_date.isoformat()}",
            ),
        ]
    )
    rows.append(
        [
            InlineKeyboardButton(
                text="⬅️ Назад к календарю",
                callback_data=f"act_cal_back:{target_date.year}-{target_date.month:02d}",
            )
        ]
    )

    return InlineKeyboardMarkup(inline_keyboard=rows)
