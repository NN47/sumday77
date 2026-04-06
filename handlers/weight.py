"""Обработчики для веса и замеров."""
import logging
from datetime import date, timedelta, datetime
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, ReplyKeyboardMarkup, KeyboardButton
from aiogram.fsm.context import FSMContext
from typing import Optional
from utils.keyboards import (
    WEIGHT_AND_MEASUREMENTS_BUTTON_TEXT,
    push_menu_stack,
    main_menu_button,
    other_day_menu,
)
from database.repositories import WeightRepository
from states.user_states import WeightStates
from utils.validators import parse_weight, parse_date
from utils.calendar_utils import (
    build_weight_calendar_keyboard,
    build_weight_day_actions_keyboard,
    build_measurement_calendar_keyboard,
    build_measurement_day_actions_keyboard,
)

logger = logging.getLogger(__name__)

router = Router()

# Меню для веса и замеров

weight_menu = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="➕ Добавить вес")],
        [KeyboardButton(text="📏 Замеры тела"), KeyboardButton(text="📊 График веса")],
        [KeyboardButton(text="📆 Календарь")],
        [KeyboardButton(text="⬅️ Назад"), main_menu_button],
    ],
    resize_keyboard=True,
)

measurements_menu = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="➕ Добавить замеры")],
        [KeyboardButton(text="📅 История замеров")],
        [KeyboardButton(text="📆 Календарь замеров")],
        [KeyboardButton(text="⬅️ Назад"), main_menu_button],
    ],
    resize_keyboard=True,
)

weight_and_measurements_menu = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="⚖️ Вес"), KeyboardButton(text="📏 Замеры")],
        [main_menu_button],
    ],
    resize_keyboard=True,
)

MEASUREMENT_STEPS = [
    {"key": "chest", "label": "Грудь", "question": "Введи грудь в см", "db_field": "chest"},
    {"key": "waist", "label": "Талия", "question": "Введи талию в см", "db_field": "waist"},
    {"key": "hips", "label": "Бёдра", "question": "Введи бёдра в см", "db_field": "hips"},
    {"key": "arm", "label": "Рука", "question": "Введи руку в см", "db_field": "biceps"},
    {"key": "leg", "label": "Нога", "question": "Введи ногу в см", "db_field": "thigh"},
]

measurements_date_menu = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="📅 Сегодня"), KeyboardButton(text="🗓 Другой день")],
        [KeyboardButton(text="⬅️ Назад")],
    ],
    resize_keyboard=True,
)

measurements_step_menu = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="Пропустить")],
        [KeyboardButton(text="Назад"), KeyboardButton(text="Отмена")],
    ],
    resize_keyboard=True,
)

measurements_review_menu = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="✅ Сохранить"), KeyboardButton(text="✏️ Изменить")],
        [KeyboardButton(text="⬅️ Назад")],
    ],
    resize_keyboard=True,
)


def _to_float_weight(raw_value: str | float | int | None) -> Optional[float]:
    """Преобразует значение веса в float."""
    if raw_value is None:
        return None
    try:
        return float(str(raw_value).replace(",", "."))
    except (TypeError, ValueError):
        return None


def _find_reference_weight(weights: list, current_date: date, days_ago: int):
    """Возвращает ближайшую запись, которая была не позднее current_date - days_ago."""
    target_date = current_date - timedelta(days=days_ago)
    for weight in weights:
        if weight.date <= target_date:
            return weight
    return None


def _format_change(current_value: float, reference_value: Optional[float]) -> str:
    """Форматирует изменение между двумя значениями веса."""
    if reference_value is None:
        return "Недостаточно данных"
    change = current_value - reference_value
    sign = "+" if change > 0 else ""
    return f"{sign}{change:.1f} кг"


def _detect_trend(last_weights: list) -> str:
    """Определяет тренд на основе 3 последних записей."""
    if len(last_weights) < 3:
        return "Недостаточно данных"

    latest = _to_float_weight(last_weights[0].value)
    previous = _to_float_weight(last_weights[1].value)
    oldest = _to_float_weight(last_weights[2].value)
    if latest is None or previous is None or oldest is None:
        return "Недостаточно данных"

    epsilon = 0.15
    if latest < previous - epsilon and previous < oldest - epsilon:
        return "Снижение веса 📉"
    if latest > previous + epsilon and previous > oldest + epsilon:
        return "Рост веса 📈"
    return "Стабильно ⚖️"


def _build_progress_bar(progress_percent: float, length: int = 10) -> str:
    """Возвращает текстовый прогресс-бар."""
    clamped = max(0.0, min(100.0, progress_percent))
    filled = round((clamped / 100) * length)
    return f"{'█' * filled}{'░' * (length - filled)} {clamped:.0f}%"


def _parse_measurement_value(raw_value: str) -> Optional[float]:
    """Парсит введённое значение замера."""
    try:
        return float(raw_value.replace(",", "."))
    except (TypeError, ValueError, AttributeError):
        return None


def _build_weight_sparkline(values: list[float]) -> str:
    """Строит компактный текстовый график веса."""
    if not values:
        return ""
    if len(values) == 1:
        return "▅"

    blocks = "▁▂▃▄▅▆▇█"
    minimum = min(values)
    maximum = max(values)

    if abs(maximum - minimum) < 1e-9:
        return "▅" * len(values)

    result = []
    for value in values:
        ratio = (value - minimum) / (maximum - minimum)
        index = round(ratio * (len(blocks) - 1))
        result.append(blocks[index])
    return "".join(result)


