"""Клавиатуры для добавок."""
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton
from utils.keyboards import main_menu_button


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
    """Меню редактирования времени."""
    buttons: list[list[KeyboardButton]] = []
    for t in times:
        buttons.append([KeyboardButton(text=f"❌ {t}")])
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
            [KeyboardButton(text="⏭️ Пропустить")],
            [KeyboardButton(text="⬅️ Назад"), KeyboardButton(text="❌ Отменить")],
        ],
        resize_keyboard=True,
    )
