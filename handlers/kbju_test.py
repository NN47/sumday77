"""Обработчики для теста КБЖУ."""
import logging
from aiogram import Router
from aiogram.types import Message, CallbackQuery
from aiogram.fsm.context import FSMContext
from states.user_states import KbjuTestStates
from utils.keyboards import (
    kbju_gender_menu,
    kbju_age_range_inline,
    kbju_height_range_inline,
    kbju_activity_menu,
    kbju_goal_menu,
    kbju_goal_speed_loss_menu,
    kbju_goal_speed_gain_menu,
    kbju_menu,
    kbju_intro_menu,
    onboarding_open_menu,
    push_menu_stack,
)
from services.nutrition_calculator import calculate_nutrition_profile
from database.repositories import MealRepository
from utils.formatters import (
    format_kbju_goal_text,
    format_current_kbju_goal,
    format_onboarding_finish_text,
    format_strategy_text,
)

logger = logging.getLogger(__name__)

router = Router()
BASE_TOTAL_STEPS = 6
TOTAL_STEPS_WITH_GOAL_SPEED = 7

BASE_STEP_BY_STATE = {
    KbjuTestStates.entering_gender.state: 1,
    KbjuTestStates.entering_age.state: 2,
    KbjuTestStates.entering_height.state: 3,
    KbjuTestStates.entering_weight.state: 4,
    KbjuTestStates.entering_goal.state: 5,
}

GOAL_SPEED_LABEL_TO_PERCENT = {
    "🌿 Мягко — 10%": 10,
    "⚖️ Стандарт — 15%": 15,
    "🔥 Быстро — 20%": 20,
    "🚀 Быстрее — 20%": 20,
    "Мягко — 10%": 10,
    "Стандарт — 15%": 15,
    "Быстро — 20%": 20,
    "Быстрее — 20%": 20,
}

AGE_MAP = {
    "under_18": 16,
    "18_24": 21,
    "25_29": 27,
    "30_34": 32,
    "35_39": 37,
    "40_44": 42,
    "45_49": 47,
    "50_54": 52,
    "55_59": 57,
    "60_64": 62,
    "65_plus": 68,
}

AGE_RANGE_LABELS = {
    "under_18": "до 18",
    "18_24": "18-24",
    "25_29": "25-29",
    "30_34": "30-34",
    "35_39": "35-39",
    "40_44": "40-44",
    "45_49": "45-49",
    "50_54": "50-54",
    "55_59": "55-59",
    "60_64": "60-64",
    "65_plus": "65+",
}

HEIGHT_MAP = {
    "under_150": 148,
    "151_155": 153,
    "156_160": 158,
    "161_165": 163,
    "166_170": 168,
    "171_175": 173,
    "176_180": 178,
    "181_185": 183,
    "186_190": 188,
    "191_195": 193,
    "196_plus": 198,
}

HEIGHT_RANGE_LABELS = {
    "under_150": "до 150",
    "151_155": "151-155",
    "156_160": "156-160",
    "161_165": "161-165",
    "166_170": "166-170",
    "171_175": "171-175",
    "176_180": "176-180",
    "181_185": "181-185",
    "186_190": "186-190",
    "191_195": "191-195",
    "196_plus": "196+",
}


def format_step_text(step: int, text: str) -> str:
    """Добавляет прогресс шага перед текстом вопроса."""
    return f"Шаг {step}/{BASE_TOTAL_STEPS}\n\n{text}"


def format_dynamic_step_text(step: int, total_steps: int, text: str) -> str:
    """Добавляет прогресс шага перед текстом вопроса с динамическим общим числом шагов."""
    return f"Шаг {step}/{total_steps}\n\n{text}"


def has_completed_kbju_test(user_id: str) -> bool:
    """Returns True if the user already has KBJU settings."""
    return MealRepository.get_kbju_settings(user_id) is not None