def _format_measurement_value(value: Optional[float]) -> str:
    """Форматирует значение замера."""
    if value is None:
        return "—"
    normalized = f"{value:.1f}"
    if normalized.endswith(".0"):
        normalized = normalized[:-2]
    return f"{normalized} см"


def _format_measurements_card_from_draft(draft: dict) -> str:
    """Формирует карточку замеров из draft-данных FSM."""
    return (
        f"Грудь: {_format_measurement_value(draft.get('chest'))}\n"
        f"Талия: {_format_measurement_value(draft.get('waist'))}\n"
        f"Бёдра: {_format_measurement_value(draft.get('hips'))}\n"
        f"Рука: {_format_measurement_value(draft.get('arm'))}\n"
        f"Нога: {_format_measurement_value(draft.get('leg'))}"
    )


async def _show_measurement_step(message: Message, state: FSMContext):
    """Показывает текущий шаг мастера замеров."""
    data = await state.get_data()
    step_index = data.get("current_step", 0)
    entry_date_str = data.get("entry_date", date.today().isoformat())
    step = MEASUREMENT_STEPS[step_index]
    entry_date = date.fromisoformat(entry_date_str)

    await message.answer(
        "📏 Замеры тела\n"
        f"📅 Дата: {entry_date.strftime('%d.%m.%Y')}\n"
        f"Шаг {step_index + 1} из {len(MEASUREMENT_STEPS)}\n\n"
        f"{step['question']}",
        reply_markup=measurements_step_menu,
    )


async def _show_measurements_review(message: Message, state: FSMContext):
    """Показывает подтверждение перед сохранением замеров."""
    data = await state.get_data()
    draft = data.get("draft_measurements", {})
    entry_date_str = data.get("entry_date", date.today().isoformat())
    entry_date = date.fromisoformat(entry_date_str)

    await state.set_state(WeightStates.reviewing_measurements)
    await message.answer(
        "✅ Проверь замеры\n\n"
        f"📅 Дата: {entry_date.strftime('%d.%m.%Y')}\n\n"
        f"{_format_measurements_card_from_draft(draft)}",
        reply_markup=measurements_review_menu,
    )


async def _start_measurements_wizard(
    message: Message,
    state: FSMContext,
    entry_date: date,
    measurement_id: Optional[int] = None,
    draft: Optional[dict] = None,
):
    """Запускает мастер ввода замеров."""
    await state.update_data(
        entry_date=entry_date.isoformat(),
        measurement_id=measurement_id,
        current_step=0,
        draft_measurements=draft or {step["key"]: None for step in MEASUREMENT_STEPS},
    )
    await state.set_state(WeightStates.entering_measurements)
    await _show_measurement_step(message, state)


@router.message(lambda m: m.text == WEIGHT_AND_MEASUREMENTS_BUTTON_TEXT)
async def weight_and_measurements(message: Message):
    """Показывает меню веса и замеров."""
    user_id = str(message.from_user.id)
    logger.info(f"User {user_id} opened weight and measurements menu")
    push_menu_stack(message.bot, weight_and_measurements_menu)
    await message.answer(
        f"{WEIGHT_AND_MEASUREMENTS_BUTTON_TEXT}\n\nВыбери действие:",
        reply_markup=weight_and_measurements_menu,
    )


@router.message(lambda m: m.text == "⚖️ Вес")
async def my_weight(message: Message):
    """Показывает сводку по весу пользователя."""
    user_id = str(message.from_user.id)
    logger.info(f"User {user_id} viewed weight dashboard")

    weights = WeightRepository.get_weights(user_id)

    if not weights:
        push_menu_stack(message.bot, weight_menu)
        await message.answer(
            "⚖️ Вес\n\n"
            "Пока нет записей веса.\n"
            "Добавь первую запись, и я покажу динамику, тренд и прогресс к цели.",
            reply_markup=weight_menu,
        )
        return

    current_entry = weights[0]
    current_weight = _to_float_weight(current_entry.value)
    if current_weight is None:
        push_menu_stack(message.bot, weight_menu)
        await message.answer("⚠️ Не удалось прочитать последнее значение веса.", reply_markup=weight_menu)
        return

    reference_7 = _find_reference_weight(weights, current_entry.date, 7)
    reference_30 = _find_reference_weight(weights, current_entry.date, 30)

    change_7 = _format_change(current_weight, _to_float_weight(reference_7.value) if reference_7 else None)
    change_30 = _format_change(current_weight, _to_float_weight(reference_30.value) if reference_30 else None)

    target_weight = WeightRepository.get_target_weight(user_id)
    to_goal_text = "Цель веса не задана"
    progress_text = "Прогресс: недоступен"
    if target_weight is not None:
        delta_to_target = target_weight - current_weight
        if abs(delta_to_target) < 0.05:
            to_goal_text = "Цель достигнута 🎉"
            progress_text = f"Прогресс:\n{_build_progress_bar(100)}"
        else:
            start_weight = _to_float_weight(weights[-1].value)
            if start_weight is not None and abs(target_weight - start_weight) > 0:
                progress = (current_weight - start_weight) / (target_weight - start_weight) * 100
                progress_text = f"Прогресс:\n{_build_progress_bar(progress)}"
            if delta_to_target > 0:
                to_goal_text = f"Осталось набрать: {delta_to_target:.1f} кг"
            else:
                to_goal_text = f"Осталось: {abs(delta_to_target):.1f} кг"

    trend_text = _detect_trend(weights[:3])

    speed_text = "Недостаточно данных"
    if len(weights) > 1:
        oldest_entry = weights[-1]
        oldest_weight = _to_float_weight(oldest_entry.value)
        if oldest_weight is not None:
            total_days = (current_entry.date - oldest_entry.date).days
            if total_days >= 7:
                weekly_speed = (current_weight - oldest_weight) / (total_days / 7)
                speed_text = f"{weekly_speed:+.2f} кг в неделю"

    recent_rows = []
    for entry in weights[:7]:
        entry_value = _to_float_weight(entry.value)
        if entry_value is None:
            continue
        recent_rows.append(f"{entry.date.strftime('%d.%m')} — {entry_value:.2f} кг")

    recent_text = "\n".join(recent_rows) if recent_rows else "Нет записей для отображения"
    text = (
        "⚖️ Вес\n\n"
        f"Текущий вес: {current_weight:.2f} кг\n\n"
        "📉 Изменение:\n"
        f"За 7 дней: {change_7}\n"
        f"За 30 дней: {change_30}\n\n"
        "🎯 До цели:\n"
        f"{to_goal_text}\n\n"
        "📊 Тренд:\n"
        f"{trend_text}\n\n"
        "🚀 Средняя скорость:\n"
        f"{speed_text}\n\n"
        f"{progress_text}\n\n"
        "📅 Последние записи:\n\n"
        f"{recent_text}"
    )

    push_menu_stack(message.bot, weight_menu)
    await message.answer(text, reply_markup=weight_menu)


