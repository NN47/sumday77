"""Обработчики для КБЖУ и питания."""
import logging
import json
import re
from datetime import date
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.exceptions import TelegramBadRequest
from typing import Optional
from aiogram.fsm.context import FSMContext
from states.user_states import MealEntryStates
from utils.keyboards import (
    MAIN_MENU_BUTTON_ALIASES,
    MEALS_BUTTON_TEXT,
    LEGACY_MEALS_BUTTON_TEXT,
    kbju_menu,
    kbju_add_menu,
    kbju_meal_type_menu,
    kbju_after_meal_menu,
    kbju_edit_type_menu,
    push_menu_stack,
)
from database.repositories import MealRepository, AnalyticsRepository
from services.nutrition_service import nutrition_service
from services.gemini_service import gemini_service
from utils.validators import parse_date
from utils.telegram_text import split_telegram_message
from datetime import datetime
from utils.meal_types import MealType, MEAL_TYPE_ORDER, normalize_meal_type, display_meal_type

logger = logging.getLogger(__name__)

router = Router()

MEAL_TYPE_BUTTONS = {
    "🍳 Завтрак": MealType.BREAKFAST.value,
    "🍲 Обед": MealType.LUNCH.value,
    "🍽 Ужин": MealType.DINNER.value,
    "🍎 Перекус": MealType.SNACK.value,
}

ADD_METHOD_TEXTS = {
    "calorieninjas": "➕ Через CalorieNinjas",
    "ai": "📝 Ввести приём пищи текстом (AI-анализ)",
    "photo": "📷 Анализ еды по фото",
    "label": "📋 Анализ этикетки",
    "barcode": "📷 Скан штрих-кода",
}


def reset_user_state(message: Message, *, keep_supplements: bool = False):
    """Сбрасывает состояние пользователя."""
    # TODO: Заменить на FSM clear
    pass


def translate_text(text: str, source_lang: str = "ru", target_lang: str = "en") -> str:
    """Переводит текст через публичное API MyMemory."""
    if not text:
        return text
    
    try:
        import requests
        url = "https://api.mymemory.translated.net/get"
        params = {"q": text, "langpair": f"{source_lang}|{target_lang}"}
        response = requests.get(url, params=params, timeout=10)
        response.raise_for_status()
        data = response.json()
        translated = (
            data.get("responseData", {}).get("translatedText")
            or data.get("matches", [{}])[0].get("translation")
        )
        return translated or text
    except Exception as e:
        logger.warning(f"Translation error: {e}")
        return text


async def _prompt_meal_type_selection(message: Message, state: FSMContext, pending_add_method: str | None = None):
    """Просит выбрать тип приёма пищи и сохраняет контекст добавления."""
    payload = {"entry_date": date.today().isoformat()}
    if pending_add_method:
        payload["pending_add_method"] = pending_add_method
    await state.set_state(MealEntryStates.choosing_meal_type)
    await state.update_data(**payload)
    push_menu_stack(message.bot, kbju_meal_type_menu)
    await message.answer(
        "Сначала выбери, к какому приёму пищи добавить запись:",
        reply_markup=kbju_meal_type_menu,
    )


async def _ensure_meal_type_selected(
    message: Message,
    state: FSMContext,
    pending_add_method: str,
) -> bool:
    """Проверяет, что meal_type выбран; иначе запрашивает выбор."""
    data = await state.get_data()
    raw_meal_type = str(data.get("meal_type") or "").strip().lower()
    if raw_meal_type in {
        MealType.BREAKFAST.value,
        MealType.LUNCH.value,
        MealType.DINNER.value,
        MealType.SNACK.value,
    }:
        return True
    await _prompt_meal_type_selection(message, state, pending_add_method=pending_add_method)
    return False


@router.message(lambda m: m.text in {MEALS_BUTTON_TEXT, LEGACY_MEALS_BUTTON_TEXT})
async def calories(message: Message, state: FSMContext):
    """Показывает меню КБЖУ."""
    user_id = str(message.from_user.id)
    logger.info(f"User {user_id} opened KBJU menu")
    AnalyticsRepository.track_event(user_id, "open_kbju", section="kbju")
    await state.clear()  # Очищаем FSM состояние
    
    # Показываем прогресс КБЖУ
    from utils.progress_formatters import format_progress_block
    progress_text = format_progress_block(user_id)
    
    push_menu_stack(message.bot, kbju_menu)
    await message.answer(
        f"{progress_text}\n\nВыбери действие:",
        reply_markup=kbju_menu,
        parse_mode="HTML",
    )


@router.message(lambda m: m.text == "🍱 Быстрый перекус")
async def quick_snack(message: Message, state: FSMContext):
    """Упрощённый вход в добавление перекуса через ИИ одним нажатием."""
    user_id = str(message.from_user.id)
    logger.info(f"User {user_id} used quick snack button")
    
    await state.update_data(meal_type=MealType.SNACK.value, pending_add_method=None, entry_date=date.today().isoformat())
    # Начинаем поток как для ИИ-ввода, но с более короткими подсказками под перекус
    await state.set_state(MealEntryStates.waiting_for_ai_food_input)
    
    text = (
        "🍱 Быстрый перекус\n\n"
        "Напиши коротко, чем перекусил(а) — одним сообщением.\n\n"
        "Примеры:\n"
        "• йогурт 150 г и горсть орехов\n"
        "• яблоко и протеиновый батончик\n"
        "• творог 100 г с ягодами\n\n"
        "Я оценю КБЖУ с помощью ИИ и добавлю это как приём пищи."
    )
    
    push_menu_stack(message.bot, kbju_add_menu)
    await message.answer(text, reply_markup=kbju_add_menu)


@router.callback_query(lambda c: c.data == "quick_snack")
async def quick_snack_cb(callback: CallbackQuery, state: FSMContext):
    """Упрощённый вход в добавление перекуса через ИИ по inline-кнопке."""
    await callback.answer()
    message = callback.message
    user_id = str(callback.from_user.id)
    logger.info(f"User {user_id} used quick snack inline button")
    
    await state.update_data(meal_type=MealType.SNACK.value, pending_add_method=None, entry_date=date.today().isoformat())
    await state.set_state(MealEntryStates.waiting_for_ai_food_input)
    
    text = (
        "🍱 Быстрый перекус\n\n"
        "Напиши коротко, чем перекусил(а) — одним сообщением.\n\n"
        "Примеры:\n"
        "• йогурт 150 г и горсть орехов\n"
        "• яблоко и протеиновый батончик\n"
        "• творог 100 г с ягодами\n\n"
        "Я оценю КБЖУ с помощью ИИ и добавлю это как приём пищи."
    )
    
    push_menu_stack(message.bot, kbju_add_menu)
    await message.answer(text, reply_markup=kbju_add_menu)


@router.callback_query(lambda c: c.data == "quick_meal_add")
async def quick_meal_add(callback: CallbackQuery, state: FSMContext):
    """Быстрый переход в добавление приёма пищи через inline-кнопку."""
    await callback.answer()
    reset_user_state(callback.message)
    await start_kbju_add_flow(callback.message, date.today(), state)


@router.message(lambda m: m.text == "🎯 Цель / Норма КБЖУ")
async def show_kbju_goal(message: Message, state: FSMContext):
    """Показывает текущую цель КБЖУ и варианты её настройки."""
    from utils.formatters import format_current_kbju_goal
    from utils.keyboards import kbju_intro_menu

    user_id = str(message.from_user.id)
    logger.info(f"User {user_id} opened KBJU goal settings")

    await state.clear()
    settings = MealRepository.get_kbju_settings(user_id)

    if settings:
        text = format_current_kbju_goal(settings)
        text += "\n\nВыбери, как хочешь обновить цель 👇"
        await message.answer(text, parse_mode="HTML", reply_markup=kbju_intro_menu)
        return

    await message.answer(
        "Цель по КБЖУ пока не настроена.\n"
        "Выбери удобный вариант: быстрый тест или ручной ввод 👇",
        reply_markup=kbju_intro_menu,
    )


@router.message(lambda m: m.text == "➕ Добавить")
async def calories_add(message: Message, state: FSMContext):
    """Начинает процесс добавления приёма пищи."""
    # Проверяем, что пользователь НЕ находится в состоянии редактирования добавок
    from states.user_states import SupplementStates
    current_state = await state.get_state()
    
    # Если пользователь редактирует время добавки, не обрабатываем эту кнопку здесь
    # В aiogram get_state() возвращает строку вида "SupplementStates:entering_time"
    # Сравниваем строки, используя строковое представление состояния
    if current_state and "entering_time" in str(current_state) and "SupplementStates" in str(current_state):
        return  # Пропускаем обработку, чтобы более специфичный обработчик в supplements.py мог обработать
    
    reset_user_state(message)
    await start_kbju_add_flow(message, date.today(), state)


async def start_kbju_add_flow(message: Message, entry_date: date, state: FSMContext):
    """Запускает поток добавления приёма пищи."""
    _ = str(message.from_user.id)
    await state.set_state(MealEntryStates.choosing_meal_type)
    await state.update_data(entry_date=entry_date.isoformat(), pending_add_method=None)
    push_menu_stack(message.bot, kbju_meal_type_menu)
    await message.answer(
        "Выбери приём пищи, к которому нужно добавить продукты:",
        reply_markup=kbju_meal_type_menu,
    )


@router.message(MealEntryStates.choosing_meal_type, lambda m: m.text in MEAL_TYPE_BUTTONS)
async def select_meal_type(message: Message, state: FSMContext):
    """Сохраняет выбранный тип приёма пищи и продолжает сценарий добавления."""
    meal_type = MEAL_TYPE_BUTTONS[message.text]
    data = await state.get_data()
    pending_method = data.get("pending_add_method")
    await state.update_data(meal_type=meal_type)

    if pending_method in ADD_METHOD_TEXTS:
        await message.answer(f"Выбрано: {display_meal_type(meal_type)}")
        if pending_method == "ai":
            await kbju_add_via_ai(message, state)
            return
        if pending_method == "photo":
            await kbju_add_via_photo(message, state)
            return
        if pending_method == "label":
            await kbju_add_via_label(message, state)
            return
        if pending_method == "barcode":
            await kbju_add_via_barcode(message, state)
            return
        if pending_method == "calorieninjas":
            await kbju_add_via_calorieninjas(message, state)
            return

    text = (
        f"Отлично! {display_meal_type(meal_type)}.\n\n"
        "<b>Теперь выбери, как добавить еду:</b>\n"
        "• 📝 Ввести приём пищи текстом (AI-анализ)\n"
        "• 📷 Анализ еды по фото\n"
        "• 📋 Анализ этикетки"
    )
    push_menu_stack(message.bot, kbju_add_menu)
    await message.answer(text, reply_markup=kbju_add_menu, parse_mode="HTML")


@router.message(lambda m: m.text == "➕ Через CalorieNinjas")
async def kbju_add_via_calorieninjas(message: Message, state: FSMContext):
    """Обработчик добавления через CalorieNinjas."""
    if not await _ensure_meal_type_selected(message, state, "calorieninjas"):
        return
    await state.update_data(pending_add_method=None)
    await state.set_state(MealEntryStates.waiting_for_food_input)
    
    text = (
        "Напиши, что ты съел(а) одним сообщением.\n\n"
        "Например:\n"
        "• 100 г овсянки, 2 яйца, 1 банан\n"
        "• 150 г куриной грудки и 200 г риса\n\n"
        "Важно: сначала указывай количество (например: 100 г или 2 шт), "
        "а после — сам продукт."
    )
    
    push_menu_stack(message.bot, kbju_add_menu)
    await message.answer(text, reply_markup=kbju_add_menu)


