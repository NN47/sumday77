"""Обработчики для теста КБЖУ."""
import logging
from aiogram import Router
from aiogram.types import Message
from aiogram.fsm.context import FSMContext
from states.user_states import KbjuTestStates
from utils.keyboards import (
    kbju_gender_menu,
    kbju_activity_menu,
    kbju_goal_menu,
    kbju_menu,
    kbju_intro_menu,
    push_menu_stack,
)
from services.nutrition_calculator import calculate_nutrition_profile
from database.repositories import MealRepository
from utils.formatters import format_kbju_goal_text, format_current_kbju_goal

logger = logging.getLogger(__name__)

router = Router()


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
        "Для начала укажи пол:",
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
        "Для начала выбери пол:",
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
    await message.answer("Сколько тебе лет? (например: 28)")


@router.message(KbjuTestStates.entering_age)
async def handle_kbju_test_age(message: Message, state: FSMContext):
    """Обрабатывает ввод возраста в тесте КБЖУ."""
    try:
        age = float(message.text.replace(",", "."))
        if age <= 0 or age > 150:
            raise ValueError
    except ValueError:
        await message.answer("Нужно ввести число от 1 до 150, попробуй ещё раз 🙂")
        return

    await state.update_data(age=age)
    await state.set_state(KbjuTestStates.entering_height)
    await message.answer("Какой у тебя рост в сантиметрах? (например: 171)")


@router.message(KbjuTestStates.entering_height)
async def handle_kbju_test_height(message: Message, state: FSMContext):
    """Обрабатывает ввод роста в тесте КБЖУ."""
    try:
        height = float(message.text.replace(",", "."))
        if height <= 0 or height > 300:
            raise ValueError
    except ValueError:
        await message.answer("Нужно ввести число от 1 до 300, попробуй ещё раз 🙂")
        return

    await state.update_data(height=height)
    await state.set_state(KbjuTestStates.entering_weight)
    await message.answer("Сколько ты весишь сейчас? В кг (например: 86.5)")


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
    await state.set_state(KbjuTestStates.entering_activity)
    
    push_menu_stack(message.bot, kbju_activity_menu)
    await message.answer(
        "Опиши свой обычный уровень активности:",
        reply_markup=kbju_activity_menu,
    )


@router.message(KbjuTestStates.entering_activity)
async def handle_kbju_test_activity(message: Message, state: FSMContext):
    """Обрабатывает выбор активности в тесте КБЖУ."""
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

    await state.update_data(activity=activity)
    await state.set_state(KbjuTestStates.entering_goal)
    
    push_menu_stack(message.bot, kbju_goal_menu)
    await message.answer(
        "Какая у тебя сейчас цель?",
        reply_markup=kbju_goal_menu,
    )


@router.message(KbjuTestStates.entering_goal)
async def handle_kbju_test_goal(message: Message, state: FSMContext):
    """Обрабатывает выбор цели в тесте КБЖУ и сохраняет настройки."""
    user_id = str(message.from_user.id)
    txt = message.text.strip()

    if txt == "📉 Похудение":
        goal = "loss"
    elif txt == "⚖️ Поддержание":
        goal = "maintain"
    elif txt == "💪 Набор массы":
        goal = "gain"
    else:
        await message.answer("Выбери вариант с кнопки, пожалуйста 🙂")
        return

    # Получаем все данные из FSM
    data = await state.get_data()
    data["goal"] = goal
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
        activity=data.get("activity"),
    )

    await state.clear()

    # Форматируем и отправляем результат
    text = format_kbju_goal_text(
        calories=profile.target_calories,
        protein=profile.proteins,
        fat=profile.fats,
        carbs=profile.carbs,
        goal_label=profile.goal_label,
        maintenance_calories=profile.tdee,
    )
    
    await message.answer(text, parse_mode="HTML")
    if required_onboarding:
        from utils.keyboards import main_menu

        push_menu_stack(message.bot, main_menu)
        await message.answer(
            "Тест завершён. Теперь можешь пользоваться ботом через главное меню 👇",
            reply_markup=main_menu,
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
