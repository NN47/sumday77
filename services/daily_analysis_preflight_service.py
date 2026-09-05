"""Единый preflight подробного анализа лимитного дня."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date
from html import escape

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy.exc import SQLAlchemyError

from database.repositories import (
    ActivityRepository,
    MealRepository,
    NoteRepository,
    WaterRepository,
    WeightRepository,
    WorkoutRepository,
)
from database.repositories.daily_analysis_preparation_repository import (
    daily_analysis_preparation_repository,
)
from services.ai_quota_service import quota_period_key
from utils.workout_formatters import (
    format_approach_count,
    format_workout_exercise_summary,
    summarize_workout_session_exercises,
)


PREFLIGHT_CALLBACK_PREFIX = "day_pf"


@dataclass(frozen=True)
class DailyAnalysisPreflight:
    user_id: str
    target_date: date
    meal_count: int
    calories: int
    water_ml: int
    steps: int
    activity_text: str
    weight_text: str
    note_present: bool
    is_empty: bool
    snapshot_hash: str


def _format_number(value: float | int) -> str:
    return f"{round(float(value)):,}".replace(",", " ")


def _activity_line(workouts: list) -> tuple[int, str]:
    steps = 0
    activity_parts: list[str] = []
    for workout in workouts:
        exercise = (workout.exercise or "Активность").strip()
        if exercise.lower() in {"шаги", "steps"}:
            steps += int(workout.count or 0)
            continue
        if workout.duration_minutes:
            value = f"{_format_number(workout.duration_minutes)} мин"
        elif workout.distance_km:
            value = f"{str(round(workout.distance_km, 2)).replace('.', ',')} км"
        elif workout.jumps_count:
            value = f"{_format_number(workout.jumps_count)} прыжков"
        elif workout.count:
            value = f"{_format_number(workout.count)} раз"
        else:
            value = "внесена"
        activity_parts.append(f"{exercise.lower()} {value}")
    return steps, _join_activity_parts(activity_parts)


def _join_activity_parts(parts: list[str], limit: int = 5) -> str:
    if not parts:
        return "не внесена"
    visible = parts[:limit]
    hidden_count = len(parts) - len(visible)
    if hidden_count:
        visible.append(f"ещё {hidden_count}")
    return "; ".join(visible)


def collect_daily_preflight(user_id: str, target_date: date) -> DailyAnalysisPreflight:
    """Собирает только отображаемую сводку и необратимый хеш исходных данных."""
    user_id = str(user_id)
    meals = MealRepository.get_meals_for_date(user_id, target_date)
    totals = MealRepository.get_daily_totals(user_id, target_date)
    water_ml = int(WaterRepository.get_daily_total(user_id, target_date) or 0)
    # Новая модель является основной. Legacy fallback нужен только на период,
    # когда тестовые/старые базы ещё не содержат новых таблиц.
    session_snapshots: list[dict] = []
    try:
        timed_entries = ActivityRepository.get_timed_activities_for_day(user_id, target_date)
        workout_sessions = [
            item for item in ActivityRepository.get_workout_sessions_for_day(user_id, target_date)
            if item.status == "completed"
        ]
        daily_steps = ActivityRepository.get_steps_for_day(user_id, target_date)
        workout_session_parts: list[str] = []
        for workout_session in workout_sessions:
            exercises = ActivityRepository.get_session_exercises(workout_session.id, user_id)
            workout_sets = ActivityRepository.get_session_sets(workout_session.id, user_id)
            summaries = summarize_workout_session_exercises(exercises, workout_sets)
            summary_lines = [format_workout_exercise_summary(summary) for summary in summaries]
            if summary_lines:
                workout_session_parts.extend(summary_lines)
            elif getattr(workout_session, "set_count", 0):
                workout_session_parts.append(
                    f"Силовая тренировка — {format_approach_count(workout_session.set_count)}"
                )
            elif workout_session.duration_seconds:
                workout_session_parts.append(
                    f"Силовая тренировка — {_format_number(workout_session.duration_seconds / 60)} мин"
                )
            else:
                workout_session_parts.append("Силовая тренировка")
            session_snapshots.append({
                "id": workout_session.id,
                "duration_seconds": workout_session.duration_seconds,
                "intensity": workout_session.intensity,
                "exercises": [
                    {
                        "code": summary.exercise_code,
                        "name": summary.name,
                        "set_count": summary.set_count,
                        "repetitions": summary.repetitions,
                        "duration_seconds": summary.duration_seconds,
                        "distance_meters": summary.distance_meters,
                        "loads": summary.loads,
                    }
                    for summary in summaries
                ],
            })
    except SQLAlchemyError:
        timed_entries, workout_sessions, daily_steps = [], [], None
        workout_session_parts, session_snapshots = [], []
    if timed_entries or workout_sessions or daily_steps is not None:
        steps = int(getattr(daily_steps, "steps", 0) or 0)
        activity_parts = [
            f"{entry.activity_name_snapshot} — {_format_number(entry.duration_minutes)} мин"
            for entry in timed_entries
        ]
        activity_parts.extend(workout_session_parts)
        activity_text = _join_activity_parts(activity_parts)
        workouts = []
    else:
        workouts = WorkoutRepository.get_workouts_for_day(user_id, target_date)
        steps, activity_text = _activity_line(workouts)
    weight = WeightRepository.get_weight_for_date(user_id, target_date)
    note = NoteRepository.get_note_for_date(user_id, target_date)

    snapshot = {
        "date": target_date.isoformat(),
        "meals": [
            {
                "id": meal.id,
                "type": meal.meal_type,
                "calories": meal.calories,
                "protein": meal.protein,
                "fat": meal.fat,
                "carbs": meal.carbs,
            }
            for meal in meals
        ],
        "water_ml": water_ml,
        "workouts": [
            {
                "id": workout.id,
                "exercise": workout.exercise,
                "count": workout.count,
                "duration_minutes": workout.duration_minutes,
                "distance_km": workout.distance_km,
                "jumps_count": workout.jumps_count,
            }
            for workout in workouts
        ],
        "timed_activities": [
            {
                "id": entry.id,
                "activity_code": entry.activity_code,
                "duration_minutes": entry.duration_minutes,
                "intensity": entry.intensity,
            }
            for entry in timed_entries
        ],
        "workout_sessions": session_snapshots,
        "steps": steps,
        "weight": getattr(weight, "value", None),
        "note": {
            "rating": getattr(note, "day_rating", None),
            "factors": getattr(note, "factors_json", None),
        },
    }
    payload = json.dumps(snapshot, ensure_ascii=False, sort_keys=True, default=str)
    snapshot_hash = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    has_non_steps_activity = bool(timed_entries or workout_sessions) or any(
        (workout.exercise or "").strip().lower() not in {"шаги", "steps"}
        for workout in workouts
    )
    is_empty = not meals and not steps and not has_non_steps_activity

    return DailyAnalysisPreflight(
        user_id=user_id,
        target_date=target_date,
        meal_count=len(meals),
        calories=round(float(totals.get("calories") or 0)),
        water_ml=water_ml,
        steps=steps,
        activity_text=activity_text,
        weight_text=(str(weight.value).replace(".", ",") + " кг") if weight else "не внесён",
        note_present=note is not None,
        is_empty=is_empty,
        snapshot_hash=snapshot_hash,
    )


def build_daily_preflight_text(data: DailyAnalysisPreflight) -> str:
    months = (
        "",
        "января",
        "февраля",
        "марта",
        "апреля",
        "мая",
        "июня",
        "июля",
        "августа",
        "сентября",
        "октября",
        "ноября",
        "декабря",
    )
    formatted_date = f"{data.target_date.day} {months[data.target_date.month]}"
    activity = (
        escape(data.activity_text)
        if data.activity_text != "не внесена"
        else "не внесена. Если её не было, ничего добавлять не нужно"
    )
    return (
        "🧠 <b>Перед анализом дня</b>\n\n"
        f"Проверь, всё ли внесено за {formatted_date}:\n\n"
        f"🍱 Питание: {_format_number(data.calories)} ккал\n"
        f"💧 В дневнике воды: {_format_number(data.water_ml)} мл\n"
        f"👣 Шаги: {_format_number(data.steps)}\n"
        f"🏃 Активность: {activity}\n"
        f"⚖️ Вес: {data.weight_text}\n"
        f"📝 Заметка дня: {'добавлена' if data.note_present else 'не добавлена'}\n\n"
        "Анализ будет построен только по данным, которые сейчас находятся в дневнике."
    )


def build_daily_preflight_keyboard(target_date: date) -> InlineKeyboardMarkup:
    day = target_date.isoformat()
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🍱 Проверить питание", callback_data=f"{PREFLIGHT_CALLBACK_PREFIX}:food:{day}")],
            [InlineKeyboardButton(text="💧 Внести воду", callback_data=f"{PREFLIGHT_CALLBACK_PREFIX}:water:{day}")],
            [InlineKeyboardButton(text="👣 Указать шаги", callback_data=f"{PREFLIGHT_CALLBACK_PREFIX}:steps:{day}")],
            [InlineKeyboardButton(text="🏃 Внести активность", callback_data=f"{PREFLIGHT_CALLBACK_PREFIX}:activity:{day}")],
            [InlineKeyboardButton(text="📝 Заметка дня", callback_data=f"{PREFLIGHT_CALLBACK_PREFIX}:note:{day}")],
            [InlineKeyboardButton(text="✅ Всё внесено — начать анализ", callback_data=f"{PREFLIGHT_CALLBACK_PREFIX}:confirm:{day}")],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data=f"{PREFLIGHT_CALLBACK_PREFIX}:back:{day}")],
        ]
    )


async def show_daily_analysis_preflight(
    message: Message,
    user_id: str,
    target_date: date | None = None,
    *,
    origin: str = "menu",
    prefer_edit: bool = False,
) -> DailyAnalysisPreflight:
    target_date = target_date or quota_period_key()
    daily_analysis_preparation_repository.activate(user_id, target_date, origin)
    data = collect_daily_preflight(user_id, target_date)
    text = build_daily_preflight_text(data)
    keyboard = build_daily_preflight_keyboard(target_date)
    if prefer_edit:
        try:
            await message.edit_text(text, parse_mode="HTML", reply_markup=keyboard)
            return data
        except Exception:
            pass
    await message.answer(text, parse_mode="HTML", reply_markup=keyboard)
    return data


async def return_to_active_daily_preflight(
    message: Message,
    user_id: str,
    target_date: date | None = None,
) -> bool:
    """Возвращает пользователя к обновлённой проверке после сохранения данных."""
    active = daily_analysis_preparation_repository.get_active(str(user_id))
    if active is None:
        return False
    if target_date is not None and active.target_date != target_date:
        return False
    await show_daily_analysis_preflight(
        message,
        str(user_id),
        active.target_date,
        origin=active.origin,
    )
    return True


def active_preflight_date(user_id: str) -> date | None:
    active = daily_analysis_preparation_repository.get_active(str(user_id))
    return active.target_date if active else None


def complete_daily_preflight(user_id: str, target_date: date | None = None) -> None:
    daily_analysis_preparation_repository.complete(str(user_id), target_date)
