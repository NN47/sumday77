"""Обработчики для веса и замеров."""
import logging
from datetime import date, timedelta, datetime
from aiogram import Router, F
from aiogram.types import (
    Message,
    CallbackQuery,
    ReplyKeyboardMarkup,
    KeyboardButton,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)
from aiogram.fsm.context import FSMContext
from typing import Optional
from utils.keyboards import (
    WEIGHT_AND_MEASUREMENTS_BUTTON_TEXT,
    main_menu,
    push_menu_stack,
    main_menu_button,
    other_day_menu,
)
from database.repositories import WeightRepository, AnalyticsRepository
from states.user_states import WeightStates
from utils.validators import parse_weight
from utils.calendar_utils import (
    build_weight_calendar_keyboard,
    show_calendar_back_button,
    build_weight_day_actions_keyboard,
    build_measurement_calendar_keyboard,
    build_measurement_day_actions_keyboard,
)

logger = logging.getLogger(__name__)

router = Router()


WEIGHT_QUICK_DELTAS = (-1.0, -0.5, 0.5, 1.0, -0.2, -0.1, 0.1, 0.2)


def _format_weight_delta_button(delta_kg: float) -> str:
    """Форматирует быстрый шаг изменения веса в килограммах для кнопки."""
    sign = "+" if delta_kg > 0 else "-"
    abs_delta = abs(delta_kg)
    formatted = f"{abs_delta:.1f}"
    if abs_delta != 1.0:
        formatted = formatted.rstrip("0").rstrip(".").replace(".", ",")
    return f"{sign}{formatted}"


_WEIGHT_QUICK_DELTAS_BY_LABEL = {
    _format_weight_delta_button(delta): delta for delta in WEIGHT_QUICK_DELTAS
}
_WEIGHT_QUICK_DELTAS_BY_LABEL.update({"-1": -1.0, "+1": 1.0})


def _build_weight_quick_adjust_keyboard(_base_weight: float) -> InlineKeyboardMarkup:
    """Инлайн-клавиатура быстрых изменений веса относительно текущего черновика."""
    delta_rows = [
        [-1.0, -0.5, 0.5, 1.0],
        [-0.2, -0.1, 0.1, 0.2],
    ]

    keyboard = []
    for delta_row in delta_rows:
        keyboard.append(
            [
                InlineKeyboardButton(
                    text=_format_weight_delta_button(delta),
                    callback_data=f"weight_adj:{delta}",
                )
                for delta in delta_row
            ]
        )

    keyboard.extend(
        [
            [InlineKeyboardButton(text="✍️ Ввести вручную", callback_data="weight_manual")],
            [InlineKeyboardButton(text="✅ Сохранить", callback_data="weight_save")],
            [
                InlineKeyboardButton(text="⬅️ Назад", callback_data="weight_back"),
                InlineKeyboardButton(text="❌ Отмена", callback_data="weight_cancel"),
            ],
        ]
    )

    return InlineKeyboardMarkup(inline_keyboard=keyboard)


def _resolve_quick_weight_value(raw_text: str, base_weight: Optional[float]) -> Optional[float]:
    """Возвращает итоговый вес по кнопке быстрого изменения."""
    delta = _WEIGHT_QUICK_DELTAS_BY_LABEL.get(raw_text.strip())
    if delta is None or base_weight is None:
        return None
    return max(1.0, base_weight + delta)


def _build_weight_confirmation_keyboard() -> ReplyKeyboardMarkup:
    """Клавиатура подтверждения перед сохранением веса."""
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="✅ Сохранить"), KeyboardButton(text="✏️ Изменить")],
            [KeyboardButton(text="⬅️ Назад"), main_menu_button],
        ],
        resize_keyboard=True,
    )


def _weight_entry_prompt() -> str:
    """Текст подсказки для экрана ввода веса."""
    return "Выбери изменение кнопкой ниже или введи точный вес вручную:"


def _format_weight_value_for_editor(raw_value: str | float | int | None) -> str:
    """Форматирует вес для редактора без длинных float-хвостов."""
    weight_value = _to_float_weight(raw_value)
    if weight_value is None:
        return str(raw_value)
    return f"{weight_value:.1f}"


