"""Функции форматирования прогресса и сводок."""
import logging
import random
from datetime import date, datetime
from database.repositories import (
    MealRepository,
    WorkoutRepository,
    WeightRepository,
    WaterRepository,
)
from database.models import Workout
from utils.formatters import get_kbju_goal_label, format_count_with_unit
from utils.workout_utils import calculate_workout_calories, get_daily_workout_calories

logger = logging.getLogger(__name__)


def build_progress_bar(current: float, target: float, length: int = 10) -> str:
    """
    Строит индикатор прогресса по КБЖУ:
    - ⬜⬜⬜⬜⬜⬜⬜⬜⬜⬜ - Пустое значение (target <= 0 или current == 0)
    - 🟩🟩🟩🟩⬜⬜⬜⬜⬜⬜ - Обычный прогресс (0-101%)
    - 🟩🟩🟩🟩🟩🟩🟩🟩🟩🟩 - 101% (ровно)
    - 🟨🟨🟨🟨🟨🟨🟨🟨🟨🟨 - 102-135%
    - 🟥🟥🟥🟥🟥🟥🟥🟥🟥🟥 - >135%
    """
    if target <= 0 or current <= 0:
        return "⬜" * length
    
    percent = (current / target) * 100
    
    if percent > 135:
        return "🟥" * length
    elif percent > 101:
        return "🟨" * length
    else:
        filled_blocks = min(int(round((current / target) * length)), length)
        empty_blocks = max(length - filled_blocks, 0)
        return "🟩" * filled_blocks + "⬜" * empty_blocks


def build_water_progress_bar(current: float, target: float, length: int = 10) -> str:
    """
    Строит индикатор прогресса по воде (аналогично build_progress_bar, но с синими кубиками):
    - ⬜⬜⬜⬜⬜⬜⬜⬜⬜⬜ - Пустое значение (target <= 0 или current == 0)
    - 🟦🟦🟦🟦⬜⬜⬜⬜⬜⬜ - Обычный прогресс (0-101%)
    - 🟦🟦🟦🟦🟦🟦🟦🟦🟦🟦 - 101% (ровно)
    - 🟨🟨🟨🟨🟨🟨🟨🟨🟨🟨 - 102-135%
    - 🟥🟥🟥🟥🟥🟥🟥🟥🟥🟥 - >135%
    """
    if target <= 0 or current <= 0:
        return "⬜" * length
    
    percent = (current / target) * 100
    
    if percent > 135:
        return "🟥" * length
    elif percent > 101:
        return "🟨" * length
    else:
        filled_blocks = min(int(round((current / target) * length)), length)
        empty_blocks = max(length - filled_blocks, 0)
        return "🟦" * filled_blocks + "⬜" * empty_blocks




def format_progress_block(user_id: str) -> str:
    """Форматирует блок прогресса КБЖУ."""
    settings = MealRepository.get_kbju_settings(user_id)
    if not settings:
        return "🍱 Настрой цель по КБЖУ через «🎯 Цель / Норма КБЖУ», чтобы я показывал прогресс."
    
    totals = MealRepository.get_daily_totals(user_id, date.today())
    burned_calories = get_daily_workout_calories(user_id, date.today())
    
    base_calories_target = settings.calories
    adjusted_calories_target = base_calories_target + burned_calories
    
    if base_calories_target > 0:
        ratio = adjusted_calories_target / base_calories_target
        adjusted_protein_target = settings.protein * ratio
        adjusted_fat_target = settings.fat * ratio
        adjusted_carbs_target = settings.carbs * ratio
    else:
        adjusted_protein_target = settings.protein
        adjusted_fat_target = settings.fat
        adjusted_carbs_target = settings.carbs
    
    def line(label: str, current: float, target: float, unit: str) -> str:
        percent = 0 if target <= 0 else round((current / target) * 100)
        bar = build_progress_bar(current, target)
        return f"{label}: {current:.0f}/{target:.0f} {unit} ({percent}%)\n{bar}"
    
    goal_label = get_kbju_goal_label(settings.goal)
    
    lines = ["🍱 <b>КБЖУ</b>"]
    lines.append(f"🎯 <b>Цель:</b> {goal_label}")
    lines.append(f"📊 <b>Базовая норма:</b> {base_calories_target:.0f} ккал")
    
    if burned_calories > 0:
        lines.append(f"🔥 <b>Сожжено:</b> ~{burned_calories:.0f} ккал")
        lines.append(f"✅ <b>Скорректированная норма:</b> {adjusted_calories_target:.0f} ккал")
    
    lines.append("")
    lines.append(line("🔥 Калории", totals["calories"], adjusted_calories_target, "ккал"))
    lines.append(line("💪 Белки", totals.get("protein_g", totals.get("protein", 0)), adjusted_protein_target, "г"))
    lines.append(line("🥑 Жиры", totals.get("fat_total_g", totals.get("fat", 0)), adjusted_fat_target, "г"))
    lines.append(line("🍩 Углеводы", totals.get("carbohydrates_total_g", totals.get("carbs", 0)), adjusted_carbs_target, "г"))
    
    return "\n".join(lines)


