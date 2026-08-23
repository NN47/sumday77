"""Новый UX раздела «Активность»: шаги, время и тренировочные сессии."""
from __future__ import annotations

from collections import defaultdict
from datetime import date
import html
import math

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
    ReplyKeyboardRemove,
)
from sqlalchemy.exc import SQLAlchemyError

from database.repositories import ActivityRepository, WorkoutDraftExistsError, WeightRepository
from services.activity_energy_service import (
    ActivityValidationError,
    DEFAULT_WEIGHT_KG,
    calculate_steps_energy,
    calculate_timed_activity_energy,
    calculate_workout_energy,
    estimate_workout_duration_seconds,
    get_daily_activity_energy_summary,
)
from states.user_states import ActivityTrackingStates
from utils.activity_catalog import (
    EXERCISE_BY_CODE,
    EXERCISE_CATEGORIES,
    EXERCISE_CATEGORY_BY_CODE,
    EXERCISES,
    INTENSITY_LABELS,
    TIMED_ACTIVITIES,
    TIMED_ACTIVITY_BY_CODE,
    TIMED_CATEGORIES,
    TIMED_CATEGORY_BY_CODE,
    TIMED_ACTIVITY_SEARCH_ALIASES,
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
from utils.calendar_utils import build_activity_calendar_keyboard, show_calendar_back_button


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


async def _hide_section_reply_keyboard(message: Message) -> None:
    """Скрывает меню раздела перед сценарием с inline-кнопками."""
    placeholder = await message.answer(
        "\u2063", reply_markup=ReplyKeyboardRemove(), disable_notification=True,
    )
    try:
        await placeholder.delete()
    except (TelegramBadRequest, AttributeError):
        pass


def format_activity_overview(
    user_id: str, target_date: date, *, from_calendar: bool | None = None,
) -> tuple[str, InlineKeyboardMarkup]:
    if from_calendar is None:
        from_calendar = target_date != date.today()
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

    for session in sessions:
        title = "Силовая тренировка"
        lines.extend([
            f"🏋️ {title} — {_format_duration(session.duration_seconds or 0)}",
            f"   {session.exercise_count} упр. · {session.set_count} подх. · ~{session.gross_calories:.0f} ккал",
        ])

    if not timed and not sessions and steps is None:
        lines.extend(["", "Пока ничего не добавлено."])
    lines.extend([
        "",
        f"🔥 <b>Всего потрачено: ~{summary.gross_calories:.0f} ккал</b>",
        f"🎯 <b>Учтено в дневной норме: +{summary.credited_calories:.0f} ккал</b>",
    ])
    if timed or sessions:
        buttons.append([InlineKeyboardButton(
            text="✏️ Редактировать активность",
            callback_data=f"act:edit_day:{target_date.isoformat()}",
        )])
    buttons.append([InlineKeyboardButton(
        text="👣 Добавить шаги",
        callback_data=f"act:steps_start:{target_date.isoformat()}",
    )])
    if from_calendar:
        buttons.append([InlineKeyboardButton(
            text="⬅️ Назад к календарю активности",
            callback_data=f"actcal_back:{target_date.year}-{target_date.month:02d}",
        )])
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


@router.callback_query(F.data == "act:main")
async def return_to_activity_main(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    await state.clear()
    await show_activity_main(callback.message, str(callback.from_user.id))


@router.callback_query(F.data.startswith("act:steps_start:"))
async def start_steps_from_report(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    target = _safe_date(callback.data.rsplit(":", 1)[-1])
    await start_steps_flow(
        callback.message, state, str(callback.from_user.id), target,
    )


@router.callback_query(F.data.startswith("act:edit_day:"))
async def open_activity_edit_list(callback: CallbackQuery) -> None:
    await callback.answer()
    target = _safe_date(callback.data.rsplit(":", 1)[-1])
    user_id = str(callback.from_user.id)
    timed = ActivityRepository.get_timed_activities_for_day(user_id, target)
    workouts = [
        item for item in ActivityRepository.get_workout_sessions_for_day(user_id, target)
        if item.status == "completed"
    ]
    rows: list[list[InlineKeyboardButton]] = []
    for entry in timed:
        rows.append([InlineKeyboardButton(
            text=f"✏️ {entry.activity_name_snapshot} · {_format_number(entry.duration_minutes)} мин",
            callback_data=f"act:timed_detail:{entry.id}",
        )])
    for session in workouts:
        rows.append([InlineKeyboardButton(
            text=f"✏️ Силовая тренировка · {_format_duration(session.duration_seconds or 0)}",
            callback_data=f"act:workout_detail:{session.id}",
        )])
    rows.append([InlineKeyboardButton(
        text="⬅️ К отчёту за день", callback_data=f"act:report:{target.isoformat()}",
    )])
    await _edit_or_answer(
        callback.message,
        "✏️ <b>Выбери активность для редактирования:</b>",
        InlineKeyboardMarkup(inline_keyboard=rows),
    )


@router.callback_query(F.data.startswith("act:report:"))
async def return_to_activity_report(callback: CallbackQuery) -> None:
    await callback.answer()
    target = _safe_date(callback.data.rsplit(":", 1)[-1])
    text, keyboard = format_activity_overview(str(callback.from_user.id), target)
    await _edit_or_answer(callback.message, text, keyboard)


@router.message(F.text == "📅 Календарь активности")
async def open_activity_calendar(message: Message) -> None:
    today = date.today()
    await show_calendar_back_button(message)
    await message.answer(
        "📆 Выбери день, чтобы посмотреть или изменить активность:",
        reply_markup=build_activity_calendar_keyboard(
            str(message.from_user.id), today.year, today.month,
        ),
    )


@router.callback_query(F.data.startswith("actcal_nav:"))
async def navigate_activity_calendar(callback: CallbackQuery) -> None:
    await callback.answer()
    year, month = map(int, callback.data.split(":", 1)[1].split("-"))
    await _edit_or_answer(
        callback.message,
        "📆 Выбери день, чтобы посмотреть или изменить активность:",
        build_activity_calendar_keyboard(str(callback.from_user.id), year, month),
    )


@router.callback_query(F.data.startswith("actcal_back:"))
async def back_to_activity_calendar(callback: CallbackQuery) -> None:
    await callback.answer()
    year, month = map(int, callback.data.split(":", 1)[1].split("-"))
    await _edit_or_answer(
        callback.message,
        "📆 Выбери день, чтобы посмотреть или изменить активность:",
        build_activity_calendar_keyboard(str(callback.from_user.id), year, month),
    )


@router.callback_query(F.data.startswith("actcal_day:"))
async def open_activity_calendar_day(callback: CallbackQuery) -> None:
    await callback.answer()
    target = _safe_date(callback.data.split(":", 1)[1])
    text, keyboard = format_activity_overview(
        str(callback.from_user.id), target, from_calendar=True,
    )
    await _edit_or_answer(callback.message, text, keyboard)


@router.callback_query(F.data == "act:noop")
async def noop(callback: CallbackQuery) -> None:
    await callback.answer()


def _timed_categories_keyboard() -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(
        text=f"{category.icon} {category.name}", callback_data=f"act:tcat:{category.code}:0"
    )] for category in TIMED_CATEGORIES]
    rows.insert(0, [InlineKeyboardButton(text="🔎 Поиск", callback_data="act:tsearch")])
    rows.append([InlineKeyboardButton(text="⬅️ В раздел активности", callback_data="act:main")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


@router.message(F.text == "⏱ Активность по времени")
async def open_timed_categories(message: Message, state: FSMContext) -> None:
    await start_timed_activity_flow(message, state, str(message.from_user.id), date.today())


async def start_timed_activity_flow(
    message: Message, state: FSMContext, user_id: str, target_date: date | None = None,
) -> None:
    await state.clear()
    await _hide_section_reply_keyboard(message)
    await state.update_data(entry_date=(target_date or date.today()).isoformat(), activity_user_id=str(user_id))
    try:
        recent_codes = ActivityRepository.get_recent_timed_activity_codes(str(user_id))
    except SQLAlchemyError:
        recent_codes = []
    category_rows = _timed_categories_keyboard().inline_keyboard
    rows = [category_rows[0]]
    for code in recent_codes:
        item = TIMED_ACTIVITY_BY_CODE.get(code)
        if item:
            rows.append([InlineKeyboardButton(text=f"⭐ {item.name}", callback_data=f"act:tpick:{item.code}")])
    rows.extend(category_rows[1:])
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
async def reopen_timed_categories(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    await state.set_state(None)
    await _edit_or_answer(callback.message, "⏱ <b>Активность по времени</b>\n\nВыбери категорию:", _timed_categories_keyboard())


def _timed_search_key(value: str) -> str:
    return " ".join((value or "").casefold().replace("ё", "е").replace("-", " ").split())


def _timed_search_matches(query: str):
    needle = _timed_search_key(query)
    if not needle:
        return []
    matches = []
    for activity in TIMED_ACTIVITIES:
        values = (activity.name, *TIMED_ACTIVITY_SEARCH_ALIASES.get(activity.code, ()))
        if any(needle in _timed_search_key(value) for value in values):
            matches.append(activity)
    return sorted(matches, key=lambda item: _timed_search_key(item.name))


def _timed_search_keyboard(matches, page: int) -> InlineKeyboardMarkup:
    pages = max(math.ceil(len(matches) / PAGE_SIZE), 1)
    page = min(max(page, 0), pages - 1)
    visible = matches[page * PAGE_SIZE:(page + 1) * PAGE_SIZE]
    rows = [[InlineKeyboardButton(
        text=f"{item.emoji} {item.name}".strip(), callback_data=f"act:tpick:{item.code}",
    )] for item in visible]
    if pages > 1:
        nav = []
        if page > 0:
            nav.append(InlineKeyboardButton(text="◀️", callback_data=f"act:tsearch_page:{page - 1}"))
        nav.append(InlineKeyboardButton(text=f"{page + 1}/{pages}", callback_data="act:noop"))
        if page + 1 < pages:
            nav.append(InlineKeyboardButton(text="▶️", callback_data=f"act:tsearch_page:{page + 1}"))
        rows.append(nav)
    rows.append([InlineKeyboardButton(text="⬅️ К категориям", callback_data="act:timed_categories")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


@router.callback_query(F.data == "act:tsearch")
async def start_timed_search(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    await state.set_state(ActivityTrackingStates.searching_timed_activity)
    await _edit_or_answer(
        callback.message,
        "🔎 <b>Поиск активности</b>\n\nВведи название или его часть, например: <i>бокс</i>",
        InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="⬅️ К категориям", callback_data="act:timed_categories"),
        ]]),
    )


@router.message(ActivityTrackingStates.searching_timed_activity)
async def receive_timed_search(message: Message, state: FSMContext) -> None:
    query = (message.text or "").strip()
    matches = _timed_search_matches(query)
    await state.update_data(timed_search_query=query)
    if not matches:
        await message.answer(
            "Ничего не найдено. Введи другое название.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="⬅️ К категориям", callback_data="act:timed_categories"),
            ]]),
        )
        return
    await message.answer(
        f"🔎 Найдено: {len(matches)}. Выбери активность:",
        reply_markup=_timed_search_keyboard(matches, 0),
    )


