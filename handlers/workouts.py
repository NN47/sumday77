"""Обработчики для тренировок."""
import logging
from datetime import date, timedelta, datetime
from typing import Optional
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery
from aiogram.fsm.context import FSMContext
from utils.keyboards import (
    MAIN_MENU_BUTTON_ALIASES,
    TRAINING_BUTTON_TEXT,
    LEGACY_TRAINING_BUTTON_TEXT,
    training_menu,
    exercise_picker_menu,
    steps_menu,
    steps_confirmation_menu,
    duration_menu,
    count_menu,
    bodyweight_exercises,
    weighted_exercises,
    frequent_exercises,
    build_exercise_selection_menu,
    push_menu_stack,
    add_another_set_menu,
    add_another_exercise_menu,
    grip_type_menu,
)
from states.user_states import WorkoutStates
from database.repositories import WorkoutRepository
from database.repositories import CustomWorkoutExerciseRepository
from utils.workout_utils import calculate_workout_calories
from utils.validators import parse_date
from utils.formatters import format_count_with_unit
from utils.calendar_utils import build_workout_calendar_keyboard
from utils.workout_formatters import build_day_actions_keyboard

logger = logging.getLogger(__name__)

router = Router()

REPS_EXERCISES = {"Отжимания", "Подтягивания", "Приседания", "Пресс", "Берпи"}
DURATION_EXERCISES = {"Планка", "Йога", "Бег", "Силовая тренировка", "Скакалка", "Велосипед", "Пробежка"}
STEPS_EXERCISES = {"Шаги", "Ходьба", "Шаги (Ходьба)"}


def _normalize_exercise_name(exercise: str) -> str:
    aliases = {
        "Пробежка": "Бег",
        "Шаги (Ходьба)": "Шаги",
        "Ходьба": "Шаги",
    }
    return aliases.get(exercise, exercise)


def _exercise_input_type(exercise: str) -> str:
    normalized = _normalize_exercise_name(exercise)
    if normalized in STEPS_EXERCISES:
        return "steps"
    if normalized in DURATION_EXERCISES:
        return "duration"
    return "reps"


def reset_user_state(message: Message, *, keep_supplements: bool = False):
    """Сбрасывает состояние пользователя."""
    # TODO: Заменить на FSM clear
    pass


@router.message(lambda m: m.text in {TRAINING_BUTTON_TEXT, LEGACY_TRAINING_BUTTON_TEXT})
async def show_training_menu(message: Message, state: FSMContext):
    """Показывает меню тренировок."""
    user_id = str(message.from_user.id)
    logger.info(f"User {user_id} opened training menu")
    await state.clear()  # Очищаем FSM состояние
    
    # Показываем прогресс тренировок
    from utils.progress_formatters import format_today_workouts_block
    workouts_text = format_today_workouts_block(user_id, include_date=False)
    
    push_menu_stack(message.bot, training_menu)
    await message.answer(
        f"🚴 Активность\n\n{workouts_text}\n\nВыбери действие:",
        reply_markup=training_menu,
        parse_mode="HTML",
    )


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


@router.message(lambda m: m.text == "💪 Тренировка")
async def add_training_entry(message: Message, state: FSMContext):
    """Начинает процесс добавления тренировки."""
    user_id = str(message.from_user.id)
    logger.info(f"User {user_id} started adding workout")
    
    await state.update_data(entry_date=date.today().isoformat())
    await state.set_state(WorkoutStates.choosing_exercise)
    push_menu_stack(message.bot, exercise_picker_menu)
    await message.answer(
        "Выбери упражнение:\n\nЧастые:\n- Отжимания\n- Подтягивания\n- Приседания\n- Планка\n- Бег\n- Ходьба\n- Йога",
        reply_markup=exercise_picker_menu,
    )


@router.message(lambda m: m.text == "➕ Добавить другое упражнение")
async def add_another_exercise(message: Message, state: FSMContext):
    """Позволяет быстро добавить следующее упражнение."""
    await add_training_entry(message, state)


@router.message(lambda m: m.text == "📅 Календарь активности")
async def show_training_calendar(message: Message):
    """Показывает календарь тренировок."""
    user_id = str(message.from_user.id)
    logger.info(f"User {user_id} opened training calendar")
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


