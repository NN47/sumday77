"""Функции форматирования для тренировок."""
import logging
from dataclasses import dataclass
from html import escape
from datetime import date
from types import SimpleNamespace
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from database.models import Workout
from utils.workout_utils import calculate_workout_calories
from utils.activity_input_config import ActivityInputMethod, infer_input_method

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


def _format_grouped_number(value: float | int) -> str:
    number = float(value)
    if number.is_integer():
        return f"{int(number):,}".replace(",", " ")
    return f"{number:g}".replace(".", ",")


def format_approach_count(count: int) -> str:
    if count % 10 == 1 and count % 100 != 11:
        word = "подход"
    elif count % 10 in {2, 3, 4} and count % 100 not in {12, 13, 14}:
        word = "подхода"
    else:
        word = "подходов"
    return f"{count} {word}"


def _format_session_duration(seconds: int) -> str:
    hours, remainder = divmod(max(int(seconds), 0), 3600)
    minutes, seconds = divmod(remainder, 60)
    parts: list[str] = []
    if hours:
        parts.append(f"{hours} ч")
    if minutes:
        parts.append(f"{minutes} мин")
    if seconds:
        parts.append(f"{seconds} сек")
    return " ".join(parts) or "0 сек"


@dataclass(frozen=True)
class WorkoutExerciseSummary:
    """Фактические параметры одного упражнения внутри тренировочной сессии."""

    exercise_code: str
    name: str
    set_count: int
    repetitions: int | None
    duration_seconds: int | None
    distance_meters: float | None
    loads: tuple[tuple[str, float], ...]


def summarize_workout_session_exercises(exercises: list, workout_sets: list) -> list[WorkoutExerciseSummary]:
    """Суммирует подходы по стабильному идентификатору упражнения в сессии."""
    sets_by_exercise: dict[int, list] = {}
    for item in workout_sets:
        sets_by_exercise.setdefault(item.session_exercise_id, []).append(item)

    summaries: list[WorkoutExerciseSummary] = []
    for exercise in exercises:
        items = sets_by_exercise.get(exercise.id, [])
        if not items:
            continue
        repetitions = [int(item.repetitions) for item in items if item.repetitions is not None]
        durations = [int(item.duration_seconds) for item in items if item.duration_seconds is not None]
        distances = [float(item.distance_meters) for item in items if item.distance_meters is not None]
        loads: list[tuple[str, float]] = []
        for item in items:
            if item.load_kg is None:
                continue
            load = (str(item.load_kind or "working"), float(item.load_kg))
            if load not in loads:
                loads.append(load)
        summaries.append(WorkoutExerciseSummary(
            exercise_code=str(exercise.exercise_code),
            name=str(exercise.exercise_name_snapshot),
            set_count=len(items),
            repetitions=sum(repetitions) if repetitions else None,
            duration_seconds=sum(durations) if durations else None,
            distance_meters=sum(distances) if distances else None,
            loads=tuple(loads),
        ))
    return summaries


def format_workout_exercise_summary(summary: WorkoutExerciseSummary) -> str:
    """Форматирует упражнение только по параметрам, которые есть в подходах."""
    params: list[str] = []
    if summary.repetitions is not None:
        params.append(f"{_format_grouped_number(summary.repetitions)} раз")
    if summary.duration_seconds is not None:
        params.append(_format_session_duration(summary.duration_seconds))
    if summary.distance_meters is not None:
        params.append(f"{_format_grouped_number(summary.distance_meters)} м")

    if summary.set_count > 1 and params:
        params[0] += f" ({format_approach_count(summary.set_count)})"
    elif not params:
        params.append(format_approach_count(summary.set_count))

    if len(summary.loads) == 1:
        load_kind, load_kg = summary.loads[0]
        formatted_load = _format_grouped_number(load_kg)
        if load_kind == "additional":
            params.append(f"+{formatted_load} кг")
        elif load_kind == "assistance":
            params.append(f"помощь {formatted_load} кг")
        else:
            params.append(f"{formatted_load} кг")
    elif summary.loads:
        load_values = [load_kg for _, load_kg in summary.loads]
        params.append(
            f"вес {_format_grouped_number(min(load_values))}–{_format_grouped_number(max(load_values))} кг"
        )

    return f"{summary.name} — {', '.join(params)}"


def format_workout_session_exercise_summaries(exercises: list, workout_sets: list) -> list[str]:
    """Возвращает компактные строки всех заполненных упражнений сессии."""
    return [
        format_workout_exercise_summary(summary)
        for summary in summarize_workout_session_exercises(exercises, workout_sets)
    ]


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


def _activity_method(activity: Workout) -> ActivityInputMethod:
    method_value = getattr(activity, "input_method", None)
    try:
        return ActivityInputMethod(method_value) if method_value else infer_input_method(activity.exercise, activity.variant)
    except ValueError:
        return infer_input_method(activity.exercise, activity.variant)