async def restart_required_kbju_test(message: Message, state: FSMContext):
    """Starts the mandatory KBJU onboarding test from the first step."""
    await state.clear()
    await state.update_data(required_onboarding=True)
    await state.set_state(KbjuTestStates.entering_gender)

    push_menu_stack(message.bot, kbju_gender_menu)
    await message.answer(
        "Для начала работы с ботом нужно пройти короткий стартовый тест КБЖУ.\n\n"
        + format_step_text(BASE_STEP_BY_STATE[KbjuTestStates.entering_gender.state], "Для начала укажи пол:"),
        reply_markup=kbju_gender_menu,
    )


@router.message(lambda m: m.text == "🎯 Цель / Норма КБЖУ")
async def show_kbju_goal(message: Message, state: FSMContext):
    """Показывает текущую цель КБЖУ и варианты её настройки."""
    user_id = str(message.from_user.id)
    logger.info(f"User {user_id} opened KBJU goal settings")

    await state.clear()
    settings = MealRepository.get_kbju_settings(user_id)

    if settings:
        text = format_current_kbju_goal(settings)
        text += "\n\nЕсли хочешь обновить норму, выбери удобный вариант 👇"
        push_menu_stack(message.bot, kbju_intro_menu)
        await message.answer(text, parse_mode="HTML", reply_markup=kbju_intro_menu)
        return

    await message.answer(
        "Привет! Давай настроим твою норму КБЖУ.\n"
        "После этого я смогу точнее показывать прогресс по питанию.\n\n"
        "Выбери удобный вариант 👇",
        reply_markup=kbju_intro_menu,
    )


@router.message(lambda m: m.text == "✅ Пройти быстрый тест КБЖУ")
async def start_kbju_test(message: Message, state: FSMContext):
    """Начинает тест КБЖУ."""
    user_id = str(message.from_user.id)
    logger.info(f"User {user_id} started KBJU test")
    
    await state.clear()
    await state.update_data(required_onboarding=False)
    await state.set_state(KbjuTestStates.entering_gender)
    
    push_menu_stack(message.bot, kbju_gender_menu)
    await message.answer(
        format_step_text(BASE_STEP_BY_STATE[KbjuTestStates.entering_gender.state], "Для начала выбери пол:"),
        reply_markup=kbju_gender_menu,
    )


@router.message(lambda m: m.text == "✏️ Ввести свою норму")
async def start_manual_kbju_goal(message: Message, state: FSMContext):
    """Запускает ручной ввод нормы КБЖУ."""
    user_id = str(message.from_user.id)
    logger.info(f"User {user_id} started manual KBJU setup")

    await state.clear()
    await state.set_state(KbjuTestStates.entering_manual_calories)

    push_menu_stack(message.bot, kbju_intro_menu)
    await message.answer(
        "Давай настроим норму вручную.\n\n"
        "Введи цель по калориям в день (ккал), например: 2200"
    )


@router.message(KbjuTestStates.entering_gender)
async def handle_kbju_test_gender(message: Message, state: FSMContext):
    """Обрабатывает выбор пола в тесте КБЖУ."""
    user_id = str(message.from_user.id)
    txt = message.text.strip()

    if txt == "🙋‍♂️ Мужчина":
        gender = "male"
    elif txt == "🙋‍♀️ Женщина":
        gender = "female"
    else:
        await message.answer("Пожалуйста, выбери вариант с кнопки 🙂")
        return

    await state.update_data(gender=gender)
    await state.set_state(KbjuTestStates.entering_age)
    await message.answer(
        format_step_text(BASE_STEP_BY_STATE[KbjuTestStates.entering_age.state], "Выбери возрастную группу:"),
        reply_markup=kbju_age_range_inline,
    )


@router.message(KbjuTestStates.entering_age)
async def handle_kbju_test_age_text(message: Message):
    """Подсказывает, что возраст выбирается только через inline-кнопки."""
    await message.answer("Выбери возрастную группу кнопкой ниже 👇", reply_markup=kbju_age_range_inline)