@router.message(lambda m: m.text == "📝 Ввести приём пищи текстом (AI-анализ)")
async def kbju_add_via_ai(message: Message, state: FSMContext):
    """Обработчик добавления через Gemini AI."""
    if not await _ensure_meal_type_selected(message, state, "ai"):
        return
    await state.update_data(pending_add_method=None)
    await state.set_state(MealEntryStates.waiting_for_ai_food_input)
    
    text = (
        "📝 Ввести приём пищи текстом (AI-анализ)\n\n"
        "Просто напиши обычным человеческим языком, что ты съел — бот сам разберётся и посчитает КБЖУ 🤖\n\n"
        "Можно писать как удобно:\n\n"
        "✔ Список продуктов\n"
        "200 г курицы, 100 г йогурта, 30 г орехов\n\n"
        "✔ Описание блюда\n"
        "Я приготовил запеканку: творог 500 г, 3 яйца, 2 ложки сметаны, 3 ложки муки, 1 мерный стакан протеина. Съел 1/3 от неё\n\n"
        "✔ Обычный разговорный текст\n"
        "Сделал бутерброд из хлеба, масла, огурца и колбасы, съел половину\n\n"
        "✔ Даже без точного веса\n"
        "Тарелка борща и кусок хлеба\n\n"
        "Бот сам:\n"
        " • распознает продукты\n"
        " • оценит примерный вес\n"
        " • посчитает калории, белки, жиры и углеводы"
    )
    
    push_menu_stack(message.bot, kbju_add_menu)
    await message.answer(text, reply_markup=kbju_add_menu)


@router.message(lambda m: m.text == "📷 Анализ еды по фото")
async def kbju_add_via_photo(message: Message, state: FSMContext):
    """Обработчик анализа еды по фото."""
    if not await _ensure_meal_type_selected(message, state, "photo"):
        return
    reset_user_state(message)
    await state.update_data(pending_add_method=None)
    await state.set_state(MealEntryStates.waiting_for_photo)
    
    text = (
        "📷 Анализ еды по фото\n\n"
        "Отправь мне фото еды, и я определю КБЖУ с помощью ИИ! 🤖\n\n"
        "Сделай фото так, чтобы еда была хорошо видна на изображении."
    )
    
    push_menu_stack(message.bot, kbju_add_menu)
    await message.answer(text, reply_markup=kbju_add_menu)


@router.message(MealEntryStates.waiting_for_food_input)
async def handle_food_input(message: Message, state: FSMContext):
    """Обрабатывает ввод текста для CalorieNinjas."""
    user_text = message.text.strip()
    if not user_text:
        await message.answer("Напиши, пожалуйста, что ты съел(а) 🙏")
        return
    
    user_id = str(message.from_user.id)
    data = await state.get_data()
    meal_type = normalize_meal_type(data.get("meal_type"), fallback=MealType.SNACK.value)
    entry_date_str = data.get("entry_date")
    if entry_date_str:
        if isinstance(entry_date_str, str):
            try:
                entry_date = date.fromisoformat(entry_date_str)
            except ValueError:
                parsed = parse_date(entry_date_str)
                entry_date = parsed.date() if isinstance(parsed, datetime) else date.today()
        else:
            entry_date = date.today()
    else:
        entry_date = date.today()
    
    translated_query = translate_text(user_text, source_lang="ru", target_lang="en")
    logger.info(f"🍱 Перевод запроса для API: {translated_query}")
    
    try:
        items, totals = nutrition_service.get_nutrition_from_api(translated_query)
    except Exception as e:
        logger.error(f"Nutrition API error: {e}")
        await message.answer(
            "⚠️ Не получилось получить КБЖУ из сервиса.\n"
            "Попробуй ещё раз чуть позже или измени формулировку."
        )
        return
    
    if not items:
        await message.answer(
            "Я не нашёл продукты в этом описании 🤔\n"
            "Попробуй написать чуть по-другому: добавь количество или уточни продукт."
        )
        return
    
    # Формируем детали для сохранения
    lines = ["🍱 Оценка по КБЖУ для этого приёма пищи:\n"]
    api_details_lines = []
    
    for item in items:
        name_en = (item.get("name") or "item").title()
        name = translate_text(name_en, source_lang="en", target_lang="ru")
        
        cal = float(item.get("_calories", 0.0))
        p = float(item.get("_protein_g", 0.0))
        f = float(item.get("_fat_total_g", 0.0))
        c = float(item.get("_carbohydrates_total_g", 0.0))
        
        line = f"• {name} — {cal:.0f} ккал (Б {p:.1f} / Ж {f:.1f} / У {c:.1f})"
        lines.append(line)
        api_details_lines.append(line)
    
    lines.append("\nИТОГО:")
    lines.append(
        f"🔥 Калории: {float(totals['calories']):.0f} ккал\n"
        f"💪 Белки: {float(totals['protein_g']):.1f} г\n"
        f"🥑 Жиры: {float(totals['fat_total_g']):.1f} г\n"
        f"🍩 Углеводы: {float(totals['carbohydrates_total_g']):.1f} г"
    )
    
    api_details = "\n".join(api_details_lines)
    
    # Сохраняем в БД
    saved_meal = MealRepository.save_meal(
        user_id=user_id,
        raw_query=user_text,
        calories=float(totals['calories']),
        protein=float(totals['protein_g']),
        fat=float(totals['fat_total_g']),
        carbs=float(totals['carbohydrates_total_g']),
        entry_date=entry_date,
        api_details=api_details,
        products_json=json.dumps(items),
        meal_type=meal_type,
    )
    
    # Сохраняем ID последнего приёма для редактирования
    if not hasattr(message.bot, "last_meal_ids"):
        message.bot.last_meal_ids = {}
    message.bot.last_meal_ids[user_id] = saved_meal.id
    
    # Показываем суммарные данные за день
    daily_totals = MealRepository.get_daily_totals(user_id, entry_date)
    lines.append("\nСУММА ЗА СЕГОДНЯ:")
    lines.append(
        f"🔥 Калории: {daily_totals['calories']:.0f} ккал\n"
        f"💪 Белки: {daily_totals.get('protein_g', daily_totals.get('protein', 0)):.1f} г\n"
        f"🥑 Жиры: {daily_totals.get('fat_total_g', daily_totals.get('fat', 0)):.1f} г\n"
        f"🍩 Углеводы: {daily_totals.get('carbohydrates_total_g', daily_totals.get('carbs', 0)):.1f} г"
    )
    
    await state.clear()
    push_menu_stack(message.bot, kbju_after_meal_menu)
    await message.answer("\n".join(lines), reply_markup=kbju_after_meal_menu)


@router.message(MealEntryStates.waiting_for_ai_food_input)
async def handle_ai_food_input(message: Message, state: FSMContext):
    """Обрабатывает ввод текста для Gemini AI."""
    user_text = message.text.strip()
    if not user_text:
        await message.answer("Напиши, пожалуйста, что ты съел(а) 🙏")
        return
    
    user_id = str(message.from_user.id)
    data = await state.get_data()
    meal_type = normalize_meal_type(data.get("meal_type"), fallback=MealType.SNACK.value)
    entry_date_str = data.get("entry_date")
    if entry_date_str:
        if isinstance(entry_date_str, str):
            try:
                entry_date = date.fromisoformat(entry_date_str)
            except ValueError:
                parsed = parse_date(entry_date_str)
                entry_date = parsed.date() if isinstance(parsed, datetime) else date.today()
        else:
            entry_date = date.today()
    else:
        entry_date = date.today()
    
    # Показываем сообщение об анализе
    await message.answer("🤖 Считаю КБЖУ с помощью ИИ, секунду...")
    
    # Получаем КБЖУ через Gemini
    kbju_data = gemini_service.estimate_kbju(user_text)
    
    if not kbju_data or "total" not in kbju_data:
        await message.answer(
            "⚠️ Не получилось определить КБЖУ.\n"
            "Попробуй ещё раз или используй другой способ добавления."
        )
        return
    
    items = kbju_data.get("items", [])
    total = kbju_data.get("total", {})
    
    # Безопасное преобразование значений
    def safe_float(value) -> float:
        try:
            if value is None:
                return 0.0
            return float(value)
        except (TypeError, ValueError):
            return 0.0
    
    # Формируем детальный ответ
    lines = ["🤖 Оценка по ИИ для этого приёма пищи:\n"]
    
    totals_for_db = {
        "calories": safe_float(total.get("kcal")),
        "protein": safe_float(total.get("protein")),
        "fat": safe_float(total.get("fat")),
        "carbs": safe_float(total.get("carbs")),
    }
    
    # Показываем каждый продукт
    for item in items:
        name = item.get("name") or "продукт"
        grams = safe_float(item.get("grams"))
        cal = safe_float(item.get("kcal"))
        p = safe_float(item.get("protein"))
        f = safe_float(item.get("fat"))
        c = safe_float(item.get("carbs"))
        
        lines.append(
            f"• {name} ({grams:.0f} г) — {cal:.0f} ккал (Б {p:.1f} / Ж {f:.1f} / У {c:.1f})"
        )
    
    lines.append("\nИТОГО:")
    lines.append(
        f"🔥 Калории: {totals_for_db['calories']:.0f} ккал\n"
        f"💪 Белки: {totals_for_db['protein']:.1f} г\n"
        f"🥑 Жиры: {totals_for_db['fat']:.1f} г\n"
        f"🍩 Углеводы: {totals_for_db['carbs']:.1f} г"
    )
    
    # Сохраняем в БД
    saved_meal = MealRepository.save_meal(
        user_id=user_id,
        raw_query=user_text,
        calories=totals_for_db["calories"],
        protein=totals_for_db["protein"],
        fat=totals_for_db["fat"],
        carbs=totals_for_db["carbs"],
        entry_date=entry_date,
        products_json=json.dumps(items),
        meal_type=meal_type,
    )
    
    # Сохраняем ID последнего приёма для редактирования
    if not hasattr(message.bot, "last_meal_ids"):
        message.bot.last_meal_ids = {}
    message.bot.last_meal_ids[user_id] = saved_meal.id
    
    # Показываем суммарные данные за день
    daily_totals = MealRepository.get_daily_totals(user_id, entry_date)
    lines.append("\nСУММА ЗА СЕГОДНЯ:")
    lines.append(
        f"🔥 Калории: {daily_totals.get('calories', 0):.0f} ккал\n"
        f"💪 Белки: {daily_totals.get('protein', 0):.1f} г\n"
        f"🥑 Жиры: {daily_totals.get('fat', 0):.1f} г\n"
        f"🍩 Углеводы: {daily_totals.get('carbs', 0):.1f} г"
    )
    
    await state.clear()
    push_menu_stack(message.bot, kbju_after_meal_menu)
    await message.answer("\n".join(lines), reply_markup=kbju_after_meal_menu)


@router.message(lambda m: m.text == "📋 Анализ этикетки")
async def kbju_add_via_label(message: Message, state: FSMContext):
    """Обработчик анализа этикетки."""
    if not await _ensure_meal_type_selected(message, state, "label"):
        return
    reset_user_state(message)
    await state.update_data(pending_add_method=None)
    await state.set_state(MealEntryStates.waiting_for_label_photo)
    
    text = (
        "📋 Анализ этикетки/упаковки\n\n"
        "Отправь мне фото этикетки или упаковки продукта, и я найду КБЖУ в тексте! 📸\n\n"
        "Я прочитаю информацию о пищевой ценности и извлеку точные данные о калориях, белках, жирах и углеводах.\n\n"
        "Если на этикетке указан вес упаковки — использую его автоматически. "
        "Если нет — спрошу у тебя, сколько грамм ты съел(а)."
    )
    
    push_menu_stack(message.bot, kbju_add_menu)
    await message.answer(text, reply_markup=kbju_add_menu)


@router.message(lambda m: m.text == "📷 Скан штрих-кода")
async def kbju_add_via_barcode(message: Message, state: FSMContext):
    """Обработчик сканирования штрих-кода."""
    if not await _ensure_meal_type_selected(message, state, "barcode"):
        return
    reset_user_state(message)
    await state.update_data(pending_add_method=None)
    await state.set_state(MealEntryStates.waiting_for_barcode_photo)
    
    text = (
        "📷 Сканирование штрих-кода\n\n"
        "Отправь мне фото штрих-кода продукта, и я найду информацию о нём в базе Open Food Facts! 📸\n\n"
        "Я распознаю штрих-код с помощью ИИ и получу точные данные о продукте: название, КБЖУ и другие факты."
    )
    
    push_menu_stack(message.bot, kbju_add_menu)
    await message.answer(text, reply_markup=kbju_add_menu)


