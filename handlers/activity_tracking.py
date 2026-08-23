"""Новый UX раздела «Активность»: шаги, время и тренировочные сессии."""
from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta
import html
import math
import re

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
)
from sqlalchemy.exc import SQLAlchemyError

from database.repositories import ActivityRepository, ActiveWorkoutExistsError, WeightRepository
from services.activity_energy_service import (
    ActivityValidationError,
    DEFAULT_WEIGHT_KG,
    calculate_steps_energy,
    calculate_timed_activity_energy,
    calculate_workout_energy,
    estimate_quick_workout_duration_seconds,
    get_daily_activity_energy_summary,
)
from states.user_states import ActivityTrackingStates
from utils.activity_catalog import (
    EXERCISE_BY_CODE,
    EXERCISE_CATEGORIES,
    EXERCISE_CATEGORY_BY_CODE,
    INTENSITY_LABELS,
    TIMED_ACTIVITIES,
    TIMED_ACTIVITY_BY_CODE,
    TIMED_CATEGORIES,
    TIMED_CATEGORY_BY_CODE,
    WORKOUT_INTENSITY_LABELS,
    exercises_for_category,
    timed_activities_for_category,
)
from utils.keyboards import (
    LEGACY_TRAINING_BUTTON_TEXT,
    MAIN_MENU_BUTTON_ALIASES,
    PREVIOUS_TRAINING_BUTTON_TEXT,
    TRAINING_BUTTON_TEXT,
    push_menu_stack,
    training_menu,
)


router = Router(name="activity_tracking")
PAGE_SIZE = 8
ACTIVITY_BUTTON_ALIASES = {
    TRAINING_BUTTON_TEXT,
    PREVIOUS_TRAINING_BUTTON_TEXT,
    LEGACY_TRAINING_BUTTON_TEXT,
}


def _safe_date(raw: str | None, default: date | None = None) -> date:
    try:
        return date.fromisoformat(str(raw))
    except (TypeError, ValueError):
        return default or date.today()


def _weight_snapshot(user_id: str) -> tuple[float, str]:
    weight = WeightRepository.get_last_weight(str(user_id))
    if weight is None or not math.isfinite(weight) or weight < 25 or weight > 350:
        return DEFAULT_WEIGHT_KG, "default"
    return float(weight), "profile"


def _format_number(value: float | int, digits: int = 1) -> str:
    number = float(value)
    if number.is_integer():
        return str(int(number))
    return f"{number:.{digits}f}".rstrip("0").rstrip(".").replace(".", ",")


def _format_duration(seconds: int | float) -> str:
    total = max(int(seconds), 0)
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours} ч {minutes:02d} мин"
    if minutes:
        return f"{minutes} мин {secs:02d} сек" if secs else f"{minutes} мин"
    return f"{secs} сек"


def _parse_positive_number(text: str | None) -> float:
    raw = (text or "").strip().casefold().replace("кг", "").replace("мин", "").replace(",", ".").strip()
    value = float(raw)
    if not math.isfinite(value) or value <= 0:
        raise ValueError
    return value


def _parse_seconds(text: str | None) -> int:
    raw = (text or "").strip().casefold().replace("сек", "").strip()
    if ":" in raw:
        minutes_raw, seconds_raw = raw.split(":", maxsplit=1)
        result = int(minutes_raw) * 60 + int(seconds_raw)
    else:
        result = int(float(raw.replace(",", ".")))
    if result <= 0 or result > 21_600:
        raise ValueError
    return result


def _minutes_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="10"), KeyboardButton(text="20"), KeyboardButton(text="30")],
            [KeyboardButton(text="45"), KeyboardButton(text="60"), KeyboardButton(text="90")],
            [KeyboardButton(text="⬅️ Назад")],
        ],
        resize_keyboard=True,
    )


def _steps_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="5 000"), KeyboardButton(text="8 000"), KeyboardButton(text="10 000")],
            [KeyboardButton(text="12 000"), KeyboardButton(text="15 000"), KeyboardButton(text="20 000")],
            [KeyboardButton(text="⬅️ Назад")],
        ],
        resize_keyboard=True,
    )


def _number_keyboard(values: tuple[str, ...]) -> ReplyKeyboardMarkup:
    rows = [[KeyboardButton(text=value) for value in values[index:index + 4]] for index in range(0, len(values), 4)]
    rows.append([KeyboardButton(text="⬅️ Назад")])
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True)


async def _edit_or_answer(message: Message, text: str, keyboard: InlineKeyboardMarkup | None = None) -> None:
    if hasattr(message, "edit_text"):
        try:
            await message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")
            return
        except TelegramBadRequest as exc:
            if "message is not modified" in str(exc).casefold():
                return
        except AttributeError:
            pass
    await message.answer(text, reply_markup=keyboard, parse_mode="HTML")


def format_activity_overview(user_id: str, target_date: date) -> tuple[str, InlineKeyboardMarkup]:
    timed = ActivityRepository.get_timed_activities_for_day(user_id, target_date)
    sessions = [
        item for item in ActivityRepository.get_workout_sessions_for_day(user_id, target_date)
        if item.status == "completed"
    ]
    steps = ActivityRepository.get_steps_for_day(user_id, target_date)
    summary = get_daily_activity_energy_summary(user_id, target_date)
    date_label = "сегодня" if target_date == date.today() else target_date.strftime("%d.%m.%Y")
    lines = [f"🏃 <b>Активность за {date_label}</b>", ""]
    buttons: list[list[InlineKeyboardButton]] = []

    if steps is not None:
        steps_text = f"{steps.steps:,}".replace(",", " ")
        overlap_note = f", без повторного учёта {summary.overlapping_steps:,}".replace(",", " ") if summary.overlapping_steps else ""
        lines.append(f"🚶 Шаги — {steps_text} (~{summary.steps_gross_calories:.0f} ккал{overlap_note})")
        buttons.append([InlineKeyboardButton(text=f"🚶 Шаги · {steps_text}", callback_data=f"act:steps_detail:{target_date.isoformat()}")])
    else:
        lines.append("🚶 Шаги — не добавлены")

    for entry in timed:
        config = TIMED_ACTIVITY_BY_CODE.get(entry.activity_code)
        icon = config.emoji if config and config.emoji else "⏱"
        intensity = INTENSITY_LABELS.get(entry.intensity, entry.intensity).split(maxsplit=1)[-1].casefold()
        lines.append(
            f"{icon} {html.escape(entry.activity_name_snapshot)} — {_format_number(entry.duration_minutes)} мин, "
            f"{intensity} (~{entry.gross_calories:.0f} ккал)"
        )
        buttons.append([InlineKeyboardButton(
            text=f"{icon} {entry.activity_name_snapshot} · {_format_number(entry.duration_minutes)} мин",
            callback_data=f"act:timed_detail:{entry.id}",
        )])

    for session in sessions:
        title = "Быстрое упражнение" if session.session_kind == "quick" else "Силовая тренировка"
        lines.extend([
            f"🏋️ {title} — {_format_duration(session.duration_seconds or 0)}",
            f"   {session.exercise_count} упр. · {session.set_count} подх. · ~{session.gross_calories:.0f} ккал",
        ])
        buttons.append([InlineKeyboardButton(
            text=f"🏋️ {title} · {_format_duration(session.duration_seconds or 0)}",
            callback_data=f"act:workout_detail:{session.id}",
        )])

    if not timed and not sessions and steps is None:
        lines.extend(["", "Пока ничего не добавлено."])
    lines.extend([
        "",
        f"🔥 <b>Всего потрачено: ~{summary.gross_calories:.0f} ккал</b>",
        f"🎯 <b>Учтено в дневной норме: +{summary.credited_calories:.0f} ккал</b>",
    ])
    nav = [
        InlineKeyboardButton(text="◀️", callback_data=f"act:day:{(target_date - timedelta(days=1)).isoformat()}"),
        InlineKeyboardButton(text=target_date.strftime("%d.%m"), callback_data="act:noop"),
    ]
    if target_date < date.today():
        nav.append(InlineKeyboardButton(text="▶️", callback_data=f"act:day:{(target_date + timedelta(days=1)).isoformat()}"))
    buttons.append(nav)
    return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=buttons)


async def show_activity_main(message: Message, user_id: str, target_date: date | None = None, prefix: str | None = None) -> None:
    target = target_date or date.today()
    text, keyboard = format_activity_overview(str(user_id), target)
    if prefix:
        text = f"{prefix}\n\n{text}"
    push_menu_stack(message.bot, training_menu)
    await message.answer(text, reply_markup=keyboard, parse_mode="HTML")
    await message.answer("Выбери действие:", reply_markup=training_menu, disable_notification=True)


@router.message(StateFilter(None), F.text.in_(ACTIVITY_BUTTON_ALIASES))
async def open_activity(message: Message, state: FSMContext) -> None:
    await state.clear()
    await show_activity_main(message, str(message.from_user.id))


@router.callback_query(F.data == "act:noop")
async def noop(callback: CallbackQuery) -> None:
    await callback.answer()


@router.callback_query(F.data.startswith("act:day:"))
async def open_activity_day(callback: CallbackQuery) -> None:
    await callback.answer()
    target = _safe_date(callback.data.rsplit(":", 1)[-1])
    text, keyboard = format_activity_overview(str(callback.from_user.id), target)
    await _edit_or_answer(callback.message, text, keyboard)