def get_age_data(age_key: str) -> tuple[str, int] | None:
    """Возвращает отображаемый диапазон и возраст для расчёта."""
    age = AGE_MAP.get(age_key)
    age_range = AGE_RANGE_LABELS.get(age_key)
    if age is None or age_range is None:
        return None
    return age_range, age


@router.callback_query(
    KbjuTestStates.entering_age,
    lambda c: c.data is not None and c.data.startswith("kbju_age:")
)
async def handle_kbju_test_age_callback(callback: CallbackQuery, state: FSMContext):
    """Обрабатывает выбор возрастного диапазона в тесте КБЖУ."""
    age_key = callback.data.split(":", maxsplit=1)[1]
    age_data = get_age_data(age_key)
    if age_data is None:
        await callback.answer("Не удалось определить возрастную группу", show_alert=True)
        return

    age_range, age = age_data
    await state.update_data(age_range=age_range, age=age)
    await state.set_state(KbjuTestStates.entering_height)
    await callback.answer()
    await callback.message.answer(
        format_step_text(BASE_STEP_BY_STATE[KbjuTestStates.entering_height.state], "Выбери диапазон роста:"),
        reply_markup=kbju_height_range_inline,
    )


@router.message(KbjuTestStates.entering_height)
async def handle_kbju_test_height_text(message: Message):
    """Подсказывает, что рост выбирается только через inline-кнопки."""
    await message.answer("Выбери диапазон роста кнопкой ниже 👇", reply_markup=kbju_height_range_inline)


def get_height_data(height_key: str) -> tuple[str, int] | None:
    """Возвращает отображаемый диапазон и рост для расчёта."""
    height = HEIGHT_MAP.get(height_key)
    height_range = HEIGHT_RANGE_LABELS.get(height_key)
    if height is None or height_range is None:
        return None
    return height_range, height


@router.callback_query(
    KbjuTestStates.entering_height,
    lambda c: c.data is not None and c.data.startswith("kbju_height:")
)
async def handle_kbju_test_height_callback(callback: CallbackQuery, state: FSMContext):
    """Обрабатывает выбор диапазона роста в тесте КБЖУ."""
    height_key = callback.data.split(":", maxsplit=1)[1]
    height_data = get_height_data(height_key)
    if height_data is None:
        await callback.answer("Не удалось определить диапазон роста", show_alert=True)
        return

    height_range, height = height_data
    await state.update_data(height_range=height_range, height=height)
    await state.set_state(KbjuTestStates.entering_weight)
    await callback.answer()
    await callback.message.answer(
        format_step_text(
            BASE_STEP_BY_STATE[KbjuTestStates.entering_weight.state],
            "Сколько ты весишь сейчас? В кг (например: 86.5)",
        )
    )


@router.message(KbjuTestStates.entering_weight)
async def handle_kbju_test_weight(message: Message, state: FSMContext):
    """Обрабатывает ввод веса в тесте КБЖУ."""
    try:
        weight = float(message.text.replace(",", "."))
        if weight <= 0 or weight > 500:
            raise ValueError
    except ValueError:
        await message.answer("Нужно ввести число от 1 до 500, попробуй ещё раз 🙂")
        return

    await state.update_data(weight=weight)
    await state.set_state(KbjuTestStates.entering_goal)

    push_menu_stack(message.bot, kbju_goal_menu)
    await message.answer(
        format_step_text(BASE_STEP_BY_STATE[KbjuTestStates.entering_goal.state], "Какая цель?"),
        reply_markup=kbju_goal_menu,
    )