def format_water_progress_block(user_id: str) -> str:
    """Форматирует блок прогресса воды."""
    from handlers.water import get_water_recommended
    
    today = date.today()
    daily_total = WaterRepository.get_daily_total(user_id, today)
    recommended = get_water_recommended(user_id)
    
    percent = 0 if recommended <= 0 else round((daily_total / recommended) * 100)
    bar = build_water_progress_bar(daily_total, recommended)
    
    return f"💧 <b>Вода</b>: {daily_total:.0f}/{recommended:.0f} мл ({percent}%)\n{bar}"


def format_today_workouts_block(user_id: str, include_date: bool = True) -> str:
    """Форматирует блок тренировок за сегодня."""
    today = date.today()
    workouts = WorkoutRepository.get_workouts_for_day(user_id, today)
    
    if not workouts:
        return "💪 <b>Тренировки</b>\n—"
    
    text = ["💪 <b>Тренировки</b>"]
    total_calories = 0.0
    aggregates: dict[tuple[str, str | None], dict[str, float]] = {}
    
    for w in workouts:
        entry_calories = w.calories or calculate_workout_calories(
            user_id, w.exercise, w.variant, w.count
        )
        total_calories += entry_calories
        
        key = (w.exercise, w.variant)
        if key not in aggregates:
            aggregates[key] = {"count": 0, "calories": 0.0}
        
        aggregates[key]["count"] += w.count
        aggregates[key]["calories"] += entry_calories
    
    for (exercise, variant), data in aggregates.items():
        variant_text = f" ({variant})" if variant else ""
        formatted_count = format_count_with_unit(data["count"], variant)
        text.append(
            f"• {exercise}{variant_text}: {formatted_count} (~{data['calories']:.0f} ккал)"
        )
    
    text.append(f"🔥 Итого за день: ~{total_calories:.0f} ккал")
    
    return "\n".join(text)


def get_today_summary_text(user_id: str) -> str:
    """Получает сводку за сегодня."""
    today = date.today()
    today_str = datetime.now().strftime("%d.%m.%Y")
    
    greetings = [
        "🔥 Новый день — новые победы!",
        "🚀 Пора действовать!",
        "💪 Сегодня ты становишься сильнее!",
        "🌟 Всё получится, просто начни!",
        "🏁 Вперёд к цели!",
    ]
    motivation = random.choice(greetings)
    
    workouts = WorkoutRepository.get_workouts_for_day(user_id, today)
    meals_today = MealRepository.get_meals_for_date(user_id, today)
    weight = WeightRepository.get_last_weight(user_id)
    measurements = WeightRepository.get_measurements(user_id, limit=1)
    m = measurements[0] if measurements else None
    
    has_today_anything = bool(workouts or meals_today)
    
    if not has_today_anything:
        summary_lines = [
            f"Сегодня ({today_str}) у тебя пока нет записей 📭\n",
            "🏋️ <b>Тренировки</b>\n"
            "Записывай подходы, время и шаги. Бот считает примерный расход калорий "
            "по типу упражнения, длительности/повторам и твоему весу.",
            "\n🍱 <b>Питание</b>\n"
            "Добавляй приёмы пищи — я посчитаю КБЖУ для каждого приёма и суммарно за день.",
            "\n⚖️ <b>Вес и замеры</b>\n"
            "Фиксируй вес и замеры (грудь, талия, бёдра), чтобы видеть прогресс не только "
            "в цифрах калорий.",
            "\nНачни с любого раздела в меню ниже 👇",
        ]
        
        if weight or m:
            summary_lines.append("\n\n<b>Последние данные:</b>")
            if weight:
                # TODO: Получить дату последнего веса
                summary_lines.append(f"\n⚖️ Вес: {weight:.1f} кг")
            if m:
                parts = []
                if m.chest:
                    parts.append(f"Грудь {m.chest} см")
                if m.waist:
                    parts.append(f"Талия {m.waist} см")
                if m.hips:
                    parts.append(f"Бёдра {m.hips} см")
                if parts:
                    summary_lines.append(f"\n📏 Замеры: {', '.join(parts)} ({m.date})")
        
        summary = "".join(summary_lines)
    else:
        if not workouts:
            summary = f"Сегодня ({today_str}) тренировок пока нет 💭\n"
        else:
            summary = f"📅 {today_str}\n 🏋️ Тренировка:\n"
            totals: dict[str, int] = {}
            for w in workouts:
                totals[w.exercise] = totals.get(w.exercise, 0) + w.count
            for ex, total in totals.items():
                summary += f"• {ex}: {total}\n"
        
        if weight:
            summary += f"\n⚖️ Вес: {weight:.1f} кг"
        
        if m:
            parts = []
            if m.chest:
                parts.append(f"Грудь {m.chest} см")
            if m.waist:
                parts.append(f"Талия {m.waist} см")
            if m.hips:
                parts.append(f"Бёдра {m.hips} см")
            if parts:
                summary += f"\n📏 Замеры: {', '.join(parts)} ({m.date})"
    
    return f"{motivation}\n\n{summary}"
