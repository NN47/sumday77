"""Функции форматирования для тренировок."""
import logging
from datetime import date
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from database.models import Workout
from utils.workout_utils import calculate_workout_calories

logger = logging.getLogger(__name__)


def _normalize_exercise_name(exercise: str) -> str:
    aliases = {
        "Пробежка": "Бег",
        "Шаги (Ходьба)": "Шаги",
        "Ходьба": "Шаги",
    }
    return aliases.get(exercise, exercise)


def is_steps_workout(workout: Workout) -> bool:
    """Проверяет, что запись относится к шагам, а не к обычной активности."""
    exercise = _normalize_exercise_name(workout.exercise)
    return exercise == "Шаги" or "шаг" in (workout.variant or "").casefold()


def _format_number(value: float | int) -> str:
    number = float(value)
    if number.is_integer():
        return str(int(number))
    return f"{number:g}".replace(".", ",")


def _positive_attr(activity: Workout, *names: str) -> float | int | None:
    for name in names:
        value = getattr(activity, name, None)
        if value is None:
            continue
        try:
            if float(value) > 0:
                return value
        except (TypeError, ValueError):
            continue
    return None


def _activity_parameters(activity: Workout) -> str:
    variant = (activity.variant or "").casefold()
    count = activity.count or 0
    sets = _positive_attr(activity, "sets", "approaches", "set_count")
    weight = _positive_attr(activity, "weight", "working_weight", "work_weight")

    params: list[str] = []
    if sets:
        reps = _format_number(count) if count else ""
        params.append(f"{_format_number(sets)} подхода × {reps} раз" if reps else f"{_format_number(sets)} подхода")
    elif variant in {"минуты", "мин"}:
        params.append(f"{_format_number(count)} мин")
    elif count:
        params.append(f"{_format_number(count)} раз")

    if weight:
        params.append(f"{_format_number(weight)} кг")
    return ", ".join(params)


def format_activity_summary(activity: Workout, user_id: str | None = None, *, include_calories: bool = True) -> str:
    """Форматирует одну запись активности для отчётов и меню редактирования."""
    name = _normalize_exercise_name(activity.exercise)
    params = _activity_parameters(activity)
    text = f"{name} — {params}" if params else name

    if include_calories:
        calories = activity.calories
        if (calories is None or calories == 0) and user_id:
            calories = calculate_workout_calories(user_id, activity.exercise, activity.variant, activity.count)
        if calories:
            text += f" (~{float(calories):.0f} ккал)"
    return text


def format_activity_edit_button(activity: Workout) -> str:
    """Короткая подпись inline-кнопки выбора записи для редактирования."""
    return format_activity_summary(activity, include_calories=False)


def build_day_actions_keyboard(
    workouts: list[Workout],
    target_date: date,
    *,
    include_calendar_back: bool = True,
) -> InlineKeyboardMarkup:
    """Строит клавиатуру с действиями для тренировок за день."""
    rows: list[list[InlineKeyboardButton]] = []
    
    for w in workouts:
        label = format_activity_edit_button(w)
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
                text="➕ Добавить упражнение",
                callback_data=f"wrk_add:{target_date.isoformat()}",
            )
        ]
    )
    
    if include_calendar_back:
        rows.append(
            [
                InlineKeyboardButton(
                    text="⬅️ Назад к календарю активности",
                    callback_data=f"cal_back:{target_date.year}-{target_date.month:02d}",
                )
            ]
        )
    
    return InlineKeyboardMarkup(inline_keyboard=rows)