@router.message(lambda m: m.text == "📏 Замеры")
async def my_measurements(message: Message):
    """Показывает экран замеров тела."""
    user_id = str(message.from_user.id)
    logger.info(f"User {user_id} opened measurements screen")

    measurements = WeightRepository.get_measurements(user_id, limit=1)
    latest = measurements[0] if measurements else None

    text = (
        "📏 Замеры тела\n\n"
        f"Грудь: {_format_measurement_value(latest.chest) if latest else '—'}\n"
        f"Талия: {_format_measurement_value(latest.waist) if latest else '—'}\n"
        f"Бёдра: {_format_measurement_value(latest.hips) if latest else '—'}\n"
        f"Рука: {_format_measurement_value(latest.biceps) if latest else '—'}\n"
        f"Нога: {_format_measurement_value(latest.thigh) if latest else '—'}"
    )

    push_menu_stack(message.bot, measurements_menu)
    await message.answer(text, reply_markup=measurements_menu)


@router.message(lambda m: m.text == "📏 Замеры тела")
async def open_measurements_from_weight(message: Message):
    """Открывает экран замеров из раздела веса."""
    await my_measurements(message)


@router.message(lambda m: m.text == "📅 История замеров")
async def show_measurements_history(message: Message):
    """Показывает компактную историю замеров."""
    user_id = str(message.from_user.id)
    measurements = WeightRepository.get_measurements(user_id, limit=7)
    if not measurements:
        await message.answer("📏 Пока нет истории замеров.")
        return

    lines = ["📅 История замеров:\n"]
    for item in measurements:
        lines.append(f"{item.date.strftime('%d.%m')} — {format_measurements_summary(item)}")
    await message.answer("\n".join(lines), reply_markup=measurements_menu)


@router.message(lambda m: m.text == "📊 График веса")
async def show_weight_graph(message: Message):
    """Показывает текстовый график и всю историю веса."""
    user_id = str(message.from_user.id)
    weights = WeightRepository.get_weights(user_id)

    if not weights:
        await message.answer(
            "📊 График веса\n\n"
            "Пока нет записей веса.\n"
            "Нажми «➕ Добавить вес», чтобы увидеть график и историю."
        )
        return

    chronological_entries = list(reversed(weights))
    chart_values = []
    for entry in chronological_entries:
        value = _to_float_weight(entry.value)
        if value is not None:
            chart_values.append(value)

    if not chart_values:
        await message.answer(
            "📊 График веса\n\n"
            "Не удалось построить график: значения веса заполнены некорректно."
        )
        return

    sparkline = _build_weight_sparkline(chart_values)
    min_value = min(chart_values)
    max_value = max(chart_values)

    rows = []
    for entry in weights:
        value = _to_float_weight(entry.value)
        if value is None:
            rows.append(f"{entry.date.strftime('%d.%m.%Y')} — некорректное значение")
            continue
        rows.append(f"{entry.date.strftime('%d.%m.%Y')} — {value:.2f} кг")

    await message.answer(
        "📊 График веса\n\n"
        f"{sparkline}\n"
        f"Мин: {min_value:.2f} кг • Макс: {max_value:.2f} кг\n\n"
        "📅 Все введённые веса:\n"
        f"{chr(10).join(rows)}"
    )


def format_measurements_summary(measurements) -> str:
    """Формирует строку замеров для отображения."""
    parts = []
    if measurements.chest is not None:
        parts.append(f"Грудь: {_format_measurement_value(measurements.chest)}")
    if measurements.waist is not None:
        parts.append(f"Талия: {_format_measurement_value(measurements.waist)}")
    if measurements.hips is not None:
        parts.append(f"Бёдра: {_format_measurement_value(measurements.hips)}")
    if measurements.biceps is not None:
        parts.append(f"Рука: {_format_measurement_value(measurements.biceps)}")
    if measurements.thigh is not None:
        parts.append(f"Нога: {_format_measurement_value(measurements.thigh)}")
    return ", ".join(parts) if parts else "нет данных"


