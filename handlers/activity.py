"""Обработчики для анализа деятельности."""
import logging
import re
import html
import json
from datetime import date, timedelta
from collections import Counter
from aiogram import Router
from aiogram.types import Message, CallbackQuery
from aiogram.fsm.context import FSMContext
from utils.keyboards import activity_analysis_menu, push_menu_stack
from utils.emoji_map import EMOJI_MAP
from utils.calendar_utils import (
    build_activity_analysis_calendar_keyboard,
    build_activity_analysis_day_actions_keyboard,
)
from database.repositories.activity_analysis_repository import ActivityAnalysisRepository
from states.user_states import ActivityAnalysisStates
from services.gemini_service import gemini_service

logger = logging.getLogger(__name__)

router = Router()


def _normalize_workout_type(exercise: str, variant: str | None = None) -> str:
    """Нормализует тип упражнения в короткий machine-readable формат."""
    text = f"{exercise or ''} {variant or ''}".lower()
    if any(token in text for token in ["шаг", "steps", "ходьб", "прогул"]):
        return "steps"
    if any(token in text for token in ["отжим", "push"]):
        return "pushups"
    if any(token in text for token in ["присед", "squat"]):
        return "squats"
    if any(token in text for token in ["пресс", "abs", "скручив"]):
        return "abs"
    if any(token in text for token in ["подтяг", "pullup"]):
        return "pullups"
    if any(token in text for token in ["бег", "run", "кардио", "вел", "bike"]):
        return "cardio"
    return "other"


def _is_strength_type(workout_type: str) -> bool:
    return workout_type in {"pushups", "squats", "abs", "pullups"}


def _extract_analysis_short_summary(analysis_text: str, max_len: int = 220) -> str:
    """Возвращает короткий подитог из полного текста ИИ-отчёта."""
    # Убираем HTML-разметку и лишние пробелы
    clean_text = re.sub(r"<[^>]+>", "", analysis_text or "")
    clean_text = re.sub(r"\s+", " ", clean_text).strip()
    if not clean_text:
        return "Короткий подитог недоступен."

    # Пробуем взять раздел с итогом/мотивацией — обычно это самый полезный подитог отчёта
    summary_match = re.search(
        r"(?:4\)\s*📈\s*Общий прогресс и мотивация|Общий прогресс и мотивация)\s*(.+)",
        clean_text,
        flags=re.IGNORECASE,
    )
    if summary_match:
        tail = summary_match.group(1).strip(" :.-")
        first_sentence = re.split(r"(?<=[.!?])\s+", tail, maxsplit=1)[0].strip()
        if first_sentence:
            return first_sentence[: max_len - 1] + "…" if len(first_sentence) > max_len else first_sentence

    # Фолбэк: первая содержательная строка после приветствия
    lines = [line.strip(" •-") for line in re.split(r"[\n\r]+", clean_text) if line.strip()]
    for line in lines:
        if "я на связи" in line.lower() or "вот твой отчёт" in line.lower():
            continue
        if len(line) < 12:
            continue
        return line[: max_len - 1] + "…" if len(line) > max_len else line

    return clean_text[: max_len - 1] + "…" if len(clean_text) > max_len else clean_text