def _activity_parameters(activity: Workout) -> str:
    variant = (activity.variant or "").casefold()
    count = activity.count or 0
    method = _activity_method(activity)
    sets = _positive_attr(activity, "sets", "approaches", "set_count")
    weight = _positive_attr(activity, "weight", "working_weight", "work_weight")

    params: list[str] = []
    if method == ActivityInputMethod.TIME:
        minutes = _positive_attr(activity, "duration_minutes") or count
        if minutes:
            params.append(f"{_format_number(minutes)} мин")
    elif method == ActivityInputMethod.DISTANCE:
        distance = _positive_attr(activity, "distance_km") or count
        if distance:
            params.append(f"{_format_number(distance)} км")
    elif method == ActivityInputMethod.JUMPS:
        jumps = _positive_attr(activity, "jumps_count") or count
        if jumps:
            params.append(f"{int(jumps):,} прыжков".replace(",", " "))
    elif sets:
        reps = _format_number(count) if count else ""
        params.append(f"{_format_number(sets)} подхода × {reps} раз" if reps else f"{_format_number(sets)} подхода")
    elif variant in {"минуты", "мин"}:
        params.append(f"{_format_number(count)} мин")
    elif count:
        params.append(f"{_format_number(count)} раз")

    if weight:
        params.append(f"{_format_number(weight)} кг")
    return ", ".join(params)


def _repetition_group_variant(activity: Workout) -> str:
    variant = (activity.variant or "").casefold()
    return "repetitions" if variant in {"", "reps", "повторения"} else variant


def _can_group_daily_activity(activity: Workout) -> bool:
    return (
        _activity_method(activity) == ActivityInputMethod.REPETITIONS
        and not _positive_attr(activity, "sets", "approaches", "set_count")
        and not _positive_attr(activity, "weight", "working_weight", "work_weight")
    )


def format_activity_daily_summaries(activities: list[Workout], user_id: str | None = None) -> list[str]:
    """Форматирует дневной список активностей, суммируя одинаковые записи повторений.

    Несколько подходов одного силового упражнения обычно сохраняются отдельными
    записями. На главном экране дня пользователю важнее увидеть общий объём за
    день, поэтому записи с одинаковым упражнением, способом ввода и рабочим
    весом объединяются. Разный вес остаётся отдельными строками, чтобы не
    смешивать параметры подходов.
    """
    grouped: dict[tuple, SimpleNamespace] = {}
    order: list[tuple] = []
    result: list[str] = []

    for activity in activities:
        method = _activity_method(activity)
        weight = _positive_attr(activity, "weight", "working_weight", "work_weight")
        if not _can_group_daily_activity(activity):
            result.append(format_activity_summary(activity, user_id))
            continue

        calories = activity.calories
        if (calories is None or calories == 0) and user_id:
            calories = calculate_workout_calories(user_id, activity.exercise, activity.variant, activity.count)
        key = (
            _normalize_exercise_name(activity.exercise),
            method.value,
            _repetition_group_variant(activity),
            float(weight) if weight is not None else None,
        )
        if key not in grouped:
            grouped[key] = SimpleNamespace(
                exercise=_normalize_exercise_name(activity.exercise),
                variant=activity.variant,
                count=0,
                calories=0.0,
                input_method=method.value,
                working_weight=weight,
            )
            order.append(key)
        grouped[key].count += activity.count or 0
        grouped[key].calories += calories or 0

    grouped_lines = [format_activity_summary(grouped[key], user_id) for key in order]
    if not result:
        return grouped_lines

    # Сохраняем исходный порядок: первая строка группы появляется на месте
    # первого подхода, остальные негруппируемые записи остаются как были.
    emitted: set[tuple] = set()
    ordered_lines: list[str] = []
    result_iter = iter(result)
    for activity in activities:
        method = _activity_method(activity)
        weight = _positive_attr(activity, "weight", "working_weight", "work_weight")
        key = (
            _normalize_exercise_name(activity.exercise),
            method.value,
            _repetition_group_variant(activity),
            float(weight) if weight is not None else None,
        )
        if _can_group_daily_activity(activity) and key in grouped:
            if key not in emitted:
                ordered_lines.append(format_activity_summary(grouped[key], user_id))
                emitted.add(key)
        else:
            ordered_lines.append(next(result_iter))
    return ordered_lines



def _format_set_line(activity: Workout) -> str:
    """Форматирует одну строку подхода/активности внутри сгруппированного отчёта."""
    method = _activity_method(activity)
    count = activity.count or 0
    weight = _positive_attr(activity, "weight", "working_weight", "work_weight")

    if method == ActivityInputMethod.TIME:
        minutes = _positive_attr(activity, "duration_minutes") or count
        return f"• {_format_number(minutes)} мин"
    if method == ActivityInputMethod.DISTANCE:
        distance = _positive_attr(activity, "distance_km") or count
        return f"• {_format_number(distance)} км"
    if method == ActivityInputMethod.JUMPS:
        jumps = _positive_attr(activity, "jumps_count") or count
        return f"• {int(jumps):,} прыжков".replace(",", " ")

    reps = _format_number(count)
    if weight:
        return f"• {_format_number(weight)} кг × {reps}"
    return f"• {reps} раз"


def format_grouped_workout_sets_report(activities: list[Workout], user_id: str | None = None) -> list[str]:
    """Форматирует подходы журналом тренировки, группируя только одинаковые упражнения."""
    groups: dict[str, dict] = {}
    order: list[str] = []

    for activity in activities:
        name = _normalize_exercise_name(activity.exercise)
        if name not in groups:
            groups[name] = {"lines": [], "calories": 0.0}
            order.append(name)
        groups[name]["lines"].append(_format_set_line(activity))
        calories = activity.calories
        if (calories is None or calories == 0) and user_id:
            calories = calculate_workout_calories(user_id, activity.exercise, activity.variant, activity.count)
        groups[name]["calories"] += calories or 0

    lines: list[str] = []
    for name in order:
        if lines:
            lines.append("")
        lines.append(f"<b>{escape(name)}</b>")
        lines.extend(groups[name]["lines"])
        lines.append(f"≈ {groups[name]['calories']:.0f} ккал")
    return lines

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