async def show_day_workouts(message: Message, user_id: str, target_date: date):
    """Показывает тренировки за день."""
    workouts = WorkoutRepository.get_workouts_for_day(user_id, target_date)
    
    if not workouts:
        await message.answer(
            f"{target_date.strftime('%d.%m.%Y')}: нет тренировок.",
            reply_markup=build_day_actions_keyboard([], target_date),
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
        reply_markup=build_day_actions_keyboard(workouts, target_date),
    )


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
    
    await state.update_data(entry_date=target_date.isoformat())
    await state.set_state(WorkoutStates.choosing_exercise)
    push_menu_stack(callback.message.bot, exercise_picker_menu)
    await callback.message.answer(
        f"📅 Дата: {target_date.strftime('%d.%m.%Y')}\n\nВыбери упражнение:",
        reply_markup=exercise_picker_menu,
    )


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
    """Обрабатывает выбор упражнения."""
    exercise = message.text

    if exercise == "⬅️ Назад":
        await state.clear()
        push_menu_stack(message.bot, training_menu)
        await message.answer("⬇️ Меню тренировок", reply_markup=training_menu)
        return

    if exercise in {"🕘 Недавние", "📂 Все упражнения"}:
        user_id = str(message.from_user.id)
        workouts = WorkoutRepository.get_workouts_for_period(user_id, date.today() - timedelta(days=30), date.today())
        recent = []
        for w in reversed(workouts):
            name = _normalize_exercise_name(w.exercise)
            if name not in recent:
                recent.append(name)
            if len(recent) >= 10:
                break
        if exercise == "📂 Все упражнения":
            all_exercises = sorted({_normalize_exercise_name(ex) for ex in (bodyweight_exercises + weighted_exercises)})
            recent = [ex for ex in all_exercises if ex != "Другое"]
        if not recent:
            recent = frequent_exercises
        exercise_menu = build_exercise_selection_menu(recent)
        push_menu_stack(message.bot, exercise_menu)
        await message.answer("Выбери упражнение из списка:", reply_markup=exercise_menu)
        return

    if exercise == "🔎 Поиск упражнения":
        await state.set_state(WorkoutStates.searching_exercise)
        await message.answer("Введи часть названия упражнения:")
        return

    known_exercises = set(bodyweight_exercises + weighted_exercises + frequent_exercises) | {"Бег", "Ходьба", "Силовая тренировка", "Велосипед", "Шаги"}
    if exercise not in known_exercises:
        await message.answer("Выбери упражнение из меню")
        return

    exercise = _normalize_exercise_name(exercise)
    await state.update_data(exercise=exercise, category="bodyweight")
    
    # Обрабатываем "Другое"
    if exercise == "Другое":
        await state.set_state(WorkoutStates.entering_custom_exercise)
        await message.answer(
            "🆕 Создай своё упражнение: напиши название, и я сохраню его в список для будущих тренировок."
        )
        return
    
    # Особый случай: подтягивания - спрашиваем тип хвата
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
        push_menu_stack(message.bot, duration_menu)
        await message.answer(f"Введи длительность для {exercise} в минутах:", reply_markup=duration_menu)
        return

    await state.update_data(variant="reps", back_target="exercise_picker")
    await state.set_state(WorkoutStates.entering_count)
    push_menu_stack(message.bot, count_menu)
    await message.answer("Выбери количество повторений:", reply_markup=count_menu)