@router.message(lambda m: m.text == "➕ Добавить вес")
async def add_weight_start(message: Message, state: FSMContext):
    """Начинает процесс добавления веса за сегодня."""
    user_id = str(message.from_user.id)
    logger.info(f"User {user_id} started adding weight for today")
    
    target_date = date.today()
    
    # Проверяем, есть ли уже вес за сегодня
    existing_weight = WeightRepository.get_weight_for_date(user_id, target_date)
    
    if existing_weight:
        # Если вес уже есть, переходим в режим редактирования
        await state.update_data(entry_date=target_date.isoformat(), weight_id=existing_weight.id)
        await state.set_state(WeightStates.entering_weight)
        await message.answer(
            f"✏️ Изменение веса\n\n"
            f"📅 Дата: {target_date.strftime('%d.%m.%Y')}\n"
            f"Текущий вес: {existing_weight.value} кг\n\n"
            f"Введи новый вес в килограммах (например: 72.5):"
        )
    else:
        # Если веса нет, создаем новую запись
        await state.update_data(entry_date=target_date.isoformat(), weight_id=None)
        await state.set_state(WeightStates.entering_weight)
        await message.answer(f"📅 Дата: {target_date.strftime('%d.%m.%Y')}\n\nВведи свой вес в килограммах (например: 72.5):")


@router.message(WeightStates.choosing_date_for_weight)
async def handle_weight_date_choice(message: Message, state: FSMContext):
    """Обрабатывает выбор даты для веса."""
    if message.text == "📅 Сегодня":
        target_date = date.today()
    elif message.text == "📆 Другой день":
        push_menu_stack(message.bot, other_day_menu)
        await message.answer(
            "Выбери день или введи дату вручную:",
            reply_markup=other_day_menu,
        )
        return
    elif message.text == "📅 Вчера":
        target_date = date.today() - timedelta(days=1)
    elif message.text == "📆 Позавчера":
        target_date = date.today() - timedelta(days=2)
    elif message.text == "✏️ Ввести дату вручную":
        await state.set_state(WeightStates.entering_weight)
        await message.answer("Введи дату в формате ДД.ММ.ГГГГ:")
        return
    else:
        # Проверяем, не дата ли это
        parsed = parse_date(message.text)
        if parsed:
            target_date = parsed.date() if isinstance(parsed, datetime) else date.today()
        else:
            await message.answer("Выбери дату из меню или введи в формате ДД.ММ.ГГГГ")
            return
    
    await state.update_data(entry_date=target_date.isoformat())
    await state.set_state(WeightStates.entering_weight)
    await message.answer(f"📅 Дата: {target_date.strftime('%d.%m.%Y')}\n\nВведи свой вес в килограммах (например: 72.5):")


@router.message(WeightStates.entering_weight)
async def handle_weight_input(message: Message, state: FSMContext):
    """Обрабатывает ввод веса."""
    user_id = str(message.from_user.id)
    
    # Сначала проверяем, не дата ли это (если пользователь ввёл дату вручную)
    data = await state.get_data()
    entry_date_str = data.get("entry_date")
    
    # Если дата ещё не установлена, проверяем, не ввёл ли пользователь дату
    if not entry_date_str:
        parsed = parse_date(message.text)
        if parsed:
            target_date = parsed.date() if isinstance(parsed, datetime) else date.today()
            await state.update_data(entry_date=target_date.isoformat())
            await message.answer(f"📅 Дата: {target_date.strftime('%d.%m.%Y')}\n\nВведи свой вес в килограммах (например: 72.5):")
            return
    
    weight_value = parse_weight(message.text)
    if weight_value is None or weight_value <= 0:
        await message.answer("⚠️ Введи положительное число (например: 72.5 или 72,5)")
        return
    
    # Получаем дату из состояния (обновляем data на случай, если дата была установлена выше)
    data = await state.get_data()
    entry_date_str = data.get("entry_date", date.today().isoformat())
    weight_id = data.get("weight_id")
    
    if isinstance(entry_date_str, str):
        try:
            entry_date = date.fromisoformat(entry_date_str)
        except ValueError:
            parsed = parse_date(entry_date_str)
            entry_date = parsed.date() if isinstance(parsed, datetime) else date.today()
    else:
        entry_date = date.today()
    
    previous_weight_value = None
    existing_weights = WeightRepository.get_weights(user_id)
    for existing in existing_weights:
        existing_value = _to_float_weight(existing.value)
        if existing_value is None:
            continue
        if weight_id and existing.id == weight_id:
            continue
        if existing.date <= entry_date:
            previous_weight_value = existing_value
            break

    # Сохраняем или обновляем вес
    try:
        if weight_id:
            # Редактирование существующей записи
            success = WeightRepository.update_weight(weight_id, user_id, str(weight_value))
            if success:
                logger.info(f"User {user_id} updated weight {weight_id}: {weight_value} kg on {entry_date}")
                await state.clear()
                # Показываем обновленный день в календаре, если это было из календаря
                delta_text = ""
                if previous_weight_value is not None:
                    delta = weight_value - previous_weight_value
                    direction = "📉" if delta < 0 else "📈" if delta > 0 else "⚖️"
                    delta_text = f"\n\nИзменение:\n{delta:+.2f} кг с прошлой записи {direction}"
                await message.answer(
                    f"✅ Вес обновлён!\n\n"
                    f"⚖️ {weight_value:.1f} кг\n"
                    f"📅 {entry_date.strftime('%d.%m.%Y')}"
                    f"{delta_text}",
                )
                # Если это было из календаря, показываем день снова
                await show_day_weight(message, user_id, entry_date)
            else:
                await message.answer("⚠️ Не удалось обновить запись.")
                await state.clear()
        else:
            # Создание новой записи
            WeightRepository.save_weight(user_id, str(weight_value), entry_date)
            logger.info(f"User {user_id} saved weight: {weight_value} kg on {entry_date}")
            
            await state.clear()
            push_menu_stack(message.bot, weight_menu)
            delta_text = "\nИзменение:\nНедостаточно данных"
            comment_text = ""
            if previous_weight_value is not None:
                delta = weight_value - previous_weight_value
                direction = "📉" if delta < 0 else "📈" if delta > 0 else "⚖️"
                delta_text = f"\nИзменение:\n{delta:+.2f} кг с прошлой записи {direction}"
                if delta < 0:
                    comment_text = "\n\nОтличная динамика."
                elif delta > 0:
                    comment_text = "\n\nВес немного вырос.\nОбрати внимание на питание и воду."
                else:
                    comment_text = "\n\nВес без изменений. Продолжай в том же ритме."
            await message.answer(
                f"✅ Вес сохранён!\n\n"
                f"{weight_value:.1f} кг"
                f"{delta_text}"
                f"{comment_text}",
                reply_markup=weight_menu,
            )
    except Exception as e:
        logger.error(f"Error saving/updating weight: {e}", exc_info=True)
        await message.answer("⚠️ Ошибка при сохранении. Повтори попытку позже.")
        await state.clear()