@router.message(KbjuTestStates.entering_goal)
async def handle_kbju_test_goal(message: Message, state: FSMContext):
    """Обрабатывает выбор цели в тесте КБЖУ."""
    txt = message.text.strip()

    if txt in {"📉 Похудение", "Похудеть"}:
        goal = "loss"
    elif txt in {"⚖️ Поддержание", "Поддерживать"}:
        goal = "maintain"
    elif txt in {"💪 Набор массы", "Набрать"}:
        goal = "gain"
    else:
        await message.answer("Выбери вариант с кнопки, пожалуйста 🙂")
        return

    await state.update_data(goal=goal)

    if goal in {"loss", "gain"}:
        await state.set_state(KbjuTestStates.entering_goal_speed)
        speed_menu = kbju_goal_speed_loss_menu if goal == "loss" else kbju_goal_speed_gain_menu

        push_menu_stack(message.bot, speed_menu)
        await message.answer(
            format_dynamic_step_text(
                step=6,
                total_steps=TOTAL_STEPS_WITH_GOAL_SPEED,
                text="Какой темп тебе комфортнее?",
            ),
            reply_markup=speed_menu,
        )
        return

    await state.update_data(goal_speed_label="Поддержание", goal_percent=0)
    await state.set_state(KbjuTestStates.entering_activity)

    push_menu_stack(message.bot, kbju_activity_menu)
    await message.answer(
        format_dynamic_step_text(
            step=6,
            total_steps=BASE_TOTAL_STEPS,
            text="Опиши свой обычный уровень активности:",
        ),
        reply_markup=kbju_activity_menu,
    )


@router.message(KbjuTestStates.entering_goal_speed)
async def handle_kbju_test_goal_speed(message: Message, state: FSMContext):
    """Обрабатывает выбор темпа изменения веса в тесте КБЖУ."""
    speed_label = message.text.strip()
    goal_percent = GOAL_SPEED_LABEL_TO_PERCENT.get(speed_label)

    if goal_percent is None:
        await message.answer("Выбери вариант с кнопки, пожалуйста 🙂")
        return

    await state.update_data(goal_speed_label=speed_label, goal_percent=goal_percent)
    await state.set_state(KbjuTestStates.entering_activity)

    push_menu_stack(message.bot, kbju_activity_menu)
    await message.answer(
        format_dynamic_step_text(
            step=7,
            total_steps=TOTAL_STEPS_WITH_GOAL_SPEED,
            text="Опиши свой обычный уровень активности:",
        ),
        reply_markup=kbju_activity_menu,
    )


@router.message(KbjuTestStates.entering_activity)
async def handle_kbju_test_activity(message: Message, state: FSMContext):
    """Обрабатывает выбор активности в тесте КБЖУ и сохраняет настройки."""
    user_id = str(message.from_user.id)
    txt = message.text.strip()

    if txt == "🪑 Мало движения":
        activity = "low"
    elif txt == "🚶 Умеренная активность":
        activity = "medium"
    elif txt == "🏋️ Тренировки 3–5 раз/нед":
        activity = "high"
    else:
        await message.answer("Выбери вариант с кнопки, пожалуйста 🙂")
        return

    # Получаем все данные из FSM
    data = await state.get_data()
    data["activity"] = activity
    goal = data.get("goal")
    if goal not in {"loss", "maintain", "gain"}:
        await state.clear()
        await message.answer("Не удалось определить цель. Давай начнём тест заново 🙂")
        return
    required_onboarding = bool(data.get("required_onboarding"))
    
    # Рассчитываем КБЖУ
    profile = calculate_nutrition_profile(data)

    # Сохраняем настройки
    MealRepository.save_kbju_settings(
        user_id=user_id,
        calories=profile.target_calories,
        protein=profile.proteins,
        fat=profile.fats,
        carbs=profile.carbs,
        goal=goal,
        activity=activity,
    )

    await state.clear()

    # Форматируем и отправляем результат
    text = format_kbju_goal_text(
        calories=profile.target_calories,
        protein=profile.proteins,
        fat=profile.fats,
        carbs=profile.carbs,
        goal_label=profile.goal_label,
        bmr_calories=profile.bmr,
        maintenance_calories=profile.tdee,
        goal_explanation=profile.goal_explanation,
    )
    text += (
        "\n\n"
        + format_strategy_text(
            calories=profile.target_calories,
            protein=profile.proteins,
            fat=profile.fats,
            carbs=profile.carbs,
            goal=goal,
        )
    )
    
    await message.answer(text, parse_mode="HTML")
    if required_onboarding:
        push_menu_stack(message.bot, onboarding_open_menu)
        await message.answer(
            format_onboarding_finish_text(),
            reply_markup=onboarding_open_menu,
        )
    else:
        push_menu_stack(message.bot, kbju_menu)
        await message.answer("Теперь можешь пользоваться разделом КБЖУ 👇", reply_markup=kbju_menu)