@router.callback_query(F.data.startswith("act:tsearch_page:"))
async def paginate_timed_search(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    data = await state.get_data()
    matches = _timed_search_matches(str(data.get("timed_search_query") or ""))
    page = int(callback.data.rsplit(":", 1)[-1])
    await _edit_or_answer(
        callback.message,
        f"🔎 Найдено: {len(matches)}. Выбери активность:",
        _timed_search_keyboard(matches, page),
    )


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
    current_state = await state.get_state()
    workout_input_states = {
        ActivityTrackingStates.entering_set_repetitions.state,
        ActivityTrackingStates.entering_set_load.state,
        ActivityTrackingStates.entering_set_duration.state,
        ActivityTrackingStates.entering_set_distance.state,
    }
    if current_state in workout_input_states:
        data = await state.get_data()
        session_id = int(data.get("session_id") or 0)
        await state.clear()
        if session_id:
            ActivityRepository.remove_empty_session_exercises(session_id, str(message.from_user.id))
        await show_workout_draft(message, str(message.from_user.id))
        return
    if current_state == ActivityTrackingStates.entering_timed_duration.state:
        await state.set_state(None)
        await _hide_section_reply_keyboard(message)
        await message.answer(
            "⏱ <b>Активность по времени</b>\n\nВыбери категорию:",
            reply_markup=_timed_categories_keyboard(),
            parse_mode="HTML",
        )
        return
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
        [InlineKeyboardButton(text="⬅️ К отчёту за день", callback_data=f"act:report:{target_date.isoformat()}")],
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
        [InlineKeyboardButton(text="⬅️ К отчёту за день", callback_data=f"act:report:{entry.entry_date.isoformat()}")],
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
    rows = [[InlineKeyboardButton(text="🔎 Поиск упражнения", callback_data="act:esearch")]]
    rows.extend([[InlineKeyboardButton(
        text=f"{category.icon} {category.name}",
        callback_data=f"act:ecat:{mode}:{category.code}:0",
    )] for category in EXERCISE_CATEGORIES])
    rows.append([InlineKeyboardButton(text="⬅️ К тренировке", callback_data="act:workout_draft")])
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


def _exercise_search_matches(query: str):
    normalized = _timed_search_key(query)
    if not normalized:
        return []
    return sorted(
        (item for item in EXERCISES if normalized in _timed_search_key(item.name)),
        key=lambda item: (_timed_search_key(item.name).find(normalized), _timed_search_key(item.name)),
    )


def _exercise_search_keyboard(query: str, page: int) -> InlineKeyboardMarkup:
    matches = _exercise_search_matches(query)
    pages = max(math.ceil(len(matches) / PAGE_SIZE), 1)
    page = min(max(page, 0), pages - 1)
    visible = matches[page * PAGE_SIZE:(page + 1) * PAGE_SIZE]
    rows = [[InlineKeyboardButton(text=item.name, callback_data=f"act:epick:workout:{item.code}")] for item in visible]
    if matches:
        nav = []
        if page > 0:
            nav.append(InlineKeyboardButton(text="◀️", callback_data=f"act:esearch_page:{page - 1}"))
        nav.append(InlineKeyboardButton(text=f"{page + 1}/{pages}", callback_data="act:noop"))
        if page + 1 < pages:
            nav.append(InlineKeyboardButton(text="▶️", callback_data=f"act:esearch_page:{page + 1}"))
        rows.append(nav)
    rows.append([InlineKeyboardButton(text="🔎 Новый поиск", callback_data="act:esearch")])
    rows.append([InlineKeyboardButton(text="⬅️ Категории", callback_data="act:ecategories:workout")])
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


def _workout_draft_text(session, user_id: str) -> str:
    exercises, grouped, _ = _sets_by_exercise(session.id, user_id)
    lines = [
        "🏋️ <b>Тренировка</b>",
        f"📅 {session.entry_date.strftime('%d.%m.%Y')}",
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


def _workout_draft_keyboard(session, user_id: str) -> InlineKeyboardMarkup:
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
    rows.extend([
        [InlineKeyboardButton(text="✅ Завершить тренировку", callback_data=f"act:finish_prompt:{session.id}")],
        [InlineKeyboardButton(text="✖️ Отменить", callback_data=f"act:cancel_confirm:{session.id}")],
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def show_workout_draft(message: Message, user_id: str, prefix: str | None = None) -> None:
    await _hide_section_reply_keyboard(message)
    session = ActivityRepository.get_workout_draft(user_id)
    if session is None:
        await message.answer("Черновик тренировки не найден.")
        return
    ActivityRepository.remove_empty_session_exercises(session.id, user_id)
    session = ActivityRepository.get_workout_draft(user_id)
    text = _workout_draft_text(session, user_id)
    if prefix:
        text = f"{prefix}\n\n{text}"
    await message.answer(text, reply_markup=_workout_draft_keyboard(session, user_id), parse_mode="HTML")


@router.message(F.text == "🏋️ Тренировка")
async def start_workout(message: Message, state: FSMContext) -> None:
    await state.clear()
    await _hide_section_reply_keyboard(message)
    user_id = str(message.from_user.id)
    existing = ActivityRepository.get_workout_draft(user_id)
    if existing is not None:
        await show_workout_draft(message, user_id, prefix="Продолжаем незавершённую тренировку.")
        return
    weight, weight_source = _weight_snapshot(user_id)
    try:
        ActivityRepository.create_workout_session(
            user_id=user_id, entry_date=date.today(), weight_kg=weight,
            weight_source=weight_source,
        )
    except WorkoutDraftExistsError:
        await show_workout_draft(message, user_id)
        return
    await state.update_data(exercise_mode="workout")
    await message.answer(
        "🏋️ <b>Тренировка</b>\n\nВыбери категорию упражнения:",
        reply_markup=_exercise_categories_keyboard("workout"), parse_mode="HTML",
    )


@router.callback_query(F.data == "act:workout_draft")
async def reopen_workout_draft(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    await state.clear()
    session = ActivityRepository.get_workout_draft(str(callback.from_user.id))
    if session is None:
        await callback.answer("Черновик тренировки не найден", show_alert=True)
        return
    ActivityRepository.remove_empty_session_exercises(session.id, str(callback.from_user.id))
    session = ActivityRepository.get_workout_draft(str(callback.from_user.id))
    await _edit_or_answer(
        callback.message,
        _workout_draft_text(session, str(callback.from_user.id)),
        _workout_draft_keyboard(session, str(callback.from_user.id)),
    )


@router.callback_query(F.data.startswith("act:repeat_set:"))
async def repeat_last_set(callback: CallbackQuery) -> None:
    await callback.answer()
    session_id = int(callback.data.rsplit(":", 1)[-1])
    repeated = ActivityRepository.repeat_last_set(session_id, str(callback.from_user.id))
    if repeated is None:
        await callback.answer("Сначала добавь подход", show_alert=True)
        return
    session = ActivityRepository.get_workout_draft(str(callback.from_user.id))
    await _edit_or_answer(
        callback.message, "✅ Подход повторён\n\n" + _workout_draft_text(session, str(callback.from_user.id)),
        _workout_draft_keyboard(session, str(callback.from_user.id)),
    )


@router.callback_query(F.data == "act:ecategories:workout")
async def open_exercise_categories(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    await state.set_state(None)
    await state.update_data(exercise_mode="workout")
    await _edit_or_answer(callback.message, "🏋️ <b>Добавить упражнение</b>\n\nВыбери категорию:", _exercise_categories_keyboard("workout"))


@router.callback_query(F.data.startswith("act:ecategories:"))
async def open_exercise_categories_for_mode(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    mode = callback.data.rsplit(":", 1)[-1]
    await state.set_state(None)
    await state.update_data(exercise_mode=mode)
    await _edit_or_answer(callback.message, "Выбери категорию упражнения:", _exercise_categories_keyboard(mode))


@router.callback_query(F.data == "act:esearch")
async def start_exercise_search(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    await state.update_data(exercise_mode="workout", exercise_search_query=None)
    await state.set_state(ActivityTrackingStates.searching_exercise)
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⬅️ Категории", callback_data="act:ecategories:workout")],
    ])
    await _edit_or_answer(
        callback.message,
        "🔎 <b>Поиск упражнения</b>\n\nВведи название, например: <i>отжимания</i> или <i>жим штанги</i>.",
        keyboard,
    )


@router.message(ActivityTrackingStates.searching_exercise)
async def receive_exercise_search(message: Message, state: FSMContext) -> None:
    query = (message.text or "").strip()
    if len(_timed_search_key(query)) < 2:
        await message.answer("Введи хотя бы две буквы названия упражнения.")
        return
    await state.update_data(exercise_search_query=query)
    matches = _exercise_search_matches(query)
    if not matches:
        await message.answer(
            f"По запросу «{html.escape(query)}» ничего не найдено.",
            reply_markup=_exercise_search_keyboard(query, 0),
            parse_mode="HTML",
        )
        return
    await message.answer(
        f"🔎 <b>Результаты поиска:</b> {html.escape(query)}",
        reply_markup=_exercise_search_keyboard(query, 0),
        parse_mode="HTML",
    )


@router.callback_query(F.data.startswith("act:esearch_page:"))
async def paginate_exercise_search(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    data = await state.get_data()
    query = str(data.get("exercise_search_query") or "")
    if not query:
        await callback.answer("Повтори поиск", show_alert=True)
        return
    page = int(callback.data.rsplit(":", 1)[-1])
    await _edit_or_answer(
        callback.message,
        f"🔎 <b>Результаты поиска:</b> {html.escape(query)}",
        _exercise_search_keyboard(query, page),
    )


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
        return "Введи вес одного снаряда (одной гантели или гири) в килограммах:"
    if config.load_input_mode == "assistance":
        return "Введи вес помощи тренажёра в килограммах:"
    if config.category_code in {"machines", "cables"}:
        return "Введи выбранный вес тренажёра или стека в килограммах:"
    if config.category_code == "free_weights":
        return "Введи общий рабочий вес в килограммах, включая гриф:"
    return "Введи рабочий вес снаряда в килограммах:"


async def _start_regular_set_input(message: Message, state: FSMContext, session, config) -> None:
    exercise = ActivityRepository.add_session_exercise(
        session_id=session.id, user_id=str(session.user_id), exercise_code=config.code,
        exercise_name=config.name, measurement_type=config.measurement_type,
        load_input_mode=config.load_input_mode, tempo_seconds_per_rep=config.tempo_seconds_per_rep,
    )
    await _prompt_regular_set_input(message, state, session, exercise.id, config)


async def _prompt_regular_set_input(
    message: Message,
    state: FSMContext,
    session,
    exercise_id: int,
    config,
    previous_set=None,
) -> None:
    await state.update_data(
        session_id=session.id, session_exercise_id=exercise_id, exercise_code=config.code,
        pending_measurement=config.measurement_type, exercise_mode="workout",
        load_kg=None, load_kind=None, functional_input=None,
    )
    if config.load_input_mode == "optional":
        await _prompt_optional_load(message, config)
    elif config.measurement_type in {"repetitions_load", "load_duration_distance"}:
        if previous_set is not None and previous_set.load_kg is not None:
            await state.update_data(load_kg=previous_set.load_kg, load_kind=previous_set.load_kind or "working")
            label = _format_number(previous_set.load_kg)
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text=f"Оставить {label} кг", callback_data="act:set_load_choice:reuse")],
                [InlineKeyboardButton(text="⚖️ Изменить вес", callback_data="act:set_load_choice:change")],
                [InlineKeyboardButton(text="⬅️ К тренировке", callback_data="act:workout_draft")],
            ])
            await message.answer(
                f"🏋️ <b>{config.name}</b>\n\nПредыдущий рабочий вес — {label} кг.",
                reply_markup=keyboard,
                parse_mode="HTML",
            )
            return
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
    session = ActivityRepository.get_workout_draft(user_id)
    if session is None:
        await callback.answer("Черновик тренировки не найден", show_alert=True)
        return
    exercise = _exercise_for_set(session.id, exercise_id, user_id)
    config = EXERCISE_BY_CODE.get(exercise.exercise_code) if exercise else None
    if exercise is None or config is None:
        await callback.answer("Упражнение не найдено", show_alert=True)
        return
    exercise_sets = [
        item for item in ActivityRepository.get_session_sets(session.id, user_id)
        if item.session_exercise_id == exercise.id
    ]
    previous_set = exercise_sets[-1] if exercise_sets else None
    await _prompt_regular_set_input(callback.message, state, session, exercise.id, config, previous_set)


async def _continue_set_input_after_load(message: Message, state: FSMContext, pending_measurement: str | None) -> None:
    if pending_measurement == "load_duration_distance":
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="⏱ По времени", callback_data="act:functional_input:time")],
            [InlineKeyboardButton(text="📏 По расстоянию", callback_data="act:functional_input:distance")],
            [InlineKeyboardButton(text="⬅️ К тренировке", callback_data="act:workout_draft")],
        ])
        await message.answer("Как записать этот подход?", reply_markup=keyboard)
        return
    await state.set_state(ActivityTrackingStates.entering_set_repetitions)
    await message.answer(
        "Сколько повторений в подходе?",
        reply_markup=_number_keyboard(("5", "8", "10", "12", "15", "20")),
    )