@router.message(MealEntryStates.waiting_for_photo, F.photo)
async def handle_photo_input(message: Message, state: FSMContext):
    """Обрабатывает фото еды."""
    
    user_id = str(message.from_user.id)
    data = await state.get_data()
    meal_type = normalize_meal_type(data.get("meal_type"), fallback=MealType.SNACK.value)
    entry_date_str = data.get("entry_date")
    if entry_date_str:
        if isinstance(entry_date_str, str):
            try:
                entry_date = date.fromisoformat(entry_date_str)
            except ValueError:
                parsed = parse_date(entry_date_str)
                entry_date = parsed.date() if isinstance(parsed, datetime) else date.today()
        else:
            entry_date = date.today()
    else:
        entry_date = date.today()
    
    # Показываем сообщение об анализе
    await message.answer("📷 Анализирую фото с помощью ИИ, секунду... 🤖")
    
    # Скачиваем фото
    photo = message.photo[-1]  # Берём самое большое разрешение
    file = await message.bot.get_file(photo.file_id)
    image_bytes = await message.bot.download_file(file.file_path)
    image_data = image_bytes.read()
    
    # Анализируем через Gemini
    kbju_data = gemini_service.estimate_kbju_from_photo(image_data)
    
    if not kbju_data or "total" not in kbju_data:
        await message.answer(
            "⚠️ Не получилось определить КБЖУ по фото.\n"
            "Попробуй сделать фото получше или используй другой способ."
        )
        return
    
    items = kbju_data.get("items", [])
    total = kbju_data.get("total", {})
    
    # Безопасное преобразование значений
    def safe_float(value) -> float:
        try:
            if value is None:
                return 0.0
            return float(value)
        except (TypeError, ValueError):
            return 0.0
    
    # Формируем детальный ответ
    lines = ["📷 Анализ фото еды (ИИ):\n"]
    
    totals_for_db = {
        "calories": safe_float(total.get("kcal")),
        "protein": safe_float(total.get("protein")),
        "fat": safe_float(total.get("fat")),
        "carbs": safe_float(total.get("carbs")),
    }
    
    # Показываем каждый продукт
    for item in items:
        name = item.get("name") or "продукт"
        grams = safe_float(item.get("grams"))
        cal = safe_float(item.get("kcal"))
        p = safe_float(item.get("protein"))
        f = safe_float(item.get("fat"))
        c = safe_float(item.get("carbs"))
        
        lines.append(
            f"• {name} ({grams:.0f} г) — {cal:.0f} ккал (Б {p:.1f} / Ж {f:.1f} / У {c:.1f})"
        )
    
    lines.append("\nИТОГО:")
    lines.append(
        f"🔥 Калории: {totals_for_db['calories']:.0f} ккал\n"
        f"💪 Белки: {totals_for_db['protein']:.1f} г\n"
        f"🥑 Жиры: {totals_for_db['fat']:.1f} г\n"
        f"🍩 Углеводы: {totals_for_db['carbs']:.1f} г"
    )
    
    # Сохраняем в БД
    saved_meal = MealRepository.save_meal(
        user_id=user_id,
        raw_query="[Анализ по фото]",
        calories=totals_for_db["calories"],
        protein=totals_for_db["protein"],
        fat=totals_for_db["fat"],
        carbs=totals_for_db["carbs"],
        entry_date=entry_date,
        products_json=json.dumps(items),
        meal_type=meal_type,
    )
    
    # Сохраняем ID последнего приёма для редактирования
    if not hasattr(message.bot, "last_meal_ids"):
        message.bot.last_meal_ids = {}
    message.bot.last_meal_ids[user_id] = saved_meal.id
    
    # Показываем суммарные данные за день
    daily_totals = MealRepository.get_daily_totals(user_id, entry_date)
    lines.append("\nСУММА ЗА СЕГОДНЯ:")
    lines.append(
        f"🔥 Калории: {daily_totals.get('calories', 0):.0f} ккал\n"
        f"💪 Белки: {daily_totals.get('protein', 0):.1f} г\n"
        f"🥑 Жиры: {daily_totals.get('fat', 0):.1f} г\n"
        f"🍩 Углеводы: {daily_totals.get('carbs', 0):.1f} г"
    )
    
    await state.clear()
    push_menu_stack(message.bot, kbju_after_meal_menu)
    await message.answer("\n".join(lines), reply_markup=kbju_after_meal_menu)


@router.message(MealEntryStates.waiting_for_label_photo, F.photo)
async def handle_label_photo(message: Message, state: FSMContext):
    """Обрабатывает фото этикетки."""
    user_id = str(message.from_user.id)
    data = await state.get_data()
    meal_type = normalize_meal_type(data.get("meal_type"), fallback=MealType.SNACK.value)
    entry_date_str = data.get("entry_date")
    if entry_date_str:
        if isinstance(entry_date_str, str):
            try:
                entry_date = date.fromisoformat(entry_date_str)
            except ValueError:
                parsed = parse_date(entry_date_str)
                entry_date = parsed.date() if isinstance(parsed, datetime) else date.today()
        else:
            entry_date = date.today()
    else:
        entry_date = date.today()
    
    # Показываем сообщение об анализе
    await message.answer("📋 Анализирую этикетку с помощью ИИ, секунду... 🤖")
    
    # Скачиваем фото
    photo = message.photo[-1]
    file = await message.bot.get_file(photo.file_id)
    image_bytes = await message.bot.download_file(file.file_path)
    image_data = image_bytes.read()
    
    # Анализируем через Gemini
    label_data = gemini_service.extract_kbju_from_label(image_data)
    
    if not label_data or "kbju_per_100g" not in label_data:
        await message.answer(
            "⚠️ Не удалось найти КБЖУ на этикетке.\n"
            "Попробуй сделать фото более чётким или используй другой способ."
        )
        return
    
    kbju_per_100g = label_data["kbju_per_100g"]
    package_weight = label_data.get("package_weight")
    found_weight = label_data.get("found_weight", False)
    product_name = label_data.get("product_name", "Продукт")
    
    # Безопасное преобразование значений
    def safe_float(value) -> float:
        try:
            if value is None:
                return 0.0
            return float(value)
        except (TypeError, ValueError):
            return 0.0
    
    kcal_100g = safe_float(kbju_per_100g.get("kcal"))
    protein_100g = safe_float(kbju_per_100g.get("protein"))
    fat_100g = safe_float(kbju_per_100g.get("fat"))
    carbs_100g = safe_float(kbju_per_100g.get("carbs"))
    
    # Сохраняем данные в FSM для дальнейшего использования
    await state.set_state(MealEntryStates.waiting_for_weight_input)
    await state.update_data(
        kbju_per_100g=kbju_per_100g,
        product_name=product_name,
        entry_date=entry_date.isoformat(),
    )
    
    push_menu_stack(message.bot, kbju_add_menu)

    # Формируем сообщение в зависимости от того, найден ли вес
    if found_weight and package_weight is not None:
        weight = safe_float(package_weight)
        if weight > 0:
            await message.answer(
                f"✅ Нашёл КБЖУ на этикетке!\n\n"
                f"📦 Продукт: {product_name}\n"
                f"📊 КБЖУ на 100 г:\n"
                f"🔥 Калории: {kcal_100g:.0f} ккал\n"
                f"💪 Белки: {protein_100g:.1f} г\n"
                f"🥑 Жиры: {fat_100g:.1f} г\n"
                f"🍩 Углеводы: {carbs_100g:.1f} г\n\n"
                f"📦 В упаковке {weight:.0f} г, сколько Вы съели?",
                reply_markup=kbju_add_menu,
            )
        else:
            await message.answer(
                f"✅ Нашёл КБЖУ на этикетке!\n\n"
                f"📦 Продукт: {product_name}\n"
                f"📊 КБЖУ на 100 г:\n"
                f"🔥 Калории: {kcal_100g:.0f} ккал\n"
                f"💪 Белки: {protein_100g:.1f} г\n"
                f"🥑 Жиры: {fat_100g:.1f} г\n"
                f"🍩 Углеводы: {carbs_100g:.1f} г\n\n"
                f"❓ Вес в упаковке не найден, сколько вы съели?",
                reply_markup=kbju_add_menu,
            )
    else:
        await message.answer(
            f"✅ Нашёл КБЖУ на этикетке!\n\n"
            f"📦 Продукт: {product_name}\n"
            f"📊 КБЖУ на 100 г:\n"
            f"🔥 Калории: {kcal_100g:.0f} ккал\n"
            f"💪 Белки: {protein_100g:.1f} г\n"
            f"🥑 Жиры: {fat_100g:.1f} г\n"
            f"🍩 Углеводы: {carbs_100g:.1f} г\n\n"
            f"❓ Вес в упаковке не найден, сколько вы съели?",
            reply_markup=kbju_add_menu,
        )


@router.message(MealEntryStates.waiting_for_barcode_photo, F.photo)
async def handle_barcode_photo(message: Message, state: FSMContext):
    """Обрабатывает фото штрих-кода."""
    user_id = str(message.from_user.id)
    data = await state.get_data()
    entry_date_str = data.get("entry_date")
    if entry_date_str:
        if isinstance(entry_date_str, str):
            try:
                entry_date = date.fromisoformat(entry_date_str)
            except ValueError:
                parsed = parse_date(entry_date_str)
                entry_date = parsed.date() if isinstance(parsed, datetime) else date.today()
        else:
            entry_date = date.today()
    else:
        entry_date = date.today()
    
    # Показываем сообщение о распознавании
    await message.answer("📷 Распознаю штрих-код, секунду... 🤖")
    
    # Скачиваем фото
    photo = message.photo[-1]
    file = await message.bot.get_file(photo.file_id)
    image_bytes = await message.bot.download_file(file.file_path)
    image_data = image_bytes.read()
    
    # Распознаём штрих-код
    barcode = gemini_service.scan_barcode(image_data)
    
    if not barcode:
        await message.answer(
            "Не удалось распознать штрих-код на фото 😔\n\n"
            "Попробуй сделать фото ещё раз:\n"
            "• Убедись, что штрих-код хорошо виден\n"
            "• Сделай фото при хорошем освещении\n"
            "• Штрих-код должен быть в фокусе\n\n"
            "Или используй другие способы добавления КБЖУ."
        )
        return
    
    await message.answer(f"✅ Штрих-код распознан: {barcode}\n\n🔍 Ищу информацию о продукте...")
    
    # Получаем данные из Open Food Facts
    product_data = nutrition_service.get_product_from_openfoodfacts(barcode)
    
    if not product_data:
        await message.answer(
            f"❌ Продукт со штрих-кодом {barcode} не найден в базе Open Food Facts.\n\n"
            "Попробуй другой способ добавления КБЖУ или используй фото этикетки."
        )
        await state.clear()
        return
    
    # Формируем информацию о продукте
    product_name = product_data.get("name", "Неизвестный продукт")
    brand = product_data.get("brand", "")
    nutriments = product_data.get("nutriments", {})
    weight = product_data.get("weight")
    
    def safe_float(value) -> float:
        try:
            if value is None:
                return 0.0
            return float(value)
        except (TypeError, ValueError):
            return 0.0
    
    # КБЖУ на 100г
    kcal_100g = safe_float(nutriments.get("kcal", 0))
    protein_100g = safe_float(nutriments.get("protein", 0))
    fat_100g = safe_float(nutriments.get("fat", 0))
    carbs_100g = safe_float(nutriments.get("carbs", 0))
    
    # Проверяем, есть ли хотя бы какое-то КБЖУ
    if not (kcal_100g or protein_100g or fat_100g or carbs_100g):
        await message.answer(
            f"❌ В базе Open Food Facts нет информации о КБЖУ для продукта со штрих-кодом {barcode}.\n\n"
            "Попробуй использовать фото этикетки или другие способы добавления КБЖУ."
        )
        await state.clear()
        return
    
    # Сохраняем данные в FSM для дальнейшего использования
    await state.set_state(MealEntryStates.waiting_for_weight_input)
    await state.update_data(
        kbju_per_100g={
            "kcal": kcal_100g,
            "protein": protein_100g,
            "fat": fat_100g,
            "carbs": carbs_100g,
        },
        product_name=product_name,
        barcode=barcode,
        entry_date=entry_date.isoformat(),
    )
    
    # Формируем сообщение с информацией о продукте
    text_parts = [f"✅ Нашёл продукт в базе Open Food Facts!\n\n"]
    text_parts.append(f"📦 Продукт: <b>{product_name}</b>\n")
    
    if brand:
        text_parts.append(f"🏷 Бренд: {brand}\n")
    
    text_parts.append(f"🔢 Штрих-код: {barcode}\n")
    text_parts.append(f"\n📊 КБЖУ на 100 г:\n")
    text_parts.append(f"🔥 Калории: {kcal_100g:.0f} ккал\n")
    text_parts.append(f"💪 Белки: {protein_100g:.1f} г\n")
    text_parts.append(f"🥑 Жиры: {fat_100g:.1f} г\n")
    text_parts.append(f"🍩 Углеводы: {carbs_100g:.1f} г\n")
    
    # Если есть вес упаковки в базе, упоминаем его, но все равно спрашиваем
    if weight:
        text_parts.append(f"\n📦 В базе указан вес упаковки: {weight} г\n")
        text_parts.append(f"Сколько грамм вы съели? (можно ввести {weight} или другое значение)")
    else:
        text_parts.append(f"\n❓ Сколько грамм вы съели?")
    
    push_menu_stack(message.bot, kbju_add_menu)
    await message.answer("".join(text_parts), reply_markup=kbju_add_menu, parse_mode="HTML")