async def generate_activity_analysis(user_id: str, start_date: date, end_date: date, period_name: str) -> str:
    """Генерирует анализ активности за указанный период через Gemini."""
    from database.repositories import (
        WorkoutRepository, MealRepository, WeightRepository,
        WaterRepository, SupplementRepository, ProcedureRepository,
        WellbeingRepository
    )
    from utils.workout_utils import calculate_workout_calories
    from utils.formatters import format_count_with_unit, get_kbju_goal_label
    
    days_count = (end_date - start_date).days + 1
    
    # 🔹 Тренировки за период
    workouts = WorkoutRepository.get_workouts_for_period(user_id, start_date, end_date)
    
    workouts_by_ex = {}
    total_workout_calories = 0.0
    workout_days = set()
    
    for w in workouts:
        key = (w.exercise, w.variant)
        entry = workouts_by_ex.setdefault(key, {"count": 0, "calories": 0.0})
        entry["count"] += w.count
        cals = w.calories or calculate_workout_calories(user_id, w.exercise, w.variant, w.count)
        entry["calories"] += cals
        total_workout_calories += cals
        workout_days.add(w.date)
    
    workout_days_count = len(workout_days)
    avg_workout_calories = total_workout_calories / workout_days_count if workout_days_count > 0 else 0
    
    if workouts_by_ex:
        workout_lines = []
        for (exercise, variant), data in workouts_by_ex.items():
            formatted_count = format_count_with_unit(data["count"], variant)
            variant_text = f" ({variant})" if variant else ""
            workout_lines.append(
                f"- {exercise}{variant_text}: {formatted_count}, ~{data['calories']:.0f} ккал"
            )
        workout_summary = "\n".join(workout_lines)
        workout_summary += f"\n\nВсего тренировочных дней: {workout_days_count} из {days_count}"
        if days_count > 1:
            workout_summary += f" ({workout_days_count * 100 // days_count if days_count > 0 else 0}%)."
        else:
            workout_summary += "."
        workout_summary += f"\nСредний расход калорий за тренировочный день: ~{avg_workout_calories:.0f} ккал."
    else:
        workout_summary = f"За {period_name.lower()} тренировки не записаны."

    # Структурированный input для блока "Тренировки"
    today_workouts = WorkoutRepository.get_workouts_for_day(user_id, end_date)
    today_workouts_by_type = {}
    today_steps = 0
    today_workout_kcal = 0.0
    today_strength_volume_score = 0

    for w in today_workouts:
        w_type = _normalize_workout_type(w.exercise, w.variant)
        unit = "steps" if w_type == "steps" else "reps"
        cals = w.calories or calculate_workout_calories(user_id, w.exercise, w.variant, w.count)
        today_workout_kcal += cals

        if w_type == "steps":
            today_steps += int(w.count or 0)
            continue

        if _is_strength_type(w_type):
            today_strength_volume_score += int(w.count or 0)

        item = today_workouts_by_type.setdefault(w_type, {"type": w_type, "value": 0, "unit": unit})
        item["value"] += int(w.count or 0)

    # История за последние 7 дней (включая выбранную дату)
    hist_start = end_date - timedelta(days=6)
    history_workouts = WorkoutRepository.get_workouts_for_period(user_id, hist_start, end_date)
    day_steps = {}
    day_strength_score = {}
    for w in history_workouts:
        day_key = w.date.isoformat()
        w_type = _normalize_workout_type(w.exercise, w.variant)
        day_steps.setdefault(day_key, 0)
        day_strength_score.setdefault(day_key, 0)
        if w_type == "steps":
            day_steps[day_key] += int(w.count or 0)
        if _is_strength_type(w_type):
            day_strength_score[day_key] += int(w.count or 0)

    last7_avg_steps = (sum(day_steps.values()) / 7) if day_steps else 0
    last7_avg_strength = (sum(day_strength_score.values()) / 7) if day_strength_score else 0
    yesterday_key = (end_date - timedelta(days=1)).isoformat()
    yesterday_strength = day_strength_score.get(yesterday_key)

    settings = MealRepository.get_kbju_settings(user_id)
    user_goal = settings.goal if settings else None

    workout_ai_input = {
        "date": end_date.isoformat(),
        "workouts": list(today_workouts_by_type.values()),
        "steps": today_steps,
        "estimated_kcal_burn": round(today_workout_kcal),
        "plan": {
            "planned_training_day": None,
            "planned_types": None,
        },
        "history": {
            "last7_avg_steps": round(last7_avg_steps),
            "last7_avg_strength_volume_score": round(last7_avg_strength),
            "today_strength_volume_score": today_strength_volume_score,
            "yesterday_strength_volume_score": yesterday_strength,
        },
        "user_goal": user_goal,
    }
    
    # 🔹 КБЖУ за период
    meals = []
    meal_days = set()
    current_date = start_date
    while current_date <= end_date:
        day_meals = MealRepository.get_meals_for_date(user_id, current_date)
        if day_meals:
            meals.extend(day_meals)
            meal_days.add(current_date)
        current_date += timedelta(days=1)
    
    total_calories = sum(m.calories or 0 for m in meals)
    total_protein = sum(m.protein or 0 for m in meals)
    total_fat = sum(m.fat or 0 for m in meals)
    total_carbs = sum(m.carbs or 0 for m in meals)
    
    # 🔹 Цель / норма КБЖУ и проценты выполнения
    settings = MealRepository.get_kbju_settings(user_id)
    if settings:
        goal_label = get_kbju_goal_label(settings.goal)
        goal_calories = settings.calories * days_count
        goal_protein = settings.protein * days_count
        goal_fat = settings.fat * days_count
        goal_carbs = settings.carbs * days_count
        
        calories_percent = (total_calories / goal_calories * 100) if goal_calories > 0 else 0
        protein_percent = (total_protein / goal_protein * 100) if goal_protein > 0 else 0
        fat_percent = (total_fat / goal_fat * 100) if goal_fat > 0 else 0
        carbs_percent = (total_carbs / goal_carbs * 100) if goal_carbs > 0 else 0
        
        meals_summary = (
            f"{EMOJI_MAP['calories']} Калории: {total_calories:.0f} / {goal_calories:.0f} ккал ({calories_percent:.0f}%), "
            f"{EMOJI_MAP['protein']} Белки: {total_protein:.1f} / {goal_protein:.1f} г ({protein_percent:.0f}%), "
            f"{EMOJI_MAP['fat']} Жиры: {total_fat:.1f} / {goal_fat:.1f} г ({fat_percent:.0f}%), "
            f"{EMOJI_MAP['carbs']} Углеводы: {total_carbs:.1f} / {goal_carbs:.1f} г ({carbs_percent:.0f}%)."
        )
        
        kbju_goal_summary = (
            f"Цель: {goal_label}. "
            f"Дней с записями питания: {len(meal_days)} из {days_count} ({len(meal_days) * 100 // days_count if days_count > 0 else 0}%)."
        )
    else:
        meals_summary = (
            f"{EMOJI_MAP['calories']} Калории: {total_calories:.0f} ккал, "
            f"{EMOJI_MAP['protein']} Белки: {total_protein:.1f} г, "
            f"{EMOJI_MAP['fat']} Жиры: {total_fat:.1f} г, "
            f"{EMOJI_MAP['carbs']} Углеводы: {total_carbs:.1f} г."
        )
        kbju_goal_summary = "Цель по КБЖУ ещё не настроена."
    
    # 🔹 Статистика по дням недели (для недели и месяца)
    weekday_stats = ""
    if days_count >= 7:
        from collections import defaultdict
        weekday_workouts = defaultdict(int)
        weekday_meals = defaultdict(int)
        weekday_names = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
        
        for w in workouts:
            weekday_workouts[w.date.weekday()] += 1
        for d in meal_days:
            weekday_meals[d.weekday()] += 1
        
        if weekday_workouts or weekday_meals:
            weekday_lines = []
            for day_idx in range(7):
                workout_count = weekday_workouts.get(day_idx, 0)
                meal_count = weekday_meals.get(day_idx, 0)
                if workout_count > 0 or meal_count > 0:
                    weekday_lines.append(
                        f"{weekday_names[day_idx]}: тренировок {workout_count}, дней с питанием {meal_count}"
                    )
            if weekday_lines:
                weekday_stats = "\nСтатистика по дням недели:\n" + "\n".join(weekday_lines)
    
    # 🔹 Вода за период
    total_water = 0.0
    water_days = set()
    current_date = start_date
    while current_date <= end_date:
        day_water = WaterRepository.get_daily_total(user_id, current_date)
        if day_water > 0:
            total_water += day_water
            water_days.add(current_date)
        current_date += timedelta(days=1)
    
    avg_water = total_water / len(water_days) if water_days else 0
    water_summary = ""
    if water_days:
        water_summary = (
            f"\nВода: всего {total_water:.0f} мл за период, "
            f"среднее {avg_water:.0f} мл/день, "
            f"дней с записями: {len(water_days)} из {days_count}."
        )
    
    # 🔹 Добавки за период
    supplements = SupplementRepository.get_supplements(user_id)
    supplement_summary = ""
    if supplements:
        supplement_entries_count = 0
        supplement_names = []
        for sup in supplements:
            for entry in sup.get("history", []):
                entry_date = entry["timestamp"].date() if hasattr(entry["timestamp"], "date") else entry["timestamp"]
                if start_date <= entry_date <= end_date:
                    supplement_entries_count += 1
                    if sup["name"] not in supplement_names:
                        supplement_names.append(sup["name"])
        
        if supplement_entries_count > 0:
            supplement_summary = (
                f"\nДобавки: {supplement_entries_count} приёмов, "
                f"активных добавок: {len(supplement_names)} ({', '.join(supplement_names[:3])}"
                f"{'...' if len(supplement_names) > 3 else ''})."
            )
    
    # 🔹 Процедуры за период
    procedure_count = 0
    current_date = start_date
    while current_date <= end_date:
        day_procedures = ProcedureRepository.get_procedures_for_day(user_id, current_date)
        procedure_count += len(day_procedures)
        current_date += timedelta(days=1)
    
    procedure_summary = ""
    if procedure_count > 0:
        procedure_summary = f"\nПроцедуры: {procedure_count} записей за период."

    # 🔹 Самочувствие за период
    wellbeing_entries = WellbeingRepository.get_entries_for_period(user_id, start_date, end_date)
    wellbeing_summary = ""
    if wellbeing_entries:
        quick_entries = [entry for entry in wellbeing_entries if entry.entry_type == "quick"]
        comment_entries = [
            entry for entry in wellbeing_entries if entry.entry_type == "comment" and entry.comment
        ]
        mood_counts = Counter(entry.mood for entry in quick_entries if entry.mood)
        influence_counts = Counter(entry.influence for entry in quick_entries if entry.influence)
        difficulty_counts = Counter(entry.difficulty for entry in quick_entries if entry.difficulty)

        mood_summary = ", ".join(
            f"{mood} — {count}" for mood, count in mood_counts.most_common()
        )
        influence_summary = ", ".join(
            f"{influence} — {count}" for influence, count in influence_counts.most_common()
        )
        difficulty_summary = ", ".join(
            f"{difficulty} — {count}" for difficulty, count in difficulty_counts.most_common()
        )

        wellbeing_parts = [
            f"Записей самочувствия: {len(wellbeing_entries)} "
            f"(быстрых опросов: {len(quick_entries)}, комментариев: {len(comment_entries)})."
        ]
        if mood_summary:
            wellbeing_parts.append(f"Настроение: {mood_summary}.")
        if influence_summary:
            wellbeing_parts.append(f"Что влияло чаще всего: {influence_summary}.")
        if difficulty_summary:
            wellbeing_parts.append(f"Сложности: {difficulty_summary}.")
        if comment_entries:
            latest_comment = comment_entries[0]
            wellbeing_parts.append(
                f"Последний комментарий ({latest_comment.date.strftime('%d.%m')}): {latest_comment.comment}."
            )
        wellbeing_summary = "\n" + " ".join(wellbeing_parts)
    else:
        wellbeing_summary = "\nСамочувствие: записей за период нет."
    
    # 🔹 Вес и история веса
    weights = WeightRepository.get_weights_for_date_range(user_id, start_date, end_date)

    # Для коротких периодов (например, анализа за день) всё равно берём минимум неделю,
    # чтобы ИИ видел динамику и мог оценить прогресс за последние дни.
    weight_trend_start = min(start_date, end_date - timedelta(days=6))
    trend_weights = WeightRepository.get_weights_for_date_range(user_id, weight_trend_start, end_date)

    if trend_weights:
        current_weight = trend_weights[0]
        if len(trend_weights) > 1:
            first_weight = trend_weights[-1]
            current_weight_value = float(str(current_weight.value).replace(",", "."))
            first_weight_value = float(str(first_weight.value).replace(",", "."))
            change = current_weight_value - first_weight_value
            change_percent = (change / first_weight_value * 100) if first_weight_value > 0 else 0
            change_text = f" ({'+' if change >= 0 else ''}{change:.1f} кг, {change_percent:+.1f}%)"
        else:
            change_text = ""

        history_lines = [
            f"{w.date.strftime('%d.%m')}: {w.value} кг"
            for w in trend_weights[:10]
        ]
        trend_window = (
            f"{weight_trend_start.strftime('%d.%m.%Y')} - {end_date.strftime('%d.%m.%Y')}"
        )
        period_note = ""
        if not weights:
            period_note = f" За {period_name.lower()} новых измерений не было."

        weight_summary = (
            f"Текущий вес: {current_weight.value} кг (от {current_weight.date.strftime('%d.%m.%Y')}){change_text}. "
            f"Динамика веса за период {trend_window}: " + "; ".join(history_lines) + "."
            f"{period_note}"
        )
    else:
        weight_summary = "Записей по весу ещё нет."
    
    # 🔹 Сравнение с предыдущим периодом (для недели и месяца)
    comparison_summary = ""
    if days_count >= 7:
        prev_start = start_date - timedelta(days=days_count)
        prev_end = start_date - timedelta(days=1)
        
        prev_workouts = WorkoutRepository.get_workouts_for_period(user_id, prev_start, prev_end)
        prev_workout_days = len(set(w.date for w in prev_workouts))
        
        prev_meals = []
        prev_date = prev_start
        while prev_date <= prev_end:
            prev_meals.extend(MealRepository.get_meals_for_date(user_id, prev_date))
            prev_date += timedelta(days=1)
        prev_calories = sum(m.calories or 0 for m in prev_meals)
        
        if prev_workout_days > 0 or prev_calories > 0:
            workout_change = workout_days_count - prev_workout_days
            calories_change = total_calories - prev_calories
            
            comparison_lines = []
            if workout_change != 0:
                comparison_lines.append(f"Тренировочных дней: {workout_change:+d} к предыдущему периоду")
            if calories_change != 0:
                comparison_lines.append(f"Калорий: {calories_change:+.0f} ккал к предыдущему периоду")
            
            if comparison_lines:
                comparison_summary = "\n\nСравнение с предыдущим периодом:\n" + "\n".join(comparison_lines)
    
    # 🔹 Собираем summary для Gemini
    date_range_str = f"{start_date.strftime('%d.%m.%Y')} - {end_date.strftime('%d.%m.%Y')}"
    summary = f"""
Период: {period_name} ({date_range_str}), всего дней: {days_count}.

Тренировки за период:
{workout_summary}
Всего ориентировочно израсходовано: ~{total_workout_calories:.0f} ккал.{weekday_stats}

Питание (КБЖУ) за период:
{meals_summary}

Норма / цель КБЖУ:
{kbju_goal_summary}{water_summary}{supplement_summary}{procedure_summary}{wellbeing_summary}

Вес:
{weight_summary}{comparison_summary}
"""
    
    # 🔹 Промпт для бота-ассистента
    prompt = f"""
Ты — бот-ассистент 🤖, персональный фитнес-помощник пользователя.
Говори дружелюбно, уверенно и по делу.

Очень важно:
- Не считай количество записей тренировок, я уже дал тебе готовый текст по объёму и видам упражнений.
- Цель по КБЖУ уже указана в данных, не используй формулировки вроде "если твоя цель...".
- История веса может включать несколько измерений — используй её для оценки тенденции, не говори, что измерение одно, если в данных есть история.
- Используй HTML-теги <b>текст</b> для выделения важных цифр и фактов жирным шрифтом.
- Обрати внимание на проценты выполнения целей КБЖУ — выдели их жирным и дай оценку.
- Если есть сравнение с предыдущим периодом, обязательно упомяни это в анализе.
- Если есть статистика по дням недели, используй её для выявления паттернов активности.
- Если период анализа = 1 день, не используй формулировки про проценты тренировочных дней и «за период». Пиши выводы только про текущий день.

Всегда начинай анализ с приветствия:
"Привет! Я на связи и уже подготовил твой отчёт {period_name.lower()}👇"

Данные пользователя за период:
{summary}

Структурированный input для блока "Тренировки" (JSON, используй как главный источник для этого блока):
{json.dumps(workout_ai_input, ensure_ascii=False, indent=2)}

Сделай краткий отчёт по 4 блокам. ОБЯЗАТЕЛЬНО используй следующий формат для заголовков блоков (без решеток #, только жирный текст с эмодзи):
<b>1) 🏋️ Тренировки</b>
<b>2) 🍱 Питание (КБЖУ)</b>
<b>3) ⚖️ Вес</b>
<b>4) 📈 Общий прогресс и мотивация</b>

Для блока <b>1) 🏋️ Тренировки</b> отвечай строго по шаблону (5-7 строк, без лишнего текста):
• Тип дня: <силовая/кардио/смешанный/активность без тренировки>
• Нагрузка: <низкая/средняя/высокая> (<почему 3-6 слов>)
• Ключевое: <1 строка про главное достижение>
• Энергия: ~<ккал> ккал (оценка)
• Совет на завтра: <1 конкретное действие>

Правила для блока тренировок:
- Не используй общие фразы типа «отличная работа» чаще 1 раза.
- Не повторяй все числа списком, выбери 2-3 ключевых.
- Всегда делай: тип дня → вывод → 1 рекомендация.
- Если history = null / неполная, не придумывай сравнения.
- Эвристики:
  - Если есть >=2 силовых упражнения (pushups/squats/abs/pullups) → тип дня «силовая» или «смешанный» (если шагов много).
  - Шаги: <5000 = низко, 5000-9999 = средне, >=10000 = высоко.
  - Нагрузка:
    - силовые + шаги >=8000 → «средняя»
    - маленький силовой объём и шаги <8000 → «низкая»
    - большой силовой объём или есть кардио → «высокая»
  - Совет на завтра:
    - при низкой нагрузке: «добавь 1 подход / +2000 шагов»
    - при высокой: «восстановление: прогулка/растяжка/сон»
- Обязательно учитывай оценку сожжённых калорий на тренировках в поле "Энергия" и выводах по нагрузке.

Пиши структурированно, но компактно. Используй <b>жирный шрифт</b> для выделения важных цифр, фактов и процентов выполнения целей.
Учитывай блок самочувствия и отражай его выводы в "Общий прогресс и мотивация" (или там, где это уместно).
В блоке "Общий прогресс и мотивация" дай конкретные рекомендации на основе данных: что улучшить, что работает хорошо, на что обратить внимание.

Рекомендации делай в стиле кнопки "🔥 Философия Sumday77", учитывай её принципы, но не вставляй текст или списки из неё дословно.
"""
    
    result = gemini_service.analyze(prompt)
    
    # Заменяем markdown звездочки на HTML-теги для жирного шрифта
    # Заменяем **текст** на <b>текст</b>
    result = re.sub(r'\*\*([^*]+)\*\*', r'<b>\1</b>', result)
    # Заменяем оставшиеся одиночные звездочки в конце (если есть)
    result = re.sub(r'\*+$', '', result)
    
    return result


