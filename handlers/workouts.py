"""Обработчики для тренировок."""
import logging
from datetime import date, timedelta, datetime
from typing import Optional
from aiogram import Router, F
from aiogram.filters import StateFilter
from aiogram.types import Message, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.fsm.context import FSMContext
from utils.keyboards import (
    MAIN_MENU_BUTTON_ALIASES,
    TRAINING_BUTTON_TEXT,
    LEGACY_TRAINING_BUTTON_TEXT,
    training_menu,
    activity_category_menu,
    search_back_menu,
    ACTIVITY_CATEGORIES,
    steps_menu,
    steps_confirmation_menu,
    duration_menu,
    plank_duration_menu,
    count_menu,
    push_menu_stack,
    add_another_set_menu,
    add_another_exercise_menu,
    grip_type_menu,
)
from states.user_states import WorkoutStates
from database.repositories import WorkoutRepository, AnalyticsRepository
from database.repositories import CustomWorkoutExerciseRepository, MealRepository
from utils.workout_utils import calculate_workout_calories
from utils.validators import parse_date
from utils.formatters import format_count_with_unit
from utils.calendar_utils import build_workout_calendar_keyboard, show_calendar_back_button
from utils.workout_formatters import build_day_actions_keyboard

logger = logging.getLogger(__name__)

router = Router()

REPS_EXERCISES = {"Отжимания", "Подтягивания", "Приседания", "Пресс", "Берпи"}
DURATION_EXERCISES = {
    "Планка",
    "Йога",
    "Бег",
    "Силовая тренировка",
    "Скакалка",
    "Велосипед",
    "Пробежка",
    "🏄 Сапбординг",
}
STEPS_EXERCISES = {"Шаги", "Ходьба", "Шаги (Ходьба)"}
SUP_BOARDING_EXERCISE = "🏄 Сапбординг"
EXERCISE_SEARCH_SYNONYMS = {
    SUP_BOARDING_EXERCISE: (
        "сап",
        "sup",
        "сапбординг",
        "сапсерфинг",
        "гребля на сапе",
        "катание на сапе",
    ),
}


def _normalize_exercise_name(exercise: str) -> str:
    aliases = {
        "Пробежка": "Бег",
        "Шаги (Ходьба)": "Шаги",
        "Ходьба": "Шаги",
    }
    return aliases.get(exercise, exercise)


def _exercise_search_tokens(exercise: str) -> tuple[str, ...]:
    """Возвращает название активности и синонимы для поиска."""
    normalized = _normalize_exercise_name(exercise)
    return (normalized, *EXERCISE_SEARCH_SYNONYMS.get(normalized, ()))


def _exercise_input_type(exercise: str) -> str:
    normalized = _normalize_exercise_name(exercise)
    if normalized in STEPS_EXERCISES:
        return "steps"
    if normalized in DURATION_EXERCISES:
        return "duration"
    return "reps"


def _format_minutes(minutes: float) -> str:
    if float(minutes).is_integer():
        return str(int(minutes))
    return f"{minutes:.1f}".replace(".", ",")


def _entry_date_from_state_data(data: dict) -> date:
    """Возвращает дату тренировки из FSM или сегодняшнюю дату при некорректном значении."""
    entry_date_value = data.get("entry_date")
    if isinstance(entry_date_value, str):
        try:
            return date.fromisoformat(entry_date_value)
        except ValueError:
            parsed = parse_date(entry_date_value)
            if isinstance(parsed, datetime):
                return parsed.date()
    if isinstance(entry_date_value, date):
        return entry_date_value
    return date.today()


def _format_activity_count(workout) -> str:
    """Форматирует введённое значение для строки упражнения."""
    variant = workout.variant or ""
    variant_lower = variant.lower()
    count = workout.count or 0

    if variant_lower in {"минуты", "мин"}:
        return f"{int(count)} мин"

    formatted_count = str(int(count)) if float(count).is_integer() else f"{count:g}"
    if variant and variant not in {"reps", "Повторения"}:
        return f"{variant}: {formatted_count}"
    return f"{formatted_count} повторений"


def _format_today_activity_overview(user_id: str) -> str:
    """Форматирует сводку главного экрана активности за сегодня с деталями упражнений."""
    workouts = WorkoutRepository.get_workouts_for_day(user_id, date.today())
    steps = 0
    steps_kcal = 0.0
    workouts_count = 0
    workouts_kcal = 0.0
    workout_details = []

    for workout in workouts:
        exercise = _normalize_exercise_name(workout.exercise)
        calories = workout.calories or calculate_workout_calories(
            user_id, workout.exercise, workout.variant, workout.count
        )
        if exercise == "Шаги" or "шаг" in (workout.variant or "").lower():
            steps += int(workout.count or 0)
            steps_kcal += calories
            continue
        workouts_count += 1
        workouts_kcal += calories
        workout_details.append(f"• {exercise}: {_format_activity_count(workout)} (~{calories:.0f} ккал)")

    total_kcal = steps_kcal + workouts_kcal
    settings = MealRepository.get_kbju_settings(user_id)
    activity_key = (settings.activity or "").strip().lower() if settings else ""
    from utils.progress_formatters import LIFESTYLE_ACTIVITY_COEFFICIENTS

    lifestyle_coef = LIFESTYLE_ACTIVITY_COEFFICIENTS.get(
        activity_key,
        LIFESTYLE_ACTIVITY_COEFFICIENTS["medium"],
    )
    counted_kcal = round(total_kcal * lifestyle_coef) if settings else 0

    steps_text = f"{steps:,}".replace(",", " ")
    lines = [
        "🏃 Активность за день",
        "",
        f"👣 Шаги: {steps_text} (~{steps_kcal:.0f} ккал)",
        f"💪 Тренировки: {workouts_count} записей (~{workouts_kcal:.0f} ккал)",
    ]
    lines.extend(workout_details)
    lines.extend([
        "",
        f"🔥 Всего сожжено: ~{total_kcal:.0f} ккал",
        "",
        f"📌 Учтено в дневной норме: ~{counted_kcal:.0f} ккал",
        "",
        "ℹ️ Почему учтено не всё?",
        "Чтобы не завышать дневную норму питания, Sumday77 учитывает только часть активности.",
    ])
    return "\n".join(lines)