def _format_weight_input_screen(
    target_date: date,
    base_weight: Optional[float],
    *,
    current_weight: Optional[str | float] = None,
    edit_title: str = "✏️ Изменение веса",
) -> str:
    """Формирует экран выбора нового значения веса."""
    if current_weight is not None:
        weight_line = f"<b>Текущий вес:</b> {_format_weight_value_for_editor(current_weight)} кг"
        title = f"<b>{edit_title}</b>"
    else:
        weight_line = (
            "<b>Последний внесённый вес:</b> "
            f"{(f'{base_weight:.1f}'.rstrip('0').rstrip('.') if base_weight else 'нет данных')} кг"
        )
        title = "<b>Изменение веса</b>"

    return (
        f"{title}\n\n"
        f"📅 <b>Дата:</b> {target_date.strftime('%d.%m.%Y')}\n"
        f"{weight_line}\n\n"
        f"{_weight_entry_prompt()}"
    )


def _format_weight_delta(delta: float) -> str:
    """Форматирует изменение веса с понятным знаком и emoji."""
    if delta < 0:
        return f"{delta:.1f} кг 📉"
    if delta > 0:
        return f"+{delta:.1f} кг 📈"
    return "0.0 кг ➖"


def _format_weight_confirmation_text(
    weight_value: float,
    entry_date: date,
    previous_weight_value: Optional[float],
    previous_weight_date: Optional[date] = None,
) -> str:
    """Формирует экран подтверждения веса перед сохранением."""
    return _format_weight_draft_text(
        weight_value,
        entry_date,
        previous_weight_value,
        previous_weight_date,
        save_hint="Нажми <b>✅ Сохранить</b>, чтобы записать вес.",
        title="✅ <b>Проверь вес перед сохранением</b>\n\n",
    )


def _format_weight_draft_text(
    weight_value: float,
    entry_date: date,
    previous_weight_value: Optional[float],
    previous_weight_date: Optional[date] = None,
    current_weight_value: Optional[float] = None,
    *,
    save_hint: str = "Можно ещё изменить вес кнопками ниже или нажать <b>✅ Сохранить</b>.",
    title: str = "",
) -> str:
    """Формирует сообщение о черновом весе после быстрой правки."""
    current_weight_lines = [
        f"⚖️ <b>Вес сейчас:</b> {weight_value:.1f} кг",
        f"📅 <b>Дата:</b> {entry_date.strftime('%d.%m.%Y')}",
    ]

    lines = [title.rstrip()] if title else []

    if previous_weight_value is None or previous_weight_date is None:
        lines.extend(current_weight_lines)
        lines.extend([
            "",
            "Это первая запись веса.",
            "После сохранения бот начнёт отслеживать динамику.",
        ])
    else:
        delta = weight_value - previous_weight_value
        lines.extend([
            "<b>Предыдущая запись:</b>",
            f"⚖️ {previous_weight_value:.1f} кг",
            f"📅 {previous_weight_date.strftime('%d.%m.%Y')}",
            "",
            *current_weight_lines,
            "",
            f"<b>Изменение с предыдущей записи:</b> {_format_weight_delta(delta)}",
        ])

    lines.extend(["", save_hint])
    return "\n".join(lines)


def _resolve_base_weight(user_id: str, existing_weight_value: Optional[str | float] = None) -> Optional[float]:
    """Возвращает опорный вес для быстрых кнопок."""
    existing = _to_float_weight(existing_weight_value)
    if existing is not None and existing > 0:
        return existing
    last = WeightRepository.get_last_weight(user_id)
    if last is not None and last > 0:
        return float(last)
    return None


# Меню для веса и замеров

weight_menu = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="➕ Добавить вес")],
        [KeyboardButton(text="📏 Замеры тела"), KeyboardButton(text="📦 Архив")],
        [KeyboardButton(text="📆 Календарь")],
        [main_menu_button],
    ],
    resize_keyboard=True,
)

measurements_menu = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="➕ Добавить замеры")],
        [KeyboardButton(text="📅 История замеров")],
        [KeyboardButton(text="📆 Календарь замеров")],
        [main_menu_button],
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


def _find_period_start_weight(weights: list, current_date: date, days_ago: int):
    """Возвращает самую раннюю запись внутри выбранного периода.

    Для блока «Изменение» нельзя брать записи старше границы периода:
    иначе недельная динамика может считаться по весу месячной давности,
    если внутри недели нет записи ровно на дату начала периода.
    """
    period_start = current_date - timedelta(days=days_ago)
    period_entry = None
    for weight in weights:
        if weight.date == current_date:
            continue
        if period_start <= weight.date <= current_date:
            period_entry = weight
        elif weight.date < period_start:
            break
    return period_entry