@router.message(lambda m: m.text in {"📊 ИИ анализ", "📊 ИИ анализ деятельности", "🤖 ИИ анализ деятельности"})
async def analyze_activity(message: Message):
    """Показывает меню анализа деятельности."""
    user_id = str(message.from_user.id)
    logger.info(f"User {user_id} opened activity analysis")
    push_menu_stack(message.bot, activity_analysis_menu)
    await message.answer(
        "📊 <b>ИИ анализ</b>\n\nВыбери период для анализа:",
        parse_mode="HTML",
        reply_markup=activity_analysis_menu,
    )


@router.message(lambda m: m.text == "🗓 Календарь")
async def show_activity_analysis_calendar(message: Message, state: FSMContext):
    """Открывает календарь сохранённых анализов деятельности."""
    await state.clear()
    user_id = str(message.from_user.id)
    await show_activity_analysis_calendar_view(message, user_id)


async def show_activity_analysis_calendar_view(
    message: Message,
    user_id: str,
    year: int | None = None,
    month: int | None = None,
):
    """Показывает календарь ИИ-анализов деятельности."""
    today = date.today()
    year = year or today.year
    month = month or today.month
    keyboard = build_activity_analysis_calendar_keyboard(user_id, year, month)
    try:
        await message.edit_text(
            "🗓 Календарь ИИ-анализа",
            reply_markup=keyboard,
        )
    except Exception:
        await message.answer(
            "🗓 Календарь ИИ-анализа",
            reply_markup=keyboard,
        )