@router.message(MealEntryStates.waiting_for_weight_input)
async def handle_weight_input(message: Message, state: FSMContext):
    """Обрабатывает ввод веса для этикетки или штрих-кода."""
    if message.text == "⬅️ Назад":
        from handlers.common import go_back
        await go_back(message, state)
        return

    user_id = str(message.from_user.id)
    data = await state.get_data()
    meal_type = normalize_meal_type(data.get("meal_type"), fallback=MealType.SNACK.value)
    
    try:
        weight_grams = float(message.text.replace(",", "."))
        if weight_grams <= 0:
            raise ValueError
    except (ValueError, AttributeError):
        await message.answer("Вес должен быть больше нуля. Введи правильное число (например: 50 или 100):")
        return
    
    entry_date_str = data.get("entry_date")
    if entry_date_str:
        if isinstance(entry_date_str, str):
            try:
                entry_date = date.fromisoformat(entry_date_str)
            except ValueError:
                parsed = parse_date(entry_date_str)
                entry_date = parsed.date() if isinstance(parsed, datetime) else date.today()
        else:
            entry_date = date.today()
    else:
        entry_date = date.today()
    
    # Безопасное преобразование значений
    def safe_float(value) -> float:
        try:
            if value is None:
                return 0.0
            return float(value)
        except (TypeError, ValueError):
            return 0.0
    
    # Пересчитываем пропорционально указанному весу
    multiplier = weight_grams / 100.0
    
    # Определяем источник (этикетка или штрих-код)
    kbju_per_100g = data.get("kbju_per_100g")
    product_name = data.get("product_name", "Продукт")
    barcode = data.get("barcode")
    
    if kbju_per_100g:
        # Этикетка или штрих-код (оба используют kbju_per_100g)
        kcal_100g = safe_float(kbju_per_100g.get("kcal"))
        protein_100g = safe_float(kbju_per_100g.get("protein"))
        fat_100g = safe_float(kbju_per_100g.get("fat"))
        carbs_100g = safe_float(kbju_per_100g.get("carbs"))
        
        totals_for_db = {
            "calories": kcal_100g * multiplier,
            "protein": protein_100g * multiplier,
            "fat": fat_100g * multiplier,
            "carbs": carbs_100g * multiplier,
        }
        
        # Определяем источник по наличию barcode
        if barcode:
            lines = [f"📷 Сканирование штрих-кода: {product_name}\n"]
            raw_query = f"[Штрих-код: {barcode}] {product_name}"
        else:
            lines = [f"📋 Анализ этикетки: {product_name}\n"]
            raw_query = f"[Этикетка: {product_name}]"
    else:
        # Старый формат (для обратной совместимости)
        ratio = weight_grams / 100.0
        totals_for_db = {
            "calories": safe_float(data.get("kcal_per_100g", 0)) * ratio,
            "protein": safe_float(data.get("protein_per_100g", 0)) * ratio,
            "fat": safe_float(data.get("fat_per_100g", 0)) * ratio,
            "carbs": safe_float(data.get("carbs_per_100g", 0)) * ratio,
        }
        product_name = data.get("product_name", "Продукт")
        barcode = data.get("barcode", "")
        lines = [f"📷 Сканирование штрих-кода: {product_name}\n"]
        raw_query = f"[Штрих-код: {barcode}] {product_name}"
    
    lines.append(f"📦 Вес: {weight_grams:.0f} г\n")
    lines.append("КБЖУ:")
    lines.append(
        f"🔥 Калории: {totals_for_db['calories']:.0f} ккал\n"
        f"💪 Белки: {totals_for_db['protein']:.1f} г\n"
        f"🥑 Жиры: {totals_for_db['fat']:.1f} г\n"
        f"🍩 Углеводы: {totals_for_db['carbs']:.1f} г"
    )

    products_json = None
    if kbju_per_100g:
        kcal_100g = safe_float(kbju_per_100g.get("kcal"))
        protein_100g = safe_float(kbju_per_100g.get("protein"))
        fat_100g = safe_float(kbju_per_100g.get("fat"))
        carbs_100g = safe_float(kbju_per_100g.get("carbs"))

        products_json = json.dumps(
            [
                {
                    "name": product_name,
                    "grams": weight_grams,
                    "kcal": totals_for_db["calories"],
                    "protein": totals_for_db["protein"],
                    "fat": totals_for_db["fat"],
                    "carbs": totals_for_db["carbs"],
                    "calories": totals_for_db["calories"],
                    "protein_g": totals_for_db["protein"],
                    "fat_total_g": totals_for_db["fat"],
                    "carbohydrates_total_g": totals_for_db["carbs"],
                    "calories_per_100g": kcal_100g,
                    "protein_per_100g": protein_100g,
                    "fat_per_100g": fat_100g,
                    "carbs_per_100g": carbs_100g,
                }
            ]
        )
    
    # Сохраняем в БД
    saved_meal = MealRepository.save_meal(
        user_id=user_id,
        raw_query=raw_query,
        calories=totals_for_db["calories"],
        protein=totals_for_db["protein"],
        fat=totals_for_db["fat"],
        carbs=totals_for_db["carbs"],
        entry_date=entry_date,
        products_json=products_json,
        meal_type=meal_type,
    )
    
    # Сохраняем ID последнего приёма для редактирования
    if not hasattr(message.bot, "last_meal_ids"):
        message.bot.last_meal_ids = {}
    message.bot.last_meal_ids[user_id] = saved_meal.id
    
    # Показываем суммарные данные за день
    daily_totals = MealRepository.get_daily_totals(user_id, entry_date)
    lines.append("\nСУММА ЗА СЕГОДНЯ:")
    lines.append(
        f"🔥 Калории: {daily_totals.get('calories', 0):.0f} ккал\n"
        f"💪 Белки: {daily_totals.get('protein', 0):.1f} г\n"
        f"🥑 Жиры: {daily_totals.get('fat', 0):.1f} г\n"
        f"🍩 Углеводы: {daily_totals.get('carbs', 0):.1f} г"
    )
    
    await state.clear()
    push_menu_stack(message.bot, kbju_after_meal_menu)
    await message.answer("\n".join(lines), reply_markup=kbju_after_meal_menu)


@router.message(lambda m: m.text == "📊 Дневной отчёт")
async def calories_today_results(message: Message):
    """Показывает дневной отчёт по КБЖУ."""
    reset_user_state(message)
    user_id = str(message.from_user.id)
    await send_today_results(message, user_id)


async def send_today_results(message: Message, user_id: str):
    """Отправляет результаты за сегодня."""
    today = date.today()
    meals = MealRepository.get_meals_for_date(user_id, today)
    
    if not meals:
        from utils.keyboards import kbju_menu
        push_menu_stack(message.bot, kbju_menu)
        await message.answer(
            "Пока нет записей за сегодня. Добавь приём пищи, и я посчитаю КБЖУ!",
            reply_markup=kbju_menu,
        )
        return
    
    daily_totals = MealRepository.get_daily_totals(user_id, today)
    day_str = today.strftime("%d.%m.%Y")

    from collections import defaultdict
    from utils.meal_formatters import (
        format_food_diary_header,
        format_meal_block,
        format_daily_totals_message,
        build_meals_actions_keyboard,
    )

    grouped: dict[str, list] = defaultdict(list)
    for meal in meals:
        grouped[normalize_meal_type(getattr(meal, "meal_type", None))].append(meal)

    messages: list[str] = []
    for meal_type in MEAL_TYPE_ORDER:
        meal_group = grouped.get(meal_type, [])
        if not meal_group:
            continue
        block_text = "\n".join(format_meal_block(meal_type, meal_group))
        if not messages:
            block_text = f"{format_food_diary_header(day_str)}\n\n{block_text}"
        messages.append(block_text)

    messages.append(format_daily_totals_message(daily_totals, day_str))
    keyboard = build_meals_actions_keyboard(meals, today)
    logger.info("KBJU daily report messages=%s", len(messages))

    async def _safe_send(chunk: str, *, with_keyboard: bool = False):
        """Безопасная отправка chunk: сначала HTML, при ошибке — plain text."""
        kwargs = {"parse_mode": "HTML"}
        if with_keyboard:
            kwargs["reply_markup"] = keyboard

        try:
            await message.answer(chunk, **kwargs)
        except TelegramBadRequest as exc:
            logger.warning(
                "KBJU daily report chunk failed with HTML, fallback to plain text: %s",
                exc,
            )
            fallback_kwargs = {}
            if with_keyboard:
                fallback_kwargs["reply_markup"] = keyboard
            await message.answer(chunk, **fallback_kwargs)

    for index, chunk in enumerate(messages):
        if len(chunk) <= 4000:
            await _safe_send(chunk, with_keyboard=(index == len(messages) - 1))
            continue

        chunks = split_telegram_message(chunk)
        logger.info("KBJU daily report split message %s into %s chunk(s)", index, len(chunks))
        for chunk_index, nested_chunk in enumerate(chunks):
            is_last_piece = index == len(messages) - 1 and chunk_index == len(chunks) - 1
            await _safe_send(nested_chunk, with_keyboard=is_last_piece)


@router.message(lambda m: m.text == "📆 Календарь КБЖУ")
async def calories_calendar(message: Message):
    """Показывает календарь КБЖУ."""
    reset_user_state(message)
    user_id = str(message.from_user.id)
    await show_kbju_calendar(message, user_id)


async def show_kbju_calendar(message: Message, user_id: str, year: Optional[int] = None, month: Optional[int] = None):
    """Показывает календарь КБЖУ."""
    today = date.today()
    if year is None:
        year = today.year
    if month is None:
        month = today.month
    
    from utils.calendar_utils import build_kbju_calendar_keyboard
    keyboard = build_kbju_calendar_keyboard(user_id, year, month)
    
    await message.answer(
        f"📆 Календарь КБЖУ\n\nВыбери день:",
        reply_markup=keyboard,
    )


@router.callback_query(lambda c: c.data.startswith("meal_cal_nav:"))
async def navigate_kbju_calendar(callback: CallbackQuery):
    """Навигация по календарю КБЖУ."""
    await callback.answer()
    parts = callback.data.split(":")
    year, month = map(int, parts[1].split("-"))
    user_id = str(callback.from_user.id)
    await show_kbju_calendar(callback.message, user_id, year, month)


@router.callback_query(lambda c: c.data.startswith("meal_cal_back:"))
async def back_to_kbju_calendar(callback: CallbackQuery):
    """Возврат к календарю КБЖУ."""
    await callback.answer()
    parts = callback.data.split(":")
    year, month = map(int, parts[1].split("-"))
    user_id = str(callback.from_user.id)
    await show_kbju_calendar(callback.message, user_id, year, month)


@router.callback_query(lambda c: c.data.startswith("meal_cal_day:"))
async def select_kbju_calendar_day(callback: CallbackQuery):
    """Выбор дня в календаре КБЖУ."""
    await callback.answer()
    parts = callback.data.split(":")
    target_date = date.fromisoformat(parts[1])
    user_id = str(callback.from_user.id)
    await show_day_meals(callback.message, user_id, target_date)