def _format_change(current_value: float, reference_value: Optional[float]) -> str:
    """Форматирует изменение между двумя значениями веса."""
    if reference_value is None:
        return "Недостаточно данных"
    change = current_value - reference_value
    sign = "+" if change > 0 else ""
    return f"{sign}{change:.1f} кг"


def _detect_trend(weights: list) -> str:
    """Определяет тренд по изменению относительно ближайшей записи недельной давности."""
    if len(weights) < 2:
        return "Недостаточно данных"

    current_entry = weights[0]
    current_value = _to_float_weight(current_entry.value)
    if current_value is None:
        return "Недостаточно данных"

    reference_entry = _find_reference_weight(weights[1:], current_entry.date, 7)
    if reference_entry is None:
        reference_entry = weights[-1]

    reference_value = _to_float_weight(reference_entry.value)
    if reference_value is None:
        return "Недостаточно данных"

    change = current_value - reference_value
    epsilon = 0.15
    if change < -epsilon:
        return "Снижение веса 📉"
    if change > epsilon:
        return "Рост веса 📈"
    return "Стабильно ⚖️"


def _parse_measurement_value(raw_value: str) -> Optional[float]:
    """Парсит введённое значение замера."""
    try:
        return float(raw_value.replace(",", "."))
    except (TypeError, ValueError, AttributeError):
        return None


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
    """Сразу открывает раздел веса из кнопки «Вес и замеры»."""
    user_id = str(message.from_user.id)
    logger.info(f"User {user_id} opened weight section from weight and measurements button")
    await my_weight(message)