@router.callback_query(lambda c: c.data.startswith("act_cal_nav:"))
async def navigate_activity_analysis_calendar(callback: CallbackQuery):
    """Навигация по календарю анализов."""
    await callback.answer()
    year, month = map(int, callback.data.split(":")[1].split("-"))
    user_id = str(callback.from_user.id)
    await show_activity_analysis_calendar_view(callback.message, user_id, year, month)


@router.callback_query(lambda c: c.data.startswith("act_cal_back:"))
async def back_to_activity_analysis_calendar(callback: CallbackQuery):
    """Возврат к календарю анализов."""
    await callback.answer()
    year, month = map(int, callback.data.split(":")[1].split("-"))
    user_id = str(callback.from_user.id)
    await show_activity_analysis_calendar_view(callback.message, user_id, year, month)


@router.callback_query(lambda c: c.data.startswith("act_cal_day:"))
async def select_activity_analysis_day(callback: CallbackQuery):
    """Открывает выбранный день в календаре анализов."""
    await callback.answer()
    target_date = date.fromisoformat(callback.data.split(":")[1])
    user_id = str(callback.from_user.id)
    await show_activity_analysis_day(callback.message, user_id, target_date)


@router.callback_query(lambda c: c.data.startswith("act_cal_add:"))
async def add_activity_analysis_from_calendar(callback: CallbackQuery, state: FSMContext):
    """Генерирует ИИ-анализ за выбранный день и сохраняет его в календарь."""
    await callback.answer()
    target_date = date.fromisoformat(callback.data.split(":")[1])
    await state.clear()

    await callback.message.answer(
        f"⏳ Подожди немного, бот анализирует день {target_date.strftime('%d.%m.%Y')}...",
        reply_markup=activity_analysis_menu,
    )

    user_id = str(callback.from_user.id)
    analysis = await generate_activity_analysis(user_id, target_date, target_date, "за день")
    ActivityAnalysisRepository.create_entry(user_id, analysis, target_date, source="generated")

    await callback.message.answer("✅ Анализ сохранён в календаре.")
    await show_activity_analysis_day(callback.message, user_id, target_date)