async def show_day_meals(message: Message, user_id: str, target_date: date):
    """Показывает приёмы пищи за день."""
    meals = MealRepository.get_meals_for_date(user_id, target_date)
    
    if not meals:
        from utils.meal_formatters import build_kbju_day_actions_keyboard
        await message.answer(
            f"{target_date.strftime('%d.%m.%Y')}: нет записей по КБЖУ.",
            reply_markup=build_kbju_day_actions_keyboard(target_date),
        )
        return
    
    daily_totals = MealRepository.get_daily_totals(user_id, target_date)
    day_str = target_date.strftime("%d.%m.%Y")
    from collections import defaultdict
    from utils.meal_formatters import (
        format_food_diary_header,
        format_meal_block,
        format_daily_totals_message,
        build_meals_actions_keyboard,
    )

    grouped: dict[str, list] = defaultdict(list)
    for meal in meals:
        grouped[normalize_meal_type(getattr(meal, "meal_type", None))].append(meal)

    messages: list[str] = []
    for meal_type in MEAL_TYPE_ORDER:
        meal_group = grouped.get(meal_type, [])
        if not meal_group:
            continue
        block_text = "\n".join(format_meal_block(meal_type, meal_group))
        if not messages:
            block_text = f"{format_food_diary_header(day_str)}\n\n{block_text}"
        messages.append(block_text)

    messages.append(format_daily_totals_message(daily_totals, day_str))
    keyboard = build_meals_actions_keyboard(meals, target_date, include_back=True)

    for index, chunk in enumerate(messages):
        await message.answer(
            chunk,
            reply_markup=keyboard if index == len(messages) - 1 else None,
        )


def _truncate_product_name(name: str, limit: int = 28) -> str:
    """Аккуратно обрезает длинные названия для inline-кнопок."""
    clean_name = (name or "продукт").strip()
    if len(clean_name) <= limit:
        return clean_name
    return f"{clean_name[:limit - 1].rstrip()}…"