async def _send_activity_main_screen(message: Message, user_id: str):
    """Отправляет главный экран раздела активности."""
    workouts_text = _format_today_activity_overview(user_id)
    push_menu_stack(message.bot, training_menu)
    await message.answer(
        f"{workouts_text}\n\nВыберите действие:",
        reply_markup=training_menu,
        parse_mode="HTML",
    )



def _activity_id(exercise: str) -> str:
    normalized = _normalize_exercise_name(exercise)
    for index, catalog_exercise in enumerate(_all_catalog_exercises()):
        if _search_key(catalog_exercise) == _search_key(normalized):
            return f"ex{index}"
    return ""


def _activity_by_id(activity_id: str) -> str | None:
    if not activity_id.startswith("ex"):
        return None
    try:
        index = int(activity_id[2:])
    except ValueError:
        return None
    exercises = _all_catalog_exercises()
    if 0 <= index < len(exercises):
        return exercises[index]
    return None


def _all_catalog_exercises() -> list[str]:
    seen: set[str] = set()
    exercises: list[str] = []
    for category in ACTIVITY_CATEGORIES.values():
        for exercise in category["activities"]:
            name = _normalize_exercise_name(exercise).strip()
            if not name or name in STEPS_EXERCISES or name.casefold() in seen:
                continue
            seen.add(name.casefold())
            exercises.append(name)
    return exercises


def _search_key(text: str) -> str:
    return (text or "").casefold().replace("ё", "е")