@router.callback_query(F.data.startswith("act:set_load_choice:"))
async def choose_set_load(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    choice = callback.data.rsplit(":", 1)[-1]
    data = await state.get_data()
    config = EXERCISE_BY_CODE.get(str(data.get("exercise_code") or ""))
    if config is None:
        await callback.answer("Упражнение не найдено", show_alert=True)
        return
    if choice == "change":
        await state.update_data(load_kg=None, load_kind="working")
        await state.set_state(ActivityTrackingStates.entering_set_load)
        await callback.message.answer(
            _load_prompt(config),
            reply_markup=_number_keyboard(("5", "10", "15", "20", "30", "40", "50", "60")),
        )
        return
    await _continue_set_input_after_load(callback.message, state, data.get("pending_measurement"))


async def _prompt_optional_load(message: Message, config) -> None:
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Без дополнительного веса", callback_data="act:optional_load:none")],
        [InlineKeyboardButton(text="➕ Дополнительный вес", callback_data="act:optional_load:additional")],
        [InlineKeyboardButton(text="⚙️ Помощь тренажёра", callback_data="act:optional_load:assistance")],
        [InlineKeyboardButton(text="⬅️ К тренировке", callback_data="act:workout_draft")],
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
        session = ActivityRepository.get_workout_draft(str(callback.from_user.id))
        if session is None:
            await callback.answer("Сначала начни тренировку", show_alert=True)
            return
        await _start_regular_set_input(callback.message, state, session, config)
        return
    await callback.answer("Этот сценарий больше не используется", show_alert=True)


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
    await _continue_set_input_after_load(message, state, data.get("pending_measurement"))


@router.callback_query(F.data.startswith("act:functional_input:"))
async def choose_functional_input(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    method = callback.data.rsplit(":", 1)[-1]
    data = await state.get_data()
    await state.update_data(functional_input=method)
    if method == "distance":
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
    await show_workout_draft(message, str(message.from_user.id), prefix="✅ Подход добавлен")


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
    await show_workout_draft(message, str(message.from_user.id), prefix="✅ Подход добавлен")


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
    await show_workout_draft(message, str(message.from_user.id), prefix="✅ Подход добавлен")


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
    user_id = str(callback.from_user.id)
    session = ActivityRepository.get_workout_session(session_id, user_id)
    if session is not None:
        ActivityRepository.remove_empty_session_exercises(session.id, user_id)
    sets = ActivityRepository.get_session_sets(session_id, user_id)
    if session is None or session.status != "draft":
        await callback.answer("Тренировка не найдена", show_alert=True)
        return
    if not sets:
        await callback.answer("Добавь хотя бы один подход", show_alert=True)
        return
    try:
        estimated_seconds = estimate_workout_duration_seconds(sets)
    except ActivityValidationError as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    await _edit_or_answer(
        callback.message,
        f"🏋️ Данные тренировки заполнены\n"
        f"⏱ Оценочное время: {_format_duration(estimated_seconds)}\n\n"
        f"<b>Как прошла тренировка?</b>",
        _workout_intensity_keyboard(session.id),
    )


@router.callback_query(F.data.startswith("act:wint:"))
async def save_workout_intensity(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    _, _, session_id_raw, intensity = callback.data.split(":")
    user_id = str(callback.from_user.id)
    session = ActivityRepository.get_workout_session(int(session_id_raw), user_id)
    if session is None or session.status != "draft":
        await callback.answer("Тренировка не найдена", show_alert=True)
        return
    try:
        sets = ActivityRepository.get_session_sets(session.id, user_id)
        estimated_seconds = estimate_workout_duration_seconds(sets)
        energy = calculate_workout_energy(
            intensity=intensity, weight_kg=session.weight_kg_snapshot,
            duration_seconds=estimated_seconds,
        )
    except ActivityValidationError as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    completed = ActivityRepository.finish_workout(
        session_id=session.id, user_id=user_id, intensity=intensity,
        duration_seconds=estimated_seconds,
        met_value=energy.met_value, gross_calories=energy.gross_calories,
        credited_calories=energy.credited_calories,
    )
    if completed is None:
        await callback.answer("Тренировка уже сохранена", show_alert=True)
        return
    await state.clear()
    note = "\nВремя тренировки оценено автоматически, поэтому калорийность приблизительная."
    text, keyboard = format_activity_overview(user_id, completed.entry_date)
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
        [InlineKeyboardButton(text="Нет, вернуться", callback_data="act:workout_draft")],
    ])
    await _edit_or_answer(callback.message, "Отменить тренировку и удалить все добавленные подходы?", keyboard)


@router.callback_query(F.data.startswith("act:cancel:"))
async def cancel_workout(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    session_id = int(callback.data.rsplit(":", 1)[-1])
    session = ActivityRepository.get_workout_session(session_id, str(callback.from_user.id))
    ActivityRepository.cancel_workout(session_id, str(callback.from_user.id))
    await state.clear()
    target_date = session.entry_date if session else date.today()
    text, keyboard = format_activity_overview(str(callback.from_user.id), target_date)
    await _edit_or_answer(callback.message, f"✅ Тренировка отменена\n\n{text}", keyboard)


def _workout_detail_text(session, user_id: str) -> str:
    exercises, grouped, _ = _sets_by_exercise(session.id, user_id)
    lines = [
        "🏋️ <b>Силовая тренировка</b>",
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
        [InlineKeyboardButton(text="🔥 Изменить интенсивность", callback_data=f"act:wedit_int_menu:{session.id}")],
        [InlineKeyboardButton(text="🗑 Удалить тренировку", callback_data=f"act:wdelete_confirm:{session.id}")],
        [InlineKeyboardButton(text="⬅️ К отчёту за день", callback_data=f"act:report:{session.entry_date.isoformat()}")],
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _recalculate_completed_workout(session, user_id: str):
    """Обновляет оценочное время и калории после правки состава тренировки."""
    if session is None or session.status != "completed" or not session.intensity:
        return session
    sets = ActivityRepository.get_session_sets(session.id, user_id)
    if not sets:
        return session
    duration_seconds = estimate_workout_duration_seconds(sets)
    energy = calculate_workout_energy(
        intensity=session.intensity,
        weight_kg=session.weight_kg_snapshot,
        duration_seconds=duration_seconds,
    )
    ActivityRepository.update_completed_workout(
        session_id=session.id,
        user_id=user_id,
        duration_seconds=duration_seconds,
        met_value=energy.met_value,
        gross_calories=energy.gross_calories,
        credited_calories=energy.credited_calories,
    )
    return ActivityRepository.get_workout_session(session.id, user_id)


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
    back_callback = "act:workout_draft" if session and session.status == "draft" else f"act:workout_detail:{item.session_id}"
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
    if session.status == "draft":
        await message.answer(
            "✅ Подход обновлён\n\n" + _workout_draft_text(session, str(message.from_user.id)),
            reply_markup=_workout_draft_keyboard(session, str(message.from_user.id)), parse_mode="HTML",
        )
    else:
        session = _recalculate_completed_workout(session, str(message.from_user.id))
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
    if session.status == "draft":
        await message.answer(
            "✅ Подход обновлён\n\n" + _workout_draft_text(session, str(message.from_user.id)),
            reply_markup=_workout_draft_keyboard(session, str(message.from_user.id)), parse_mode="HTML",
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
    session = ActivityRepository.get_workout_session(item.session_id, str(callback.from_user.id))
    session_sets = ActivityRepository.get_session_sets(item.session_id, str(callback.from_user.id))
    if session and session.status == "completed" and len(session_sets) <= 1:
        await callback.answer(
            "Нельзя удалить единственный подход. Удали тренировку целиком.",
            show_alert=True,
        )
        return
    ActivityRepository.delete_workout_set(item.id, str(callback.from_user.id))
    session = ActivityRepository.get_workout_session(item.session_id, str(callback.from_user.id))
    if session.status == "draft":
        await _edit_or_answer(
            callback.message, "✅ Подход удалён\n\n" + _workout_draft_text(session, str(callback.from_user.id)),
            _workout_draft_keyboard(session, str(callback.from_user.id)),
        )
    else:
        session = _recalculate_completed_workout(session, str(callback.from_user.id))
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