@router.callback_query(lambda c: c.data.startswith("act_cal_del:"))
async def delete_activity_analysis(callback: CallbackQuery):
    """Удаляет сохранённый анализ из календаря."""
    await callback.answer()
    parts = callback.data.split(":")
    target_date = date.fromisoformat(parts[1])
    entry_id = int(parts[2])
    user_id = str(callback.from_user.id)

    success = ActivityAnalysisRepository.delete_entry(entry_id, user_id)
    if success:
        await callback.message.answer("✅ Анализ удалён")
    else:
        await callback.message.answer("❌ Не удалось удалить анализ")
    await show_activity_analysis_day(callback.message, user_id, target_date)


async def show_activity_analysis_day(message: Message, user_id: str, target_date: date):
    """Показывает сохранённые анализы за конкретный день."""
    entries = ActivityAnalysisRepository.get_entries_for_date(user_id, target_date)

    if not entries:
        text = f"📅 {target_date.strftime('%d.%m.%Y')}\n\nЗа этот день анализов нет."
        keyboard = build_activity_analysis_day_actions_keyboard([], target_date)
        try:
            await message.edit_text(text, reply_markup=keyboard)
        except Exception:
            await message.answer(text, reply_markup=keyboard)
        return

    lines = [f"📅 {target_date.strftime('%d.%m.%Y')}\n\nСохранённые анализы:"]
    for idx, entry in enumerate(entries, start=1):
        source = "🤖 ИИ" if entry.source == "generated" else "📝 Ручной"
        short_summary = _extract_analysis_short_summary(entry.analysis_text)
        preview = html.escape(short_summary)
        lines.append(f"{idx}. {source}\nПодитог: {preview}")

    text = "\n\n".join(lines)
    keyboard = build_activity_analysis_day_actions_keyboard(entries, target_date)
    try:
        await message.edit_text(text, reply_markup=keyboard)
    except Exception:
        await message.answer(text, reply_markup=keyboard)