@router.message(KbjuTestStates.entering_manual_calories)
async def handle_manual_calories(message: Message, state: FSMContext):
    """Обрабатывает ручной ввод калорий."""
    try:
        calories = float(message.text.replace(",", "."))
        if calories <= 0 or calories > 10000:
            raise ValueError
    except ValueError:
        await message.answer("Нужно ввести число от 1 до 10000, попробуй ещё раз 🙂")
        return

    await state.update_data(calories=calories)
    await state.set_state(KbjuTestStates.entering_manual_protein)
    await message.answer("Теперь укажи белки в граммах (например: 130)")


@router.message(KbjuTestStates.entering_manual_protein)
async def handle_manual_protein(message: Message, state: FSMContext):
    """Обрабатывает ручной ввод белков."""
    try:
        protein = float(message.text.replace(",", "."))
        if protein <= 0 or protein > 1000:
            raise ValueError
    except ValueError:
        await message.answer("Нужно ввести число от 1 до 1000, попробуй ещё раз 🙂")
        return

    await state.update_data(protein=protein)
    await state.set_state(KbjuTestStates.entering_manual_fat)
    await message.answer("Укажи жиры в граммах (например: 70)")


@router.message(KbjuTestStates.entering_manual_fat)
async def handle_manual_fat(message: Message, state: FSMContext):
    """Обрабатывает ручной ввод жиров."""
    try:
        fat = float(message.text.replace(",", "."))
        if fat <= 0 or fat > 1000:
            raise ValueError
    except ValueError:
        await message.answer("Нужно ввести число от 1 до 1000, попробуй ещё раз 🙂")
        return

    await state.update_data(fat=fat)
    await state.set_state(KbjuTestStates.entering_manual_carbs)
    await message.answer("И последний шаг: укажи углеводы в граммах (например: 220)")


@router.message(KbjuTestStates.entering_manual_carbs)
async def handle_manual_carbs(message: Message, state: FSMContext):
    """Обрабатывает ручной ввод углеводов и сохраняет норму."""
    user_id = str(message.from_user.id)

    try:
        carbs = float(message.text.replace(",", "."))
        if carbs <= 0 or carbs > 1000:
            raise ValueError
    except ValueError:
        await message.answer("Нужно ввести число от 1 до 1000, попробуй ещё раз 🙂")
        return

    data = await state.get_data()
    calories = data.get("calories")
    protein = data.get("protein")
    fat = data.get("fat")

    MealRepository.save_kbju_settings(
        user_id=user_id,
        calories=calories,
        protein=protein,
        fat=fat,
        carbs=carbs,
        goal="custom",
    )

    await state.clear()

    text = format_kbju_goal_text(calories, protein, fat, carbs, "✍️ Своя норма")
    push_menu_stack(message.bot, kbju_menu)
    await message.answer(text, parse_mode="HTML")
    await message.answer("Готово! Ручная норма сохранена ✅", reply_markup=kbju_menu)


def register_kbju_test_handlers(dp):
    """Регистрирует обработчики теста КБЖУ."""
    dp.include_router(router)
