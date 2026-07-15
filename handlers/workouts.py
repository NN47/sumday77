"""Обработчики для тренировок."""
import logging
from datetime import date, timedelta, datetime
from types import SimpleNamespace
from typing import Optional
from aiogram import Router, F
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import StateFilter
from aiogram.types import Message, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove
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
    distance_menu,
    jumps_menu,
    plank_duration_menu,
    count_menu,
    working_weight_menu,
    push_menu_stack,
    add_another_set_menu,
    add_another_exercise_menu,
    grip_type_menu,
)
from utils.pagination import PAGINATION_NOOP_CALLBACK, build_pagination_keyboard, total_pages_for
from states.user_states import WorkoutStates
from database.repositories import WorkoutRepository, AnalyticsRepository, MealRepository
from database.repositories import CustomWorkoutExerciseRepository
from utils.workout_utils import calculate_workout_calories
from utils.workout_equipment import get_equipment_config
from utils.validators import parse_date
from utils.formatters import format_count_with_unit
from utils.calendar_utils import build_workout_calendar_keyboard, show_calendar_back_button
from utils.activity_input_config import ActivityInputMethod, get_activity_config_by_exercise, get_activity_methods, infer_input_method
from utils.workout_formatters import (
    build_day_actions_keyboard,
    format_activity_summary,
    format_activity_daily_summaries,
    format_activity_edit_button,
    is_steps_workout,
)

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
EXERCISE_SEARCH_BUTTON_TEXT = "🔍 Поиск упражнения"
CARDIO_CATEGORY_ID = "cardio"

category_search_reply_menu = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text=EXERCISE_SEARCH_BUTTON_TEXT)],
        [KeyboardButton(text="⬅️ Назад")],
    ],
    resize_keyboard=True,
)

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
    method = infer_input_method(_normalize_exercise_name(exercise))
    if method == ActivityInputMethod.STEPS:
        return "steps"
    if method == ActivityInputMethod.TIME:
        return "duration"
    if method == ActivityInputMethod.DISTANCE:
        return "distance"
    if method == ActivityInputMethod.JUMPS:
        return "jumps"
    return "reps"


def _gym_exercises() -> set[str]:
    """Возвращает упражнения категории «Тренажёрный зал» из централизованного каталога."""
    gym_category = ACTIVITY_CATEGORIES.get("gym", {})
    return {_normalize_exercise_name(ex) for ex in gym_category.get("activities", [])}


def _is_gym_exercise(exercise: str | None) -> bool:
    return bool(exercise) and _normalize_exercise_name(exercise) in _gym_exercises()


def _weight_label(exercise: str | None) -> str:
    return get_equipment_config(_normalize_exercise_name(exercise) if exercise else exercise).weight_label


def _weight_saved_description(exercise: str | None) -> str:
    return get_equipment_config(_normalize_exercise_name(exercise) if exercise else exercise).saved_weight_description


def _format_working_weight(weight: float | int | None) -> str:
    if weight is None or float(weight) <= 0:
        return "без веса"
    number = float(weight)
    return f"{int(number) if number.is_integer() else str(number).replace('.', ',')} кг"


def _parse_working_weight(text: str | None) -> float | None:
    raw = (text or "").strip().casefold()
    if raw == "без веса":
        return None
    raw = raw.replace("кг", "").replace(",", ".").strip()
    weight = float(raw)
    if weight < 0:
        raise ValueError
    return weight


async def _open_working_weight_input(message: Message, state: FSMContext, exercise: str) -> None:
    await state.update_data(exercise=_normalize_exercise_name(exercise), variant="reps", input_method=ActivityInputMethod.REPETITIONS.value)
    await state.set_state(WorkoutStates.entering_working_weight)
    push_menu_stack(message.bot, working_weight_menu)
    await message.answer(
        f"🏋️ {exercise}\n\n"
        f"1️⃣ Укажи {_weight_label(exercise).lower()}:",
        reply_markup=working_weight_menu,
    )


async def _open_reps_input(message: Message, state: FSMContext, exercise: str, working_weight: float | None, *, step_prefix: str = "2️⃣ ") -> None:
    await state.set_state(WorkoutStates.entering_count)
    push_menu_stack(message.bot, count_menu)
    weight_line = f"⚖️ {_weight_label(exercise)}: {_format_working_weight(working_weight)}\n\n" if _is_gym_exercise(exercise) else ""
    await message.answer(
        f"🏋️ {exercise}\n"
        f"{weight_line}"
        f"{step_prefix}Выбери количество повторений:",
        reply_markup=count_menu,
    )


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



def _format_activity_overview(user_id: str, target_date: date) -> tuple[str, list]:
    """Форматирует компактную сводку активности за выбранный день."""
    workouts = WorkoutRepository.get_workouts_for_day(user_id, target_date)
    steps = 0
    steps_kcal = 0.0
    activities = []
    activities_kcal = 0.0

    for workout in workouts:
        calories = workout.calories or calculate_workout_calories(
            user_id, workout.exercise, workout.variant, workout.count
        )
        if is_steps_workout(workout):
            steps += int(workout.count or 0)
            steps_kcal += calories
            continue
        activities.append(workout)
        activities_kcal += calories

    steps_text = f"{steps:,}".replace(",", " ")
    lines = [
        "🏃 Активность за день",
        "",
        f"👣 Шаги: {steps_text} (~{steps_kcal:.0f} ккал)",
        "",
    ]

    if activities:
        lines.append("🏃 Активность:")
        lines.extend(f"• {summary}" for summary in format_activity_daily_summaries(activities, user_id))
    else:
        lines.append("🏃 Активность: пока не добавлена")

    total_kcal = steps_kcal + activities_kcal
    lines.extend(["", f"🔥 Всего сожжено: ~{total_kcal:.0f} ккал"])
    return "\n".join(lines), activities