@router.message(ActivityAnalysisStates.entering_manual_analysis)
async def save_manual_activity_analysis(message: Message, state: FSMContext):
    """Сохраняет ручной анализ, введённый для выбранного дня."""
    text = (message.text or "").strip()
    if not text:
        await message.answer("❌ Текст пустой. Введи анализ текстом.")
        return

    data = await state.get_data()
    entry_date_raw = data.get("entry_date")
    if not entry_date_raw:
        await state.clear()
        await message.answer("❌ Не удалось определить дату. Открой календарь анализов заново.")
        return

    entry_date = date.fromisoformat(entry_date_raw)
    user_id = str(message.from_user.id)
    ActivityAnalysisRepository.create_entry(user_id, text, entry_date, source="manual")
    await state.clear()
    await message.answer("✅ Анализ сохранён в календаре.")
    await show_activity_analysis_day(message, user_id, entry_date)


@router.message(lambda m: m.text in {"🔍 Проанализировать день", "📅 Анализ за день", "Проанализировать день"})
async def analyze_activity_day(message: Message):
    """Анализ за день."""
    user_id = str(message.from_user.id)
    today = date.today()
    await message.answer("⏳ Подожди немного, бот анализирует твой день...")
    analysis = await generate_activity_analysis(user_id, today, today, "за день")
    ActivityAnalysisRepository.create_entry(user_id, analysis, today, source="generated")
    push_menu_stack(message.bot, activity_analysis_menu)
    await message.answer(analysis, parse_mode="HTML", reply_markup=activity_analysis_menu)