@router.message(lambda m: m.text == "🗑 Удалить вес")
async def delete_weight_start(message: Message, state: FSMContext):
    """Начинает процесс удаления веса."""
    user_id = str(message.from_user.id)
    weights = WeightRepository.get_weights(user_id)
    
    if not weights:
        push_menu_stack(message.bot, weight_menu)
        await message.answer("⚖️ У тебя нет записей веса для удаления.", reply_markup=weight_menu)
        return
    
    # Сохраняем веса в FSM для выбора
    await state.update_data(weights_to_delete=[{"id": w.id, "date": w.date.isoformat(), "value": w.value} for w in weights])
    await state.set_state(WeightStates.choosing_period)
    
    text = "Выбери номер веса для удаления:\n\n"
    for i, w in enumerate(weights, 1):
        text += f"{i}. {w.date.strftime('%d.%m.%Y')} — {w.value} кг\n"
    
    await message.answer(text)


@router.message(WeightStates.choosing_period)
async def handle_weight_delete_choice(message: Message, state: FSMContext):
    """Обрабатывает выбор веса для удаления."""
    user_id = str(message.from_user.id)
    
    try:
        index = int(message.text) - 1
        data = await state.get_data()
        weights_list = data.get("weights_to_delete", [])
        
        if 0 <= index < len(weights_list):
            weight_data = weights_list[index]
            weight_id = weight_data["id"]
            
            success = WeightRepository.delete_weight(weight_id, user_id)
            if success:
                await message.answer(
                    f"✅ Удалил запись: {weight_data['date']} — {weight_data['value']} кг"
                )
            else:
                await message.answer("❌ Не нашёл такую запись в базе.")
        else:
            await message.answer("⚠️ Нет такой записи.")
    except (ValueError, KeyError):
        await message.answer("⚠️ Введи номер записи")
    
    await state.clear()
    push_menu_stack(message.bot, weight_menu)
    await message.answer("Выбери действие:", reply_markup=weight_menu)


@router.message(lambda m: m.text == "➕ Добавить замеры")
async def add_measurements_start(message: Message, state: FSMContext):
    """Начинает процесс добавления замеров."""
    user_id = str(message.from_user.id)
    logger.info(f"User {user_id} started adding measurements")

    await state.set_state(WeightStates.choosing_date_for_measurements)

    push_menu_stack(message.bot, measurements_date_menu)
    await message.answer(
        "📏 Добавим замеры\n\n"
        "За какую дату сохранить замеры?",
        reply_markup=measurements_date_menu,
    )


@router.message(WeightStates.choosing_date_for_measurements)
async def handle_measurements_date_choice(message: Message, state: FSMContext):
    """Обрабатывает выбор даты для замеров."""
    if message.text == "📅 Сегодня":
        await _start_measurements_wizard(message, state, date.today())
        return
    if message.text == "🗓 Другой день":
        await state.set_state(WeightStates.entering_measurements_date)
        await message.answer("Введи дату в формате ДД.ММ.ГГГГ:")
        return
    if message.text == "⬅️ Назад":
        await state.clear()
        await my_measurements(message)
        return

    await message.answer("Выбери дату кнопкой или нажми «🗓 Другой день».")


@router.message(WeightStates.entering_measurements_date)
async def handle_measurements_manual_date(message: Message, state: FSMContext):
    """Обрабатывает ввод даты для мастера замеров."""
    if message.text == "⬅️ Назад":
        await state.set_state(WeightStates.choosing_date_for_measurements)
        await message.answer(
            "📏 Добавим замеры\n\nЗа какую дату сохранить замеры?",
            reply_markup=measurements_date_menu,
        )
        return

    parsed = parse_date(message.text)
    if not parsed:
        await message.answer("Не удалось распознать дату. Введи дату в формате ДД.ММ.ГГГГ")
        return

    await _start_measurements_wizard(message, state, parsed.date())