@router.message(WorkoutStates.searching_exercise)
async def search_exercise(message: Message, state: FSMContext):
    """Поиск упражнений по подстроке."""
    query = (message.text or "").strip().lower()
    all_exercises = sorted({_normalize_exercise_name(ex) for ex in (bodyweight_exercises + weighted_exercises + frequent_exercises)})
    matches = [ex for ex in all_exercises if query and query in ex.lower()]
    await state.set_state(WorkoutStates.choosing_exercise)
    push_menu_stack(message.bot, exercise_picker_menu)
    if not matches:
        await message.answer("Ничего не нашёл. Выбери упражнение из меню:", reply_markup=exercise_picker_menu)
        return
    await message.answer("Нашёл:\n" + "\n".join(f"• {m}" for m in matches[:15]) + "\n\nВыбери упражнение из меню ниже.", reply_markup=exercise_picker_menu)


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
            push_menu_stack(message.bot, exercise_picker_menu)
            await message.answer("Выбери упражнение:", reply_markup=exercise_picker_menu)
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

    if message.text == "🗑 Удалить шаги":
        for entry in step_entries:
            WorkoutRepository.delete_workout(entry.id, user_id)
        await state.clear()
        push_menu_stack(message.bot, training_menu)
        await message.answer("✅ Шаги за сегодня удалены.", reply_markup=training_menu)
        return

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

    await state.clear()
    from utils.progress_formatters import format_today_workouts_block
    workouts_text = format_today_workouts_block(user_id, include_date=False)
    push_menu_stack(message.bot, training_menu)
    await message.answer(f"✅ Шаги сохранены!\n\n{workouts_text}", reply_markup=training_menu, parse_mode="HTML")


@router.message(WorkoutStates.entering_duration)
async def handle_duration_input(message: Message, state: FSMContext):
    """Обрабатывает ввод длительности упражнения."""
    if message.text == "✍️ Ввести вручную":
        await message.answer("Введи длительность в минутах:")
        return
    if message.text == "⬅️ Назад":
        await state.set_state(WorkoutStates.choosing_exercise)
        push_menu_stack(message.bot, exercise_picker_menu)
        await message.answer("Выбери упражнение:", reply_markup=exercise_picker_menu)
        return
    if message.text in MAIN_MENU_BUTTON_ALIASES:
        from handlers.common import go_main_menu
        await go_main_menu(message, state)
        return

    try:
        minutes = int(message.text)
        if minutes <= 0:
            raise ValueError
    except (ValueError, TypeError):
        await message.answer("⚠️ Введи положительное число минут.")
        return

    data = await state.get_data()
    exercise = data.get("exercise")
    user_id = str(message.from_user.id)
    calories = calculate_workout_calories(user_id, exercise, "Минуты", minutes)
    WorkoutRepository.save_workout(
        user_id=user_id,
        exercise=exercise,
        count=minutes,
        entry_date=date.today(),
        variant="Минуты",
        calories=calories,
    )
    await state.clear()
    await message.answer(
        f"✅ Записал!\n💪 {exercise}\n⏱ {minutes} мин\n🔥 ~{calories:.0f}\n📅 сегодня",
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
            push_menu_stack(message.bot, exercise_picker_menu)
            await message.answer("Выбери упражнение:", reply_markup=exercise_picker_menu)
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
            push_menu_stack(message.bot, exercise_picker_menu)
            await message.answer("Выбери упражнение:", reply_markup=exercise_picker_menu)
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
    
    if message.text == "⬅️ Назад":
        # Возвращаемся к выбору упражнения
        await state.set_state(WorkoutStates.choosing_exercise)
        push_menu_stack(message.bot, exercise_picker_menu)
        await message.answer("Выбери упражнение:", reply_markup=exercise_picker_menu)
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
    
    if message.text == "✅ Завершить упражнение":
        # Завершаем и возвращаемся в меню
        await state.clear()
        from utils.progress_formatters import format_today_workouts_block

        workouts_text = format_today_workouts_block(user_id, include_date=False)
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
    entry_date_str = data.get("entry_date", date.today().isoformat())
    
    # Проверяем, что данные есть
    if not exercise:
        logger.error(f"User {user_id}: missing exercise or variant in state. Data: {data}")
        await message.answer("❌ Ошибка: данные потеряны. Начни добавление тренировки заново.")
        await state.clear()
        push_menu_stack(message.bot, training_menu)
        await message.answer("Выбери действие:", reply_markup=training_menu)
        return
    
    if isinstance(entry_date_str, str):
        try:
            entry_date = date.fromisoformat(entry_date_str)
        except ValueError:
            parsed = parse_date(entry_date_str)
            entry_date = parsed.date() if isinstance(parsed, datetime) else date.today()
    else:
        entry_date = date.today()
    
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
        f"Хотите ввести еще подход?",
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