def _timed_categories_keyboard() -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(
        text=f"{category.icon} {category.name}", callback_data=f"act:tcat:{category.code}:0"
    )] for category in TIMED_CATEGORIES]
    rows.append([InlineKeyboardButton(text="✖️ Закрыть", callback_data="act:close")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


@router.message(F.text == "⏱ Добавить по времени")
async def open_timed_categories(message: Message, state: FSMContext) -> None:
    await start_timed_activity_flow(message, state, str(message.from_user.id), date.today())


async def start_timed_activity_flow(
    message: Message, state: FSMContext, user_id: str, target_date: date | None = None,
) -> None:
    await state.clear()
    await state.update_data(entry_date=(target_date or date.today()).isoformat(), activity_user_id=str(user_id))
    try:
        recent_codes = ActivityRepository.get_recent_timed_activity_codes(str(user_id))
    except SQLAlchemyError:
        recent_codes = []
    rows = []
    for code in recent_codes:
        item = TIMED_ACTIVITY_BY_CODE.get(code)
        if item:
            rows.append([InlineKeyboardButton(text=f"⭐ {item.name}", callback_data=f"act:tpick:{item.code}")])
    rows.extend(_timed_categories_keyboard().inline_keyboard)
    intro = "⏱ <b>Активность по времени</b>\n\nВыбери вид активности:"
    if recent_codes:
        intro += "\n\n⭐ Сначала показаны часто используемые."
    await message.answer(intro, reply_markup=InlineKeyboardMarkup(inline_keyboard=rows), parse_mode="HTML")


@router.callback_query(F.data.startswith("act:tcat:"))
async def open_timed_category(callback: CallbackQuery) -> None:
    await callback.answer()
    _, _, category_code, page_raw = callback.data.split(":")
    category = TIMED_CATEGORY_BY_CODE.get(category_code)
    if category is None:
        await callback.answer("Категория не найдена", show_alert=True)
        return
    activities = timed_activities_for_category(category_code)
    pages = max(math.ceil(len(activities) / PAGE_SIZE), 1)
    page = min(max(int(page_raw), 0), pages - 1)
    visible = activities[page * PAGE_SIZE:(page + 1) * PAGE_SIZE]
    rows = [[InlineKeyboardButton(
        text=f"{item.emoji} {item.name}".strip(), callback_data=f"act:tpick:{item.code}"
    )] for item in visible]
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="◀️", callback_data=f"act:tcat:{category_code}:{page - 1}"))
    nav.append(InlineKeyboardButton(text=f"{page + 1}/{pages}", callback_data="act:noop"))
    if page + 1 < pages:
        nav.append(InlineKeyboardButton(text="▶️", callback_data=f"act:tcat:{category_code}:{page + 1}"))
    rows.append(nav)
    rows.append([InlineKeyboardButton(text="⬅️ Категории", callback_data="act:timed_categories")])
    await _edit_or_answer(
        callback.message,
        f"{category.icon} <b>{category.name}</b>\n\nВыбери активность:",
        InlineKeyboardMarkup(inline_keyboard=rows),
    )


@router.callback_query(F.data == "act:timed_categories")
async def reopen_timed_categories(callback: CallbackQuery) -> None:
    await callback.answer()
    await _edit_or_answer(callback.message, "⏱ <b>Активность по времени</b>\n\nВыбери категорию:", _timed_categories_keyboard())


def _intensity_keyboard(activity_code: str, *, edit_entry_id: int | None = None) -> InlineKeyboardMarkup:
    config = TIMED_ACTIVITY_BY_CODE[activity_code]
    rows = []
    for level in config.intensity_mets:
        callback = f"act:tedit_int:{edit_entry_id}:{level}" if edit_entry_id else f"act:tint:{activity_code}:{level}"
        rows.append([InlineKeyboardButton(text=INTENSITY_LABELS[level], callback_data=callback)])
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data=f"act:tcat:{config.category_code}:0")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def _prompt_timed_duration(message: Message, state: FSMContext, activity_code: str, intensity: str) -> None:
    config = TIMED_ACTIVITY_BY_CODE[activity_code]
    existing = await state.get_data()
    await state.update_data(
        activity_code=activity_code,
        intensity=intensity,
        entry_date=existing.get("entry_date") or date.today().isoformat(),
        activity_user_id=existing.get("activity_user_id"),
    )
    await state.set_state(ActivityTrackingStates.entering_timed_duration)
    await message.answer(
        f"{config.emoji} <b>{config.name}</b>\n"
        f"{INTENSITY_LABELS[intensity]}\n\nСколько минут?",
        reply_markup=_minutes_keyboard(),
        parse_mode="HTML",
    )