@router.message(WeightStates.entering_measurements)
async def handle_measurements_input(message: Message, state: FSMContext):
    """Обрабатывает пошаговый ввод замеров."""
    text = (message.text or "").strip()
    data = await state.get_data()
    draft = data.get("draft_measurements", {step["key"]: None for step in MEASUREMENT_STEPS})
    step_index = data.get("current_step", 0)

    if text == "Отмена":
        await state.clear()
        await my_measurements(message)
        return

    if text == "Назад":
        if step_index > 0:
            await state.update_data(current_step=step_index - 1)
        await _show_measurement_step(message, state)
        return

    if text == "Пропустить":
        draft[MEASUREMENT_STEPS[step_index]["key"]] = None
        step_index += 1
    else:
        value = _parse_measurement_value(text)
        if value is None:
            await message.answer(
                "Нужно ввести только число в сантиметрах.\nНапример: 90",
                reply_markup=measurements_step_menu,
            )
            return
        draft[MEASUREMENT_STEPS[step_index]["key"]] = value
        step_index += 1

    await state.update_data(draft_measurements=draft, current_step=step_index)
    if step_index >= len(MEASUREMENT_STEPS):
        await _show_measurements_review(message, state)
        return
    await _show_measurement_step(message, state)


@router.message(WeightStates.reviewing_measurements)
async def handle_measurements_review(message: Message, state: FSMContext):
    """Обрабатывает действия на шаге подтверждения замеров."""
    text = (message.text or "").strip()
    data = await state.get_data()
    draft = data.get("draft_measurements", {})
    current_step = data.get("current_step", len(MEASUREMENT_STEPS))

    if text == "⬅️ Назад":
        await state.update_data(current_step=max(0, current_step - 1))
        await state.set_state(WeightStates.entering_measurements)
        await _show_measurement_step(message, state)
        return

    if text == "✏️ Изменить":
        await state.update_data(current_step=0, draft_measurements=draft)
        await state.set_state(WeightStates.entering_measurements)
        await _show_measurement_step(message, state)
        return

    if text != "✅ Сохранить":
        await message.answer("Выбери действие: ✅ Сохранить, ✏️ Изменить или ⬅️ Назад.")
        return

    user_id = str(message.from_user.id)
    entry_date = date.fromisoformat(data.get("entry_date", date.today().isoformat()))
    measurement_id = data.get("measurement_id")
    db_payload = {
        step["db_field"]: draft.get(step["key"])
        for step in MEASUREMENT_STEPS
    }

    try:
        if measurement_id:
            success = WeightRepository.update_measurement(measurement_id, user_id, db_payload)
            if not success:
                await message.answer("⚠️ Не удалось обновить замеры.")
                await state.clear()
                return
            logger.info(f"User {user_id} updated measurements {measurement_id} on {entry_date}")
        else:
            WeightRepository.save_measurements(user_id, db_payload, entry_date)
            logger.info(f"User {user_id} saved measurements on {entry_date}")

        await state.clear()
        push_menu_stack(message.bot, measurements_menu)
        await message.answer("✅ Замеры сохранены", reply_markup=measurements_menu)
        await show_day_measurements(message, user_id, entry_date)
    except Exception as e:
        logger.error(f"Error saving measurements: {e}", exc_info=True)
        await message.answer("⚠️ Ошибка при сохранении. Повтори попытку позже.")
        await state.clear()


@router.message(lambda m: m.text == "🗑 Удалить замеры")
async def delete_measurements_start(message: Message, state: FSMContext):
    """Начинает процесс удаления замеров."""
    user_id = str(message.from_user.id)
    measurements = WeightRepository.get_measurements(user_id)
    
    if not measurements:
        push_menu_stack(message.bot, measurements_menu)
        await message.answer("📏 У тебя нет замеров для удаления.", reply_markup=measurements_menu)
        return
    
    # Сохраняем замеры в FSM
    await state.update_data(
        measurements_to_delete=[
            {"id": m.id, "date": m.date.isoformat()} for m in measurements
        ]
    )
    await state.set_state(WeightStates.choosing_period)
    
    text = "Выбери номер замеров для удаления:\n\n"
    for i, m in enumerate(measurements, 1):
        parts = []
        if m.chest:
            parts.append(f"Грудь: {m.chest}")
        if m.waist:
            parts.append(f"Талия: {m.waist}")
        if m.hips:
            parts.append(f"Бёдра: {m.hips}")
        if m.biceps:
            parts.append(f"Рука: {m.biceps}")
        if m.thigh:
            parts.append(f"Нога: {m.thigh}")
        
        summary = ", ".join(parts) if parts else "нет данных"
        text += f"{i}. {m.date.strftime('%d.%m.%Y')} — {summary}\n"
    
    await message.answer(text)


@router.message(WeightStates.choosing_period)
async def handle_measurements_delete_choice(message: Message, state: FSMContext):
    """Обрабатывает выбор замеров для удаления."""
    user_id = str(message.from_user.id)
    
    try:
        index = int(message.text) - 1
        data = await state.get_data()
        measurements_list = data.get("measurements_to_delete", [])
        
        if 0 <= index < len(measurements_list):
            measurement_data = measurements_list[index]
            measurement_id = measurement_data["id"]
            
            success = WeightRepository.delete_measurement(measurement_id, user_id)
            if success:
                await message.answer(
                    f"✅ Удалил замеры от {measurement_data['date']}"
                )
            else:
                await message.answer("❌ Не нашёл такие замеры в базе.")
        else:
            await message.answer("⚠️ Нет такой записи.")
    except (ValueError, KeyError):
        # Если это не число, возможно это выбор веса для удаления
        data = await state.get_data()
        if "weights_to_delete" in data:
            await handle_weight_delete_choice(message, state)
            return
        await message.answer("⚠️ Введи номер записи")
    
    await state.clear()
    push_menu_stack(message.bot, measurements_menu)
    await message.answer("Выбери действие:", reply_markup=measurements_menu)


@router.message(lambda m: m.text == "📆 Календарь")
async def show_weight_calendar(message: Message):
    """Показывает календарь веса."""
    user_id = str(message.from_user.id)
    logger.info(f"User {user_id} opened weight calendar")
    await show_weight_calendar_view(message, user_id)


