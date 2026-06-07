"""Клавиатуры для добавок."""
from aiogram.types import (
    ReplyKeyboardMarkup,
    KeyboardButton,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from utils.keyboards import main_menu_button


SUPPLEMENT_CREATE_TIME_PREFIX = "sup_create_time"


def supplement_test_time_inline_menu(times: list[str]) -> InlineKeyboardMarkup:
    """Inline-меню выбора времени при создании добавки."""
    selected = set(times or [])
    rows: list[list[InlineKeyboardButton]] = []
    hours = [f"{hour:02d}:00" for hour in range(6, 24)]

    for index in range(0, len(hours), 3):
        row = []
        for time_text in hours[index:index + 3]:
            prefix = "✅ " if time_text in selected else ""
            row.append(
                InlineKeyboardButton(
                    text=f"{prefix}{time_text}",
                    callback_data=f"{SUPPLEMENT_CREATE_TIME_PREFIX}:add:{time_text}",
                )
            )
        rows.append(row)

    if times:
        rows.append([
            InlineKeyboardButton(
                text="💾 Сохранить время",
                callback_data=f"{SUPPLEMENT_CREATE_TIME_PREFIX}:save",
            )
        ])
    else:
        rows.append([
            InlineKeyboardButton(
                text="⏭️ Пропустить",
                callback_data=f"{SUPPLEMENT_CREATE_TIME_PREFIX}:skip",
            )
        ])

    rows.append([
        InlineKeyboardButton(
            text="⬅️ Назад",
            callback_data=f"{SUPPLEMENT_CREATE_TIME_PREFIX}:back",
        ),
        InlineKeyboardButton(
            text="❌ Отменить",
            callback_data=f"{SUPPLEMENT_CREATE_TIME_PREFIX}:cancel",
        ),
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def supplement_creation_cancel_menu() -> ReplyKeyboardMarkup:
    """Меню процесса создания добавки на шаге ввода названия."""
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="❌ Отменить")]],
        resize_keyboard=True,
    )


def supplements_main_menu(has_items: bool = False) -> ReplyKeyboardMarkup:
    """Главное меню добавок."""
    buttons = [[KeyboardButton(text="➕ Создать добавку")]]
    if has_items:
        buttons.append([KeyboardButton(text="📋 Мои добавки"), KeyboardButton(text="📅 Календарь добавок")])
        buttons.append([KeyboardButton(text="✅ Отметить приём")])
    buttons.append([main_menu_button])
    return ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)


def supplements_choice_menu(supplements: list[dict]) -> ReplyKeyboardMarkup:
    """Меню выбора добавки."""
    rows = [[KeyboardButton(text=item["name"])] for item in supplements]
    rows.append([KeyboardButton(text="⬅️ Назад"), main_menu_button])
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True)


def supplements_view_menu(supplements: list[dict]) -> ReplyKeyboardMarkup:
    """Меню просмотра добавок."""
    rows = [[KeyboardButton(text=item["name"])] for item in supplements]
    rows.append([KeyboardButton(text="⬅️ Назад"), main_menu_button])
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True)


def supplement_details_menu() -> ReplyKeyboardMarkup:
    """Меню деталей добавки."""
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="✏️ Редактировать добавку")],
            [KeyboardButton(text="🗑 Удалить добавку"), KeyboardButton(text="✅ Отметить добавку")],
            [KeyboardButton(text="⬅️ Назад"), main_menu_button],
        ],
        resize_keyboard=True,
    )


def supplement_edit_menu(show_save: bool = False) -> ReplyKeyboardMarkup:
    """Меню редактирования добавки."""
    buttons = [
        [KeyboardButton(text="✏️ Редактировать время"), KeyboardButton(text="📅 Редактировать дни")],
        [KeyboardButton(text="⏳ Длительность приема"), KeyboardButton(text="✏️ Изменить название")],
        [KeyboardButton(text="🔔 Уведомления")],
    ]
    if show_save:
        buttons.append([KeyboardButton(text="💾 Сохранить")])
    buttons.append([KeyboardButton(text="❌ Отменить")])
    return ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)