@router.callback_query(F.data.startswith("act:tpick:"))
async def pick_timed_activity(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    code = callback.data.rsplit(":", 1)[-1]
    config = TIMED_ACTIVITY_BY_CODE.get(code)
    if config is None:
        await callback.answer("Активность не найдена", show_alert=True)
        return
    if len(config.intensity_mets) == 1:
        intensity = next(iter(config.intensity_mets))
        await _prompt_timed_duration(callback.message, state, code, intensity)
        return
    await _edit_or_answer(
        callback.message,
        f"{config.emoji} <b>{config.name}</b>\n\nКакой была интенсивность?",
        _intensity_keyboard(code),
    )


@router.callback_query(F.data.startswith("act:tint:"))
async def pick_timed_intensity(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    _, _, code, intensity = callback.data.split(":")
    config = TIMED_ACTIVITY_BY_CODE.get(code)
    if config is None or intensity not in config.intensity_mets:
        await callback.answer("Вариант не найден", show_alert=True)
        return
    await _prompt_timed_duration(callback.message, state, code, intensity)


@router.message(StateFilter(ActivityTrackingStates), F.text == "⬅️ Назад")
async def cancel_activity_input(message: Message, state: FSMContext) -> None:
    await state.clear()
    await show_activity_main(message, str(message.from_user.id))


@router.message(ActivityTrackingStates.entering_timed_duration)
async def save_timed_duration(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    user_id = str(data.get("activity_user_id") or message.from_user.id)
    config = TIMED_ACTIVITY_BY_CODE.get(str(data.get("activity_code")))
    intensity = str(data.get("intensity") or "")
    if config is None:
        await state.clear()
        await message.answer("Не удалось найти активность. Начни добавление заново.")
        return
    try:
        duration = _parse_positive_number(message.text)
        weight, weight_source = _weight_snapshot(user_id)
        energy = calculate_timed_activity_energy(
            activity_code=config.code, intensity=intensity,
            weight_kg=weight, duration_minutes=duration,
        )
    except (ValueError, ActivityValidationError) as exc:
        await message.answer(f"Укажи корректное количество минут. {html.escape(str(exc))}", reply_markup=_minutes_keyboard())
        return
    if duration > 240 and not data.get("anomaly_confirmed"):
        await state.update_data(pending_duration=duration)
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✅ Да, сохранить", callback_data="act:confirm_timed_anomaly")],
            [InlineKeyboardButton(text="✏️ Изменить", callback_data="act:retry_timed_duration")],
        ])
        await message.answer(
            f"Получилось {_format_number(duration)} мин и ~{energy.gross_calories:.0f} ккал. Проверь, всё верно?",
            reply_markup=keyboard,
        )
        return
    await _persist_timed_activity(message, state, user_id, data, config, intensity, duration, weight, weight_source, energy)


async def _persist_timed_activity(message, state: FSMContext, user_id: str, data: dict, config, intensity: str, duration: float, weight: float, weight_source: str, energy) -> None:
    target_date = _safe_date(data.get("entry_date"))
    ActivityRepository.save_timed_activity(
        user_id=user_id, activity_code=config.code, activity_name=config.name,
        entry_date=target_date, duration_minutes=duration, intensity=intensity,
        met_value=energy.met_value, weight_kg=weight, weight_source=weight_source,
        gross_calories=energy.gross_calories, credited_calories=energy.credited_calories,
    )
    await state.clear()
    note = "\nРасчёт выполнен с базовым весом 70 кг — добавь актуальный вес для более точной оценки." if weight_source == "default" else ""
    await show_activity_main(
        message, user_id, target_date,
        prefix=(
            f"✅ {config.name} — {_format_number(duration)} мин\n"
            f"🔥 Потрачено: ~{energy.gross_calories:.0f} ккал\n"
            f"🎯 Учтено в норме: +{energy.credited_calories:.0f} ккал{note}"
        ),
    )


@router.callback_query(F.data == "act:retry_timed_duration")
async def retry_timed_duration(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    await state.update_data(anomaly_confirmed=False, pending_duration=None)
    await callback.message.answer("Введи количество минут заново:", reply_markup=_minutes_keyboard())


@router.callback_query(F.data == "act:confirm_timed_anomaly")
async def confirm_timed_anomaly(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    data = await state.get_data()
    user_id = str(data.get("activity_user_id") or callback.from_user.id)
    config = TIMED_ACTIVITY_BY_CODE.get(str(data.get("activity_code")))
    intensity = str(data.get("intensity") or "")
    duration = data.get("pending_duration")
    if config is None or duration is None:
        await callback.answer("Данные устарели", show_alert=True)
        return
    weight, weight_source = _weight_snapshot(user_id)
    try:
        energy = calculate_timed_activity_energy(
            activity_code=config.code, intensity=intensity,
            weight_kg=weight, duration_minutes=duration,
        )
    except ActivityValidationError as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    await state.update_data(anomaly_confirmed=True)
    await _persist_timed_activity(
        callback.message, state, user_id, data, config, intensity,
        float(duration), weight, weight_source, energy,
    )


@router.message(F.text == "🚶 Добавить шаги")
async def start_steps_flow(message: Message, state: FSMContext, user_id: str | None = None, target_date: date | None = None) -> None:
    normalized_user_id = str(user_id or message.from_user.id)
    await state.clear()
    await state.update_data(entry_date=(target_date or date.today()).isoformat(), activity_user_id=normalized_user_id)
    await state.set_state(ActivityTrackingStates.entering_steps)
    await message.answer("🚶 Сколько шагов за день?\n\nВведи общее суточное значение:", reply_markup=_steps_keyboard())


@router.message(ActivityTrackingStates.entering_steps)
async def save_steps(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    user_id = str(data.get("activity_user_id") or message.from_user.id)
    raw = (message.text or "").replace(" ", "")
    try:
        steps = int(raw)
        weight, weight_source = _weight_snapshot(user_id)
        energy = calculate_steps_energy(steps=steps, weight_kg=weight)
    except (ValueError, ActivityValidationError) as exc:
        await message.answer(f"Укажи целое количество шагов. {html.escape(str(exc))}", reply_markup=_steps_keyboard())
        return
    if steps > 50_000 and not data.get("anomaly_confirmed"):
        await state.update_data(pending_steps=steps)
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✅ Да, сохранить", callback_data="act:confirm_steps_anomaly")],
            [InlineKeyboardButton(text="✏️ Изменить", callback_data="act:retry_steps")],
        ])
        await message.answer(
            f"Получилось {steps:,} шагов и ~{energy.gross_calories:.0f} ккал. Проверь, всё верно?".replace(",", " "),
            reply_markup=keyboard,
        )
        return
    await _persist_steps(message, state, user_id, data, energy, weight, weight_source)


async def _persist_steps(message, state: FSMContext, user_id: str, data: dict, energy, weight: float, weight_source: str) -> None:
    target_date = _safe_date(data.get("entry_date"))
    ActivityRepository.upsert_steps(
        user_id=user_id, entry_date=target_date, steps=energy.steps,
        weight_kg=weight, weight_source=weight_source,
        gross_calories=energy.gross_calories, credited_calories=energy.credited_calories,
    )
    await state.clear()
    await show_activity_main(message, user_id, target_date, prefix="✅ Шаги сохранены")


@router.callback_query(F.data == "act:retry_steps")
async def retry_steps(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    await state.update_data(anomaly_confirmed=False, pending_steps=None)
    await callback.message.answer("Введи количество шагов заново:", reply_markup=_steps_keyboard())


@router.callback_query(F.data == "act:confirm_steps_anomaly")
async def confirm_steps_anomaly(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    data = await state.get_data()
    user_id = str(data.get("activity_user_id") or callback.from_user.id)
    steps = data.get("pending_steps")
    if steps is None:
        await callback.answer("Данные устарели", show_alert=True)
        return
    weight, weight_source = _weight_snapshot(user_id)
    try:
        energy = calculate_steps_energy(steps=steps, weight_kg=weight)
    except ActivityValidationError as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    await _persist_steps(callback.message, state, user_id, data, energy, weight, weight_source)


@router.callback_query(F.data.startswith("act:steps_detail:"))
async def show_steps_detail(callback: CallbackQuery) -> None:
    await callback.answer()
    target_date = _safe_date(callback.data.rsplit(":", 1)[-1])
    row = ActivityRepository.get_steps_for_day(str(callback.from_user.id), target_date)
    if row is None:
        await callback.answer("Запись не найдена", show_alert=True)
        return
    summary = get_daily_activity_energy_summary(str(callback.from_user.id), target_date)
    text = (
        f"🚶 <b>Шаги</b>\n\n"
        f"Количество: {row.steps:,}\n".replace(",", " ")
        + f"Потрачено: ~{summary.steps_gross_calories:.0f} ккал\n"
        + f"Учтено в норме: +{summary.steps_credited_calories:.0f} ккал"
    )
    if summary.overlapping_steps:
        text += f"\n\nℹ️ {summary.overlapping_steps:,} шагов уже входят в записанную ходьбу или бег и повторно не учтены.".replace(",", " ")
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✏️ Изменить", callback_data=f"act:steps_edit:{target_date.isoformat()}")],
        [InlineKeyboardButton(text="🗑 Удалить", callback_data=f"act:steps_delete_confirm:{target_date.isoformat()}")],
        [InlineKeyboardButton(text="⬅️ К дню", callback_data=f"act:day:{target_date.isoformat()}")],
    ])
    await _edit_or_answer(callback.message, text, keyboard)


@router.callback_query(F.data.startswith("act:steps_edit:"))
async def edit_steps(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    await start_steps_flow(
        callback.message, state, str(callback.from_user.id),
        _safe_date(callback.data.rsplit(":", 1)[-1]),
    )


@router.callback_query(F.data.startswith("act:steps_delete_confirm:"))
async def confirm_delete_steps(callback: CallbackQuery) -> None:
    await callback.answer()
    target = callback.data.rsplit(":", 1)[-1]
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🗑 Да, удалить", callback_data=f"act:steps_delete:{target}")],
        [InlineKeyboardButton(text="Отмена", callback_data=f"act:steps_detail:{target}")],
    ])
    await _edit_or_answer(callback.message, "Удалить шаги за этот день?", keyboard)


@router.callback_query(F.data.startswith("act:steps_delete:"))
async def delete_steps(callback: CallbackQuery) -> None:
    await callback.answer()
    target = _safe_date(callback.data.rsplit(":", 1)[-1])
    ActivityRepository.delete_steps(str(callback.from_user.id), target)
    text, keyboard = format_activity_overview(str(callback.from_user.id), target)
    await _edit_or_answer(callback.message, f"✅ Шаги удалены\n\n{text}", keyboard)


@router.message(F.text == "📅 История")
async def show_history(message: Message) -> None:
    rows = []
    for offset in range(14):
        target = date.today() - timedelta(days=offset)
        summary = get_daily_activity_energy_summary(str(message.from_user.id), target)
        label = "Сегодня" if offset == 0 else target.strftime("%d.%m.%Y")
        rows.append([InlineKeyboardButton(
            text=f"{label} · ~{summary.gross_calories:.0f} ккал",
            callback_data=f"act:day:{target.isoformat()}",
        )])
    await message.answer(
        "📅 <b>История активности</b>\n\nВыбери день:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
        parse_mode="HTML",
    )


def _timed_detail_text(entry) -> str:
    config = TIMED_ACTIVITY_BY_CODE.get(entry.activity_code)
    icon = config.emoji if config and config.emoji else "⏱"
    return (
        f"{icon} <b>{html.escape(entry.activity_name_snapshot)}</b>\n\n"
        f"Продолжительность: {_format_number(entry.duration_minutes)} мин\n"
        f"Интенсивность: {INTENSITY_LABELS.get(entry.intensity, entry.intensity)}\n"
        f"Потрачено: ~{entry.gross_calories:.0f} ккал\n"
        f"Учтено в норме: +{entry.credited_calories:.0f} ккал\n\n"
        f"Вес при расчёте: {_format_number(entry.weight_kg_snapshot)} кг\n"
        f"MET: {_format_number(entry.met_value)}"
    )


def _timed_detail_keyboard(entry) -> InlineKeyboardMarkup:
    config = TIMED_ACTIVITY_BY_CODE.get(entry.activity_code)
    rows = [[InlineKeyboardButton(text="✏️ Изменить время", callback_data=f"act:tedit_time:{entry.id}")]]
    if config and len(config.intensity_mets) > 1:
        rows.append([InlineKeyboardButton(text="🔥 Изменить интенсивность", callback_data=f"act:tedit_int_menu:{entry.id}")])
    rows.extend([
        [InlineKeyboardButton(text="🗑 Удалить", callback_data=f"act:tdelete_confirm:{entry.id}")],
        [InlineKeyboardButton(text="⬅️ К дню", callback_data=f"act:day:{entry.entry_date.isoformat()}")],
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


@router.callback_query(F.data.startswith("act:timed_detail:"))
async def show_timed_detail(callback: CallbackQuery) -> None:
    await callback.answer()
    entry_id = int(callback.data.rsplit(":", 1)[-1])
    entry = ActivityRepository.get_timed_activity(entry_id, str(callback.from_user.id))
    if entry is None:
        await callback.answer("Запись не найдена", show_alert=True)
        return
    await _edit_or_answer(callback.message, _timed_detail_text(entry), _timed_detail_keyboard(entry))


@router.callback_query(F.data.startswith("act:tedit_time:"))
async def request_timed_duration_edit(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    entry_id = int(callback.data.rsplit(":", 1)[-1])
    entry = ActivityRepository.get_timed_activity(entry_id, str(callback.from_user.id))
    if entry is None:
        await callback.answer("Запись не найдена", show_alert=True)
        return
    await state.update_data(edit_entry_id=entry.id)
    await state.set_state(ActivityTrackingStates.editing_timed_duration)
    await callback.message.answer(
        f"Сейчас: {_format_number(entry.duration_minutes)} мин.\n\nВведи новую продолжительность:",
        reply_markup=_minutes_keyboard(),
    )


@router.message(ActivityTrackingStates.editing_timed_duration)
async def save_timed_duration_edit(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    entry = ActivityRepository.get_timed_activity(int(data.get("edit_entry_id") or 0), str(message.from_user.id))
    if entry is None:
        await state.clear()
        await message.answer("Запись не найдена.")
        return
    try:
        duration = _parse_positive_number(message.text)
        energy = calculate_timed_activity_energy(
            activity_code=entry.activity_code, intensity=entry.intensity,
            weight_kg=entry.weight_kg_snapshot, duration_minutes=duration,
        )
    except (ValueError, ActivityValidationError) as exc:
        await message.answer(f"Укажи корректное количество минут. {html.escape(str(exc))}", reply_markup=_minutes_keyboard())
        return
    ActivityRepository.update_timed_activity(
        entry_id=entry.id, user_id=str(message.from_user.id), duration_minutes=duration,
        intensity=entry.intensity, met_value=energy.met_value,
        gross_calories=energy.gross_calories, credited_calories=energy.credited_calories,
    )
    await state.clear()
    await show_activity_main(message, str(message.from_user.id), entry.entry_date, prefix="✅ Активность обновлена")


@router.callback_query(F.data.startswith("act:tedit_int_menu:"))
async def show_timed_intensity_edit(callback: CallbackQuery) -> None:
    await callback.answer()
    entry_id = int(callback.data.rsplit(":", 1)[-1])
    entry = ActivityRepository.get_timed_activity(entry_id, str(callback.from_user.id))
    if entry is None or entry.activity_code not in TIMED_ACTIVITY_BY_CODE:
        await callback.answer("Запись не найдена", show_alert=True)
        return
    await _edit_or_answer(
        callback.message,
        f"{html.escape(entry.activity_name_snapshot)}\n\nВыбери новую интенсивность:",
        _intensity_keyboard(entry.activity_code, edit_entry_id=entry.id),
    )


@router.callback_query(F.data.startswith("act:tedit_int:"))
async def save_timed_intensity_edit(callback: CallbackQuery) -> None:
    await callback.answer()
    _, _, entry_id_raw, intensity = callback.data.split(":")
    entry = ActivityRepository.get_timed_activity(int(entry_id_raw), str(callback.from_user.id))
    if entry is None:
        await callback.answer("Запись не найдена", show_alert=True)
        return
    try:
        energy = calculate_timed_activity_energy(
            activity_code=entry.activity_code, intensity=intensity,
            weight_kg=entry.weight_kg_snapshot, duration_minutes=entry.duration_minutes,
        )
    except ActivityValidationError as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    ActivityRepository.update_timed_activity(
        entry_id=entry.id, user_id=str(callback.from_user.id), duration_minutes=entry.duration_minutes,
        intensity=intensity, met_value=energy.met_value,
        gross_calories=energy.gross_calories, credited_calories=energy.credited_calories,
    )
    updated = ActivityRepository.get_timed_activity(entry.id, str(callback.from_user.id))
    await _edit_or_answer(callback.message, "✅ Обновлено\n\n" + _timed_detail_text(updated), _timed_detail_keyboard(updated))


@router.callback_query(F.data.startswith("act:tdelete_confirm:"))
async def confirm_timed_delete(callback: CallbackQuery) -> None:
    await callback.answer()
    entry_id = int(callback.data.rsplit(":", 1)[-1])
    entry = ActivityRepository.get_timed_activity(entry_id, str(callback.from_user.id))
    if entry is None:
        await callback.answer("Запись не найдена", show_alert=True)
        return
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🗑 Да, удалить", callback_data=f"act:tdelete:{entry.id}")],
        [InlineKeyboardButton(text="Отмена", callback_data=f"act:timed_detail:{entry.id}")],
    ])
    await _edit_or_answer(callback.message, f"Удалить «{html.escape(entry.activity_name_snapshot)}»?", keyboard)


@router.callback_query(F.data.startswith("act:tdelete:"))
async def delete_timed_entry(callback: CallbackQuery) -> None:
    await callback.answer()
    entry_id = int(callback.data.rsplit(":", 1)[-1])
    entry = ActivityRepository.get_timed_activity(entry_id, str(callback.from_user.id))
    if entry is None:
        await callback.answer("Запись уже удалена", show_alert=True)
        return
    ActivityRepository.delete_timed_activity(entry.id, str(callback.from_user.id))
    text, keyboard = format_activity_overview(str(callback.from_user.id), entry.entry_date)
    await _edit_or_answer(callback.message, f"✅ Активность удалена\n\n{text}", keyboard)


def _exercise_categories_keyboard(mode: str = "workout") -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(
        text=f"{category.icon} {category.name}",
        callback_data=f"act:ecat:{mode}:{category.code}:0",
    )] for category in EXERCISE_CATEGORIES]
    rows.append([InlineKeyboardButton(text="⬅️ К тренировке", callback_data="act:workout_screen")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _exercise_list_keyboard(mode: str, category_code: str, page: int) -> InlineKeyboardMarkup:
    exercises = exercises_for_category(category_code)
    pages = max(math.ceil(len(exercises) / PAGE_SIZE), 1)
    page = min(max(page, 0), pages - 1)
    visible = exercises[page * PAGE_SIZE:(page + 1) * PAGE_SIZE]
    rows = [[InlineKeyboardButton(text=item.name, callback_data=f"act:epick:{mode}:{item.code}")] for item in visible]
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="◀️", callback_data=f"act:ecat:{mode}:{category_code}:{page - 1}"))
    nav.append(InlineKeyboardButton(text=f"{page + 1}/{pages}", callback_data="act:noop"))
    if page + 1 < pages:
        nav.append(InlineKeyboardButton(text="▶️", callback_data=f"act:ecat:{mode}:{category_code}:{page + 1}"))
    rows.append(nav)
    rows.append([InlineKeyboardButton(text="⬅️ Категории", callback_data=f"act:ecategories:{mode}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _sets_by_exercise(session_id: int, user_id: str):
    exercises = ActivityRepository.get_session_exercises(session_id, user_id)
    sets = ActivityRepository.get_session_sets(session_id, user_id)
    grouped = defaultdict(list)
    for item in sets:
        grouped[item.session_exercise_id].append(item)
    return exercises, grouped, sets


def _format_set(item, load_mode: str = "none") -> str:
    if item.repetitions:
        result = f"{item.repetitions} раз"
    elif item.duration_seconds:
        result = _format_duration(item.duration_seconds)
    elif item.distance_meters:
        result = f"{_format_number(item.distance_meters)} м"
    else:
        result = "без данных"
    if item.load_kg is not None:
        if getattr(item, "load_kind", None) == "assistance":
            result += f", помощь {_format_number(item.load_kg)} кг"
        elif getattr(item, "load_kind", None) == "additional":
            result += f", +{_format_number(item.load_kg)} кг"
        else:
            label = "кг на снаряд" if load_mode == "per_item" else "кг"
            result += f", {_format_number(item.load_kg)} {label}"
    return result


def _active_workout_text(session, user_id: str) -> str:
    exercises, grouped, _ = _sets_by_exercise(session.id, user_id)
    status = "⏸ На паузе" if session.status == "paused" else "▶️ Идёт"
    lines = [
        "🏋️ <b>Тренировка</b>",
        f"{status} · {_format_duration(ActivityRepository.elapsed_seconds(session))}",
        "",
    ]
    if not exercises:
        lines.append("Упражнения пока не добавлены.")
    for exercise in exercises:
        items = grouped.get(exercise.id, [])
        lines.append(f"<b>{exercise.position}. {html.escape(exercise.exercise_name_snapshot)}</b>")
        if items:
            lines.append(" · ".join(
                f"{item.position}) {_format_set(item, exercise.load_input_mode_snapshot)}" for item in items
            ))
        else:
            lines.append("Подходы не добавлены")
    return "\n".join(lines)


def _active_workout_keyboard(session, user_id: str) -> InlineKeyboardMarkup:
    exercises, grouped, sets = _sets_by_exercise(session.id, user_id)
    rows = [[InlineKeyboardButton(text="➕ Добавить упражнение", callback_data="act:ecategories:workout")]]
    for exercise in exercises:
        rows.append([InlineKeyboardButton(
            text=f"➕ Подход · {exercise.exercise_name_snapshot}",
            callback_data=f"act:add_set:{exercise.id}",
        )])
        for item in grouped.get(exercise.id, []):
            rows.append([InlineKeyboardButton(
                text=f"✏️ {exercise.exercise_name_snapshot}: {item.position}-й подход",
                callback_data=f"act:set_detail:{item.id}",
            )])
    if sets:
        rows.append([InlineKeyboardButton(text="🔁 Повторить последний подход", callback_data=f"act:repeat_set:{session.id}")])
    if session.status == "paused":
        rows.append([InlineKeyboardButton(text="▶️ Продолжить", callback_data=f"act:resume:{session.id}")])
    else:
        rows.append([InlineKeyboardButton(text="⏸ Пауза", callback_data=f"act:pause:{session.id}")])
    rows.extend([
        [InlineKeyboardButton(text="✅ Завершить", callback_data=f"act:finish_prompt:{session.id}")],
        [InlineKeyboardButton(text="✖️ Отменить", callback_data=f"act:cancel_confirm:{session.id}")],
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def show_active_workout(message: Message, user_id: str, prefix: str | None = None) -> None:
    session = ActivityRepository.get_active_workout(user_id)
    if session is None:
        await message.answer("Активная тренировка не найдена.", reply_markup=training_menu)
        return
    text = _active_workout_text(session, user_id)
    if prefix:
        text = f"{prefix}\n\n{text}"
    await message.answer(text, reply_markup=_active_workout_keyboard(session, user_id), parse_mode="HTML")


@router.message(F.text == "🏋️ Начать тренировку")
async def start_workout(message: Message, state: FSMContext) -> None:
    await state.clear()
    user_id = str(message.from_user.id)
    existing = ActivityRepository.get_active_workout(user_id)
    if existing is not None:
        await show_active_workout(message, user_id, prefix="У тебя уже есть незавершённая тренировка.")
        return
    weight, weight_source = _weight_snapshot(user_id)
    ActivityRepository.create_workout_session(
        user_id=user_id, entry_date=date.today(), weight_kg=weight,
        weight_source=weight_source, session_kind="workout",
        duration_source="timer", started_at=datetime.utcnow(),
    )
    await show_active_workout(message, user_id, prefix="✅ Таймер запущен")


@router.callback_query(F.data == "act:workout_screen")
async def reopen_active_workout(callback: CallbackQuery) -> None:
    await callback.answer()
    session = ActivityRepository.get_active_workout(str(callback.from_user.id))
    if session is None:
        await callback.answer("Активная тренировка не найдена", show_alert=True)
        return
    await _edit_or_answer(
        callback.message,
        _active_workout_text(session, str(callback.from_user.id)),
        _active_workout_keyboard(session, str(callback.from_user.id)),
    )


@router.callback_query(F.data.startswith("act:pause:"))
async def pause_workout(callback: CallbackQuery) -> None:
    await callback.answer()
    session_id = int(callback.data.rsplit(":", 1)[-1])
    ActivityRepository.pause_workout(session_id, str(callback.from_user.id))
    session = ActivityRepository.get_active_workout(str(callback.from_user.id))
    await _edit_or_answer(
        callback.message, _active_workout_text(session, str(callback.from_user.id)),
        _active_workout_keyboard(session, str(callback.from_user.id)),
    )


@router.callback_query(F.data.startswith("act:resume:"))
async def resume_workout(callback: CallbackQuery) -> None:
    await callback.answer()
    session_id = int(callback.data.rsplit(":", 1)[-1])
    ActivityRepository.resume_workout(session_id, str(callback.from_user.id))
    session = ActivityRepository.get_active_workout(str(callback.from_user.id))
    await _edit_or_answer(
        callback.message, _active_workout_text(session, str(callback.from_user.id)),
        _active_workout_keyboard(session, str(callback.from_user.id)),
    )


@router.callback_query(F.data.startswith("act:repeat_set:"))
async def repeat_last_set(callback: CallbackQuery) -> None:
    await callback.answer()
    session_id = int(callback.data.rsplit(":", 1)[-1])
    repeated = ActivityRepository.repeat_last_set(session_id, str(callback.from_user.id))
    if repeated is None:
        await callback.answer("Сначала добавь подход", show_alert=True)
        return
    session = ActivityRepository.get_active_workout(str(callback.from_user.id))
    await _edit_or_answer(
        callback.message, "✅ Подход повторён\n\n" + _active_workout_text(session, str(callback.from_user.id)),
        _active_workout_keyboard(session, str(callback.from_user.id)),
    )


@router.callback_query(F.data == "act:ecategories:workout")
async def open_exercise_categories(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    await state.update_data(exercise_mode="workout")
    await _edit_or_answer(callback.message, "🏋️ <b>Добавить упражнение</b>\n\nВыбери категорию:", _exercise_categories_keyboard("workout"))


@router.callback_query(F.data.startswith("act:ecategories:"))
async def open_exercise_categories_for_mode(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    mode = callback.data.rsplit(":", 1)[-1]
    await state.update_data(exercise_mode=mode)
    await _edit_or_answer(callback.message, "Выбери категорию упражнения:", _exercise_categories_keyboard(mode))


@router.callback_query(F.data.startswith("act:ecat:"))
async def open_exercise_category(callback: CallbackQuery) -> None:
    await callback.answer()
    _, _, mode, category_code, page_raw = callback.data.split(":")
    category = EXERCISE_CATEGORY_BY_CODE.get(category_code)
    if category is None:
        await callback.answer("Категория не найдена", show_alert=True)
        return
    await _edit_or_answer(
        callback.message,
        f"{category.icon} <b>{category.name}</b>\n\nВыбери упражнение:",
        _exercise_list_keyboard(mode, category_code, int(page_raw)),
    )


def _load_prompt(config) -> str:
    if config.load_input_mode == "per_item":
        return "Введи вес одного снаряда в килограммах:"
    if config.load_input_mode == "assistance":
        return "Введи вес помощи тренажёра в килограммах:"
    return "Введи общий рабочий вес в килограммах, включая гриф:"


async def _start_regular_set_input(message: Message, state: FSMContext, session, config) -> None:
    exercise = ActivityRepository.add_session_exercise(
        session_id=session.id, user_id=str(session.user_id), exercise_code=config.code,
        exercise_name=config.name, measurement_type=config.measurement_type,
        load_input_mode=config.load_input_mode, tempo_seconds_per_rep=config.tempo_seconds_per_rep,
    )
    await _prompt_regular_set_input(message, state, session, exercise.id, config)


async def _prompt_regular_set_input(message: Message, state: FSMContext, session, exercise_id: int, config) -> None:
    await state.update_data(
        session_id=session.id, session_exercise_id=exercise_id, exercise_code=config.code,
        pending_measurement=config.measurement_type, exercise_mode="workout",
        load_kg=None, load_kind=None, functional_input=None,
    )
    if config.load_input_mode == "optional":
        await _prompt_optional_load(message, config)
    elif config.measurement_type in {"repetitions_load", "load_duration_distance"}:
        await state.update_data(load_kind="working")
        await state.set_state(ActivityTrackingStates.entering_set_load)
        await message.answer(f"🏋️ <b>{config.name}</b>\n\n{_load_prompt(config)}", reply_markup=_number_keyboard(("5", "10", "15", "20", "30", "40", "50", "60")), parse_mode="HTML")
    elif config.measurement_type == "duration":
        await state.set_state(ActivityTrackingStates.entering_set_duration)
        await message.answer(f"🏋️ <b>{config.name}</b>\n\nВведи длительность подхода в секундах или формате 1:30:", reply_markup=_number_keyboard(("30", "45", "60", "90")), parse_mode="HTML")
    else:
        await state.set_state(ActivityTrackingStates.entering_set_repetitions)
        await message.answer(f"🏋️ <b>{config.name}</b>\n\nСколько повторений в подходе?", reply_markup=_number_keyboard(("5", "8", "10", "12", "15", "20")), parse_mode="HTML")


@router.callback_query(F.data.startswith("act:add_set:"))
async def add_set_to_existing_exercise(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    user_id = str(callback.from_user.id)
    exercise_id = int(callback.data.rsplit(":", 1)[-1])
    session = ActivityRepository.get_active_workout(user_id)
    if session is None:
        await callback.answer("Активная тренировка не найдена", show_alert=True)
        return
    exercise = _exercise_for_set(session.id, exercise_id, user_id)
    config = EXERCISE_BY_CODE.get(exercise.exercise_code) if exercise else None
    if exercise is None or config is None:
        await callback.answer("Упражнение не найдено", show_alert=True)
        return
    await _prompt_regular_set_input(callback.message, state, session, exercise.id, config)


async def _prompt_optional_load(message: Message, config) -> None:
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Без дополнительного веса", callback_data="act:optional_load:none")],
        [InlineKeyboardButton(text="➕ Дополнительный вес", callback_data="act:optional_load:additional")],
        [InlineKeyboardButton(text="⚙️ Помощь тренажёра", callback_data="act:optional_load:assistance")],
    ])
    await message.answer(
        f"🏋️ <b>{config.name}</b>\n\nКак выполнялось упражнение?",
        reply_markup=keyboard, parse_mode="HTML",
    )


@router.callback_query(F.data.startswith("act:optional_load:"))
async def choose_optional_load(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    load_kind = callback.data.rsplit(":", 1)[-1]
    data = await state.get_data()
    if load_kind == "none":
        await state.update_data(load_kg=None, load_kind=None)
        if data.get("exercise_mode") in {"quick_now", "quick_past"}:
            await state.set_state(ActivityTrackingStates.entering_quick_sets)
            await callback.message.answer("Введи подходы и повторения, например <b>3×10</b> или <b>20</b>:", parse_mode="HTML")
        else:
            await state.set_state(ActivityTrackingStates.entering_set_repetitions)
            await callback.message.answer("Сколько повторений в подходе?", reply_markup=_number_keyboard(("5", "8", "10", "12", "15", "20")))
        return
    await state.update_data(load_kind=load_kind)
    await state.set_state(ActivityTrackingStates.entering_set_load)
    prompt = "Введи вес помощи тренажёра в килограммах:" if load_kind == "assistance" else "Введи дополнительный вес в килограммах:"
    await callback.message.answer(prompt, reply_markup=_number_keyboard(("5", "10", "15", "20", "30", "40")))


@router.callback_query(F.data.startswith("act:epick:"))
async def pick_exercise(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    _, _, mode, exercise_code = callback.data.split(":")
    config = EXERCISE_BY_CODE.get(exercise_code)
    if config is None:
        await callback.answer("Упражнение не найдено", show_alert=True)
        return
    if mode == "workout":
        session = ActivityRepository.get_active_workout(str(callback.from_user.id))
        if session is None:
            await callback.answer("Сначала начни тренировку", show_alert=True)
            return
        await _start_regular_set_input(callback.message, state, session, config)
        return
    await _start_quick_exercise(callback.message, state, str(callback.from_user.id), config, mode)


@router.message(ActivityTrackingStates.entering_set_load)
async def receive_set_load(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    try:
        load = _parse_positive_number(message.text)
        if load > 1000:
            raise ValueError
    except ValueError:
        await message.answer("Введи корректный вес от 0,1 до 1000 кг.")
        return
    await state.update_data(load_kg=load)
    if data.get("pending_measurement") == "load_duration_distance":
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="⏱ По времени", callback_data="act:functional_input:time")],
            [InlineKeyboardButton(text="📏 По расстоянию", callback_data="act:functional_input:distance")],
        ])
        await message.answer("Как записать этот подход?", reply_markup=keyboard)
    elif data.get("exercise_mode") in {"quick_now", "quick_past"}:
        await state.set_state(ActivityTrackingStates.entering_quick_sets)
        await message.answer("Введи подходы и повторения, например <b>3×10</b> или <b>20</b>:", parse_mode="HTML")
    else:
        await state.set_state(ActivityTrackingStates.entering_set_repetitions)
        await message.answer("Сколько повторений в подходе?", reply_markup=_number_keyboard(("5", "8", "10", "12", "15", "20")))


@router.callback_query(F.data.startswith("act:functional_input:"))
async def choose_functional_input(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    method = callback.data.rsplit(":", 1)[-1]
    data = await state.get_data()
    await state.update_data(functional_input=method)
    if data.get("exercise_mode") in {"quick_now", "quick_past"}:
        await state.set_state(ActivityTrackingStates.entering_quick_sets)
    elif method == "distance":
        await state.set_state(ActivityTrackingStates.entering_set_distance)
    else:
        await state.set_state(ActivityTrackingStates.entering_set_duration)
    if method == "distance":
        await callback.message.answer("Введи расстояние в метрах:", reply_markup=_number_keyboard(("20", "30", "50", "100")))
    else:
        await callback.message.answer("Введи длительность в секундах или формате 1:30:", reply_markup=_number_keyboard(("30", "45", "60", "90")))


@router.message(ActivityTrackingStates.entering_set_repetitions)
async def receive_set_repetitions(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    try:
        repetitions = int((message.text or "").strip())
        if repetitions <= 0 or repetitions > 1000:
            raise ValueError
    except ValueError:
        await message.answer("Введи целое количество повторений от 1 до 1000.")
        return
    ActivityRepository.add_workout_set(
        session_id=int(data["session_id"]), session_exercise_id=int(data["session_exercise_id"]),
        user_id=str(message.from_user.id), repetitions=repetitions, load_kg=data.get("load_kg"),
        load_kind=data.get("load_kind"),
    )
    await state.clear()
    await show_active_workout(message, str(message.from_user.id), prefix="✅ Подход добавлен")


@router.message(ActivityTrackingStates.entering_set_duration)
async def receive_set_duration(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    try:
        seconds = _parse_seconds(message.text)
    except (TypeError, ValueError):
        await message.answer("Введи секунды числом или время в формате 1:30.")
        return
    ActivityRepository.add_workout_set(
        session_id=int(data["session_id"]), session_exercise_id=int(data["session_exercise_id"]),
        user_id=str(message.from_user.id), duration_seconds=seconds, load_kg=data.get("load_kg"),
        load_kind=data.get("load_kind"),
    )
    await state.clear()
    await show_active_workout(message, str(message.from_user.id), prefix="✅ Подход добавлен")


@router.message(ActivityTrackingStates.entering_set_distance)
async def receive_set_distance(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    try:
        distance_meters = _parse_positive_number(message.text)
        if distance_meters > 100_000:
            raise ValueError
    except ValueError:
        await message.answer("Введи расстояние от 1 до 100 000 метров.")
        return
    ActivityRepository.add_workout_set(
        session_id=int(data["session_id"]), session_exercise_id=int(data["session_exercise_id"]),
        user_id=str(message.from_user.id), distance_meters=distance_meters,
        load_kg=data.get("load_kg"), load_kind=data.get("load_kind"),
    )
    await state.clear()
    await show_active_workout(message, str(message.from_user.id), prefix="✅ Подход добавлен")


def _workout_intensity_keyboard(session_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=label, callback_data=f"act:wint:{session_id}:{level}")]
        for level, label in WORKOUT_INTENSITY_LABELS.items()
    ])


async def _ask_workout_intensity(message: Message, session_id: int) -> None:
    await message.answer(
        "<b>Как прошла тренировка?</b>",
        reply_markup=_workout_intensity_keyboard(session_id),
        parse_mode="HTML",
    )


@router.callback_query(F.data.startswith("act:finish_prompt:"))
async def finish_workout_prompt(callback: CallbackQuery) -> None:
    await callback.answer()
    session_id = int(callback.data.rsplit(":", 1)[-1])
    session = ActivityRepository.mark_workout_awaiting_intensity(session_id, str(callback.from_user.id))
    if session is None:
        await callback.answer("Тренировка не найдена", show_alert=True)
        return
    await _edit_or_answer(
        callback.message,
        f"🏋️ Тренировка завершена\n⏱ {_format_duration(session.duration_seconds or 0)}\n\n<b>Как прошла тренировка?</b>",
        _workout_intensity_keyboard(session.id),
    )


@router.callback_query(F.data.startswith("act:wint:"))
async def save_workout_intensity(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    _, _, session_id_raw, intensity = callback.data.split(":")
    session = ActivityRepository.get_workout_session(int(session_id_raw), str(callback.from_user.id))
    if session is None or session.duration_seconds is None:
        await callback.answer("Тренировка не найдена", show_alert=True)
        return
    try:
        energy = calculate_workout_energy(
            intensity=intensity, weight_kg=session.weight_kg_snapshot,
            duration_seconds=session.duration_seconds,
        )
    except ActivityValidationError as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    completed = ActivityRepository.finish_workout(
        session_id=session.id, user_id=str(callback.from_user.id), intensity=intensity,
        met_value=energy.met_value, gross_calories=energy.gross_calories,
        credited_calories=energy.credited_calories,
    )
    if completed is None:
        await callback.answer("Тренировка уже сохранена", show_alert=True)
        return
    await state.clear()
    note = "\nВремя выполнения оценено автоматически, поэтому калорийность особенно приблизительная." if completed.duration_source == "estimated" else ""
    text, keyboard = format_activity_overview(str(callback.from_user.id), completed.entry_date)
    await _edit_or_answer(
        callback.message,
        f"✅ Тренировка сохранена\n"
        f"🔥 Потрачено: ~{energy.gross_calories:.0f} ккал\n"
        f"🎯 Учтено в норме: +{energy.credited_calories:.0f} ккал{note}\n\n{text}",
        keyboard,
    )


@router.callback_query(F.data.startswith("act:cancel_confirm:"))
async def confirm_workout_cancel(callback: CallbackQuery) -> None:
    await callback.answer()
    session_id = int(callback.data.rsplit(":", 1)[-1])
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✖️ Да, отменить", callback_data=f"act:cancel:{session_id}")],
        [InlineKeyboardButton(text="Продолжить тренировку", callback_data="act:workout_screen")],
    ])
    await _edit_or_answer(callback.message, "Отменить тренировку и удалить все добавленные подходы?", keyboard)


@router.callback_query(F.data.startswith("act:cancel:"))
async def cancel_workout(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    session_id = int(callback.data.rsplit(":", 1)[-1])
    ActivityRepository.cancel_workout(session_id, str(callback.from_user.id))
    await state.clear()
    text, keyboard = format_activity_overview(str(callback.from_user.id), date.today())
    await _edit_or_answer(callback.message, f"✅ Тренировка отменена\n\n{text}", keyboard)


@router.message(F.text == "⚡ Быстрое упражнение")
async def open_quick_exercise(message: Message, state: FSMContext) -> None:
    await state.clear()
    existing = ActivityRepository.get_active_workout(str(message.from_user.id))
    if existing is not None:
        await show_active_workout(message, str(message.from_user.id), prefix="Сначала заверши текущую тренировку.")
        return
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="▶️ Выполнить сейчас", callback_data="act:qmode:quick_now")],
        [InlineKeyboardButton(text="📝 Уже выполнено", callback_data="act:qmode:quick_past")],
        [InlineKeyboardButton(text="✖️ Закрыть", callback_data="act:close")],
    ])
    await message.answer(
        "⚡ <b>Быстрое упражнение</b>\n\n"
        "Если начнёшь сейчас, бот измерит реальное время. Для уже выполненного упражнения время будет оценено по подходам.",
        reply_markup=keyboard,
        parse_mode="HTML",
    )


@router.callback_query(F.data.startswith("act:qmode:"))
async def choose_quick_mode(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    mode = callback.data.rsplit(":", 1)[-1]
    await state.update_data(exercise_mode=mode)
    await _edit_or_answer(callback.message, "Выбери категорию упражнения:", _exercise_categories_keyboard(mode))


async def _start_quick_exercise(message: Message, state: FSMContext, user_id: str, config, mode: str) -> None:
    if ActivityRepository.get_active_workout(user_id) is not None:
        await message.answer("Сначала заверши текущую тренировку.")
        return
    weight, weight_source = _weight_snapshot(user_id)
    try:
        session = ActivityRepository.create_workout_session(
            user_id=user_id, entry_date=date.today(), weight_kg=weight,
            weight_source=weight_source, session_kind="quick",
            duration_source="timer" if mode == "quick_now" else "estimated",
            started_at=datetime.utcnow() if mode == "quick_now" else None,
        )
    except ActiveWorkoutExistsError:
        await message.answer("Сначала заверши текущую тренировку.")
        return
    exercise = ActivityRepository.add_session_exercise(
        session_id=session.id, user_id=user_id, exercise_code=config.code,
        exercise_name=config.name, measurement_type=config.measurement_type,
        load_input_mode=config.load_input_mode, tempo_seconds_per_rep=config.tempo_seconds_per_rep,
    )
    await state.update_data(
        session_id=session.id, session_exercise_id=exercise.id,
        exercise_code=config.code, exercise_mode=mode,
        pending_measurement=config.measurement_type,
    )
    if mode == "quick_now":
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✅ Закончить упражнение", callback_data=f"act:qstop:{session.id}")],
            [InlineKeyboardButton(text="✖️ Отменить", callback_data=f"act:cancel_confirm:{session.id}")],
        ])
        await message.answer(
            f"▶️ <b>{config.name}</b>\n\nТаймер запущен. Выполни упражнение и нажми «Закончить упражнение».",
            reply_markup=keyboard,
            parse_mode="HTML",
        )
        return
    await _prompt_quick_result_input(message, state, config)


async def _prompt_quick_result_input(message: Message, state: FSMContext, config) -> None:
    if config.measurement_type == "duration":
        await state.set_state(ActivityTrackingStates.entering_quick_sets)
        await message.answer("Введи фактическое время упражнения в секундах или формате 1:30:")
    elif config.load_input_mode == "optional":
        await _prompt_optional_load(message, config)
    elif config.measurement_type in {"repetitions_load", "load_duration_distance"}:
        await state.update_data(load_kind="working")
        await state.set_state(ActivityTrackingStates.entering_set_load)
        await message.answer(_load_prompt(config), reply_markup=_number_keyboard(("5", "10", "15", "20", "30", "40", "50", "60")))
    else:
        await state.set_state(ActivityTrackingStates.entering_quick_sets)
        await message.answer("Введи подходы и повторения, например <b>3×10</b> или <b>20</b>:", parse_mode="HTML")


@router.callback_query(F.data.startswith("act:qstop:"))
async def stop_quick_timer(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    session_id = int(callback.data.rsplit(":", 1)[-1])
    data = await state.get_data()
    session = ActivityRepository.mark_workout_awaiting_intensity(session_id, str(callback.from_user.id))
    config = EXERCISE_BY_CODE.get(str(data.get("exercise_code")))
    if session is None or config is None:
        await callback.answer("Быстрое упражнение не найдено", show_alert=True)
        return
    if config.measurement_type == "duration":
        ActivityRepository.add_workout_set(
            session_id=session.id, session_exercise_id=int(data["session_exercise_id"]),
            user_id=str(callback.from_user.id), duration_seconds=max(session.duration_seconds or 1, 1),
        )
        await _edit_or_answer(
            callback.message,
            f"✅ Время: {_format_duration(session.duration_seconds or 0)}\n\n<b>Как прошла тренировка?</b>",
            _workout_intensity_keyboard(session.id),
        )
        return
    await _prompt_quick_result_input(callback.message, state, config)


def _parse_sets_and_reps(text: str | None) -> tuple[int, int]:
    raw = (text or "").strip().casefold().replace(" ", "")
    match = re.fullmatch(r"(\d+)[xх×*](\d+)", raw)
    if match:
        set_count, repetitions = int(match.group(1)), int(match.group(2))
    else:
        set_count, repetitions = 1, int(raw)
    if set_count <= 0 or set_count > 50 or repetitions <= 0 or repetitions > 1000:
        raise ValueError
    return set_count, repetitions


@router.message(ActivityTrackingStates.entering_quick_sets)
async def receive_quick_sets(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    config = EXERCISE_BY_CODE.get(str(data.get("exercise_code")))
    session_id = int(data.get("session_id") or 0)
    exercise_id = int(data.get("session_exercise_id") or 0)
    if config is None or not session_id or not exercise_id:
        await state.clear()
        await message.answer("Не удалось восстановить быстрое упражнение.")
        return
    try:
        if config.measurement_type == "duration" or (
            config.measurement_type == "load_duration_distance" and data.get("functional_input") != "distance"
        ):
            duration = _parse_seconds(message.text)
            ActivityRepository.add_workout_set(
                session_id=session_id, session_exercise_id=exercise_id,
                user_id=str(message.from_user.id), duration_seconds=duration,
                load_kg=data.get("load_kg"),
                load_kind=data.get("load_kind"),
            )
        elif config.measurement_type == "load_duration_distance":
            distance_meters = _parse_positive_number(message.text)
            if distance_meters > 100_000:
                raise ValueError
            ActivityRepository.add_workout_set(
                session_id=session_id, session_exercise_id=exercise_id,
                user_id=str(message.from_user.id), distance_meters=distance_meters,
                load_kg=data.get("load_kg"), load_kind=data.get("load_kind"),
            )
        else:
            set_count, repetitions = _parse_sets_and_reps(message.text)
            for _ in range(set_count):
                ActivityRepository.add_workout_set(
                    session_id=session_id, session_exercise_id=exercise_id,
                    user_id=str(message.from_user.id), repetitions=repetitions,
                    load_kg=data.get("load_kg"),
                    load_kind=data.get("load_kind"),
                )
    except (ValueError, TypeError):
        if config.measurement_type == "load_duration_distance" and data.get("functional_input") == "distance":
            prompt = "Введи расстояние в метрах."
        elif config.measurement_type in {"duration", "load_duration_distance"}:
            prompt = "Введи секунды числом или время 1:30."
        else:
            prompt = "Используй формат 3×10 или одно число, например 20."
        await message.answer(prompt)
        return

    session = ActivityRepository.get_workout_session(session_id, str(message.from_user.id))
    if session is None:
        await state.clear()
        await message.answer("Тренировка не найдена.")
        return
    if data.get("exercise_mode") == "quick_past":
        sets = ActivityRepository.get_session_sets(session_id, str(message.from_user.id))
        estimated_seconds = estimate_quick_workout_duration_seconds(sets)
        session = ActivityRepository.mark_workout_awaiting_intensity(
            session_id, str(message.from_user.id), duration_seconds=estimated_seconds,
        )
        prefix = f"⏱ Оценочное время: {_format_duration(estimated_seconds)}\n\n"
    else:
        prefix = f"⏱ Время: {_format_duration(session.duration_seconds or 0)}\n\n"
    await _ask_workout_intensity_with_prefix(message, session.id, prefix)


async def _ask_workout_intensity_with_prefix(message: Message, session_id: int, prefix: str) -> None:
    await message.answer(
        prefix + "<b>Как прошла тренировка?</b>",
        reply_markup=_workout_intensity_keyboard(session_id),
        parse_mode="HTML",
    )


def _workout_detail_text(session, user_id: str) -> str:
    exercises, grouped, _ = _sets_by_exercise(session.id, user_id)
    title = "Быстрое упражнение" if session.session_kind == "quick" else "Силовая тренировка"
    lines = [
        f"🏋️ <b>{title}</b>",
        "",
        f"Продолжительность: {_format_duration(session.duration_seconds or 0)}",
        f"Интенсивность: {WORKOUT_INTENSITY_LABELS.get(session.intensity, session.intensity or '—')}",
        f"Потрачено: ~{session.gross_calories:.0f} ккал",
        f"Учтено в норме: +{session.credited_calories:.0f} ккал",
        "",
    ]
    for exercise in exercises:
        lines.append(f"<b>{exercise.position}. {html.escape(exercise.exercise_name_snapshot)}</b>")
        for item in grouped.get(exercise.id, []):
            lines.append(f"  {item.position}-й подход — {_format_set(item, exercise.load_input_mode_snapshot)}")
    if session.duration_source == "estimated":
        lines.extend(["", "ℹ️ Время оценено автоматически по подходам, поэтому калорийность особенно приблизительная."])
    return "\n".join(lines)


def _workout_detail_keyboard(session, user_id: str) -> InlineKeyboardMarkup:
    _, _, sets = _sets_by_exercise(session.id, user_id)
    rows = []
    for item in sets:
        rows.append([InlineKeyboardButton(text=f"✏️ Подход {item.id} · {_format_set(item)}", callback_data=f"act:set_detail:{item.id}")])
    rows.extend([
        [InlineKeyboardButton(text="⏱ Изменить время", callback_data=f"act:wedit_time:{session.id}")],
        [InlineKeyboardButton(text="🔥 Изменить интенсивность", callback_data=f"act:wedit_int_menu:{session.id}")],
        [InlineKeyboardButton(text="🗑 Удалить тренировку", callback_data=f"act:wdelete_confirm:{session.id}")],
        [InlineKeyboardButton(text="⬅️ К дню", callback_data=f"act:day:{session.entry_date.isoformat()}")],
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


@router.callback_query(F.data.startswith("act:workout_detail:"))
async def show_workout_detail(callback: CallbackQuery) -> None:
    await callback.answer()
    session_id = int(callback.data.rsplit(":", 1)[-1])
    session = ActivityRepository.get_workout_session(session_id, str(callback.from_user.id))
    if session is None or session.status != "completed":
        await callback.answer("Тренировка не найдена", show_alert=True)
        return
    await _edit_or_answer(
        callback.message, _workout_detail_text(session, str(callback.from_user.id)),
        _workout_detail_keyboard(session, str(callback.from_user.id)),
    )


@router.callback_query(F.data.startswith("act:wedit_time:"))
async def request_workout_duration_edit(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    session_id = int(callback.data.rsplit(":", 1)[-1])
    session = ActivityRepository.get_workout_session(session_id, str(callback.from_user.id))
    if session is None or session.status != "completed":
        await callback.answer("Тренировка не найдена", show_alert=True)
        return
    await state.update_data(edit_session_id=session.id)
    await state.set_state(ActivityTrackingStates.editing_workout_duration)
    await callback.message.answer(
        f"Сейчас: {_format_duration(session.duration_seconds or 0)}.\n\nВведи новую продолжительность в минутах:",
        reply_markup=_minutes_keyboard(),
    )


@router.message(ActivityTrackingStates.editing_workout_duration)
async def save_workout_duration_edit(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    session = ActivityRepository.get_workout_session(int(data.get("edit_session_id") or 0), str(message.from_user.id))
    if session is None or not session.intensity:
        await state.clear()
        await message.answer("Тренировка не найдена.")
        return
    try:
        minutes = _parse_positive_number(message.text)
        energy = calculate_workout_energy(
            intensity=session.intensity, weight_kg=session.weight_kg_snapshot,
            duration_seconds=int(round(minutes * 60)),
        )
    except (ValueError, ActivityValidationError) as exc:
        await message.answer(f"Укажи корректное количество минут. {html.escape(str(exc))}", reply_markup=_minutes_keyboard())
        return
    ActivityRepository.update_completed_workout(
        session_id=session.id, user_id=str(message.from_user.id),
        duration_seconds=int(round(minutes * 60)), met_value=energy.met_value,
        gross_calories=energy.gross_calories, credited_calories=energy.credited_calories,
    )
    await state.clear()
    await show_activity_main(message, str(message.from_user.id), session.entry_date, prefix="✅ Тренировка обновлена")


@router.callback_query(F.data.startswith("act:wedit_int_menu:"))
async def show_workout_intensity_edit(callback: CallbackQuery) -> None:
    await callback.answer()
    session_id = int(callback.data.rsplit(":", 1)[-1])
    session = ActivityRepository.get_workout_session(session_id, str(callback.from_user.id))
    if session is None or session.status != "completed":
        await callback.answer("Тренировка не найдена", show_alert=True)
        return
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=label, callback_data=f"act:wedit_int:{session.id}:{level}")]
        for level, label in WORKOUT_INTENSITY_LABELS.items()
    ])
    await _edit_or_answer(callback.message, "Как прошла тренировка?", keyboard)


@router.callback_query(F.data.startswith("act:wedit_int:"))
async def save_workout_intensity_edit(callback: CallbackQuery) -> None:
    await callback.answer()
    _, _, session_id_raw, intensity = callback.data.split(":")
    session = ActivityRepository.get_workout_session(int(session_id_raw), str(callback.from_user.id))
    if session is None or session.duration_seconds is None:
        await callback.answer("Тренировка не найдена", show_alert=True)
        return
    try:
        energy = calculate_workout_energy(
            intensity=intensity, weight_kg=session.weight_kg_snapshot,
            duration_seconds=session.duration_seconds,
        )
    except ActivityValidationError as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    ActivityRepository.update_completed_workout(
        session_id=session.id, user_id=str(callback.from_user.id),
        intensity=intensity, met_value=energy.met_value,
        gross_calories=energy.gross_calories, credited_calories=energy.credited_calories,
    )
    updated = ActivityRepository.get_workout_session(session.id, str(callback.from_user.id))
    await _edit_or_answer(
        callback.message, "✅ Обновлено\n\n" + _workout_detail_text(updated, str(callback.from_user.id)),
        _workout_detail_keyboard(updated, str(callback.from_user.id)),
    )


@router.callback_query(F.data.startswith("act:wdelete_confirm:"))
async def confirm_workout_delete(callback: CallbackQuery) -> None:
    await callback.answer()
    session_id = int(callback.data.rsplit(":", 1)[-1])
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🗑 Да, удалить", callback_data=f"act:wdelete:{session_id}")],
        [InlineKeyboardButton(text="Отмена", callback_data=f"act:workout_detail:{session_id}")],
    ])
    await _edit_or_answer(callback.message, "Удалить тренировку вместе со всеми подходами?", keyboard)


@router.callback_query(F.data.startswith("act:wdelete:"))
async def delete_workout(callback: CallbackQuery) -> None:
    await callback.answer()
    session_id = int(callback.data.rsplit(":", 1)[-1])
    session = ActivityRepository.get_workout_session(session_id, str(callback.from_user.id))
    if session is None:
        await callback.answer("Тренировка уже удалена", show_alert=True)
        return
    ActivityRepository.delete_workout_session(session.id, str(callback.from_user.id))
    text, keyboard = format_activity_overview(str(callback.from_user.id), session.entry_date)
    await _edit_or_answer(callback.message, f"✅ Тренировка удалена\n\n{text}", keyboard)


def _exercise_for_set(session_id: int, session_exercise_id: int, user_id: str):
    return next(
        (item for item in ActivityRepository.get_session_exercises(session_id, user_id) if item.id == session_exercise_id),
        None,
    )


@router.callback_query(F.data.startswith("act:set_detail:"))
async def show_set_detail(callback: CallbackQuery) -> None:
    await callback.answer()
    set_id = int(callback.data.rsplit(":", 1)[-1])
    item = ActivityRepository.get_workout_set(set_id, str(callback.from_user.id))
    if item is None:
        await callback.answer("Подход не найден", show_alert=True)
        return
    exercise = _exercise_for_set(item.session_id, item.session_exercise_id, str(callback.from_user.id))
    session = ActivityRepository.get_workout_session(item.session_id, str(callback.from_user.id))
    back_callback = "act:workout_screen" if session and session.status in {"active", "paused", "awaiting_intensity"} else f"act:workout_detail:{item.session_id}"
    rows = []
    if item.repetitions is not None:
        rows.append([InlineKeyboardButton(text="🔁 Изменить повторения", callback_data=f"act:set_edit_reps:{item.id}")])
    if item.load_kg is not None or (exercise and exercise.load_input_mode_snapshot != "none"):
        rows.append([InlineKeyboardButton(text="⚖️ Изменить вес", callback_data=f"act:set_edit_load:{item.id}")])
    rows.extend([
        [InlineKeyboardButton(text="🗑 Удалить подход", callback_data=f"act:set_delete_confirm:{item.id}")],
        [InlineKeyboardButton(text="⬅️ К тренировке", callback_data=back_callback)],
    ])
    name = exercise.exercise_name_snapshot if exercise else "Подход"
    await _edit_or_answer(
        callback.message,
        f"🏋️ <b>{html.escape(name)}</b>\n\n{item.position}-й подход — {_format_set(item, exercise.load_input_mode_snapshot if exercise else 'none')}",
        InlineKeyboardMarkup(inline_keyboard=rows),
    )


@router.callback_query(F.data.startswith("act:set_edit_reps:"))
async def request_set_reps_edit(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    set_id = int(callback.data.rsplit(":", 1)[-1])
    item = ActivityRepository.get_workout_set(set_id, str(callback.from_user.id))
    if item is None:
        await callback.answer("Подход не найден", show_alert=True)
        return
    await state.update_data(edit_set_id=item.id)
    await state.set_state(ActivityTrackingStates.editing_set_repetitions)
    await callback.message.answer("Введи новое количество повторений:", reply_markup=_number_keyboard(("5", "8", "10", "12", "15", "20")))


@router.message(ActivityTrackingStates.editing_set_repetitions)
async def save_set_reps_edit(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    try:
        repetitions = int((message.text or "").strip())
        if repetitions <= 0 or repetitions > 1000:
            raise ValueError
    except ValueError:
        await message.answer("Введи целое число от 1 до 1000.")
        return
    item = ActivityRepository.get_workout_set(int(data.get("edit_set_id") or 0), str(message.from_user.id))
    if item is None:
        await state.clear()
        await message.answer("Подход не найден.")
        return
    ActivityRepository.update_workout_set(set_id=item.id, user_id=str(message.from_user.id), repetitions=repetitions)
    await state.clear()
    session = ActivityRepository.get_workout_session(item.session_id, str(message.from_user.id))
    if session.status in {"active", "paused", "awaiting_intensity"}:
        await message.answer(
            "✅ Подход обновлён\n\n" + _active_workout_text(session, str(message.from_user.id)),
            reply_markup=_active_workout_keyboard(session, str(message.from_user.id)), parse_mode="HTML",
        )
    else:
        await message.answer(
            "✅ Подход обновлён\n\n" + _workout_detail_text(session, str(message.from_user.id)),
            reply_markup=_workout_detail_keyboard(session, str(message.from_user.id)), parse_mode="HTML",
        )


@router.callback_query(F.data.startswith("act:set_edit_load:"))
async def request_set_load_edit(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    set_id = int(callback.data.rsplit(":", 1)[-1])
    item = ActivityRepository.get_workout_set(set_id, str(callback.from_user.id))
    if item is None:
        await callback.answer("Подход не найден", show_alert=True)
        return
    await state.update_data(edit_set_id=item.id)
    await state.set_state(ActivityTrackingStates.editing_set_load)
    await callback.message.answer("Введи новый рабочий вес в килограммах:")


@router.message(ActivityTrackingStates.editing_set_load)
async def save_set_load_edit(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    try:
        load = _parse_positive_number(message.text)
        if load > 1000:
            raise ValueError
    except ValueError:
        await message.answer("Введи корректный вес от 0,1 до 1000 кг.")
        return
    item = ActivityRepository.get_workout_set(int(data.get("edit_set_id") or 0), str(message.from_user.id))
    if item is None:
        await state.clear()
        await message.answer("Подход не найден.")
        return
    ActivityRepository.update_workout_set(
        set_id=item.id, user_id=str(message.from_user.id), load_kg=load, update_load=True,
    )
    await state.clear()
    session = ActivityRepository.get_workout_session(item.session_id, str(message.from_user.id))
    if session.status in {"active", "paused", "awaiting_intensity"}:
        await message.answer(
            "✅ Подход обновлён\n\n" + _active_workout_text(session, str(message.from_user.id)),
            reply_markup=_active_workout_keyboard(session, str(message.from_user.id)), parse_mode="HTML",
        )
    else:
        await message.answer(
            "✅ Подход обновлён\n\n" + _workout_detail_text(session, str(message.from_user.id)),
            reply_markup=_workout_detail_keyboard(session, str(message.from_user.id)), parse_mode="HTML",
        )


@router.callback_query(F.data.startswith("act:set_delete_confirm:"))
async def confirm_set_delete(callback: CallbackQuery) -> None:
    await callback.answer()
    set_id = int(callback.data.rsplit(":", 1)[-1])
    item = ActivityRepository.get_workout_set(set_id, str(callback.from_user.id))
    if item is None:
        await callback.answer("Подход не найден", show_alert=True)
        return
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🗑 Да, удалить", callback_data=f"act:set_delete:{item.id}")],
        [InlineKeyboardButton(text="Отмена", callback_data=f"act:set_detail:{item.id}")],
    ])
    await _edit_or_answer(callback.message, "Удалить этот подход?", keyboard)


@router.callback_query(F.data.startswith("act:set_delete:"))
async def delete_set(callback: CallbackQuery) -> None:
    await callback.answer()
    set_id = int(callback.data.rsplit(":", 1)[-1])
    item = ActivityRepository.get_workout_set(set_id, str(callback.from_user.id))
    if item is None:
        await callback.answer("Подход уже удалён", show_alert=True)
        return
    ActivityRepository.delete_workout_set(item.id, str(callback.from_user.id))
    session = ActivityRepository.get_workout_session(item.session_id, str(callback.from_user.id))
    if session.status in {"active", "paused", "awaiting_intensity"}:
        await _edit_or_answer(
            callback.message, "✅ Подход удалён\n\n" + _active_workout_text(session, str(callback.from_user.id)),
            _active_workout_keyboard(session, str(callback.from_user.id)),
        )
    else:
        await _edit_or_answer(
            callback.message, "✅ Подход удалён\n\n" + _workout_detail_text(session, str(callback.from_user.id)),
            _workout_detail_keyboard(session, str(callback.from_user.id)),
        )


@router.callback_query(F.data == "act:close")
async def close_inline(callback: CallbackQuery) -> None:
    await callback.answer()
    try:
        await callback.message.delete()
    except TelegramBadRequest:
        pass