@router.message(lambda m: m.text == "📆 Календарь замеров")
async def show_measurements_calendar(message: Message):
    """Показывает календарь замеров."""
    user_id = str(message.from_user.id)
    logger.info(f"User {user_id} opened measurements calendar")
    await show_measurements_calendar_view(message, user_id)


async def show_weight_calendar_view(message: Message, user_id: str, year: Optional[int] = None, month: Optional[int] = None):
    """Показывает календарь веса."""
    today = date.today()
    year = year or today.year
    month = month or today.month
    keyboard = build_weight_calendar_keyboard(user_id, year, month)
    await message.answer(
        "📆 Календарь веса\n\nВыбери день, чтобы посмотреть, изменить или удалить вес:",
        reply_markup=keyboard,
    )


async def show_measurements_calendar_view(message: Message, user_id: str, year: Optional[int] = None, month: Optional[int] = None):
    """Показывает календарь замеров."""
    today = date.today()
    year = year or today.year
    month = month or today.month
    keyboard = build_measurement_calendar_keyboard(user_id, year, month)
    await message.answer(
        "📆 Календарь замеров\n\nВыбери день, чтобы посмотреть, изменить или удалить замеры:",
        reply_markup=keyboard,
    )


@router.callback_query(lambda c: c.data.startswith("weight_cal_nav:"))
async def navigate_weight_calendar(callback: CallbackQuery):
    """Навигация по календарю веса."""
    await callback.answer()
    parts = callback.data.split(":")
    year, month = map(int, parts[1].split("-"))
    user_id = str(callback.from_user.id)
    await show_weight_calendar_view(callback.message, user_id, year, month)


@router.callback_query(lambda c: c.data.startswith("weight_cal_back:"))
async def back_to_weight_calendar(callback: CallbackQuery):
    """Возврат к календарю веса."""
    await callback.answer()
    parts = callback.data.split(":")
    year, month = map(int, parts[1].split("-"))
    user_id = str(callback.from_user.id)
    await show_weight_calendar_view(callback.message, user_id, year, month)


@router.callback_query(lambda c: c.data.startswith("meas_cal_nav:"))
async def navigate_measurements_calendar(callback: CallbackQuery):
    """Навигация по календарю замеров."""
    await callback.answer()
    parts = callback.data.split(":")
    year, month = map(int, parts[1].split("-"))
    user_id = str(callback.from_user.id)
    await show_measurements_calendar_view(callback.message, user_id, year, month)


@router.callback_query(lambda c: c.data.startswith("meas_cal_back:"))
async def back_to_measurements_calendar(callback: CallbackQuery):
    """Возврат к календарю замеров."""
    await callback.answer()
    parts = callback.data.split(":")
    year, month = map(int, parts[1].split("-"))
    user_id = str(callback.from_user.id)
    await show_measurements_calendar_view(callback.message, user_id, year, month)


@router.callback_query(lambda c: c.data.startswith("weight_cal_day:"))
async def select_weight_calendar_day(callback: CallbackQuery):
    """Выбор дня в календаре веса."""
    await callback.answer()
    parts = callback.data.split(":")
    target_date = date.fromisoformat(parts[1])
    user_id = str(callback.from_user.id)
    await show_day_weight(callback.message, user_id, target_date)


@router.callback_query(lambda c: c.data.startswith("meas_cal_day:"))
async def select_measurements_calendar_day(callback: CallbackQuery):
    """Выбор дня в календаре замеров."""
    await callback.answer()
    parts = callback.data.split(":")
    target_date = date.fromisoformat(parts[1])
    user_id = str(callback.from_user.id)
    await show_day_measurements(callback.message, user_id, target_date)


async def show_day_weight(message: Message, user_id: str, target_date: date):
    """Показывает вес за день."""
    weight = WeightRepository.get_weight_for_date(user_id, target_date)
    
    if not weight:
        await message.answer(
            f"{target_date.strftime('%d.%m.%Y')}: нет записи веса.",
            reply_markup=build_weight_day_actions_keyboard(None, target_date),
        )
        return
    
    text = f"📅 {target_date.strftime('%d.%m.%Y')}\n\n⚖️ Вес: {weight.value} кг"
    
    await message.answer(
        text,
        reply_markup=build_weight_day_actions_keyboard(weight, target_date),
    )


async def show_day_measurements(message: Message, user_id: str, target_date: date):
    """Показывает замеры за день."""
    measurements = WeightRepository.get_measurement_for_date(user_id, target_date)

    if not measurements:
        await message.answer(
            f"{target_date.strftime('%d.%m.%Y')}: нет записи замеров.",
            reply_markup=build_measurement_day_actions_keyboard(None, target_date),
        )
        return

    draft = {
        "chest": measurements.chest,
        "waist": measurements.waist,
        "hips": measurements.hips,
        "arm": measurements.biceps,
        "leg": measurements.thigh,
    }
    text = (
        "📏 Замеры тела\n\n"
        f"📅 Дата: {target_date.strftime('%d.%m.%Y')}\n\n"
        f"{_format_measurements_card_from_draft(draft)}"
    )

    await message.answer(
        text,
        reply_markup=build_measurement_day_actions_keyboard(measurements, target_date),
    )