def time_edit_menu(times: list[str]) -> ReplyKeyboardMarkup:
    """Меню редактирования времени с готовыми вариантами времени."""
    buttons: list[list[KeyboardButton]] = []
    selected_times = set(times or [])

    for t in times:
        buttons.append([KeyboardButton(text=f"❌ {t}")])

    ready_times = [f"{hour:02d}:00" for hour in range(6, 24)]
    available_ready_times = [
        time_text for time_text in ready_times
        if time_text not in selected_times
    ]
    for index in range(0, len(available_ready_times), 3):
        buttons.append([
            KeyboardButton(text=time_text)
            for time_text in available_ready_times[index:index + 3]
        ])

    buttons.append([KeyboardButton(text="💾 Сохранить")])
    buttons.append([KeyboardButton(text="⬅️ Назад")])
    return ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)


def days_menu(selected: list[str], show_cancel: bool = False) -> ReplyKeyboardMarkup:
    """Меню выбора дней."""
    week_days = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
    rows = []
    for day in week_days:
        prefix = "✅ " if day in selected else ""
        rows.append([KeyboardButton(text=f"{prefix}{day}")])
    rows.append([KeyboardButton(text="Выбрать все"), KeyboardButton(text="💾 Сохранить")])
    if show_cancel:
        rows.append([KeyboardButton(text="⬅️ Назад"), KeyboardButton(text="❌ Отменить")])
    else:
        rows.append([KeyboardButton(text="⬅️ Назад")])
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True)


def duration_menu() -> ReplyKeyboardMarkup:
    """Меню выбора длительности."""
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="Постоянно"), KeyboardButton(text="14 дней")],
            [KeyboardButton(text="30 дней")],
            [KeyboardButton(text="⏭️ Пропустить")],
            [KeyboardButton(text="⬅️ Назад"), KeyboardButton(text="❌ Отменить")],
        ],
        resize_keyboard=True,
    )


def time_first_menu() -> ReplyKeyboardMarkup:
    """Меню для первого времени."""
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="💾 Сохранить"), KeyboardButton(text="⬅️ Назад")]],
        resize_keyboard=True,
    )


def supplement_test_skip_menu(show_back: bool = False) -> ReplyKeyboardMarkup:
    """Меню для пропуска шага в тесте добавки."""
    buttons = [[KeyboardButton(text="⏭️ Пропустить")]]
    if show_back:
        buttons.append([KeyboardButton(text="⬅️ Назад"), KeyboardButton(text="❌ Отменить")])
    else:
        buttons.append([KeyboardButton(text="❌ Отменить")])
    return ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)


def supplement_test_time_menu(times: list[str], show_back: bool = False) -> ReplyKeyboardMarkup:
    """Меню для шага времени в тесте добавки. Показывает 'Сохранить' если есть времена, иначе 'Пропустить'."""
    buttons = []
    if times and len(times) > 0:
        buttons.append([KeyboardButton(text="💾 Сохранить")])
    else:
        buttons.append([KeyboardButton(text="⏭️ Пропустить")])
    
    if show_back:
        buttons.append([KeyboardButton(text="⬅️ Назад"), KeyboardButton(text="❌ Отменить")])
    else:
        buttons.append([KeyboardButton(text="❌ Отменить")])
    return ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)


def supplement_test_notifications_menu() -> ReplyKeyboardMarkup:
    """Меню выбора уведомлений в тесте."""
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="✅ Включить"), KeyboardButton(text="❌ Выключить")],
            [KeyboardButton(text="⏭️ Пропустить")],
            [KeyboardButton(text="⬅️ Назад"), KeyboardButton(text="❌ Отменить")],
        ],
        resize_keyboard=True,
    )


def supplement_history_time_menu() -> ReplyKeyboardMarkup:
    """Меню для ввода времени приёма в истории."""
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📅 Выбрать дату")],
            [KeyboardButton(text="⏭️ Пропустить")],
            [KeyboardButton(text="⬅️ Назад"), KeyboardButton(text="❌ Отменить")],
        ],
        resize_keyboard=True,
    )