def _build_weight_products_keyboard(products: list[dict]) -> InlineKeyboardMarkup:
    """Клавиатура выбора продукта для редактирования веса."""
    rows: list[list[InlineKeyboardButton]] = []
    for idx, product in enumerate(products, start=1):
        name = _truncate_product_name(product.get("name") or "продукт")
        grams = float(product.get("grams") or 0)
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"{idx}. {name} — {grams:.0f} г",
                    callback_data=f"meal_wsel:{idx - 1}",
                )
            ]
        )

    rows.append(
        [
            InlineKeyboardButton(text="⬅️ Назад", callback_data="meal_wback_edit"),
            InlineKeyboardButton(text="❌ Отмена", callback_data="meal_wcancel"),
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _build_weight_editor_keyboard(product_idx: int) -> InlineKeyboardMarkup:
    """Клавиатура изменения веса одного продукта."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="−50 г", callback_data=f"meal_wchg:{product_idx}:-50"),
                InlineKeyboardButton(text="−10 г", callback_data=f"meal_wchg:{product_idx}:-10"),
                InlineKeyboardButton(text="−1 г", callback_data=f"meal_wchg:{product_idx}:-1"),
            ],
            [
                InlineKeyboardButton(text="+1 г", callback_data=f"meal_wchg:{product_idx}:1"),
                InlineKeyboardButton(text="+10 г", callback_data=f"meal_wchg:{product_idx}:10"),
                InlineKeyboardButton(text="+50 г", callback_data=f"meal_wchg:{product_idx}:50"),
            ],
            [
                InlineKeyboardButton(text="⌨️ Ввести вручную", callback_data=f"meal_wmanual:{product_idx}"),
            ],
            [
                InlineKeyboardButton(text="✅ Сохранить", callback_data=f"meal_wsave:{product_idx}"),
                InlineKeyboardButton(text="🗑 Удалить", callback_data=f"meal_wdelask:{product_idx}"),
            ],
            [
                InlineKeyboardButton(text="⬅️ К списку продуктов", callback_data="meal_wback_list"),
                InlineKeyboardButton(text="❌ Отмена", callback_data="meal_wcancel"),
            ],
        ]
    )


def _build_weight_delete_confirm_keyboard(product_idx: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🗑 Да, удалить", callback_data=f"meal_wdel:{product_idx}")],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data=f"meal_wdelback:{product_idx}")],
        ]
    )


def _extract_product_macros(product: dict) -> tuple[float, float, float, float]:
    """Возвращает калории/белки/жиры/углеводы из любого поддерживаемого формата."""
    calories = float(product.get("kcal") or product.get("calories") or product.get("_calories") or 0)
    protein = float(product.get("protein") or product.get("protein_g") or product.get("_protein_g") or 0)
    fat = float(product.get("fat") or product.get("fat_total_g") or product.get("_fat_total_g") or 0)
    carbs = float(product.get("carbs") or product.get("carbohydrates_total_g") or product.get("_carbohydrates_total_g") or 0)
    return calories, protein, fat, carbs


def _ensure_per_100g(product: dict) -> tuple[float, float, float, float]:
    """Гарантирует наличие КБЖУ на 100 г для пересчётов."""
    calories_per_100g = float(product.get("calories_per_100g") or 0)
    protein_per_100g = float(product.get("protein_per_100g") or 0)
    fat_per_100g = float(product.get("fat_per_100g") or 0)
    carbs_per_100g = float(product.get("carbs_per_100g") or 0)

    if calories_per_100g or protein_per_100g or fat_per_100g or carbs_per_100g:
        return calories_per_100g, protein_per_100g, fat_per_100g, carbs_per_100g

    grams = float(product.get("grams") or 0)
    if grams <= 0:
        return 0, 0, 0, 0

    calories, protein, fat, carbs = _extract_product_macros(product)
    return (
        (calories / grams) * 100 if calories else 0,
        (protein / grams) * 100 if protein else 0,
        (fat / grams) * 100 if fat else 0,
        (carbs / grams) * 100 if carbs else 0,
    )


def _apply_product_weight(product: dict, new_weight: float) -> bool:
    """Обновляет вес и пересчитывает КБЖУ продукта. Возвращает False, если данных недостаточно."""
    calories_per_100g, protein_per_100g, fat_per_100g, carbs_per_100g = _ensure_per_100g(product)
    if not calories_per_100g and not protein_per_100g and not fat_per_100g and not carbs_per_100g:
        return False

    new_calories = (calories_per_100g * new_weight) / 100 if calories_per_100g else 0
    new_protein = (protein_per_100g * new_weight) / 100 if protein_per_100g else 0
    new_fat = (fat_per_100g * new_weight) / 100 if fat_per_100g else 0
    new_carbs = (carbs_per_100g * new_weight) / 100 if carbs_per_100g else 0

    product["grams"] = new_weight
    product["kcal"] = new_calories
    product["protein"] = new_protein
    product["fat"] = new_fat
    product["carbs"] = new_carbs
    product["calories"] = new_calories
    product["protein_g"] = new_protein
    product["fat_total_g"] = new_fat
    product["carbohydrates_total_g"] = new_carbs
    product["calories_per_100g"] = calories_per_100g
    product["protein_per_100g"] = protein_per_100g
    product["fat_per_100g"] = fat_per_100g
    product["carbs_per_100g"] = carbs_per_100g
    return True


def _build_meal_update_payload(products: list[dict]) -> tuple[dict, str]:
    """Формирует суммарные КБЖУ и api_details для сохранения приёма пищи."""
    totals = {
        "calories": 0.0,
        "protein_g": 0.0,
        "fat_total_g": 0.0,
        "carbohydrates_total_g": 0.0,
    }
    api_details_lines = []

    for product in products:
        name = product.get("name", "продукт")
        grams = float(product.get("grams", 0))
        calories, protein, fat, carbs = _extract_product_macros(product)

        totals["calories"] += calories
        totals["protein_g"] += protein
        totals["fat_total_g"] += fat
        totals["carbohydrates_total_g"] += carbs

        api_details_lines.append(
            f"• {name} ({grams:.0f} г) — {calories:.0f} ккал "
            f"(Б {protein:.1f} / Ж {fat:.1f} / У {carbs:.1f})"
        )

    return totals, "\n".join(api_details_lines) if api_details_lines else None


def _render_weight_editor_text(product: dict, draft_weight: Optional[float] = None) -> str:
    """Текст экрана изменения веса конкретного продукта."""
    name = product.get("name") or "продукт"
    current_weight = float(product.get("grams") or 0)
    lines = [
        "✏️ Изменение веса продукта",
        "",
        f"Продукт: {name}",
        f"Текущий вес: {current_weight:.0f} г",
    ]
    if draft_weight is not None and round(draft_weight, 2) != round(current_weight, 2):
        lines.append(f"Новый вес: {draft_weight:.0f} г")

    lines.extend(["", "Выбери действие:"])
    return "\n".join(lines)


@router.callback_query(lambda c: c.data.startswith("meal_cal_add:"))
async def add_meal_from_calendar(callback: CallbackQuery, state: FSMContext):
    """Добавляет приём пищи из календаря."""
    await callback.answer()
    parts = callback.data.split(":")
    target_date = date.fromisoformat(parts[1])
    await start_kbju_add_flow(callback.message, target_date, state)


@router.message(F.text == "➕ Внести ещё приём")
async def kbju_add_more_meal(message: Message, state: FSMContext):
    """Добавляет ещё один приём пищи."""
    await start_kbju_add_flow(message, date.today(), state)


@router.message(F.text == "✏️ Редактировать")
async def edit_last_meal(message: Message, state: FSMContext):
    """Редактирует последний добавленный приём пищи."""
    user_id = str(message.from_user.id)
    
    # Получаем ID последнего приёма
    if not hasattr(message.bot, "last_meal_ids"):
        message.bot.last_meal_ids = {}
    
    last_meal_id = message.bot.last_meal_ids.get(user_id)
    if not last_meal_id:
        await message.answer(
            "❌ Не найден последний приём пищи для редактирования.\n"
            "Попробуй добавить приём пищи, а затем отредактировать его."
        )
        return
    
    # Получаем приём пищи
    meal = MealRepository.get_meal_by_id(last_meal_id, user_id)
    if not meal:
        await message.answer("❌ Не нашёл запись для изменения.")
        return
    
    # Извлекаем продукты из products_json
    products = []
    if meal.products_json:
        try:
            products = json.loads(meal.products_json)
        except Exception:
            pass
    
    if not products:
        await message.answer(
            "❌ Не удалось извлечь список продуктов из этой записи.\n"
            "Попробуй удалить и создать запись заново."
        )
        return
    
    # Сохраняем данные в FSM для редактирования веса
    await state.set_state(MealEntryStates.editing_meal_weight)
    await state.update_data(
        meal_id=last_meal_id,
        target_date=meal.date.isoformat(),
        saved_products=products,
        weight_drafts={},
        editing_product_idx=None,
    )

    # Сразу переходим к изменению веса продукта
    await message.answer(
        "⚖️ Выбери продукт, вес которого хочешь изменить:",
        reply_markup=_build_weight_products_keyboard(products),
    )


@router.callback_query(lambda c: c.data.startswith("meal_edit:"))
async def start_meal_edit(callback: CallbackQuery, state: FSMContext):
    """Начинает редактирование приёма пищи."""
    await callback.answer()
    parts = callback.data.split(":")
    meal_id = int(parts[1])
    target_date = date.fromisoformat(parts[2]) if len(parts) > 2 else date.today()
    user_id = str(callback.from_user.id)

    await _start_meal_edit_flow(callback.message, state, user_id, meal_id, target_date)


async def _start_meal_edit_flow(
    message: Message,
    state: FSMContext,
    user_id: str,
    meal_id: int,
    target_date: date,
) -> None:
    """Общий сценарий запуска редактирования конкретной записи приёма пищи."""
    meal = MealRepository.get_meal_by_id(meal_id, user_id)
    if not meal:
        await message.answer("❌ Не нашёл запись для изменения.")
        return
    
    # Извлекаем продукты из products_json
    products = []
    if meal.products_json:
        try:
            products = json.loads(meal.products_json)
        except Exception:
            pass
    
    # Если продуктов нет, пробуем извлечь из api_details
    if not products and meal.api_details:
        # Парсим api_details для извлечения продуктов
        lines = meal.api_details.split("\n")
        for line in lines:
            if line.strip().startswith("•"):
                # Извлекаем название и вес
                match = re.match(r"•\s*(.+?)\s*\((\d+(?:\.\d+)?)\s*г\)", line)
                if match:
                    name = match.group(1).strip()
                    grams = float(match.group(2))
                    # Извлекаем КБЖУ
                    kbju_match = re.search(
                        r"(\d+(?:\.\d+)?)\s*ккал.*?Б\s*(\d+(?:\.\d+)?).*?Ж\s*(\d+(?:\.\d+)?).*?У\s*(\d+(?:\.\d+)?)",
                        line
                    )
                    if kbju_match:
                        cal = float(kbju_match.group(1))
                        prot = float(kbju_match.group(2))
                        fat = float(kbju_match.group(3))
                        carbs = float(kbju_match.group(4))
                        # Вычисляем КБЖУ на 100г
                        if grams > 0:
                            products.append({
                                "name": name,
                                "grams": grams,
                                "calories": cal,
                                "protein_g": prot,
                                "fat_total_g": fat,
                                "carbohydrates_total_g": carbs,
                                "calories_per_100g": (cal / grams) * 100,
                                "protein_per_100g": (prot / grams) * 100,
                                "fat_per_100g": (fat / grams) * 100,
                                "carbs_per_100g": (carbs / grams) * 100,
                            })
    
    if not products:
        await message.answer(
            "❌ Не удалось извлечь список продуктов из этой записи.\n"
            "Попробуй удалить и создать запись заново."
        )
        return
    
    # Сохраняем данные в FSM и сразу открываем изменение веса
    await state.update_data(
        meal_id=meal_id,
        target_date=target_date.isoformat(),
        saved_products=products,
        weight_drafts={},
        editing_product_idx=None,
    )
    await state.set_state(MealEntryStates.editing_meal_weight)

    await message.answer(
        "⚖️ Выбери продукт, вес которого хочешь изменить:",
        reply_markup=_build_weight_products_keyboard(products),
    )


@router.callback_query(lambda c: c.data.startswith("food:add:"))
async def add_meal_from_diary_block(callback: CallbackQuery, state: FSMContext):
    """Добавляет приём в выбранный блок meal_type/дата из дневника."""
    await callback.answer()
    parts = callback.data.split(":")
    if len(parts) < 4:
        await callback.message.answer("❌ Не удалось открыть добавление: некорректные данные.")
        return
    meal_type = normalize_meal_type(parts[2], fallback=MealType.SNACK.value)
    target_date = date.fromisoformat(parts[3])
    await state.update_data(meal_type=meal_type, entry_date=target_date.isoformat(), pending_add_method=None)
    await callback.message.answer(
        f"Добавляем в приём пищи: {display_meal_type(meal_type)} ({target_date.strftime('%d.%m.%Y')})"
    )
    await _show_input_methods(callback.message, state)


@router.callback_query(lambda c: c.data.startswith("food:edit_meal:"))
async def edit_meal_from_diary_block(callback: CallbackQuery, state: FSMContext):
    """Редактирует последний приём выбранного meal_type за дату."""
    await callback.answer()
    parts = callback.data.split(":")
    if len(parts) < 4:
        await callback.message.answer("❌ Не удалось открыть редактирование: некорректные данные.")
        return
    meal_type = normalize_meal_type(parts[2], fallback=MealType.SNACK.value)
    target_date = date.fromisoformat(parts[3])
    user_id = str(callback.from_user.id)
    meals_for_type = MealRepository.get_meals_for_type_for_date(user_id, target_date, meal_type)
    if not meals_for_type:
        await callback.message.answer("❌ В этом приёме пищи пока нечего редактировать.")
        return
    target_meal = meals_for_type[-1]
    await _start_meal_edit_flow(callback.message, state, user_id, target_meal.id, target_date)


@router.callback_query(lambda c: c.data.startswith("food:clear_meal:"))
async def clear_meal_from_diary_block(callback: CallbackQuery):
    """Запрашивает подтверждение очистки выбранного приёма пищи (meal_type) за дату."""
    await callback.answer()
    parts = callback.data.split(":")
    if len(parts) < 4:
        await callback.message.answer("❌ Не удалось очистить приём пищи: некорректные данные.")
        return

    meal_type = normalize_meal_type(parts[2], fallback=MealType.SNACK.value)
    target_date = date.fromisoformat(parts[3])
    meal_title = display_meal_type(meal_type).lower()
    target_date_iso = target_date.isoformat()

    confirm_keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Да, удалить",
                    callback_data=f"food:clear_meal_confirm:{meal_type}:{target_date_iso}",
                ),
                InlineKeyboardButton(
                    text="❌ Отмена",
                    callback_data=f"food:clear_meal_cancel:{meal_type}:{target_date_iso}",
                ),
            ]
        ]
    )
    await callback.message.answer(
        f"❓ Вы точно хотите удалить все данные за {meal_title}?",
        reply_markup=confirm_keyboard,
    )


@router.callback_query(lambda c: c.data.startswith("food:clear_meal_confirm:"))
async def clear_meal_from_diary_block_confirmed(callback: CallbackQuery):
    """Очищает выбранный приём пищи (meal_type) за дату после подтверждения."""
    await callback.answer()
    parts = callback.data.split(":")
    if len(parts) < 4:
        await callback.message.answer("❌ Не удалось очистить приём пищи: некорректные данные.")
        return

    meal_type = normalize_meal_type(parts[2], fallback=MealType.SNACK.value)
    target_date = date.fromisoformat(parts[3])
    user_id = str(callback.from_user.id)
    deleted_count = MealRepository.delete_meals_by_type_for_date(user_id, target_date, meal_type)
    if deleted_count <= 0:
        await callback.message.answer("ℹ️ В этом приёме пищи уже нет записей.")
        return

    await callback.message.answer(f"✅ {display_meal_type(meal_type)} очищен: удалено записей — {deleted_count}.")
    await show_day_meals(callback.message, user_id, target_date)


@router.callback_query(lambda c: c.data.startswith("food:clear_meal_cancel:"))
async def clear_meal_from_diary_block_cancelled(callback: CallbackQuery):
    """Отменяет очистку выбранного приёма пищи и возвращает отчёт за день."""
    await callback.answer("Очистка отменена")
    parts = callback.data.split(":")
    if len(parts) < 4:
        return

    target_date = date.fromisoformat(parts[3])
    user_id = str(callback.from_user.id)
    await callback.message.answer("👌 Очистку отменили.")
    await show_day_meals(callback.message, user_id, target_date)


@router.message(MealEntryStates.choosing_edit_type)
async def handle_edit_type_choice(message: Message, state: FSMContext):
    """Обрабатывает выбор типа редактирования."""
    user_id = str(message.from_user.id)
    text = message.text.strip()
    
    # Проверяем, не является ли это кнопкой меню
    menu_buttons = ["⬅️ Назад"]
    if text in menu_buttons or text in MAIN_MENU_BUTTON_ALIASES:
        await state.clear()
        if text == "⬅️ Назад":
            from handlers.common import go_back
            await go_back(message, state)
        elif text in MAIN_MENU_BUTTON_ALIASES:
            from handlers.common import go_main_menu
            await go_main_menu(message, state)
        return
    
    data = await state.get_data()
    saved_products = data.get("saved_products", [])
    
    if not saved_products:
        await message.answer("❌ Не удалось найти сохраненные данные продуктов.")
        await state.clear()
        return
    
    if text == "⚖️ Изменить вес продукта":
        await state.set_state(MealEntryStates.editing_meal_weight)
        await state.update_data(weight_drafts={}, editing_product_idx=None)

        await message.answer(
            "⚖️ Выбери продукт, вес которого хочешь изменить:",
            reply_markup=_build_weight_products_keyboard(saved_products),
        )
        
    elif text == "📝 Изменить состав продуктов":
        # Переходим к редактированию состава через ИИ
        await state.set_state(MealEntryStates.editing_meal_composition)
        
        edit_lines = ["✏️ Изменение состава продуктов\n\nТекущий состав:"]
        for i, p in enumerate(saved_products, 1):
            name = p.get("name") or "продукт"
            grams = p.get("grams", 0)
            edit_lines.append(f"{i}. {name}, {grams:.0f} г")
        
        edit_lines.append("\nВведи новый состав текстом (как в «Ввести приём пищи»):")
        edit_lines.append("Например: 200 г курицы, 100 г йогурта, 30 г орехов")
        edit_lines.append("\nИИ автоматически определит КБЖУ на основе типичных значений продуктов.")
        
        push_menu_stack(message.bot, kbju_after_meal_menu)
        await message.answer("\n".join(edit_lines), reply_markup=kbju_after_meal_menu)
    else:
        await message.answer("Пожалуйста, выбери вариант с кнопки.")


@router.message(MealEntryStates.editing_meal_weight)
async def handle_meal_weight_edit(message: Message, state: FSMContext):
    """Обрабатывает сообщения в режиме изменения веса."""
    text = (message.text or "").strip()
    if text in {"⬅️ Назад", "❌ Отмена"}:
        await state.set_state(MealEntryStates.choosing_edit_type)
        push_menu_stack(message.bot, kbju_edit_type_menu)
        await message.answer(
            "✏️ Редактирование приёма пищи\n\nВыбери, что хочешь изменить:",
            reply_markup=kbju_edit_type_menu,
        )
        return

    await message.answer("Используй кнопки ниже для изменения веса продукта 👇")


@router.callback_query(lambda c: c.data == "meal_wback_edit")
async def meal_weight_back_to_edit_type(callback: CallbackQuery, state: FSMContext):
    """Возврат к выбору типа редактирования."""
    await callback.answer()
    await state.set_state(MealEntryStates.choosing_edit_type)
    await callback.message.answer(
        "✏️ Редактирование приёма пищи\n\nВыбери, что хочешь изменить:",
        reply_markup=kbju_edit_type_menu,
    )


@router.callback_query(lambda c: c.data == "meal_wcancel")
async def meal_weight_cancel(callback: CallbackQuery, state: FSMContext):
    """Отмена редактирования веса."""
    await callback.answer("Редактирование отменено")
    await state.clear()
    await callback.message.answer("Ок, отменил изменение веса 👌", reply_markup=kbju_after_meal_menu)


@router.callback_query(lambda c: c.data == "meal_wback_list")
async def meal_weight_back_to_products(callback: CallbackQuery, state: FSMContext):
    """Возврат к списку продуктов."""
    await callback.answer()
    data = await state.get_data()
    saved_products = data.get("saved_products", [])
    await state.set_state(MealEntryStates.editing_meal_weight)
    try:
        await callback.message.edit_text(
            "⚖️ Выбери продукт, вес которого хочешь изменить:",
            reply_markup=_build_weight_products_keyboard(saved_products),
        )
    except TelegramBadRequest:
        await callback.message.answer(
            "⚖️ Выбери продукт, вес которого хочешь изменить:",
            reply_markup=_build_weight_products_keyboard(saved_products),
        )


@router.callback_query(lambda c: c.data.startswith("meal_wsel:"))
async def meal_weight_select_product(callback: CallbackQuery, state: FSMContext):
    """Открывает экран изменения веса выбранного продукта."""
    await callback.answer()
    product_idx = int(callback.data.split(":")[1])
    data = await state.get_data()
    saved_products = data.get("saved_products", [])
    drafts = data.get("weight_drafts", {})

    if product_idx < 0 or product_idx >= len(saved_products):
        await callback.answer("Не нашёл продукт", show_alert=True)
        return

    product = saved_products[product_idx]
    draft_weight = drafts.get(str(product_idx))

    await state.set_state(MealEntryStates.editing_meal_weight)
    await state.update_data(editing_product_idx=product_idx)

    try:
        await callback.message.edit_text(
            _render_weight_editor_text(product, draft_weight=draft_weight),
            reply_markup=_build_weight_editor_keyboard(product_idx),
        )
    except TelegramBadRequest:
        await callback.message.answer(
            _render_weight_editor_text(product, draft_weight=draft_weight),
            reply_markup=_build_weight_editor_keyboard(product_idx),
        )


@router.callback_query(lambda c: c.data.startswith("meal_wchg:"))
async def meal_weight_change_draft(callback: CallbackQuery, state: FSMContext):
    """Меняет временный вес продукта кнопками +/−."""
    _, raw_idx, raw_delta = callback.data.split(":")
    product_idx = int(raw_idx)
    delta = int(raw_delta)

    data = await state.get_data()
    saved_products = data.get("saved_products", [])
    drafts = data.get("weight_drafts", {})
    if product_idx < 0 or product_idx >= len(saved_products):
        await callback.answer("Не нашёл продукт", show_alert=True)
        return

    product = saved_products[product_idx]
    base_weight = float(drafts.get(str(product_idx), product.get("grams", 0)))
    new_weight = base_weight + delta
    if new_weight < 1:
        await callback.answer("Вес не может быть меньше 1 г")
        return

    drafts[str(product_idx)] = int(new_weight)
    await state.update_data(weight_drafts=drafts, editing_product_idx=product_idx)
    await callback.answer()

    await callback.message.edit_text(
        _render_weight_editor_text(product, draft_weight=new_weight),
        reply_markup=_build_weight_editor_keyboard(product_idx),
    )


@router.callback_query(lambda c: c.data.startswith("meal_wmanual:"))
async def meal_weight_manual_input_start(callback: CallbackQuery, state: FSMContext):
    """Запрашивает ручной ввод веса для конкретного продукта."""
    await callback.answer()
    product_idx = int(callback.data.split(":")[1])
    data = await state.get_data()
    saved_products = data.get("saved_products", [])
    if product_idx < 0 or product_idx >= len(saved_products):
        await callback.answer("Не нашёл продукт", show_alert=True)
        return

    product_name = saved_products[product_idx].get("name") or "продукт"
    await state.set_state(MealEntryStates.editing_meal_weight_manual_input)
    await state.update_data(editing_product_idx=product_idx)
    await callback.message.answer(
        f'Введи новый вес для продукта "{product_name}" в граммах:'
    )


@router.message(MealEntryStates.editing_meal_weight_manual_input)
async def meal_weight_manual_input_value(message: Message, state: FSMContext):
    """Обрабатывает ручной ввод нового веса."""
    raw_value = (message.text or "").strip().replace(",", ".")
    if not raw_value.isdigit():
        await message.answer("Пожалуйста, введи вес числом в граммах, например: 180")
        return

    new_weight = int(raw_value)
    if new_weight < 1:
        await message.answer("Вес должен быть не меньше 1 г.")
        return

    data = await state.get_data()
    product_idx = data.get("editing_product_idx")
    saved_products = data.get("saved_products", [])
    drafts = data.get("weight_drafts", {})
    if product_idx is None or product_idx < 0 or product_idx >= len(saved_products):
        await message.answer("❌ Не удалось найти продукт для редактирования.")
        await state.set_state(MealEntryStates.editing_meal_weight)
        return

    drafts[str(product_idx)] = new_weight
    product = saved_products[product_idx]
    await state.set_state(MealEntryStates.editing_meal_weight)
    await state.update_data(weight_drafts=drafts)

    await message.answer(
        _render_weight_editor_text(product, draft_weight=new_weight),
        reply_markup=_build_weight_editor_keyboard(product_idx),
    )


@router.callback_query(lambda c: c.data.startswith("meal_wsave:"))
async def meal_weight_save(callback: CallbackQuery, state: FSMContext):
    """Сохраняет новый вес выбранного продукта и пересчитывает КБЖУ."""
    product_idx = int(callback.data.split(":")[1])
    user_id = str(callback.from_user.id)
    data = await state.get_data()
    meal_id = data.get("meal_id")
    target_date_str = data.get("target_date", date.today().isoformat())
    saved_products = data.get("saved_products", [])
    drafts = data.get("weight_drafts", {})

    if not meal_id or product_idx < 0 or product_idx >= len(saved_products):
        await callback.answer("Не удалось сохранить", show_alert=True)
        return

    product = saved_products[product_idx]
    draft_weight = float(drafts.get(str(product_idx), product.get("grams", 0)))
    if draft_weight < 1:
        await callback.answer("Вес не может быть меньше 1 г", show_alert=True)
        return

    if not _apply_product_weight(product, draft_weight):
        await callback.answer("Не удалось пересчитать КБЖУ", show_alert=True)
        return

    totals, api_details = _build_meal_update_payload(saved_products)
    meal = MealRepository.get_meal_by_id(meal_id, user_id)
    raw_query = meal.raw_query if meal and hasattr(meal, "raw_query") else None

    success = MealRepository.update_meal(
        meal_id=meal_id,
        user_id=user_id,
        description=raw_query,
        calories=totals["calories"],
        protein=totals["protein_g"],
        fat=totals["fat_total_g"],
        carbs=totals["carbohydrates_total_g"],
        products_json=json.dumps(saved_products),
        api_details=api_details,
    )
    if not success:
        await callback.answer("Не удалось обновить запись", show_alert=True)
        return

    drafts.pop(str(product_idx), None)
    await state.update_data(saved_products=saved_products, weight_drafts=drafts)
    await callback.answer("✅ Вес продукта обновлён")
    await callback.message.edit_text(
        "⚖️ Выбери продукт, вес которого хочешь изменить:",
        reply_markup=_build_weight_products_keyboard(saved_products),
    )

    if isinstance(target_date_str, str):
        try:
            target_date = date.fromisoformat(target_date_str)
        except ValueError:
            target_date = date.today()
    else:
        target_date = date.today()
    await show_day_meals(callback.message, user_id, target_date)


@router.callback_query(lambda c: c.data.startswith("meal_wdelask:"))
async def meal_weight_delete_confirm_with_state(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    product_idx = int(callback.data.split(":")[1])
    data = await state.get_data()
    saved_products = data.get("saved_products", [])
    if product_idx < 0 or product_idx >= len(saved_products):
        await callback.answer("Не нашёл продукт", show_alert=True)
        return

    product_name = saved_products[product_idx].get("name") or "продукт"
    await callback.message.edit_text(
        f'Удалить продукт "{product_name}" из этого приёма пищи?',
        reply_markup=_build_weight_delete_confirm_keyboard(product_idx),
    )


@router.callback_query(lambda c: c.data.startswith("meal_wdelback:"))
async def meal_weight_delete_back(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    product_idx = int(callback.data.split(":")[1])
    data = await state.get_data()
    saved_products = data.get("saved_products", [])
    drafts = data.get("weight_drafts", {})
    if product_idx < 0 or product_idx >= len(saved_products):
        await callback.answer("Не нашёл продукт", show_alert=True)
        return

    product = saved_products[product_idx]
    draft_weight = drafts.get(str(product_idx))
    await callback.message.edit_text(
        _render_weight_editor_text(product, draft_weight=draft_weight),
        reply_markup=_build_weight_editor_keyboard(product_idx),
    )


@router.callback_query(lambda c: c.data.startswith("meal_wdel:"))
async def meal_weight_delete(callback: CallbackQuery, state: FSMContext):
    """Удаляет продукт из приёма пищи после подтверждения."""
    await callback.answer()
    product_idx = int(callback.data.split(":")[1])
    user_id = str(callback.from_user.id)
    data = await state.get_data()
    meal_id = data.get("meal_id")
    target_date_str = data.get("target_date", date.today().isoformat())
    saved_products = data.get("saved_products", [])
    drafts = data.get("weight_drafts", {})

    if not meal_id or product_idx < 0 or product_idx >= len(saved_products):
        await callback.answer("Не удалось удалить продукт", show_alert=True)
        return

    saved_products.pop(product_idx)
    drafts = {
        str(int(idx) - 1 if int(idx) > product_idx else int(idx)): value
        for idx, value in drafts.items()
        if int(idx) != product_idx
    }

    if not saved_products:
        success = MealRepository.delete_meal(meal_id, user_id)
        if not success:
            await callback.answer("Не удалось обновить приём пищи", show_alert=True)
            return
        await state.clear()
        await callback.message.answer("Приём пищи теперь пуст. Запись удалена.")
    else:
        totals, api_details = _build_meal_update_payload(saved_products)
        meal = MealRepository.get_meal_by_id(meal_id, user_id)
        raw_query = meal.raw_query if meal and hasattr(meal, "raw_query") else None
        success = MealRepository.update_meal(
            meal_id=meal_id,
            user_id=user_id,
            description=raw_query,
            calories=totals["calories"],
            protein=totals["protein_g"],
            fat=totals["fat_total_g"],
            carbs=totals["carbohydrates_total_g"],
            products_json=json.dumps(saved_products),
            api_details=api_details,
        )
        if not success:
            await callback.answer("Не удалось обновить запись", show_alert=True)
            return

        await state.update_data(saved_products=saved_products, weight_drafts=drafts)
        await callback.message.edit_text(
            "⚖️ Выбери продукт, вес которого хочешь изменить:",
            reply_markup=_build_weight_products_keyboard(saved_products),
        )
        await callback.message.answer("✅ Продукт удалён")

    if isinstance(target_date_str, str):
        try:
            target_date = date.fromisoformat(target_date_str)
        except ValueError:
            target_date = date.today()
    else:
        target_date = date.today()
    await show_day_meals(callback.message, user_id, target_date)


@router.message(MealEntryStates.editing_meal_composition)
async def handle_meal_composition_edit(message: Message, state: FSMContext):
    """Обрабатывает изменение состава продуктов через ИИ."""
    user_id = str(message.from_user.id)
    user_text = message.text.strip()
    
    # Проверяем, не является ли это кнопкой меню
    menu_buttons = ["⬅️ Назад", "📊 Дневной отчёт", "➕ Внести ещё приём", "✏️ Редактировать"]
    if user_text in menu_buttons or user_text in MAIN_MENU_BUTTON_ALIASES:
        await state.clear()
        if user_text == "⬅️ Назад":
            from handlers.common import go_back
            await go_back(message, state)
        elif user_text in MAIN_MENU_BUTTON_ALIASES:
            from handlers.common import go_main_menu
            await go_main_menu(message, state)
        else:
            await message.answer("Редактирование отменено.")
        return
    
    if not user_text:
        await message.answer("Напиши, пожалуйста, новый состав продуктов 🙏")
        return
    
    data = await state.get_data()
    meal_id = data.get("meal_id")
    target_date_str = data.get("target_date", date.today().isoformat())
    
    if not meal_id:
        await message.answer("❌ Не удалось найти запись для обновления.")
        await state.clear()
        return
    
    # Показываем сообщение об анализе
    await message.answer("🤖 Считаю КБЖУ с помощью ИИ, секунду...")
    
    # Получаем КБЖУ через Gemini (как в "ввести прием пищи")
    kbju_data = gemini_service.estimate_kbju(user_text)
    
    if not kbju_data or "total" not in kbju_data:
        await message.answer(
            "⚠️ Не получилось определить КБЖУ.\n"
            "Попробуй ещё раз или используй другой способ редактирования."
        )
        return
    
    items = kbju_data.get("items", [])
    total = kbju_data.get("total", {})
    
    # Безопасное преобразование значений
    def safe_float(value) -> float:
        try:
            if value is None:
                return 0.0
            return float(value)
        except (TypeError, ValueError):
            return 0.0
    
    totals_for_db = {
        "calories": safe_float(total.get("kcal")),
        "protein": safe_float(total.get("protein")),
        "fat": safe_float(total.get("fat")),
        "carbs": safe_float(total.get("carbs")),
    }
    
    # Обновляем запись
    success = MealRepository.update_meal(
        meal_id=meal_id,
        user_id=user_id,
        description=user_text,
        calories=totals_for_db["calories"],
        protein=totals_for_db["protein"],
        fat=totals_for_db["fat"],
        carbs=totals_for_db["carbs"],
        products_json=json.dumps(items),
    )
    
    if not success:
        await message.answer("❌ Не удалось обновить запись.")
        await state.clear()
        return
    
    await state.clear()
    
    # Показываем обновлённый день
    if isinstance(target_date_str, str):
        try:
            target_date = date.fromisoformat(target_date_str)
        except ValueError:
            target_date = date.today()
    else:
        target_date = date.today()
    
    await message.answer("✅ Состав продуктов обновлён! КБЖУ пересчитано через ИИ.")
    await show_day_meals(message, user_id, target_date)


@router.message(MealEntryStates.editing_meal)
async def handle_meal_edit_input(message: Message, state: FSMContext):
    """Обрабатывает ввод нового состава продуктов при редактировании."""
    user_id = str(message.from_user.id)
    logger.info(f"User {user_id} editing meal, input: {message.text[:50]}")
    
    data = await state.get_data()
    meal_id = data.get("meal_id")
    target_date_str = data.get("target_date", date.today().isoformat())
    saved_products = data.get("saved_products", [])
    new_text = message.text.strip()
    
    # Проверяем, не является ли это кнопкой меню
    menu_buttons = ["⬅️ Назад", "📊 Дневной отчёт", "➕ Внести ещё приём", "✏️ Редактировать"]
    if new_text in menu_buttons or new_text in MAIN_MENU_BUTTON_ALIASES:
        await state.clear()
        if new_text == "⬅️ Назад":
            from handlers.common import go_back
            await go_back(message, state)
        elif new_text in MAIN_MENU_BUTTON_ALIASES:
            from handlers.common import go_main_menu
            await go_main_menu(message, state)
        else:
            await message.answer("Редактирование отменено.")
        return
    
    if not meal_id:
        logger.warning(f"User {user_id}: meal_id not found in FSM state")
        await message.answer("❌ Не получилось определить запись для обновления.")
        await state.clear()
        return
    
    if not new_text:
        await message.answer("Напиши новый состав продуктов в формате: название, вес г")
        return
    
    if not saved_products:
        logger.warning(f"User {user_id}: saved_products not found in FSM state")
        await message.answer(
            "❌ Не удалось найти сохраненные данные продуктов.\n"
            "Попробуй удалить и создать запись заново."
        )
        await state.clear()
        return
    
    # Парсим ввод пользователя: каждая строка = "название, вес г"
    try:
        lines = [line.strip() for line in new_text.split("\n") if line.strip()]
        if not lines:
            await message.answer("Напиши новый состав продуктов в формате: название, вес г")
            return
        
        edited_products = []
        
        for i, line in enumerate(lines):
            # Парсим формат "название, вес г" или "название, вес"
            match = re.match(r"(.+?),\s*(\d+(?:[.,]\d+)?)\s*г?", line, re.IGNORECASE)
            if not match:
                await message.answer(
                    f"❌ Неверный формат в строке {i+1}: {line}\n"
                    "Используй формат: название, вес г\n"
                    "Пример: курица, 200 г"
                )
                return
            
            name = match.group(1).strip()
            grams_str = match.group(2).replace(",", ".")
            grams = float(grams_str)
            
            # Определяем, является ли продукт новым или существующим
            is_new_product = i >= len(saved_products)
            original_product = saved_products[i] if not is_new_product else None
            
            # Проверяем, изменилось ли название продукта
            name_changed = False
            if original_product:
                original_name = original_product.get("name", "").strip().lower()
                name_changed = original_name != name.lower()
            
            # Пытаемся получить КБЖУ из сохраненных данных, если продукт существует и название не изменилось
            calories_per_100g = None
            protein_per_100g = None
            fat_per_100g = None
            carbs_per_100g = None
            
            if original_product and not name_changed:
                # Получаем КБЖУ на 100г из сохраненных данных
                calories_per_100g = original_product.get("calories_per_100g")
                protein_per_100g = original_product.get("protein_per_100g")
                fat_per_100g = original_product.get("fat_per_100g")
                carbs_per_100g = original_product.get("carbs_per_100g")
                
                # Если нет значений на 100г, вычисляем из сохраненных данных
                if not calories_per_100g or calories_per_100g == 0:
                    orig_grams = original_product.get("grams", 0)
                    if orig_grams > 0:
                        orig_calories = original_product.get("calories", 0) or 0
                        orig_protein = original_product.get("protein_g", 0) or 0
                        orig_fat = original_product.get("fat_total_g", 0) or 0
                        orig_carbs = original_product.get("carbohydrates_total_g", 0) or 0
                        
                        if orig_calories > 0:  # Только если есть валидные данные
                            calories_per_100g = (orig_calories / orig_grams) * 100
                            protein_per_100g = (orig_protein / orig_grams) * 100
                            fat_per_100g = (orig_fat / orig_grams) * 100
                            carbs_per_100g = (orig_carbs / orig_grams) * 100
            
            # Если продукт новый, название изменилось или данные некорректны, получаем КБЖУ через API
            if is_new_product or name_changed or not calories_per_100g or calories_per_100g == 0:
                api_success = False
                
                # Пробуем несколько вариантов запроса
                query_variants = [
                    f"{name} 100g",  # С весом 100г (для получения данных на 100г)
                    f"{name} {int(grams)}g",  # С указанным пользователем весом
                    name,  # Только название
                ]
                
                for query_variant in query_variants:
                    if api_success:
                        break
                        
                    try:
                        translated_query = translate_text(query_variant, source_lang="ru", target_lang="en")
                        logger.info(f"Getting nutrition for product '{name}': trying query '{translated_query}'")
                        
                        items, _ = nutrition_service.get_nutrition_from_api(translated_query)
                        
                        if items:
                            logger.debug(f"API returned {len(items)} items for '{name}': {[item.get('name', 'unknown') for item in items]}")
                            # Пробуем найти продукт с валидными данными
                            for item_idx, item in enumerate(items):
                                # API возвращает значения для указанного количества
                                # Используем ключи с подчеркиванием, которые добавляет nutrition_service
                                cal = float(item.get("_calories", 0.0))
                                p = float(item.get("_protein_g", 0.0))
                                f = float(item.get("_fat_total_g", 0.0))
                                c = float(item.get("_carbohydrates_total_g", 0.0))
                                
                                # Если значения с подчеркиванием нулевые, пробуем оригинальные ключи
                                if cal == 0:
                                    cal = float(item.get("calories", 0.0))
                                    p = float(item.get("protein_g", 0.0))
                                    f = float(item.get("fat_total_g", 0.0))
                                    c = float(item.get("carbohydrates_total_g", 0.0))
                                
                                item_name = item.get("name", "unknown")
                                logger.debug(f"Item {item_idx} '{item_name}': cal={cal}, p={p}, f={f}, c={c}")
                                
                                # Проверяем, что хотя бы калории не нулевые
                                if cal > 0:
                                    # CalorieNinjas API возвращает данные для указанного количества в запросе
                                    # Если запрос был с "100g", значения уже на 100г
                                    if "100g" in query_variant.lower():
                                        calories_per_100g = cal
                                        protein_per_100g = p
                                        fat_per_100g = f
                                        carbs_per_100g = c
                                    elif f"{int(grams)}g" in query_variant.lower():
                                        # Если запрос был с указанным пользователем весом, пересчитываем на 100г
                                        query_grams = int(grams)
                                        if query_grams > 0:
                                            calories_per_100g = (cal / query_grams) * 100
                                            protein_per_100g = (p / query_grams) * 100
                                            fat_per_100g = (f / query_grams) * 100
                                            carbs_per_100g = (c / query_grams) * 100
                                        else:
                                            calories_per_100g = cal
                                            protein_per_100g = p
                                            fat_per_100g = f
                                            carbs_per_100g = c
                                    else:
                                        # Если запрос был без веса, API может вернуть данные на порцию
                                        # Нужно проверить, есть ли информация о весе порции
                                        serving_size = float(item.get("serving_size_g", 0.0))
                                        if serving_size > 0:
                                            # Пересчитываем на 100г
                                            calories_per_100g = (cal / serving_size) * 100
                                            protein_per_100g = (p / serving_size) * 100
                                            fat_per_100g = (f / serving_size) * 100
                                            carbs_per_100g = (c / serving_size) * 100
                                        else:
                                            # Если вес порции не указан, предполагаем что данные на 100г
                                            calories_per_100g = cal
                                            protein_per_100g = p
                                            fat_per_100g = f
                                            carbs_per_100g = c
                                    
                                    api_success = True
                                    logger.info(f"Successfully got nutrition for '{name}': {calories_per_100g:.0f} kcal/100g (from query: {query_variant})")
                                    break
                            
                            if not api_success:
                                logger.warning(f"API вернул данные для '{name}', но все значения нулевые")
                        else:
                            logger.warning(f"API не вернул данные для продукта '{name}' с запросом '{translated_query}'")
                    except Exception as e:
                        logger.error(f"Error getting nutrition from API for '{name}' with query '{query_variant}': {e}")
                        continue
                
                # Если не удалось получить данные через API
                if not api_success:
                    logger.warning(f"Не удалось получить КБЖУ для продукта '{name}' через API")
                    # Используем нули только если это действительно новый продукт
                    if is_new_product or name_changed:
                        calories_per_100g = 0
                        protein_per_100g = 0
                        fat_per_100g = 0
                        carbs_per_100g = 0
                    # Если это существующий продукт с некорректными данными, оставляем как есть
            
            # Пересчитываем КБЖУ для указанного веса
            new_calories = (calories_per_100g * grams) / 100 if calories_per_100g else 0
            new_protein = (protein_per_100g * grams) / 100 if protein_per_100g else 0
            new_fat = (fat_per_100g * grams) / 100 if fat_per_100g else 0
            new_carbs = (carbs_per_100g * grams) / 100 if carbs_per_100g else 0
            
            edited_products.append({
                "name": name,
                "grams": grams,
                "calories": new_calories,
                "protein_g": new_protein,
                "fat_total_g": new_fat,
                "carbohydrates_total_g": new_carbs,
                "calories_per_100g": calories_per_100g,
                "protein_per_100g": protein_per_100g,
                "fat_per_100g": fat_per_100g,
                "carbs_per_100g": carbs_per_100g,
            })
        
        # Суммируем КБЖУ всех продуктов
        totals = {
            "calories": sum(p["calories"] for p in edited_products),
            "protein_g": sum(p["protein_g"] for p in edited_products),
            "fat_total_g": sum(p["fat_total_g"] for p in edited_products),
            "carbohydrates_total_g": sum(p["carbohydrates_total_g"] for p in edited_products),
        }
        
        # Формируем api_details
        api_details_lines = []
        for p in edited_products:
            api_details_lines.append(
                f"• {p['name']} ({p['grams']:.0f} г) — {p['calories']:.0f} ккал "
                f"(Б {p['protein_g']:.1f} / Ж {p['fat_total_g']:.1f} / У {p['carbohydrates_total_g']:.1f})"
            )
        api_details = "\n".join(api_details_lines) if api_details_lines else None
        
        # Обновляем запись
        success = MealRepository.update_meal(
            meal_id=meal_id,
            user_id=user_id,
            description=new_text,
            calories=totals["calories"],
            protein=totals["protein_g"],
            fat=totals["fat_total_g"],
            carbs=totals["carbohydrates_total_g"],
            products_json=json.dumps(edited_products),
            api_details=api_details,
        )
        
        if not success:
            logger.error(f"User {user_id}: Failed to update meal {meal_id}")
            await message.answer("❌ Не нашёл запись для обновления.")
            await state.clear()
            return
        
        await state.clear()
        
        # Показываем обновлённый день
        if isinstance(target_date_str, str):
            try:
                target_date = date.fromisoformat(target_date_str)
            except ValueError:
                target_date = date.today()
        else:
            target_date = date.today()
        
        await message.answer("✅ Приём пищи обновлён!")
        await show_day_meals(message, user_id, target_date)
        
    except Exception as e:
        logger.error(f"Error in handle_meal_edit_input for user {user_id}: {e}", exc_info=True)
        await message.answer(
            "❌ Произошла ошибка при обработке данных.\n"
            "Попробуй ещё раз или удали и создай запись заново."
        )
        await state.clear()


@router.callback_query(lambda c: c.data.startswith("meal_del:"))
async def delete_meal(callback: CallbackQuery):
    """Удаляет приём пищи."""
    await callback.answer()
    parts = callback.data.split(":")
    meal_id = int(parts[1])
    target_date = date.fromisoformat(parts[2]) if len(parts) > 2 else date.today()
    user_id = str(callback.from_user.id)
    
    success = MealRepository.delete_meal(meal_id, user_id)
    if success:
        await callback.message.answer("✅ Запись удалена")
        await show_day_meals(callback.message, user_id, target_date)
    else:
        await callback.message.answer("❌ Не удалось удалить запись")


@router.callback_query(lambda c: c.data == "kbju_test_start")
async def start_kbju_test_from_button(callback: CallbackQuery, state: FSMContext):
    """Начинает тест КБЖУ из inline кнопки."""
    await callback.answer()
    from utils.keyboards import kbju_gender_menu
    from states.user_states import KbjuTestStates
    
    await state.clear()
    await state.set_state(KbjuTestStates.entering_gender)
    
    push_menu_stack(callback.message.bot, kbju_gender_menu)
    await callback.message.answer(
        "Для начала выбери пол:",
        reply_markup=kbju_gender_menu,
    )


def register_meal_handlers(dp):
    """Регистрирует обработчики КБЖУ."""
    dp.include_router(router)