@router.message(lambda m: m.text == "⚖️ Вес")
async def my_weight(message: Message):
    """Показывает сводку по весу пользователя."""
    user_id = str(message.from_user.id)
    logger.info(f"User {user_id} viewed weight dashboard")
    AnalyticsRepository.track_event(user_id, "open_weight", section="weight")

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

    reference_7 = _find_period_start_weight(weights, current_entry.date, 7)
    reference_30 = _find_period_start_weight(weights, current_entry.date, 30)

    change_7 = _format_change(current_weight, _to_float_weight(reference_7.value) if reference_7 else None)
    change_30 = _format_change(current_weight, _to_float_weight(reference_30.value) if reference_30 else None)

    target_weight = WeightRepository.get_target_weight(user_id)
    to_goal_text = "Цель веса не задана"
    if target_weight is not None:
        delta_to_target = target_weight - current_weight
        target_goal_text = f"Цель: {target_weight:.1f} кг"
        if abs(delta_to_target) < 0.05:
            remaining_goal_text = "Цель достигнута 🎉"
        elif delta_to_target > 0:
            remaining_goal_text = f"Осталось набрать: {delta_to_target:.1f} кг"
        else:
            remaining_goal_text = f"Осталось: {abs(delta_to_target):.1f} кг"
        to_goal_text = f"{target_goal_text}\n{remaining_goal_text}"

    trend_text = _detect_trend(weights)

    speed_text = "Недостаточно данных"
    if len(weights) > 1:
        oldest_entry = weights[-1]
        oldest_weight = _to_float_weight(oldest_entry.value)
        if oldest_weight is not None:
            total_days = (current_entry.date - oldest_entry.date).days
            if total_days >= 7:
                weekly_speed = (current_weight - oldest_weight) / (total_days / 7)
                speed_text = f"{weekly_speed:+.2f} кг в неделю"

    weight_values = []
    recent_rows = []
    for entry in weights:
        entry_value = _to_float_weight(entry.value)
        if entry_value is None:
            continue
        weight_values.append(entry_value)
        if len(recent_rows) < 7:
            recent_rows.append(f"{entry.date.strftime('%d.%m')} — {entry_value:.2f} кг")

    range_text = "Недостаточно данных"
    if weight_values:
        range_text = f"Мин: {min(weight_values):.2f} кг • Макс: {max(weight_values):.2f} кг"

    recent_text = "\n".join(recent_rows) if recent_rows else "Нет записей для отображения"
    text = (
        "⚖️ <b>Вес</b>\n\n"
        f"<b>Текущий вес:</b> <b>{current_weight:.2f} кг</b>\n\n"
        "📉 <b>Изменение:</b>\n"
        f"<b>За 7 дней:</b> {change_7}\n"
        f"<b>За 30 дней:</b> {change_30}\n\n"
        "🎯 <b>До цели:</b>\n"
        f"<b>{to_goal_text}</b>\n\n"
        "📊 <b>Тренд:</b>\n"
        f"<b>{trend_text}</b>\n\n"
        "🚀 <b>Средняя скорость:</b>\n"
        f"<b>{speed_text}</b>\n\n"
        "📊 <b>Диапазон:</b>\n"
        f"<b>{range_text}</b>\n\n"
        "📅 <b>Последние записи:</b>\n\n"
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


@router.message(lambda m: m.text == "📦 Архив")
async def show_weight_archive(message: Message):
    """Показывает архив всех введённых весов без графика и диапазона."""
    user_id = str(message.from_user.id)
    weights = WeightRepository.get_weights(user_id)

    if not weights:
        await message.answer(
            "📅 Все введённые веса:\n"
            "Пока нет записей веса."
        )
        return

    rows = []
    for entry in weights:
        value = _to_float_weight(entry.value)
        if value is None:
            rows.append(f"{entry.date.strftime('%d.%m.%Y')} — некорректное значение")
            continue
        rows.append(f"{entry.date.strftime('%d.%m.%Y')} — {value:.2f} кг")

    await message.answer(
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


async def start_add_weight_for_user(message: Message, state: FSMContext, user_id: str):
    """Начинает процесс добавления веса за сегодня для указанного пользователя."""
    logger.info(f"User {user_id} started adding weight for today")

    target_date = date.today()

    # Проверяем, есть ли уже вес за сегодня
    existing_weight = WeightRepository.get_weight_for_date(user_id, target_date)

    if existing_weight:
        # Если вес уже есть, переходим в режим редактирования
        base_weight = _resolve_base_weight(user_id, existing_weight.value)
        await state.update_data(
            entry_date=target_date.isoformat(),
            weight_id=existing_weight.id,
            quick_base_weight=base_weight or 70.0,
        )
        await state.set_state(WeightStates.entering_weight)
        await _send_weight_editor_message(
            message,
            state,
            _format_weight_input_screen(
                target_date,
                base_weight,
                current_weight=existing_weight.value,
                edit_title="✏️ Изменение веса",
            ),
            base_weight or 70.0,
        )
    else:
        # Если веса нет, создаем новую запись
        base_weight = _resolve_base_weight(user_id)
        await state.update_data(
            entry_date=target_date.isoformat(),
            weight_id=None,
            quick_base_weight=base_weight or 70.0,
        )
        await state.set_state(WeightStates.entering_weight)
        await _send_weight_editor_message(
            message,
            state,
            _format_weight_input_screen(target_date, base_weight),
            base_weight or 70.0,
        )


@router.message(lambda m: m.text == "➕ Добавить вес")
async def add_weight_start(message: Message, state: FSMContext):
    """Начинает процесс добавления веса за сегодня."""
    await start_add_weight_for_user(message, state, str(message.from_user.id))


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
    
    base_weight = _resolve_base_weight(str(message.from_user.id))
    await state.update_data(entry_date=target_date.isoformat(), quick_base_weight=base_weight or 70.0)
    await state.set_state(WeightStates.entering_weight)
    await _send_weight_editor_message(
        message,
        state,
        _format_weight_input_screen(target_date, base_weight),
        base_weight or 70.0,
    )


async def _remember_weight_editor_message(state: FSMContext, sent_message):
    """Запоминает сообщение редактора веса, чтобы дальше обновлять его вместо новых сообщений."""
    message_id = getattr(sent_message, "message_id", None)
    if message_id is not None:
        await state.update_data(weight_editor_message_id=message_id)


async def _send_weight_editor_message(message: Message, state: FSMContext, text: str, base_weight: float):
    """Отправляет стартовый экран редактора веса и запоминает его message_id."""
    sent_message = await message.answer(
        text,
        reply_markup=_build_weight_quick_adjust_keyboard(base_weight),
    )
    await _remember_weight_editor_message(state, sent_message)


async def _update_weight_editor_from_message(message: Message, state: FSMContext, text: str, base_weight: float):
    """Обновляет сохранённое сообщение редактора веса или создаёт новое, если старого нет."""
    data = await state.get_data()
    editor_message_id = data.get("weight_editor_message_id")
    if editor_message_id:
        try:
            await message.bot.edit_message_text(
                chat_id=message.chat.id,
                message_id=editor_message_id,
                text=text,
                reply_markup=_build_weight_quick_adjust_keyboard(base_weight),
            )
            return
        except Exception as e:
            logger.warning("Could not edit weight editor message %s: %s", editor_message_id, e)

    await _send_weight_editor_message(message, state, text, base_weight)


def _parse_weight_delta_callback(callback_data: str) -> Optional[float]:
    """Достаёт дельту веса из callback_data инлайн-кнопки."""
    try:
        return float(callback_data.split(":", 1)[1])
    except (IndexError, TypeError, ValueError):
        return None


def _resolve_weight_entry_date(entry_date_value) -> date:
    """Возвращает дату записи веса из FSM-значения."""
    if isinstance(entry_date_value, str):
        try:
            return date.fromisoformat(entry_date_value)
        except ValueError:
            parsed = parse_date(entry_date_value)
            return parsed.date() if isinstance(parsed, datetime) else date.today()
    return date.today()


def _resolve_optional_weight_date(entry_date_value) -> Optional[date]:
    """Возвращает дату записи веса из FSM или None."""
    if not entry_date_value:
        return None
    if isinstance(entry_date_value, date):
        return entry_date_value
    if isinstance(entry_date_value, str):
        try:
            return date.fromisoformat(entry_date_value)
        except ValueError:
            parsed = parse_date(entry_date_value)
            return parsed.date() if isinstance(parsed, datetime) else None
    return None


def _find_previous_weight_entry(
    user_id: str,
    entry_date: date,
    weight_id: Optional[int],
) -> tuple[Optional[float], Optional[date]]:
    """Находит последнюю сохранённую запись веса до текущей записи."""
    existing_weights = WeightRepository.get_weights(user_id)
    for existing in existing_weights:
        existing_value = _to_float_weight(existing.value)
        if existing_value is None:
            continue
        if weight_id and existing.id == weight_id:
            continue
        if existing.date <= entry_date:
            return existing_value, existing.date
    return None, None


def _find_previous_weight_value(user_id: str, entry_date: date, weight_id: Optional[int]) -> Optional[float]:
    """Находит предыдущий вес для расчёта динамики."""
    previous_weight_value, _ = _find_previous_weight_entry(user_id, entry_date, weight_id)
    return previous_weight_value


def _resolve_current_weight_value_for_draft(
    user_id: str,
    entry_date: date,
    weight_id: Optional[int],
    data: dict,
) -> Optional[float]:
    """Возвращает исходный текущий вес для отображения рядом с новым весом."""
    remembered_current_weight = _to_float_weight(data.get("draft_current_weight_value"))
    if remembered_current_weight is not None:
        return remembered_current_weight

    if weight_id:
        existing_weight = WeightRepository.get_weight_for_date(user_id, entry_date)
        existing_weight_value = _to_float_weight(existing_weight.value if existing_weight else None)
        if existing_weight_value is not None:
            return existing_weight_value

    return _to_float_weight(data.get("quick_base_weight"))


async def _show_weight_input_screen_from_state(message: Message, state: FSMContext):
    """Возвращает пользователя на экран выбора веса без сохранения draft."""
    data = await state.get_data()
    user_id = str(message.from_user.id)
    entry_date = _resolve_weight_entry_date(data.get("entry_date", date.today().isoformat()))
    weight_id = data.get("weight_id")
    current_weight = None
    edit_title = "✏️ Изменение веса"

    if weight_id:
        existing_weight = WeightRepository.get_weight_for_date(user_id, entry_date)
        current_weight = existing_weight.value if existing_weight else None
        edit_title = "✏️ Редактирование веса"

    base_weight = _to_float_weight(data.get("quick_base_weight")) or _resolve_base_weight(
        user_id,
        current_weight,
    )
    await state.update_data(
        draft_weight_value=None,
        draft_previous_weight_value=None,
        draft_previous_weight_date=None,
        quick_base_weight=base_weight or 70.0,
    )
    await state.set_state(WeightStates.entering_weight)
    await _update_weight_editor_from_message(
        message,
        state,
        _format_weight_input_screen(
            entry_date,
            base_weight,
            current_weight=current_weight,
            edit_title=edit_title,
        ),
        base_weight or 70.0,
    )


async def _save_weight_draft(message: Message, state: FSMContext, user_id: str, data: dict):
    """Сохраняет черновик веса из FSM."""
    weight_value = data.get("draft_weight_value")
    weight_value = _to_float_weight(weight_value)
    if weight_value is None or weight_value <= 0:
        await message.answer("⚠️ Сначала выбери новый вес кнопкой или введи значение вручную.")
        return

    entry_date = _resolve_weight_entry_date(data.get("entry_date", date.today().isoformat()))
    weight_id = data.get("weight_id")
    previous_weight_value = data.get("draft_previous_weight_value")
    previous_weight_value = _to_float_weight(previous_weight_value)
    stored_weight_value = f"{weight_value:.1f}"

    try:
        if weight_id:
            success = WeightRepository.update_weight(weight_id, user_id, stored_weight_value)
            if success:
                logger.info(f"User {user_id} updated weight {weight_id}: {weight_value} kg on {entry_date}")
                await state.clear()
                delta_text = ""
                if previous_weight_value is not None:
                    delta = weight_value - previous_weight_value
                    direction = "📉" if delta < 0 else "📈" if delta > 0 else "⚖️"
                    delta_text = f"\n\n<b>Изменение:</b>\n{delta:+.2f} кг с прошлой записи {direction}"
                await message.answer(
                    f"✅ <b>Вес обновлён!</b>\n\n"
                    f"⚖️ <b>{weight_value:.1f} кг</b>\n"
                    f"📅 {entry_date.strftime('%d.%m.%Y')}"
                    f"{delta_text}",
                    reply_markup=build_weight_day_actions_keyboard(
                        WeightRepository.get_weight_for_date(user_id, entry_date),
                        entry_date,
                    ),
                )
            else:
                await message.answer("⚠️ Не удалось обновить запись.")
                await state.clear()
        else:
            WeightRepository.save_weight(user_id, stored_weight_value, entry_date)
            logger.info(f"User {user_id} saved weight: {weight_value} kg on {entry_date}")
            AnalyticsRepository.track_event(user_id, "add_weight", section="weight")

            await state.clear()
            push_menu_stack(message.bot, weight_menu)
            delta_text = "\n<b>Изменение:</b>\nНедостаточно данных"
            comment_text = ""
            if previous_weight_value is not None:
                delta = weight_value - previous_weight_value
                direction = "📉" if delta < 0 else "📈" if delta > 0 else "⚖️"
                delta_text = f"\n<b>Изменение:</b>\n{delta:+.2f} кг с прошлой записи {direction}"
                if delta < 0:
                    comment_text = "\n\n<b>Отличная динамика.</b>"
                elif delta > 0:
                    comment_text = "\n\n<b>Вес немного вырос.</b>\nОбрати внимание на питание и воду."
                else:
                    comment_text = "\n\n<b>Вес без изменений.</b> Продолжай в том же ритме."
            await message.answer(
                f"✅ <b>Вес сохранён!</b>\n\n"
                f"⚖️ <b>{weight_value:.1f} кг</b>"
                f"{delta_text}"
                f"{comment_text}",
                reply_markup=weight_menu,
            )
    except Exception as e:
        logger.error(f"Error saving/updating weight: {e}", exc_info=True)
        await message.answer("⚠️ Ошибка при сохранении. Повтори попытку позже.")
        await state.clear()



async def _apply_weight_value_to_editor(
    message: Message,
    state: FSMContext,
    user_id: str,
    weight_value: float,
):
    """Сохраняет черновик веса в FSM и обновляет то же сообщение редактора."""
    data = await state.get_data()
    entry_date = _resolve_weight_entry_date(data.get("entry_date", date.today().isoformat()))
    weight_id = data.get("weight_id")
    previous_weight_value, previous_weight_date = _find_previous_weight_entry(user_id, entry_date, weight_id)
    current_weight_value = _resolve_current_weight_value_for_draft(user_id, entry_date, weight_id, data)

    await state.update_data(
        draft_weight_value=weight_value,
        draft_previous_weight_value=previous_weight_value,
        draft_previous_weight_date=previous_weight_date.isoformat() if previous_weight_date else None,
        draft_current_weight_value=current_weight_value,
        entry_date=entry_date.isoformat(),
        quick_base_weight=weight_value,
    )
    await state.set_state(WeightStates.entering_weight)
    await _update_weight_editor_from_message(
        message,
        state,
        _format_weight_draft_text(
            weight_value,
            entry_date,
            previous_weight_value,
            previous_weight_date,
            current_weight_value,
        ),
        weight_value,
    )


@router.message(WeightStates.entering_weight)
async def handle_weight_input(message: Message, state: FSMContext):
    """Обрабатывает ввод веса и оставляет быстрые кнопки до сохранения."""
    user_id = str(message.from_user.id)
    text = (message.text or "").strip()
    data = await state.get_data()
    entry_date_str = data.get("entry_date")

    # Если дата ещё не установлена, проверяем, не ввёл ли пользователь дату вручную.
    if not entry_date_str:
        parsed = parse_date(text)
        if parsed:
            target_date = parsed.date() if isinstance(parsed, datetime) else date.today()
            base_weight = _resolve_base_weight(user_id)
            await state.update_data(entry_date=target_date.isoformat(), quick_base_weight=base_weight or 70.0)
            await _update_weight_editor_from_message(
                message,
                state,
                _format_weight_input_screen(target_date, base_weight),
                base_weight or 70.0,
            )
            return

    if text == "✅ Сохранить":
        await _save_weight_draft(message, state, user_id, data)
        return

    if text == "⬅️ Назад":
        await state.clear()
        push_menu_stack(message.bot, weight_menu)
        await message.answer("Выбери действие:", reply_markup=weight_menu)
        return

    if text == "✍️ Ввести вручную":
        await _update_weight_editor_from_message(
            message,
            state,
            f"{_format_weight_input_screen(_resolve_weight_entry_date(entry_date_str), _to_float_weight(data.get('quick_base_weight')) or 70.0)}\n\n"
            "✍️ Введи вес в килограммах сообщением, например 72,5.",
            _to_float_weight(data.get("quick_base_weight")) or 70.0,
        )
        return

    draft_weight = _to_float_weight(data.get("draft_weight_value"))
    quick_base_weight = draft_weight if draft_weight is not None else data.get("quick_base_weight")
    if quick_base_weight is None:
        quick_base_weight = _resolve_base_weight(user_id)
    quick_weight_value = _resolve_quick_weight_value(text, _to_float_weight(quick_base_weight))
    weight_value = quick_weight_value
    if weight_value is None:
        weight_value = parse_weight(text)
    if weight_value is None or weight_value <= 0:
        await message.answer("⚠️ Введи положительное число: 72.5 или 72,5")
        return

    await _apply_weight_value_to_editor(message, state, user_id, weight_value)


@router.callback_query(lambda c: c.data and c.data.startswith("weight_adj:"))
async def handle_weight_inline_adjust(callback: CallbackQuery, state: FSMContext):
    """Обрабатывает быстрые инлайн-кнопки и редактирует текущее сообщение веса."""
    await callback.answer()
    delta = _parse_weight_delta_callback(callback.data or "")
    if delta is None:
        return

    user_id = str(callback.from_user.id)
    data = await state.get_data()
    await state.update_data(weight_editor_message_id=callback.message.message_id)
    draft_weight = _to_float_weight(data.get("draft_weight_value"))
    quick_base_weight = draft_weight if draft_weight is not None else data.get("quick_base_weight")
    if quick_base_weight is None:
        quick_base_weight = _resolve_base_weight(user_id)
    base_weight = _to_float_weight(quick_base_weight)
    if base_weight is None:
        base_weight = 70.0

    weight_value = max(1.0, base_weight + delta)
    await _apply_weight_value_to_editor(callback.message, state, user_id, weight_value)


@router.callback_query(lambda c: c.data == "weight_manual")
async def handle_weight_inline_manual(callback: CallbackQuery, state: FSMContext):
    """Переводит редактор в режим ручного ввода без отправки нового сообщения."""
    await callback.answer()
    data = await state.get_data()
    await state.update_data(weight_editor_message_id=callback.message.message_id)
    entry_date = _resolve_weight_entry_date(data.get("entry_date", date.today().isoformat()))
    draft_weight = _to_float_weight(data.get("draft_weight_value"))
    base_weight = draft_weight or _to_float_weight(data.get("quick_base_weight")) or 70.0
    previous_weight_value = _to_float_weight(data.get("draft_previous_weight_value"))
    previous_weight_date = _resolve_optional_weight_date(data.get("draft_previous_weight_date"))
    current_weight_value = _to_float_weight(data.get("draft_current_weight_value"))
    if draft_weight is not None:
        draft_text = _format_weight_draft_text(
            draft_weight,
            entry_date,
            previous_weight_value,
            previous_weight_date,
            current_weight_value,
        )
        text = (
            f"{draft_text}\n\n"
            "✍️ Введи вес в килограммах сообщением, например 72,5."
        )
    else:
        text = (
            f"{_format_weight_input_screen(entry_date, base_weight)}\n\n"
            "✍️ Введи вес в килограммах сообщением, например 72,5."
        )
    await callback.message.edit_text(
        text,
        reply_markup=_build_weight_quick_adjust_keyboard(base_weight),
    )


@router.callback_query(lambda c: c.data == "weight_save")
async def handle_weight_inline_save(callback: CallbackQuery, state: FSMContext):
    """Сохраняет выбранный в инлайн-редакторе вес."""
    await callback.answer()
    await state.update_data(weight_editor_message_id=callback.message.message_id)
    data = await state.get_data()
    await _save_weight_draft(callback.message, state, str(callback.from_user.id), data)


@router.callback_query(lambda c: c.data in {"weight_back", "weight_cancel"})
async def handle_weight_inline_cancel(callback: CallbackQuery, state: FSMContext):
    """Закрывает инлайн-редактор веса."""
    await callback.answer()
    await state.clear()
    push_menu_stack(callback.message.bot, weight_menu)
    await callback.message.edit_text("Ввод веса отменён. Выбери действие ниже:")
    await callback.message.answer("Выбери действие:", reply_markup=weight_menu)


@router.message(WeightStates.confirming_weight)
async def handle_weight_confirmation(message: Message, state: FSMContext):
    """Сохраняет вес из старого экрана подтверждения."""
    user_id = str(message.from_user.id)
    text = (message.text or "").strip()
    data = await state.get_data()

    if text == "✏️ Изменить":
        await _show_weight_input_screen_from_state(message, state)
        return

    if text == "⬅️ Назад":
        await state.clear()
        push_menu_stack(message.bot, weight_menu)
        await message.answer("Выбери действие:", reply_markup=weight_menu)
        return

    if text != "✅ Сохранить":
        await message.answer("Нажми ✅ Сохранить, чтобы записать вес, или ✏️ Изменить.")
        return

    await _save_weight_draft(message, state, user_id, data)


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
    await _start_measurements_wizard(message, state, date.today())


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
    await show_calendar_back_button(message)
    await show_weight_calendar_view(message, user_id)


@router.message(lambda m: m.text == "📆 Календарь замеров")
async def show_measurements_calendar(message: Message):
    """Показывает календарь замеров."""
    user_id = str(message.from_user.id)
    logger.info(f"User {user_id} opened measurements calendar")
    await show_calendar_back_button(message)
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
    
    weight_value = _to_float_weight(weight.value)
    weight_text = f"{weight_value:.1f}" if weight_value is not None else str(weight.value)
    text = f"📅 {target_date.strftime('%d.%m.%Y')}\n\n⚖️ Вес: {weight_text} кг"
    
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
        base_weight = _resolve_base_weight(user_id, existing_weight.value)
        await state.update_data(
            entry_date=target_date.isoformat(),
            weight_id=existing_weight.id,
            quick_base_weight=base_weight or 70.0,
        )
        await state.set_state(WeightStates.entering_weight)
        await _send_weight_editor_message(
            callback.message,
            state,
            _format_weight_input_screen(
                target_date,
                base_weight,
                current_weight=existing_weight.value,
                edit_title="✏️ Изменение веса",
            ),
            base_weight or 70.0,
        )
    else:
        # Если веса нет, создаем новую запись
        base_weight = _resolve_base_weight(user_id)
        await state.update_data(
            entry_date=target_date.isoformat(),
            weight_id=None,
            quick_base_weight=base_weight or 70.0,
        )
        await state.set_state(WeightStates.entering_weight)
        await _send_weight_editor_message(
            callback.message,
            state,
            _format_weight_input_screen(target_date, base_weight),
            base_weight or 70.0,
        )


@router.callback_query(lambda c: c.data == "weight_cal_main")
async def back_to_main_menu_from_weight(callback: CallbackQuery):
    """Возвращает пользователя в главное меню из карточки веса."""
    await callback.answer()
    push_menu_stack(callback.message.bot, main_menu)
    await callback.message.answer("⬇️ Кнопки управления", reply_markup=main_menu, disable_notification=True)


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
    
    base_weight = _resolve_base_weight(user_id, weight.value)
    await state.update_data(
        entry_date=target_date.isoformat(),
        weight_id=weight.id,
        quick_base_weight=base_weight or 70.0,
    )
    await state.set_state(WeightStates.entering_weight)
    
    await _send_weight_editor_message(
        callback.message,
        state,
        _format_weight_input_screen(
            target_date,
            base_weight,
            current_weight=weight.value,
            edit_title="✏️ Редактирование веса",
        ),
        base_weight or 70.0,
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