def _paginate(items: list[str], page: int, per_page: int = 8) -> tuple[list[str], int]:
    max_page = max((len(items) - 1) // per_page, 0)
    page = min(max(page, 0), max_page)
    return items[page * per_page : (page + 1) * per_page], page


def _get_recent_exercises(user_id: str, limit: int | None = None) -> list[str]:
    """Возвращает уникальные недавно добавленные активности пользователя без шагов."""
    workouts = WorkoutRepository.get_workouts_for_period(
        user_id,
        date.today() - timedelta(days=365),
        date.today(),
    )
    catalog = {_search_key(ex) for ex in _all_catalog_exercises()}
    recent: list[str] = []
    seen: set[str] = set()
    for workout in reversed(workouts):
        name = _normalize_exercise_name(workout.exercise).strip()
        key = _search_key(name)
        if not name or name in STEPS_EXERCISES or "шаг" in (workout.variant or "").casefold() or key in seen:
            continue
        if key not in catalog:
            continue
        seen.add(key)
        recent.append(name)
        if limit is not None and len(recent) >= limit:
            break
    return recent


def _build_activity_inline(exercises: list[str], prefix: str, page: int, has_prev: bool, has_next: bool, *, numbered: bool = False) -> InlineKeyboardMarkup:
    numbers = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣"]
    rows = [[InlineKeyboardButton(text=f"{numbers[i]} {ex}" if numbered and i < len(numbers) else ex, callback_data=f"wrk_pick:{_activity_id(ex)}")] for i, ex in enumerate(exercises)]
    nav = []
    if has_prev:
        nav.append(InlineKeyboardButton(text="⬅️ Предыдущая страница", callback_data=f"{prefix}:{page - 1}"))
    if has_next:
        nav.append(InlineKeyboardButton(text="➡️ Следующая страница", callback_data=f"{prefix}:{page + 1}"))
    if nav:
        rows.append(nav)
    rows.append([InlineKeyboardButton(text="🔍 Поиск упражнения", callback_data="wrk_search")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def _send_add_activity_screen(message: Message, state: FSMContext, user_id: str, page: int = 0) -> None:
    recent = _get_recent_exercises(user_id)
    page_items, page = _paginate(recent, page)
    await state.update_data(add_activity_screen="main", recent_exercises=recent, recent_page=page)
    if page_items:
        numbers = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣"]
        text = "⭐ Недавние активности:\n\n" + "\n".join(f"{numbers[i]} {ex}" for i, ex in enumerate(page_items))
    else:
        text = "⭐ Недавних активностей пока нет.\n\nНайди упражнение через поиск или выбери категорию ниже."
    await message.answer(
        text,
        reply_markup=_build_activity_inline(page_items, "wrk_recent_page", page, page > 0, (page + 1) * 8 < len(recent), numbered=True),
    )
    push_menu_stack(message.bot, activity_category_menu)
    await message.answer("📂 Или выбери категорию:", reply_markup=activity_category_menu)


async def _show_category(message: Message, state: FSMContext, category_id: str, page: int = 0) -> None:
    category = ACTIVITY_CATEGORIES[category_id]
    exercises = [_normalize_exercise_name(ex) for ex in category["activities"] if _normalize_exercise_name(ex) not in STEPS_EXERCISES]
    page_items, page = _paginate(exercises, page)
    await state.update_data(add_activity_screen="category", category_id=category_id, category_page=page)
    await message.answer(
        f"{category['title']}\n\nВыбери упражнение:",
        reply_markup=_build_activity_inline(page_items, f"wrk_cat_page:{category_id}", page, page > 0, (page + 1) * 8 < len(exercises)),
    )


async def _show_search_results(message: Message, state: FSMContext, query: str, page: int = 0) -> None:
    matches = [
        ex for ex in sorted(_all_catalog_exercises())
        if query and any(_search_key(query) in _search_key(token) for token in _exercise_search_tokens(ex))
    ]
    page_items, page = _paginate(matches, page)
    await state.update_data(add_activity_screen="search", search_query=query, search_results=matches, search_page=page)
    if not matches:
        await message.answer(f"По запросу «{query}» ничего не найдено.\n\nПопробуй изменить запрос.", reply_markup=search_back_menu)
        await state.set_state(WorkoutStates.searching_exercise)
        return
    await state.set_state(WorkoutStates.choosing_exercise)
    await message.answer(
        f"🔍 Результаты поиска по запросу «{query}»:",
        reply_markup=_build_activity_inline(page_items, "wrk_search_page", page, page > 0, (page + 1) * 8 < len(matches)),
    )

def reset_user_state(message: Message, *, keep_supplements: bool = False):
    """Сбрасывает состояние пользователя."""
    # TODO: Заменить на FSM clear
    pass


@router.message(StateFilter(None), lambda m: m.text in {TRAINING_BUTTON_TEXT, LEGACY_TRAINING_BUTTON_TEXT})
async def show_training_menu(message: Message, state: FSMContext):
    """Показывает меню тренировок."""
    user_id = str(message.from_user.id)
    logger.info(f"User {user_id} opened training menu")
    AnalyticsRepository.track_event(user_id, "open_activity", section="activity")
    await state.clear()  # Очищаем FSM состояние
    
    await _send_activity_main_screen(message, user_id)


@router.message(lambda m: m.text == "🏋️ Сегодня тренировка")
async def quick_today_workout(message: Message, state: FSMContext):
    """Быстрый вход к списку тренировок за сегодня."""
    user_id = str(message.from_user.id)
    logger.info(f"User {user_id} used quick 'today workout' button")
    
    # Очищаем предыдущее состояние и сразу открываем меню тренировок
    await state.clear()
    await show_day_workouts(message, user_id, date.today())
    push_menu_stack(message.bot, training_menu)
    await message.answer("⬇️ Меню тренировок", reply_markup=training_menu)


@router.message(lambda m: m.text == "👣 Шаги")
async def open_steps_flow(message: Message, state: FSMContext):
    """Быстрый сценарий добавления шагов."""
    await state.update_data(
        entry_date=date.today().isoformat(),
        exercise="Шаги",
        variant="Количество шагов",
        back_target="training_menu",
    )
    await state.set_state(WorkoutStates.entering_steps)
    push_menu_stack(message.bot, steps_menu)
    await message.answer("Введи количество шагов за сегодня:", reply_markup=steps_menu)


@router.callback_query(lambda c: c.data == "quick_today_workout")
async def quick_today_workout_cb(callback: CallbackQuery, state: FSMContext):
    """Быстрый вход к списку тренировок за сегодня по inline-кнопке."""
    await callback.answer()
    message = callback.message
    user_id = str(callback.from_user.id)
    logger.info(f"User {user_id} used quick 'today workout' inline button")
    
    await state.clear()
    await show_day_workouts(message, user_id, date.today())
    push_menu_stack(message.bot, training_menu)
    await message.answer("⬇️ Меню тренировок", reply_markup=training_menu)


async def start_exercise_selection(
    message: Message,
    state: FSMContext,
    target_date: date | None = None,
):
    """Открывает новый каталог добавления активности."""
    entry_date = target_date or date.today()
    await state.update_data(entry_date=entry_date.isoformat())
    await state.set_state(WorkoutStates.choosing_exercise)
    if entry_date != date.today():
        await message.answer(f"📅 Дата: {entry_date.strftime('%d.%m.%Y')}")
    await _send_add_activity_screen(message, state, str(message.from_user.id))


@router.message(lambda m: m.text == "➕ Добавить активность")
async def add_activity_entry(message: Message, state: FSMContext):
    """Открывает единое меню добавления шагов и упражнений."""
    user_id = str(message.from_user.id)
    logger.info(f"User {user_id} opened add activity menu")

    await state.clear()
    await start_exercise_selection(message, state, date.today())


@router.message(lambda m: m.text == "💪 Тренировка")
async def add_training_entry(message: Message, state: FSMContext):
    """Поддерживает устаревшую кнопку тренировки через меню выбора активности."""
    user_id = str(message.from_user.id)
    logger.info(f"User {user_id} opened legacy workout activity menu")

    await state.clear()
    await start_exercise_selection(message, state, date.today())


@router.message(lambda m: m.text == "➕ Добавить другое упражнение")
async def add_another_exercise(message: Message, state: FSMContext):
    """Позволяет быстро добавить следующее упражнение в той же тренировочной дате."""
    data = await state.get_data()
    await start_exercise_selection(message, state, _entry_date_from_state_data(data))


@router.message(StateFilter(None), lambda m: m.text == "✅ Завершить упражнение")
async def finish_exercise_without_active_state(message: Message, state: FSMContext):
    """Завершает ввод упражнения, когда пользователь уже вне FSM-сценария."""
    user_id = str(message.from_user.id)
    await state.clear()

    from utils.progress_formatters import format_today_workouts_block

    workouts_text = format_today_workouts_block(user_id, include_date=False, include_exercise_details=True)
    push_menu_stack(message.bot, training_menu)
    await message.answer(
        f"✅ Тренировка завершена!\n\n{workouts_text}\n\nВыбери действие:",
        reply_markup=training_menu,
        parse_mode="HTML",
    )


@router.message(lambda m: m.text == "📅 Календарь активности")
async def show_training_calendar(message: Message):
    """Показывает календарь тренировок."""
    user_id = str(message.from_user.id)
    logger.info(f"User {user_id} opened training calendar")
    await show_calendar_back_button(message)
    await show_workout_calendar(message, user_id)


async def show_workout_calendar(message: Message, user_id: str, year: Optional[int] = None, month: Optional[int] = None):
    """Показывает календарь тренировок."""
    today = date.today()
    year = year or today.year
    month = month or today.month
    keyboard = build_workout_calendar_keyboard(user_id, year, month)
    await message.answer(
        "📆 Выбери день, чтобы посмотреть, изменить или удалить тренировку:",
        reply_markup=keyboard,
    )


async def show_day_workouts(
    message: Message,
    user_id: str,
    target_date: date,
    *,
    include_calendar_back: bool = True,
):
    """Показывает тренировки за день."""
    workouts = WorkoutRepository.get_workouts_for_day(user_id, target_date)
    
    if not workouts:
        await message.answer(
            f"{target_date.strftime('%d.%m.%Y')}: нет тренировок.",
            reply_markup=build_day_actions_keyboard(
                [],
                target_date,
                include_calendar_back=include_calendar_back,
            ),
        )
        return
    
    text = [f"📅 {target_date.strftime('%d.%m.%Y')} — активность:"]
    total_calories = 0.0
    aggregates: dict[str, dict[str, float | str]] = {}

    for w in workouts:
        clean_exercise = _normalize_exercise_name(w.exercise)
        entry_calories = w.calories or calculate_workout_calories(user_id, w.exercise, w.variant, w.count)
        total_calories += entry_calories
        key = clean_exercise
        row = aggregates.setdefault(key, {"count": 0, "calories": 0.0, "variant": w.variant or "reps"})
        row["count"] += w.count
        row["calories"] += entry_calories
        if (w.variant or "").lower() in {"минуты", "мин"}:
            row["variant"] = "мин"
        elif "шаг" in (w.variant or "").lower() or clean_exercise == "Шаги":
            row["variant"] = "steps"

    for exercise, data in aggregates.items():
        variant = data["variant"]
        if variant == "steps":
            formatted_count = f"{int(data['count']):,}".replace(",", " ")
        elif variant == "мин":
            formatted_count = f"{int(data['count'])} мин"
        else:
            formatted_count = f"{int(data['count'])} повторений"
        text.append(f"• {exercise}: {formatted_count} (~{data['calories']:.0f} ккал)")
    
    text.append(f"\n🔥 Итого за день: ~{total_calories:.0f} ккал")
    
    await message.answer(
        "\n".join(text),
        reply_markup=build_day_actions_keyboard(
            workouts,
            target_date,
            include_calendar_back=include_calendar_back,
        ),
    )


async def _continue_with_selected_exercise(message: Message, state: FSMContext, exercise: str) -> None:
    """Продолжает сценарий ввода после выбора активности из меню или недавних."""
    exercise = _normalize_exercise_name(exercise)
    await state.update_data(exercise=exercise, category="bodyweight")

    if exercise == "Другое":
        await state.set_state(WorkoutStates.entering_custom_exercise)
        await message.answer(
            "🆕 Создай своё упражнение: напиши название, и я сохраню его в список для будущих тренировок."
        )
        return

    if exercise == "Подтягивания":
        await state.set_state(WorkoutStates.choosing_grip_type)
        push_menu_stack(message.bot, grip_type_menu)
        await message.answer("Каким хватом выполнял подтягивания?", reply_markup=grip_type_menu)
        return

    input_type = _exercise_input_type(exercise)
    if input_type == "steps":
        await state.update_data(back_target="exercise_picker")
        await state.set_state(WorkoutStates.entering_steps)
        push_menu_stack(message.bot, steps_menu)
        await message.answer("Введи количество шагов за сегодня:", reply_markup=steps_menu)
        return
    if input_type == "duration":
        await state.update_data(variant="Минуты", back_target="exercise_picker")
        await state.set_state(WorkoutStates.entering_duration)
        selected_duration_menu = plank_duration_menu if exercise == "Планка" else duration_menu
        push_menu_stack(message.bot, selected_duration_menu)
        await message.answer(
            f"Введи длительность для {exercise} в минутах или выбери предложенное время:",
            reply_markup=selected_duration_menu,
        )
        return

    await state.update_data(variant="reps", back_target="exercise_picker")
    await state.set_state(WorkoutStates.entering_count)
    push_menu_stack(message.bot, count_menu)
    await message.answer("Выбери количество повторений:", reply_markup=count_menu)


@router.callback_query(lambda c: c.data and c.data.startswith("wrk_pick:"))
async def pick_catalog_exercise(callback: CallbackQuery, state: FSMContext):
    """Выбирает упражнение по стабильному callback id."""
    await callback.answer()
    activity_id = callback.data.split(":", maxsplit=1)[1]
    exercise = _activity_by_id(activity_id)
    if not exercise:
        await callback.message.answer("❌ Не удалось выбрать активность. Попробуй открыть добавление заново.")
        return
    await _continue_with_selected_exercise(callback.message, state, exercise)


@router.callback_query(lambda c: c.data and c.data.startswith("wrk_recent_page:"))
async def paginate_recent_exercises(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    page = int(callback.data.rsplit(":", maxsplit=1)[1])
    await _send_add_activity_screen(callback.message, state, str(callback.from_user.id), page)


@router.callback_query(lambda c: c.data and c.data.startswith("wrk_cat_page:"))
async def paginate_category_exercises(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    _, _, category_id, page_text = callback.data.split(":")
    await _show_category(callback.message, state, category_id, int(page_text))


@router.callback_query(lambda c: c.data and c.data.startswith("wrk_search_page:"))
async def paginate_search_exercises(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    data = await state.get_data()
    page = int(callback.data.rsplit(":", maxsplit=1)[1])
    await _show_search_results(callback.message, state, data.get("search_query", ""), page)


@router.callback_query(lambda c: c.data == "wrk_search")
async def start_exercise_search(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.set_state(WorkoutStates.searching_exercise)
    push_menu_stack(callback.message.bot, search_back_menu)
    await callback.message.answer("🔍 Введи название упражнения или его часть:", reply_markup=search_back_menu)


@router.callback_query(lambda c: c.data.startswith("cal_nav:"))
async def navigate_calendar(callback: CallbackQuery):
    """Навигация по календарю тренировок."""
    await callback.answer()
    parts = callback.data.split(":")
    year, month = map(int, parts[1].split("-"))
    user_id = str(callback.from_user.id)
    await show_workout_calendar(callback.message, user_id, year, month)


@router.callback_query(lambda c: c.data.startswith("cal_back:"))
async def back_to_calendar(callback: CallbackQuery):
    """Возврат к календарю тренировок."""
    await callback.answer()
    parts = callback.data.split(":")
    year, month = map(int, parts[1].split("-"))
    user_id = str(callback.from_user.id)
    await show_workout_calendar(callback.message, user_id, year, month)


@router.callback_query(lambda c: c.data.startswith("cal_day:"))
async def select_calendar_day(callback: CallbackQuery):
    """Выбор дня в календаре тренировок."""
    await callback.answer()
    parts = callback.data.split(":")
    target_date = date.fromisoformat(parts[1])
    user_id = str(callback.from_user.id)
    await show_day_workouts(callback.message, user_id, target_date)


@router.callback_query(lambda c: c.data.startswith("wrk_add:"))
async def add_workout_from_calendar(callback: CallbackQuery, state: FSMContext):
    """Добавляет тренировку из календаря."""
    await callback.answer()
    parts = callback.data.split(":")
    target_date = date.fromisoformat(parts[1])
    
    await start_exercise_selection(callback.message, state, target_date)


@router.callback_query(lambda c: c.data.startswith("wrk_edit:"))
async def edit_workout(callback: CallbackQuery, state: FSMContext):
    """Начинает редактирование тренировки."""
    await callback.answer()
    parts = callback.data.split(":")
    workout_id = int(parts[1])
    target_date = date.fromisoformat(parts[2]) if len(parts) > 2 else date.today()
    user_id = str(callback.from_user.id)
    
    workout = WorkoutRepository.get_workout_by_id(workout_id, user_id)
    if not workout:
        await callback.message.answer("❌ Не нашёл тренировку для изменения.")
        return
    
    await state.update_data(
        workout_id=workout_id,
        workout_exercise=workout.exercise,
        workout_variant=workout.variant,
        workout_date=workout.date.isoformat(),
        target_date=target_date.isoformat(),
    )
    await state.set_state(WorkoutStates.editing_count)
    
    await callback.message.answer(
        f"✏️ Редактирование тренировки\n\n"
        f"💪 {workout.exercise}\n"
        f"📅 {workout.date.strftime('%d.%m.%Y')}\n"
        f"📊 Текущее количество: {workout.count}\n\n"
        f"Введи новое количество:"
    )


@router.message(WorkoutStates.editing_count)
async def handle_workout_edit_count(message: Message, state: FSMContext):
    """Обрабатывает ввод нового количества при редактировании тренировки."""
    user_id = str(message.from_user.id)
    
    try:
        count = int(message.text)
        if count <= 0:
            raise ValueError
    except (ValueError, AttributeError):
        await message.answer("⚠️ Введи положительное число")
        return
    
    data = await state.get_data()
    workout_id = data.get("workout_id")
    exercise = data.get("workout_exercise")
    variant = data.get("workout_variant")
    target_date_str = data.get("target_date", date.today().isoformat())
    
    if not workout_id:
        await message.answer("❌ Ошибка: не найдена тренировка для обновления.")
        await state.clear()
        return
    
    # Пересчитываем калории
    calories = calculate_workout_calories(user_id, exercise, variant, count)
    
    # Обновляем тренировку
    success = WorkoutRepository.update_workout(workout_id, user_id, count, calories)
    
    if success:
        if isinstance(target_date_str, str):
            try:
                target_date = date.fromisoformat(target_date_str)
            except ValueError:
                target_date = date.today()
        else:
            target_date = date.today()
        
        await state.clear()
        formatted_count = format_count_with_unit(count, variant)
        await message.answer(
            f"✅ Обновлено!\n\n"
            f"💪 {exercise}\n"
            f"📊 {formatted_count}\n"
            f"🔥 ~{calories:.0f} ккал"
        )
        await show_day_workouts(message, user_id, target_date)
    else:
        await message.answer("❌ Не удалось обновить тренировку. Попробуйте позже.")
        await state.clear()


@router.callback_query(lambda c: c.data.startswith("wrk_del:"))
async def delete_workout_from_calendar(callback: CallbackQuery):
    """Удаляет тренировку из календаря."""
    await callback.answer()
    parts = callback.data.split(":")
    workout_id = int(parts[1])
    target_date = date.fromisoformat(parts[2]) if len(parts) > 2 else date.today()
    user_id = str(callback.from_user.id)
    
    success = WorkoutRepository.delete_workout(workout_id, user_id)
    if success:
        await callback.message.answer("✅ Тренировка удалена")
        await show_day_workouts(callback.message, user_id, target_date)
    else:
        await callback.message.answer("❌ Не удалось удалить тренировку")


@router.message(WorkoutStates.choosing_exercise)
async def choose_exercise(message: Message, state: FSMContext):
    """Обрабатывает reply-навигацию нового каталога активности."""
    text = message.text or ""
    category_by_title = {data["title"]: key for key, data in ACTIVITY_CATEGORIES.items()}

    if text in MAIN_MENU_BUTTON_ALIASES:
        from handlers.common import go_main_menu
        await go_main_menu(message, state)
        return

    if text in {"⬅️ Назад", "◀️ Назад"}:
        data = await state.get_data()
        if data.get("add_activity_screen") in {"category", "search"}:
            await _send_add_activity_screen(message, state, str(message.from_user.id))
            return
        await state.clear()
        await _send_activity_main_screen(message, str(message.from_user.id))
        return

    if text in category_by_title:
        await _show_category(message, state, category_by_title[text])
        return

    if text == "🔍 Поиск упражнения":
        await state.set_state(WorkoutStates.searching_exercise)
        push_menu_stack(message.bot, search_back_menu)
        await message.answer("🔍 Введи название упражнения или его часть:", reply_markup=search_back_menu)
        return

    if text in _all_catalog_exercises():
        await _continue_with_selected_exercise(message, state, text)
        return

    await message.answer("Выбери категорию ниже или нажми «🔍 Поиск упражнения».", reply_markup=activity_category_menu)


@router.message(WorkoutStates.searching_exercise)
async def search_exercise(message: Message, state: FSMContext):
    """Поиск упражнений по подстроке без учета регистра и ё/е."""
    text = (message.text or "").strip()
    if text in MAIN_MENU_BUTTON_ALIASES:
        from handlers.common import go_main_menu
        await go_main_menu(message, state)
        return
    if text in {"⬅️ Назад", "◀️ Назад"}:
        await state.set_state(WorkoutStates.choosing_exercise)
        await _send_add_activity_screen(message, state, str(message.from_user.id))
        return
    await _show_search_results(message, state, text)


@router.message(WorkoutStates.entering_steps)
async def handle_steps_input(message: Message, state: FSMContext):
    """Обрабатывает ввод шагов."""
    if message.text == "✍️ Ввести вручную":
        await message.answer("Введи количество шагов числом:")
        return
    if message.text in MAIN_MENU_BUTTON_ALIASES:
        from handlers.common import go_main_menu
        await go_main_menu(message, state)
        return
    if message.text == "⬅️ Назад":
        data = await state.get_data()
        if data.get("back_target") == "training_menu":
            await state.clear()
            push_menu_stack(message.bot, training_menu)
            await message.answer("⬇️ Меню тренировок", reply_markup=training_menu)
        else:
            await state.set_state(WorkoutStates.choosing_exercise)
            await _send_add_activity_screen(message, state, str(message.from_user.id))
        return

    try:
        steps = int(message.text)
        if steps < 0:
            raise ValueError
    except (ValueError, TypeError):
        await message.answer("⚠️ Введи корректное число шагов.")
        return

    user_id = str(message.from_user.id)
    calories = calculate_workout_calories(user_id, "Шаги", "Количество шагов", steps)
    await state.update_data(steps=steps, steps_calories=calories)
    await state.set_state(WorkoutStates.confirming_steps)
    push_menu_stack(message.bot, steps_confirmation_menu)
    await message.answer(
        f"👣 Шаги: {steps:,}".replace(",", " ") + f"\n🔥 Примерный расход: ~{calories:.0f} ккал",
        reply_markup=steps_confirmation_menu,
    )


@router.message(WorkoutStates.confirming_steps)
async def confirm_steps(message: Message, state: FSMContext):
    """Подтверждение сохранения шагов."""
    user_id = str(message.from_user.id)
    data = await state.get_data()
    steps = int(data.get("steps", 0))
    calories = float(data.get("steps_calories", 0))
    today = date.today()

    if message.text == "✏️ Изменить":
        await state.set_state(WorkoutStates.entering_steps)
        push_menu_stack(message.bot, steps_menu)
        await message.answer("Введи количество шагов за сегодня:", reply_markup=steps_menu)
        return
    if message.text == "⬅️ Назад":
        await state.set_state(WorkoutStates.entering_steps)
        push_menu_stack(message.bot, steps_menu)
        await message.answer("Введи количество шагов за сегодня:", reply_markup=steps_menu)
        return
    if message.text in MAIN_MENU_BUTTON_ALIASES:
        from handlers.common import go_main_menu
        await go_main_menu(message, state)
        return

    step_entries = [w for w in WorkoutRepository.get_workouts_for_day(user_id, today) if _normalize_exercise_name(w.exercise) == "Шаги"]

    if message.text != "✅ Сохранить":
        await message.answer("Выбери действие кнопкой ниже.")
        return

    if step_entries:
        first = step_entries[0]
        WorkoutRepository.update_workout(first.id, user_id, steps, calories)
        for entry in step_entries[1:]:
            WorkoutRepository.delete_workout(entry.id, user_id)
    else:
        WorkoutRepository.save_workout(
            user_id=user_id,
            exercise="Шаги",
            count=steps,
            entry_date=today,
            variant="Количество шагов",
            calories=calories,
        )
    AnalyticsRepository.track_event(user_id, "add_steps", section="activity")

    await state.clear()
    from utils.progress_formatters import format_today_workouts_block
    workouts_text = format_today_workouts_block(user_id, include_date=False, include_exercise_details=True)
    push_menu_stack(message.bot, training_menu)
    await message.answer(f"✅ Шаги сохранены!\n\n{workouts_text}", reply_markup=training_menu, parse_mode="HTML")


@router.message(WorkoutStates.entering_duration)
async def handle_duration_input(message: Message, state: FSMContext):
    """Обрабатывает ввод длительности упражнения."""
    if message.text == "✍️ Ввести вручную":
        await message.answer("Введи длительность в минутах (например, 1,5):")
        return
    if message.text == "⬅️ Назад":
        await state.set_state(WorkoutStates.choosing_exercise)
        await _send_add_activity_screen(message, state, str(message.from_user.id))
        return
    if message.text in MAIN_MENU_BUTTON_ALIASES:
        from handlers.common import go_main_menu
        await go_main_menu(message, state)
        return

    try:
        minutes = float((message.text or "").replace(",", ".").strip())
        if minutes <= 0:
            raise ValueError
    except (ValueError, TypeError):
        await message.answer("⚠️ Введи положительное число минут (например, 1 или 1,5).")
        return

    data = await state.get_data()
    exercise = data.get("exercise")
    user_id = str(message.from_user.id)
    calories = calculate_workout_calories(user_id, exercise, "Минуты", minutes)
    if exercise == SUP_BOARDING_EXERCISE:
        await state.update_data(duration_minutes=minutes, duration_calories=calories, variant="Минуты")
        await state.set_state(WorkoutStates.confirming_duration)
        push_menu_stack(message.bot, steps_confirmation_menu)
        await message.answer(
            f"🏄 Сапбординг\n"
            f"⏱ {_format_minutes(minutes)} мин\n"
            f"🔥 Примерный расход: ~{calories:.0f} ккал",
            reply_markup=steps_confirmation_menu,
        )
        return

    WorkoutRepository.save_workout(
        user_id=user_id,
        exercise=exercise,
        count=minutes,
        entry_date=_entry_date_from_state_data(data),
        variant="Минуты",
        calories=calories,
    )
    await state.clear()
    await message.answer(
        f"✅ Записал!\n💪 {exercise}\n⏱ {_format_minutes(minutes)} мин\n🔥 ~{calories:.0f}\n📅 сегодня",
        reply_markup=add_another_exercise_menu,
    )


@router.message(WorkoutStates.confirming_duration)
async def confirm_duration_workout(message: Message, state: FSMContext):
    """Подтверждает сохранение активности, введённой по длительности."""
    data = await state.get_data()
    exercise = data.get("exercise")
    minutes = float(data.get("duration_minutes", 0) or 0)
    calories = float(data.get("duration_calories", 0) or 0)
    user_id = str(message.from_user.id)

    if message.text == "✏️ Изменить":
        await state.set_state(WorkoutStates.entering_duration)
        push_menu_stack(message.bot, duration_menu)
        await message.answer(
            f"Введи длительность для {exercise} в минутах или выбери предложенное время:",
            reply_markup=duration_menu,
        )
        return
    if message.text == "⬅️ Назад":
        await state.set_state(WorkoutStates.entering_duration)
        push_menu_stack(message.bot, duration_menu)
        await message.answer(
            f"Введи длительность для {exercise} в минутах или выбери предложенное время:",
            reply_markup=duration_menu,
        )
        return
    if message.text in MAIN_MENU_BUTTON_ALIASES:
        from handlers.common import go_main_menu
        await go_main_menu(message, state)
        return
    if message.text != "✅ Сохранить":
        await message.answer("Выбери действие кнопкой ниже.")
        return
    if not exercise or minutes <= 0:
        await message.answer("❌ Ошибка: данные потеряны. Начни добавление активности заново.")
        await state.clear()
        push_menu_stack(message.bot, training_menu)
        await message.answer("Выбери действие:", reply_markup=training_menu)
        return

    entry_date = _entry_date_from_state_data(data)
    WorkoutRepository.save_workout(
        user_id=user_id,
        exercise=exercise,
        count=minutes,
        entry_date=entry_date,
        variant="Минуты",
        calories=calories,
    )
    AnalyticsRepository.track_event(user_id, "add_workout", section="activity")
    await state.clear()

    date_label = "сегодня" if entry_date == date.today() else entry_date.strftime("%d.%m.%Y")
    await message.answer(
        f"✅ Записал!\n💪 {exercise}\n⏱ {_format_minutes(minutes)} мин\n🔥 ~{calories:.0f} ккал\n📅 {date_label}",
        reply_markup=add_another_exercise_menu,
    )


@router.message(WorkoutStates.choosing_grip_type)
async def choose_grip_type(message: Message, state: FSMContext):
    """Обрабатывает выбор типа хвата для подтягиваний."""
    grip_type = message.text
    
    # Обработка кнопок навигации
    if grip_type == "⬅️ Назад" or grip_type in MAIN_MENU_BUTTON_ALIASES:
        if grip_type == "⬅️ Назад":
            await state.set_state(WorkoutStates.choosing_exercise)
            await _send_add_activity_screen(message, state, str(message.from_user.id))
        else:
            from handlers.common import go_main_menu
            await go_main_menu(message, state)
        return
    
    # Маппинг типов хвата на варианты
    grip_mapping = {
        "Прямой хват": "Прямой хват",
        "Обратный хват": "Обратный хват",
        "Нейтральный хват": "Нейтральный хват",
        "Пропустить": "reps"
    }
    
    if grip_type not in grip_mapping:
        await message.answer("Выбери тип хвата из меню")
        return
    
    variant = grip_mapping[grip_type]
    await state.update_data(variant=variant)
    await state.set_state(WorkoutStates.entering_count)
    push_menu_stack(message.bot, count_menu)
    await message.answer("Выбери количество повторений:", reply_markup=count_menu)


@router.message(WorkoutStates.entering_custom_exercise)
async def handle_custom_exercise(message: Message, state: FSMContext):
    """Обрабатывает ввод названия упражнения."""
    # Обработка кнопок навигации
    if message.text == "⬅️ Назад" or message.text in MAIN_MENU_BUTTON_ALIASES:
        if message.text == "⬅️ Назад":
            await state.set_state(WorkoutStates.choosing_exercise)
            await _send_add_activity_screen(message, state, str(message.from_user.id))
        else:
            from handlers.common import go_main_menu
            await go_main_menu(message, state)
        return
    
    category = "bodyweight"
    
    exercise = (message.text or "").strip()
    if not exercise:
        await message.answer("⚠️ Название упражнения не может быть пустым. Введи текстом.")
        return

    if len(exercise) > 64:
        await message.answer("⚠️ Слишком длинное название. Ограничение — 64 символа.")
        return

    CustomWorkoutExerciseRepository.save_exercise(
        user_id=str(message.from_user.id),
        category=category,
        name=exercise,
    )

    await state.update_data(exercise=exercise)
    
    await state.update_data(variant="reps")
    await state.set_state(WorkoutStates.entering_count)
    push_menu_stack(message.bot, count_menu)
    await message.answer(
        f"✅ Упражнение «{exercise}» сохранено. Теперь введи количество:",
        reply_markup=count_menu,
    )


@router.message(WorkoutStates.entering_count)
async def handle_count_input(message: Message, state: FSMContext):
    """Обрабатывает ввод количества."""
    user_id = str(message.from_user.id)
    
    # Проверяем, не является ли это кнопкой меню
    if message.text == "✏️ Ввести вручную":
        await message.answer("Введи количество повторений числом:")
        return
    
    if message.text in {"❌ Отмена", "⬅️ Назад"}:
        # Отменяем незавершённый ввод подхода без сохранения данных.
        await state.clear()
        push_menu_stack(message.bot, training_menu)
        await message.answer("⬇️ Меню тренировок", reply_markup=training_menu)
        return
    
    if message.text in MAIN_MENU_BUTTON_ALIASES:
        from handlers.common import go_main_menu
        await go_main_menu(message, state)
        return
    
    # Обработка ответа на вопрос "добавить еще подход?"
    if message.text == "💪 Добавить еще подход":
        # Остаемся в том же состоянии, просто просим ввести количество
        # Явно убеждаемся, что состояние установлено правильно и данные сохранены
        data = await state.get_data()
        exercise = data.get("exercise")
        variant = data.get("variant")
        entry_date_str = data.get("entry_date")
        
        if not exercise:
            logger.error(f"User {user_id}: missing exercise or variant when adding another set. Data: {data}")
            await message.answer("❌ Ошибка: данные потеряны. Начни добавление тренировки заново.")
            await state.clear()
            push_menu_stack(message.bot, training_menu)
            await message.answer("Выбери действие:", reply_markup=training_menu)
            return
        
        # Явно сохраняем данные обратно в state (на случай, если они потерялись)
        await state.update_data(
            exercise=exercise,
            variant=variant,
            entry_date=entry_date_str or date.today().isoformat(),
        )
        await state.set_state(WorkoutStates.entering_count)
        
        # Показываем клавиатуру для выбора количества
        push_menu_stack(message.bot, count_menu)
        await message.answer(
            f"Введи количество повторений для {exercise}:",
            reply_markup=count_menu
        )
        return
    
    if message.text == "➕ Добавить другое упражнение":
        data = await state.get_data()
        await start_exercise_selection(message, state, _entry_date_from_state_data(data))
        return

    if message.text == "✅ Завершить упражнение":
        # Завершаем и возвращаемся в меню
        await state.clear()
        from utils.progress_formatters import format_today_workouts_block

        workouts_text = format_today_workouts_block(user_id, include_date=False, include_exercise_details=True)
        push_menu_stack(message.bot, training_menu)
        await message.answer(
            f"✅ Тренировка завершена!\n\n{workouts_text}\n\nВыбери действие:",
            reply_markup=training_menu,
            parse_mode="HTML",
        )
        return
    
    try:
        count = int(message.text)
        if count <= 0:
            raise ValueError
    except (ValueError, AttributeError):
        await message.answer("⚠️ Введи положительное число")
        return
    
    data = await state.get_data()
    exercise = data.get("exercise")
    variant = data.get("variant")
    
    # Проверяем, что данные есть
    if not exercise:
        logger.error(f"User {user_id}: missing exercise or variant in state. Data: {data}")
        await message.answer("❌ Ошибка: данные потеряны. Начни добавление тренировки заново.")
        await state.clear()
        push_menu_stack(message.bot, training_menu)
        await message.answer("Выбери действие:", reply_markup=training_menu)
        return
    
    entry_date = _entry_date_from_state_data(data)
    
    # Рассчитываем калории
    calories = calculate_workout_calories(user_id, exercise, variant, count)
    
    # Сохраняем тренировку
    workout = WorkoutRepository.save_workout(
        user_id=user_id,
        exercise=exercise,
        count=count,
        entry_date=entry_date,
        variant=variant,
        calories=calories,
    )
    
    logger.info(f"User {user_id} saved workout: {exercise} x {count} on {entry_date}")
    AnalyticsRepository.track_event(user_id, "add_workout", section="activity")
    
    # Получаем общее количество для этого упражнения за день
    workouts_today = WorkoutRepository.get_workouts_for_day(user_id, entry_date)
    total_count = sum(w.count for w in workouts_today if w.exercise == exercise and w.variant == variant)
    
    # Формируем ответ
    formatted_count = format_count_with_unit(count, variant)
    total_formatted = format_count_with_unit(total_count, variant)
    
    date_label = "сегодня" if entry_date == date.today() else entry_date.strftime("%d.%m.%Y")
    
    await message.answer(
        f"✅ Записал! 👍\n"
        f"💪 {exercise}\n"
        f"📊 {formatted_count}\n"
        f"🔥 ~{calories:.0f} ккал\n"
        f"📅 {date_label}\n\n"
        f"Всего {exercise} за {date_label}: {total_formatted}\n\n"
        f"Хотите внести еще подход?",
        reply_markup=add_another_set_menu,
    )


@router.message(lambda m: m.text == "✏️ Ввести вручную")
async def enter_manual_count(message: Message, state: FSMContext):
    """Обработчик кнопки 'Ввести вручную'."""
    await state.set_state(WorkoutStates.entering_count)
    await message.answer("Введи количество повторений числом:")


def register_workout_handlers(dp):
    """Регистрирует обработчики тренировок."""
    dp.include_router(router)