def _format_today_activity_overview(user_id: str) -> str:
    """Форматирует сводку главного экрана активности за сегодня."""
    return _format_activity_overview(user_id, date.today())[0]


def _build_activity_report_inline(activities: list, target_date: date) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    if activities:
        rows.append([InlineKeyboardButton(text="✏️ Редактировать активность", callback_data=f"wrk_edit_menu:{target_date.isoformat()}")])
    rows.append([InlineKeyboardButton(text="👣 Добавить шаги", callback_data=f"wrk_steps:{target_date.isoformat()}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

async def _send_activity_main_screen(message: Message, user_id: str, target_date: date | None = None, *, prefix: str | None = None):
    """Отправляет главный экран раздела активности."""
    report_date = target_date or date.today()
    workouts_text, activities = _format_activity_overview(user_id, report_date)
    if prefix:
        workouts_text = f"{prefix}\n\n{workouts_text}"
    push_menu_stack(message.bot, training_menu)
    await message.answer(
        workouts_text,
        reply_markup=_build_activity_report_inline(activities, report_date),
        parse_mode="HTML",
    )
    await message.answer("Выберите действие:", reply_markup=training_menu)



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
    total_pages = total_pages_for(len(items), per_page)
    page = min(max(page, 0), total_pages - 1)
    return items[page * per_page : (page + 1) * per_page], page


def _russian_sort_key(text: str) -> str:
    return _search_key(text)


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


def _exercise_button_rows(exercises: list[str], *, numbered: bool = False, prefix_text: str = "") -> list[list[InlineKeyboardButton]]:
    numbers = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣"]
    return [[InlineKeyboardButton(text=f"{prefix_text}{numbers[i]} {ex}" if numbered and i < len(numbers) else f"{prefix_text}{ex}", callback_data=f"wrk_pick:{_activity_id(ex)}")] for i, ex in enumerate(exercises)]


def _build_activity_inline(exercises: list[str], prefix: str, page: int, total_pages: int, *, numbered: bool = False, extra_top_exercises: list[str] | None = None) -> InlineKeyboardMarkup:
    rows = []
    if page == 0 and extra_top_exercises:
        rows.extend(_exercise_button_rows(extra_top_exercises, prefix_text="⭐ "))
    rows.extend(_exercise_button_rows(exercises, numbered=numbered))
    return build_pagination_keyboard(page, total_pages, prefix, rows)


async def _edit_or_answer(message: Message, text: str, *, reply_markup: InlineKeyboardMarkup) -> None:
    if hasattr(message, "edit_text"):
        try:
            await message.edit_text(text, reply_markup=reply_markup)
            return
        except TelegramBadRequest as exc:
            if "message is not modified" in str(exc).lower():
                return
    await message.answer(text, reply_markup=reply_markup)


def _format_category_text(category: dict, page_items: list[str], page: int, all_exercises: list[str], *, recent: list[str] | None = None) -> str:
    lines = [f"{category['title']}", "", "Выбери упражнение:"]
    if page == 0 and recent:
        lines.extend(["", "⭐ Недавние:"])
        lines.extend(f"• {exercise}" for exercise in recent)
        if page_items:
            lines.append("")
    return "\n".join(lines)


async def _send_add_activity_screen(message: Message, state: FSMContext, user_id: str, page: int = 0) -> None:
    recent = _get_recent_exercises(user_id)
    page_items, page = _paginate(recent, page)
    await state.update_data(add_activity_screen="main", recent_exercises=recent, recent_page=page)
    if page_items:
        text = "⭐ Недавние активности:"
    else:
        text = "⭐ Недавних активностей пока нет.\n\nНайди упражнение через поиск или выбери категорию ниже."
    await message.answer(
        text,
        reply_markup=_build_activity_inline(page_items, "wrk_recent_page", page, total_pages_for(len(recent), 8), numbered=True),
    )
    push_menu_stack(message.bot, activity_category_menu)
    await message.answer("📂 Или выбери категорию:", reply_markup=activity_category_menu)


async def _show_category(message: Message, state: FSMContext, category_id: str, page: int = 0, user_id: str | None = None, *, send_reply_keyboard: bool = False) -> None:
    category = ACTIVITY_CATEGORIES[category_id]
    exercises = sorted([_normalize_exercise_name(ex) for ex in category["activities"] if _normalize_exercise_name(ex) not in STEPS_EXERCISES], key=_russian_sort_key)
    recent = _get_recent_exercises(user_id, limit=5) if category_id == "gym" and page == 0 and user_id else []
    if category_id == "gym":
        gym_keys = {_search_key(ex) for ex in exercises}
        unique_recent: list[str] = []
        seen_recent: set[str] = set()
        for ex in recent:
            key = _search_key(ex)
            if key in gym_keys and key not in seen_recent:
                unique_recent.append(ex)
                seen_recent.add(key)
        recent = unique_recent[:5]
    page_items, page = _paginate(exercises, page)
    await state.update_data(add_activity_screen="category", category_id=category_id, category_page=page)
    category_reply = category_search_reply_menu
    push_menu_stack(message.bot, category_reply)
    if category_id == "gym" and page == 0 and send_reply_keyboard:
        await message.answer("⬇️ Управление разделом", reply_markup=category_reply, disable_notification=True)
    await _edit_or_answer(
        message,
        _format_category_text(category, page_items, page, exercises, recent=recent),
        reply_markup=_build_activity_inline(
            page_items,
            f"wrk_cat_page:{category_id}",
            page,
            total_pages_for(len(exercises), 8),
            extra_top_exercises=recent,
        ),
    )


async def _show_search_results(message: Message, state: FSMContext, query: str, page: int = 0) -> None:
    matches = [
        ex for ex in sorted(_all_catalog_exercises(), key=_russian_sort_key)
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
        reply_markup=_build_activity_inline(page_items, "wrk_search_page", page, total_pages_for(len(matches), 8)),
    )


def _method_label(method: ActivityInputMethod) -> str:
    return {
        ActivityInputMethod.TIME: "⏱ По времени",
        ActivityInputMethod.DISTANCE: "📏 По расстоянию",
        ActivityInputMethod.JUMPS: "🔢 По прыжкам",
        ActivityInputMethod.REPETITIONS: "🔢 По повторениям",
    }.get(method, str(method))



def _build_activity_input_keyboard(exercise: str, method: ActivityInputMethod) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    if method == ActivityInputMethod.TIME:
        values = [5, 10, 15, 20, 25, 30, 35, 40, 45, 50, 55, 60]
        rows.extend([
            [InlineKeyboardButton(text=f"{value} мин", callback_data=f"wrk_value:time:{value}") for value in values[i:i + 3]]
            for i in range(0, len(values), 3)
        ])
    elif method == ActivityInputMethod.DISTANCE:
        values = [1, 2, 3, 4, 5, 7, 10, 15, 20]
        rows.extend([
            [InlineKeyboardButton(text=f"{value} км", callback_data=f"wrk_value:distance:{value}") for value in values[i:i + 3]]
            for i in range(0, len(values), 3)
        ])
    elif method == ActivityInputMethod.JUMPS:
        values = [500, 1000, 1500, 2000, 2500, 3000]
        rows.extend([
            [InlineKeyboardButton(text=f"{value:,}".replace(",", " "), callback_data=f"wrk_value:jumps:{value}") for value in values[:3]],
            [InlineKeyboardButton(text=f"{value:,}".replace(",", " "), callback_data=f"wrk_value:jumps:{value}") for value in values[3:]],
        ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _switch_method_label(method: ActivityInputMethod) -> str:
    return {
        ActivityInputMethod.TIME: "⏱ Добавить по времени",
        ActivityInputMethod.DISTANCE: "📏 Добавить по расстоянию",
        ActivityInputMethod.JUMPS: "🔢 Добавить по количеству",
    }.get(method, _method_label(method))


def _activity_input_reply_keyboard(exercise: str, method: ActivityInputMethod) -> ReplyKeyboardMarkup:
    rows: list[list[KeyboardButton]] = []
    for candidate in get_activity_methods(exercise):
        if candidate != method and candidate in {ActivityInputMethod.TIME, ActivityInputMethod.DISTANCE, ActivityInputMethod.JUMPS}:
            rows.append([KeyboardButton(text=_switch_method_label(candidate))])
    rows.append([KeyboardButton(text="⬅️ Назад")])
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True)


def _input_prompt(method: ActivityInputMethod) -> str:
    return {
        ActivityInputMethod.TIME: "Выбери время кнопкой ниже или введи количество минут сообщением:",
        ActivityInputMethod.DISTANCE: "Выбери расстояние кнопкой ниже или введи количество километров сообщением:",
        ActivityInputMethod.JUMPS: "Выбери количество кнопкой ниже или введи число прыжков сообщением:",
    }.get(method, "Выбери значение кнопкой ниже или введи число сообщением:")

def _build_input_method_keyboard(exercise: str) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text=_method_label(method), callback_data=f"wrk_method:{method.value}")]
        for method in get_activity_methods(exercise)
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def _open_input_method(message: Message, state: FSMContext, exercise: str, method: ActivityInputMethod) -> None:
    config = get_activity_config_by_exercise(exercise)
    title = config.title if config else exercise
    await state.update_data(input_method=method.value, back_target="exercise_picker")
    if method == ActivityInputMethod.TIME:
        await state.update_data(variant="Минуты")
        await state.set_state(WorkoutStates.entering_duration)
        reply_keyboard = _activity_input_reply_keyboard(exercise, method)
        push_menu_stack(message.bot, reply_keyboard)
        await message.answer(
            f"{title}\n\n{_input_prompt(method)}",
            reply_markup=_build_activity_input_keyboard(exercise, method),
        )
        return
    if method == ActivityInputMethod.DISTANCE:
        await state.update_data(variant="Км")
        await state.set_state(WorkoutStates.entering_distance)
        reply_keyboard = _activity_input_reply_keyboard(exercise, method)
        push_menu_stack(message.bot, reply_keyboard)
        await message.answer(
            f"{title}\n\n{_input_prompt(method)}",
            reply_markup=_build_activity_input_keyboard(exercise, method),
        )
        return
    if method == ActivityInputMethod.JUMPS:
        await state.update_data(variant="Прыжки")
        await state.set_state(WorkoutStates.entering_jumps)
        reply_keyboard = _activity_input_reply_keyboard(exercise, method)
        push_menu_stack(message.bot, reply_keyboard)
        await message.answer(
            f"{title}\n\n{_input_prompt(method)}",
            reply_markup=_build_activity_input_keyboard(exercise, method),
        )
        return
    await state.update_data(variant="reps")
    await state.set_state(WorkoutStates.entering_count)
    push_menu_stack(message.bot, count_menu)
    await message.answer("Выбери количество повторений:", reply_markup=count_menu)


async def _switch_input_method_from_reply(message: Message, state: FSMContext, method: ActivityInputMethod) -> bool:
    data = await state.get_data()
    exercise = data.get("exercise")
    if not exercise or method not in get_activity_methods(exercise):
        return False
    await _open_input_method(message, state, exercise, method)
    return True

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


async def start_steps_flow(message: Message, state: FSMContext, user_id: str, target_date: date | None = None):
    """Общий сценарий добавления или изменения шагов."""
    entry_date = target_date or date.today()
    await state.update_data(
        entry_date=entry_date.isoformat(),
        exercise="Шаги",
        variant="Количество шагов",
        back_target="training_menu",
    )
    await state.set_state(WorkoutStates.entering_steps)
    push_menu_stack(message.bot, steps_menu)
    date_label = "сегодня" if entry_date == date.today() else entry_date.strftime("%d.%m.%Y")
    await message.answer(f"Введи количество шагов за {date_label}:", reply_markup=steps_menu)


@router.message(lambda m: m.text == "👣 Шаги")
async def open_steps_flow(message: Message, state: FSMContext):
    """Быстрый сценарий добавления шагов."""
    await start_steps_flow(message, state, str(message.from_user.id), date.today())


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

    await _send_activity_main_screen(message, user_id, prefix="✅ Тренировка завершена!")


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

    for w in workouts:
        entry_calories = w.calories or calculate_workout_calories(user_id, w.exercise, w.variant, w.count)
        total_calories += entry_calories
        text.append(f"• {format_activity_summary(w, user_id)}")

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

    if _is_gym_exercise(exercise):
        await _open_working_weight_input(message, state, exercise)
        return

    methods = get_activity_methods(exercise)
    method = methods[0]
    if method == ActivityInputMethod.STEPS:
        await state.update_data(back_target="exercise_picker")
        await state.set_state(WorkoutStates.entering_steps)
        push_menu_stack(message.bot, steps_menu)
        await message.answer("Введи количество шагов за сегодня:", reply_markup=steps_menu)
        return
    await _open_input_method(message, state, exercise, method)


@router.callback_query(lambda c: c.data == PAGINATION_NOOP_CALLBACK)
async def pagination_noop(callback: CallbackQuery):
    await callback.answer()


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
    _, category_id, page_text = callback.data.split(":", maxsplit=2)
    await _show_category(callback.message, state, category_id, int(page_text), user_id=str(callback.from_user.id))


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


@router.callback_query(WorkoutStates.choosing_input_method, lambda c: c.data and c.data.startswith("wrk_method:"))
async def choose_activity_input_method(callback: CallbackQuery, state: FSMContext):
    """Выбирает универсальный способ ввода активности."""
    await callback.answer()
    data = await state.get_data()
    exercise = data.get("exercise")
    if not exercise:
        await callback.message.answer("❌ Не удалось выбрать способ ввода. Начни добавление заново.")
        await state.clear()
        return
    method_value = callback.data.split(":", maxsplit=1)[1]
    try:
        method = ActivityInputMethod(method_value)
    except ValueError:
        await callback.message.answer("❌ Неизвестный способ ввода.")
        return
    await _open_input_method(callback.message, state, exercise, method)


@router.callback_query(lambda c: c.data and c.data.startswith("wrk_method:"))
async def switch_activity_input_method(callback: CallbackQuery, state: FSMContext):
    """Переключает способ ввода внутри текущей формы активности."""
    await callback.answer()
    data = await state.get_data()
    exercise = data.get("exercise")
    if not exercise:
        await callback.message.answer("❌ Не удалось выбрать способ ввода. Начни добавление заново.")
        await state.clear()
        return
    method_value = callback.data.split(":", maxsplit=1)[1]
    try:
        method = ActivityInputMethod(method_value)
    except ValueError:
        await callback.message.answer("❌ Неизвестный способ ввода.")
        return

    config = get_activity_config_by_exercise(exercise)
    title = config.title if config else exercise
    state_by_method = {
        ActivityInputMethod.TIME: WorkoutStates.entering_duration,
        ActivityInputMethod.DISTANCE: WorkoutStates.entering_distance,
        ActivityInputMethod.JUMPS: WorkoutStates.entering_jumps,
    }
    variant_by_method = {
        ActivityInputMethod.TIME: "Минуты",
        ActivityInputMethod.DISTANCE: "Км",
        ActivityInputMethod.JUMPS: "Прыжки",
    }
    await state.update_data(input_method=method.value, variant=variant_by_method.get(method))
    await state.set_state(state_by_method[method])
    reply_keyboard = _activity_input_reply_keyboard(exercise, method)
    push_menu_stack(callback.message.bot, reply_keyboard)
    await callback.message.answer(
        f"{title}\n\n{_input_prompt(method)}",
        reply_markup=_build_activity_input_keyboard(exercise, method),
    )


@router.callback_query(lambda c: c.data == "wrk_manual")
async def request_manual_activity_value(callback: CallbackQuery):
    """Просит ввести значение вручную для текущей формы."""
    await callback.answer()
    await callback.message.answer("Введи значение вручную:")


@router.callback_query(lambda c: c.data and c.data.startswith("wrk_value:"))
async def choose_activity_preset_value(callback: CallbackQuery, state: FSMContext):
    """Обрабатывает inline-пресеты значения без изменения бизнес-логики сохранения."""
    await callback.answer()
    _, method, value = callback.data.split(":", maxsplit=2)
    text = f"{value} км" if method == ActivityInputMethod.DISTANCE.value else value
    fake_message = SimpleNamespace(
        text=text,
        from_user=callback.from_user,
        bot=callback.message.bot,
        answer=callback.message.answer,
    )
    if method == ActivityInputMethod.TIME.value:
        await handle_duration_input(fake_message, state)
    elif method == ActivityInputMethod.DISTANCE.value:
        await handle_distance_input(fake_message, state)
    elif method == ActivityInputMethod.JUMPS.value:
        await handle_jumps_input(fake_message, state)


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




@router.callback_query(lambda c: c.data.startswith("wrk_steps:"))
async def open_steps_flow_from_activity_report(callback: CallbackQuery, state: FSMContext):
    """Открывает общий сценарий шагов из inline-кнопки отчёта активности."""
    await callback.answer()
    parts = callback.data.split(":")
    target_date = date.fromisoformat(parts[1]) if len(parts) > 1 else date.today()
    await start_steps_flow(callback.message, state, str(callback.from_user.id), target_date)


@router.callback_query(lambda c: c.data.startswith("wrk_edit_menu:"))
async def show_activity_edit_menu(callback: CallbackQuery):
    """Показывает список обычных активностей выбранного дня для редактирования."""
    await callback.answer()
    parts = callback.data.split(":")
    target_date = date.fromisoformat(parts[1]) if len(parts) > 1 else date.today()
    user_id = str(callback.from_user.id)
    activities = [
        workout
        for workout in WorkoutRepository.get_workouts_for_day(user_id, target_date)
        if not is_steps_workout(workout)
    ]
    if not activities:
        await callback.message.answer("🏃 Активность: пока не добавлена")
        await _send_activity_main_screen(callback.message, user_id, target_date)
        return

    rows = [
        [
            InlineKeyboardButton(
                text=format_activity_edit_button(activity),
                callback_data=f"wrk_edit:{activity.id}:{target_date.isoformat()}",
            )
        ]
        for activity in activities
    ]
    await callback.message.answer(
        "✏️ Выбери активность для редактирования:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )

def _build_set_edit_keyboard(workout) -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(text="🔁 Изменить повторения", callback_data=f"wrk_edit_reps:{workout.id}")]]
    if _is_gym_exercise(workout.exercise):
        rows.append([InlineKeyboardButton(text="⚖️ Изменить вес", callback_data=f"wrk_edit_weight:{workout.id}")])
    rows.extend([
        [InlineKeyboardButton(text="🗑 Удалить подход", callback_data=f"wrk_delete_confirm:{workout.id}")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data=f"wrk_edit_cancel:{workout.date.isoformat()}")],
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _build_reps_preset_keyboard(workout_id: int) -> InlineKeyboardMarkup:
    values = [1, 5, 8, 10, 12, 15, 20, 25, 30, 40]
    rows = [
        [InlineKeyboardButton(text=str(value), callback_data=f"wrk_set_reps:{workout_id}:{value}") for value in values[i:i + 5]]
        for i in range(0, len(values), 5)
    ]
    rows.append([InlineKeyboardButton(text="❌ Отмена", callback_data=f"wrk_edit_cancel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _set_edit_context(workout, target_date: date) -> dict:
    return {
        "workout_id": workout.id,
        "workout_exercise": workout.exercise,
        "workout_variant": workout.variant,
        "workout_working_weight": getattr(workout, "working_weight", None),
        "workout_date": workout.date.isoformat(),
        "target_date": target_date.isoformat(),
    }


def _format_set_details(workout) -> str:
    weight = getattr(workout, "working_weight", None)
    calories = float(workout.calories or 0)
    lines = [
        "Текущие данные:",
    ]
    if _is_gym_exercise(workout.exercise):
        lines.append(f"⚖️ {_weight_label(workout.exercise)}: {_format_working_weight(weight)}")
    lines.extend([
        f"🔁 Повторения: {int(workout.count or 0)}",
        f"🔥 Калории: ~{calories:.0f} ккал",
    ])
    return "\n".join(lines)


@router.callback_query(lambda c: c.data.startswith("wrk_edit:"))
async def edit_workout(callback: CallbackQuery, state: FSMContext):
    """Открывает экран действий для отдельного подхода."""
    await callback.answer()
    parts = callback.data.split(":")
    workout_id = int(parts[1])
    target_date = date.fromisoformat(parts[2]) if len(parts) > 2 else date.today()
    user_id = str(callback.from_user.id)

    workout = WorkoutRepository.get_workout_by_id(workout_id, user_id)
    if not workout:
        await callback.message.answer("❌ Не нашёл подход для изменения.")
        return

    await state.update_data(**_set_edit_context(workout, target_date))
    await callback.message.answer(
        f"✏️ Редактирование подхода\n\n"
        f"🏋️ {workout.exercise}\n"
        f"📅 {workout.date.strftime('%d.%m.%Y')}\n\n"
        f"{_format_set_details(workout)}\n\n"
        f"Выбери действие:",
        reply_markup=_build_set_edit_keyboard(workout),
    )
    await callback.message.answer("Режим редактирования подхода.", reply_markup=ReplyKeyboardRemove())


@router.callback_query(lambda c: c.data.startswith("wrk_edit_reps:"))
async def request_workout_reps_edit(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    workout_id = int(callback.data.split(":")[1])
    user_id = str(callback.from_user.id)
    workout = WorkoutRepository.get_workout_by_id(workout_id, user_id)
    if not workout:
        await callback.message.answer("❌ Не нашёл подход для изменения.")
        return
    await state.update_data(**_set_edit_context(workout, workout.date))
    await state.set_state(WorkoutStates.editing_count)
    weight_line = f"⚖️ {_weight_label(workout.exercise)}: {_format_working_weight(getattr(workout, 'working_weight', None))}\n" if _is_gym_exercise(workout.exercise) else ""
    await callback.message.answer(
        f"🏋️ {workout.exercise}\n"
        f"{weight_line}"
        f"🔁 Сейчас: {int(workout.count or 0)} повторений\n\n"
        f"Введи новое количество повторений:",
        reply_markup=_build_reps_preset_keyboard(workout.id),
    )


@router.callback_query(lambda c: c.data.startswith("wrk_edit_weight:"))
async def request_workout_weight_edit(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    workout_id = int(callback.data.split(":")[1])
    user_id = str(callback.from_user.id)
    workout = WorkoutRepository.get_workout_by_id(workout_id, user_id)
    if not workout or not _is_gym_exercise(workout.exercise):
        await callback.message.answer("❌ Не нашёл подход с рабочим весом для изменения.")
        return
    await state.update_data(**_set_edit_context(workout, workout.date))
    await state.set_state(WorkoutStates.editing_weight)
    await callback.message.answer(
        f"🏋️ {workout.exercise}\n"
        f"🔁 Повторения: {int(workout.count or 0)}\n"
        f"⚖️ Сейчас: {_format_working_weight(getattr(workout, 'working_weight', None))}\n\n"
        f"Укажи {_weight_label(workout.exercise).lower()}:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="❌ Отмена", callback_data="wrk_edit_cancel")]]),
    )


@router.message(WorkoutStates.editing_count)
async def handle_workout_edit_count(message: Message, state: FSMContext):
    """Обрабатывает ввод нового количества при редактировании тренировки."""
    user_id = str(message.from_user.id)
    
    data = await state.get_data()
    workout_id = data.get("workout_id")
    exercise = data.get("workout_exercise")
    variant = data.get("workout_variant")
    working_weight = data.get("workout_working_weight")
    input_method = infer_input_method(exercise, variant)
    target_date_str = data.get("target_date", date.today().isoformat())

    try:
        count = int((message.text or "").replace(" ", ""))
        if count <= 0:
            raise ValueError
    except (ValueError, AttributeError):
        await message.answer("⚠️ Введи целое положительное число повторений")
        return
    
    if not workout_id:
        await message.answer("❌ Ошибка: не найдена тренировка для обновления.")
        await state.clear()
        return
    
    # Пересчитываем калории только существующими проверенными путями.
    calories = 0.0 if input_method in {ActivityInputMethod.DISTANCE, ActivityInputMethod.JUMPS} else calculate_workout_calories(user_id, exercise, variant, count)
    success = WorkoutRepository.update_workout(
        workout_id,
        user_id,
        count,
        calories,
        input_method=input_method.value,
        duration_minutes=count if input_method == ActivityInputMethod.TIME else None,
        distance_km=count if input_method == ActivityInputMethod.DISTANCE else None,
        jumps_count=int(count) if input_method == ActivityInputMethod.JUMPS else None,
        working_weight=working_weight if _is_gym_exercise(exercise) else None,
    )
    
    if success:
        if isinstance(target_date_str, str):
            try:
                target_date = date.fromisoformat(target_date_str)
            except ValueError:
                target_date = date.today()
        else:
            target_date = date.today()
        
        await state.clear()
        await _send_activity_main_screen(message, user_id, target_date, prefix="✅ Подход обновлён")
    else:
        await message.answer("❌ Не удалось обновить тренировку. Попробуйте позже.")
        await state.clear()


@router.callback_query(lambda c: c.data.startswith("wrk_set_reps:"))
async def choose_workout_reps_preset(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    _, workout_id, count = callback.data.split(":")
    fake_message = SimpleNamespace(
        text=count,
        from_user=callback.from_user,
        bot=callback.message.bot,
        answer=callback.message.answer,
    )
    await state.update_data(workout_id=int(workout_id))
    await handle_workout_edit_count(fake_message, state)


@router.message(WorkoutStates.editing_weight)
async def handle_workout_edit_weight(message: Message, state: FSMContext):
    user_id = str(message.from_user.id)
    data = await state.get_data()
    workout_id = data.get("workout_id")
    target_date_str = data.get("target_date", date.today().isoformat())
    try:
        weight = _parse_working_weight(message.text)
    except (ValueError, TypeError):
        await message.answer("⚠️ Укажи положительный вес числом, например 7,5")
        return
    if weight is not None and weight <= 0:
        await message.answer("⚠️ Укажи положительный вес числом, например 7,5")
        return
    workout = WorkoutRepository.get_workout_by_id(int(workout_id), user_id) if workout_id else None
    if not workout:
        await message.answer("❌ Ошибка: не найден подход для обновления.")
        await state.clear()
        return
    calories = calculate_workout_calories(user_id, workout.exercise, workout.variant, workout.count)
    success = WorkoutRepository.update_workout(
        workout.id,
        user_id,
        workout.count,
        calories,
        input_method=workout.input_method,
        duration_minutes=workout.duration_minutes,
        distance_km=workout.distance_km,
        jumps_count=workout.jumps_count,
        working_weight=weight,
    )
    target_date = date.fromisoformat(target_date_str) if isinstance(target_date_str, str) else date.today()
    await state.clear()
    if success:
        await _send_activity_main_screen(message, user_id, target_date, prefix="✅ Подход обновлён")
    else:
        await message.answer("❌ Не удалось обновить подход. Попробуйте позже.")


@router.callback_query(lambda c: c.data.startswith("wrk_delete_confirm:"))
async def confirm_delete_workout_set(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    workout_id = int(callback.data.split(":")[1])
    user_id = str(callback.from_user.id)
    workout = WorkoutRepository.get_workout_by_id(workout_id, user_id)
    if not workout:
        await callback.message.answer("❌ Не нашёл подход для удаления.")
        return
    await state.update_data(**_set_edit_context(workout, workout.date))
    weight_line = f"⚖️ {_format_working_weight(getattr(workout, 'working_weight', None))} — {_weight_saved_description(workout.exercise)}\n" if _is_gym_exercise(workout.exercise) else ""
    await callback.message.answer(
        f"Удалить этот подход?\n\n"
        f"🏋️ {workout.exercise}\n"
        f"{weight_line}"
        f"🔁 {int(workout.count or 0)} повторений\n"
        f"🔥 ~{float(workout.calories or 0):.0f} ккал",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🗑 Да, удалить", callback_data=f"wrk_delete_set:{workout.id}")],
            [InlineKeyboardButton(text="❌ Отмена", callback_data="wrk_edit_cancel")],
        ]),
    )


@router.callback_query(lambda c: c.data.startswith("wrk_delete_set:"))
async def delete_workout_set(callback: CallbackQuery, state: FSMContext):
    await callback.answer("✅ Подход удалён")
    workout_id = int(callback.data.split(":")[1])
    user_id = str(callback.from_user.id)
    data = await state.get_data()
    target_date_str = data.get("target_date")
    workout = WorkoutRepository.get_workout_by_id(workout_id, user_id)
    target_date = workout.date if workout else (date.fromisoformat(target_date_str) if target_date_str else date.today())
    success = WorkoutRepository.delete_workout(workout_id, user_id)
    await state.clear()
    if success:
        await _send_activity_main_screen(callback.message, user_id, target_date, prefix="✅ Подход удалён")
    else:
        await callback.message.answer("❌ Не удалось удалить подход")


@router.callback_query(lambda c: c.data.startswith("wrk_edit_cancel"))
async def cancel_workout_set_edit(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    data = await state.get_data()
    target_date_str = data.get("target_date")
    if not target_date_str and ":" in callback.data:
        target_date_str = callback.data.split(":", maxsplit=1)[1]
    try:
        target_date = date.fromisoformat(target_date_str) if target_date_str else date.today()
    except ValueError:
        target_date = date.today()
    await state.clear()
    await _send_activity_main_screen(callback.message, str(callback.from_user.id), target_date)


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
        await _send_activity_main_screen(callback.message, user_id, target_date)
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
        await _show_category(message, state, category_by_title[text], user_id=str(message.from_user.id), send_reply_keyboard=True)
        return

    if text in {EXERCISE_SEARCH_BUTTON_TEXT, "🔍 Поиск упражнения"}:
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
    target_date = _entry_date_from_state_data(data)

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

    step_entries = [w for w in WorkoutRepository.get_workouts_for_day(user_id, target_date) if _normalize_exercise_name(w.exercise) == "Шаги"]

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
            entry_date=target_date,
            variant="Количество шагов",
            calories=calories,
        )
    AnalyticsRepository.track_event(user_id, "add_steps", section="activity")

    await state.clear()
    await message.answer("✅ Шаги сохранены!")
    await _send_activity_main_screen(message, user_id, target_date)


@router.message(WorkoutStates.entering_duration)
async def handle_duration_input(message: Message, state: FSMContext):
    """Обрабатывает ввод длительности упражнения."""
    if message.text == "📏 Добавить по расстоянию":
        if await _switch_input_method_from_reply(message, state, ActivityInputMethod.DISTANCE):
            return
    if message.text == "🔢 Добавить по количеству":
        if await _switch_input_method_from_reply(message, state, ActivityInputMethod.JUMPS):
            return
    if message.text == "✍️ Ввести вручную":
        await message.answer("Введи длительность в минутах (например, 1,5):")
        return
    if message.text == "⬅️ Назад":
        data = await state.get_data()
        exercise = data.get("exercise")
        await state.set_state(WorkoutStates.choosing_exercise)
        category_id = data.get("category_id")
        if category_id in ACTIVITY_CATEGORIES:
            await _show_category(message, state, category_id)
        else:
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
        input_method=ActivityInputMethod.TIME.value,
        duration_minutes=minutes,
    )
    await state.clear()
    await message.answer(
        f"✅ Записал!\n💪 {exercise}\n⏱ {_format_minutes(minutes)} мин\n🔥 ~{calories:.0f}\n📅 сегодня",
        reply_markup=add_another_exercise_menu,
    )


@router.message(WorkoutStates.entering_distance)
async def handle_distance_input(message: Message, state: FSMContext):
    """Обрабатывает ввод дистанции активности."""
    if message.text == "⏱ Добавить по времени":
        if await _switch_input_method_from_reply(message, state, ActivityInputMethod.TIME):
            return
    if message.text == "✍️ Ввести вручную":
        await message.answer("Введи дистанцию в километрах (например, 2.5 или 5,3):")
        return
    if message.text == "⬅️ Назад":
        data = await state.get_data()
        await state.set_state(WorkoutStates.choosing_exercise)
        category_id = data.get("category_id")
        if category_id in ACTIVITY_CATEGORIES:
            await _show_category(message, state, category_id)
        else:
            await _send_add_activity_screen(message, state, str(message.from_user.id))
        return
    if message.text in MAIN_MENU_BUTTON_ALIASES:
        from handlers.common import go_main_menu
        await go_main_menu(message, state)
        return
    raw = (message.text or "").replace("км", "").replace("КМ", "").replace(",", ".").strip()
    try:
        distance_km = float(raw)
        if distance_km <= 0:
            raise ValueError
    except (ValueError, TypeError):
        await message.answer("⚠️ Введи положительную дистанцию в километрах (например, 2.5 или 5,3).")
        return
    data = await state.get_data()
    exercise = data.get("exercise")
    user_id = str(message.from_user.id)
    entry_date = _entry_date_from_state_data(data)
    calories = 0.0
    WorkoutRepository.save_workout(
        user_id=user_id,
        exercise=exercise,
        count=distance_km,
        entry_date=entry_date,
        variant="Км",
        calories=calories,
        input_method=ActivityInputMethod.DISTANCE.value,
        distance_km=distance_km,
    )
    await state.clear()
    date_label = "сегодня" if entry_date == date.today() else entry_date.strftime("%d.%m.%Y")
    await message.answer(f"✅ Записал!\n💪 {exercise}\n📏 {_format_minutes(distance_km)} км\n🔥 ~{calories:.0f} ккал\n📅 {date_label}", reply_markup=add_another_exercise_menu)


@router.message(WorkoutStates.entering_jumps)
async def handle_jumps_input(message: Message, state: FSMContext):
    """Обрабатывает ввод количества прыжков."""
    if message.text == "⏱ Добавить по времени":
        if await _switch_input_method_from_reply(message, state, ActivityInputMethod.TIME):
            return
    if message.text == "✍️ Ввести вручную":
        await message.answer("Введи количество прыжков числом:")
        return
    if message.text == "⬅️ Назад":
        data = await state.get_data()
        await state.set_state(WorkoutStates.choosing_exercise)
        category_id = data.get("category_id")
        if category_id in ACTIVITY_CATEGORIES:
            await _show_category(message, state, category_id)
        else:
            await _send_add_activity_screen(message, state, str(message.from_user.id))
        return
    if message.text in MAIN_MENU_BUTTON_ALIASES:
        from handlers.common import go_main_menu
        await go_main_menu(message, state)
        return
    try:
        jumps = int((message.text or "").replace(" ", ""))
        if jumps <= 0:
            raise ValueError
    except (ValueError, TypeError):
        await message.answer("⚠️ Введи положительное количество прыжков.")
        return
    data = await state.get_data()
    exercise = data.get("exercise")
    user_id = str(message.from_user.id)
    entry_date = _entry_date_from_state_data(data)
    calories = 0.0
    WorkoutRepository.save_workout(
        user_id=user_id,
        exercise=exercise,
        count=jumps,
        entry_date=entry_date,
        variant="Прыжки",
        calories=calories,
        input_method=ActivityInputMethod.JUMPS.value,
        jumps_count=jumps,
    )
    await state.clear()
    date_label = "сегодня" if entry_date == date.today() else entry_date.strftime("%d.%m.%Y")
    await message.answer(f"✅ Записал!\n💪 {exercise}\n🔢 {jumps:,} прыжков\n🔥 ~{calories:.0f} ккал\n📅 {date_label}".replace(",", " "), reply_markup=add_another_exercise_menu)


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
        input_method=ActivityInputMethod.TIME.value,
        duration_minutes=minutes,
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


@router.message(WorkoutStates.entering_working_weight)
async def handle_working_weight_input(message: Message, state: FSMContext):
    """Обрабатывает выбор рабочего веса для упражнений тренажёрного зала."""
    if message.text == "✍️ Ввести вручную":
        data = await state.get_data()
        label = _weight_label(data.get("exercise"))
        await message.answer(f"Введи {label.lower()} в килограммах, например 32,5. Если упражнение без веса — нажми «Без веса».")
        return
    if message.text in {"❌ Отмена", "⬅️ Назад"}:
        await state.clear()
        push_menu_stack(message.bot, training_menu)
        await message.answer("⬇️ Меню тренировок", reply_markup=training_menu)
        return
    if message.text in MAIN_MENU_BUTTON_ALIASES:
        from handlers.common import go_main_menu
        await go_main_menu(message, state)
        return

    try:
        working_weight = _parse_working_weight(message.text)
    except (ValueError, TypeError):
        data = await state.get_data()
        exercise = data.get("exercise")
        weight_label = _weight_label(exercise).lower()
        await message.answer(f"⚠️ Введи {weight_label} положительным числом или нажми «Без веса».")
        return

    data = await state.get_data()
    exercise = data.get("exercise")
    if not exercise:
        await message.answer("❌ Ошибка: данные потеряны. Начни добавление тренировки заново.")
        await state.clear()
        return

    await state.update_data(working_weight=working_weight)
    await _open_reps_input(message, state, exercise, working_weight)


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
    if message.text == "⚖️ Изменить вес":
        data = await state.get_data()
        exercise = data.get("exercise")
        if not exercise or not _is_gym_exercise(exercise):
            await message.answer("Сменить вес можно для упражнений тренажёрного зала.")
            return
        await _open_working_weight_input(message, state, exercise)
        return

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
        
        # Показываем клавиатуру для выбора количества, сохраняя рабочий вес текущего упражнения.
        await _open_reps_input(message, state, exercise, data.get("working_weight"), step_prefix="")
        return
    
    if message.text == "➕ Добавить другое упражнение":
        data = await state.get_data()
        await start_exercise_selection(message, state, _entry_date_from_state_data(data))
        return

    if message.text == "✅ Завершить упражнение":
        await state.clear()
        await _send_activity_main_screen(message, user_id, prefix="✅ Тренировка завершена!")
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
        input_method=ActivityInputMethod.REPETITIONS.value,
        working_weight=data.get("working_weight") if _is_gym_exercise(exercise) else None,
    )
    
    logger.info(f"User {user_id} saved workout: {exercise} x {count} on {entry_date}")
    AnalyticsRepository.track_event(user_id, "add_workout", section="activity")
    
    # Получаем общее количество для этого упражнения за день
    workouts_today = WorkoutRepository.get_workouts_for_day(user_id, entry_date)
    total_count = sum(w.count for w in workouts_today if w.exercise == exercise and w.variant == variant)
    
    # Формируем ответ
    formatted_count = format_count_with_unit(count, variant)
    total_formatted = format_count_with_unit(total_count, variant)
    working_weight = data.get("working_weight") if _is_gym_exercise(exercise) else None
    weight_line = f"⚖️ {_format_working_weight(working_weight)} — {_weight_saved_description(exercise)}\n" if _is_gym_exercise(exercise) else ""
    total_weight_line = f"• {_weight_label(exercise)}: {_format_working_weight(working_weight)}\n" if _is_gym_exercise(exercise) else ""
    title_icon = "🏋️" if _is_gym_exercise(exercise) else "💪"
    
    date_label = "сегодня" if entry_date == date.today() else entry_date.strftime("%d.%m.%Y")
    
    await message.answer(
        f"✅ Записал! 👍\n\n"
        f"{title_icon} {exercise}\n"
        f"{weight_line}"
        f"🔁 {formatted_count}\n"
        f"🔥 ~{calories:.0f} ккал\n"
        f"📅 {date_label.capitalize()}\n\n"
        f"Всего за {date_label}:\n"
        f"• {total_formatted}\n"
        f"{total_weight_line}\n"
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