@router.callback_query(lambda c: c.data.startswith("weight_cal_add:"))
async def add_weight_from_calendar(callback: CallbackQuery, state: FSMContext):
    """Добавляет или обновляет вес из календаря."""
    await callback.answer()
    parts = callback.data.split(":")
    target_date = date.fromisoformat(parts[1])
    user_id = str(callback.from_user.id)
    
    # Проверяем, есть ли уже вес за этот день
    existing_weight = WeightRepository.get_weight_for_date(user_id, target_date)
    
    if existing_weight:
        # Если вес уже есть, переходим в режим редактирования
        await state.update_data(entry_date=target_date.isoformat(), weight_id=existing_weight.id)
        await state.set_state(WeightStates.entering_weight)
        await callback.message.answer(
            f"✏️ Изменение веса\n\n"
            f"📅 Дата: {target_date.strftime('%d.%m.%Y')}\n"
            f"Текущий вес: {existing_weight.value} кг\n\n"
            f"Введи новый вес в килограммах (например: 72.5):"
        )
    else:
        # Если веса нет, создаем новую запись
        await state.update_data(entry_date=target_date.isoformat(), weight_id=None)
        await state.set_state(WeightStates.entering_weight)
        await callback.message.answer(f"📅 Дата: {target_date.strftime('%d.%m.%Y')}\n\nВведи свой вес в килограммах (например: 72.5):")


@router.callback_query(lambda c: c.data.startswith("meas_cal_add:"))
async def add_measurements_from_calendar(callback: CallbackQuery, state: FSMContext):
    """Добавляет или обновляет замеры из календаря."""
    await callback.answer()
    parts = callback.data.split(":")
    target_date = date.fromisoformat(parts[1])
    user_id = str(callback.from_user.id)

    existing_measurements = WeightRepository.get_measurement_for_date(user_id, target_date)

    if existing_measurements:
        draft = {
            "chest": existing_measurements.chest,
            "waist": existing_measurements.waist,
            "hips": existing_measurements.hips,
            "arm": existing_measurements.biceps,
            "leg": existing_measurements.thigh,
        }
        await _start_measurements_wizard(
            callback.message,
            state,
            target_date,
            measurement_id=existing_measurements.id,
            draft=draft,
        )
    else:
        await _start_measurements_wizard(callback.message, state, target_date)


@router.callback_query(lambda c: c.data.startswith("weight_cal_edit:"))
async def edit_weight_from_calendar(callback: CallbackQuery, state: FSMContext):
    """Редактирует вес из календаря."""
    await callback.answer()
    parts = callback.data.split(":")
    target_date = date.fromisoformat(parts[1])
    user_id = str(callback.from_user.id)
    
    weight = WeightRepository.get_weight_for_date(user_id, target_date)
    if not weight:
        await callback.message.answer("❌ Не найдена запись веса для редактирования.")
        return
    
    await state.update_data(entry_date=target_date.isoformat(), weight_id=weight.id)
    await state.set_state(WeightStates.entering_weight)
    
    await callback.message.answer(
        f"✏️ Редактирование веса\n\n"
        f"📅 Дата: {target_date.strftime('%d.%m.%Y')}\n"
        f"Текущий вес: {weight.value} кг\n\n"
        f"Введи новый вес в килограммах (например: 72.5):"
    )


@router.callback_query(lambda c: c.data.startswith("meas_cal_edit:"))
async def edit_measurements_from_calendar(callback: CallbackQuery, state: FSMContext):
    """Редактирует замеры из календаря."""
    await callback.answer()
    parts = callback.data.split(":")
    target_date = date.fromisoformat(parts[1])
    user_id = str(callback.from_user.id)

    measurements = WeightRepository.get_measurement_for_date(user_id, target_date)
    if not measurements:
        await callback.message.answer("❌ Не найдены замеры для редактирования.")
        return

    draft = {
        "chest": measurements.chest,
        "waist": measurements.waist,
        "hips": measurements.hips,
        "arm": measurements.biceps,
        "leg": measurements.thigh,
    }
    await _start_measurements_wizard(
        callback.message,
        state,
        target_date,
        measurement_id=measurements.id,
        draft=draft,
    )


@router.callback_query(lambda c: c.data.startswith("weight_cal_del:"))
async def delete_weight_from_calendar(callback: CallbackQuery):
    """Удаляет вес из календаря."""
    await callback.answer()
    parts = callback.data.split(":")
    target_date = date.fromisoformat(parts[1])
    user_id = str(callback.from_user.id)
    
    weight = WeightRepository.get_weight_for_date(user_id, target_date)
    if not weight:
        await callback.message.answer("❌ Не найдена запись веса для удаления.")
        return
    
    success = WeightRepository.delete_weight(weight.id, user_id)
    if success:
        await callback.message.answer("✅ Вес удалён")
        await show_day_weight(callback.message, user_id, target_date)
    else:
        await callback.message.answer("❌ Не удалось удалить запись")


@router.callback_query(lambda c: c.data.startswith("meas_cal_del:"))
async def delete_measurements_from_calendar(callback: CallbackQuery):
    """Удаляет замеры из календаря."""
    await callback.answer()
    parts = callback.data.split(":")
    target_date = date.fromisoformat(parts[1])
    user_id = str(callback.from_user.id)

    measurements = WeightRepository.get_measurement_for_date(user_id, target_date)
    if not measurements:
        await callback.message.answer("❌ Не найдены замеры для удаления.")
        return

    success = WeightRepository.delete_measurement(measurements.id, user_id)
    if success:
        await callback.message.answer("✅ Замеры удалены")
        await show_day_measurements(callback.message, user_id, target_date)
    else:
        await callback.message.answer("❌ Не удалось удалить запись")


def register_weight_handlers(dp):
    """Регистрирует обработчики веса и замеров."""
    dp.include_router(router)