@router.message(lambda m: m.text in {"🔍 Проанализировать\nнеделю", "🔍 Проанализировать неделю", "📆 Анализ за неделю", "проанализировать неделю"})
async def analyze_activity_week(message: Message):
    """Анализ за неделю."""
    user_id = str(message.from_user.id)
    today = date.today()
    week_start = today - timedelta(days=today.weekday())
    analysis = await generate_activity_analysis(user_id, week_start, today, "за неделю")
    push_menu_stack(message.bot, activity_analysis_menu)
    await message.answer(analysis, parse_mode="HTML", reply_markup=activity_analysis_menu)


@router.message(lambda m: m.text in {"🔍 Проанализировать\nмесяц", "🔍 Проанализировать месяц", "📊 Анализ за месяц", "проанализировать месяц"})
async def analyze_activity_month(message: Message):
    """Анализ за месяц."""
    user_id = str(message.from_user.id)
    today = date.today()
    month_start = date(today.year, today.month, 1)
    analysis = await generate_activity_analysis(user_id, month_start, today, "за месяц")
    push_menu_stack(message.bot, activity_analysis_menu)
    await message.answer(analysis, parse_mode="HTML", reply_markup=activity_analysis_menu)

def register_activity_handlers(dp):
    """Регистрирует обработчики анализа деятельности."""
    dp.include_router(router)
