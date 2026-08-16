"""Клавиатуры для добавок."""
from aiogram.types import (
    ReplyKeyboardMarkup,
    KeyboardButton,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from utils.keyboards import main_menu_button
from utils.supplement_catalog import SUPPLEMENT_CATEGORIES, SupplementCategory


SUPPLEMENT_CREATE_TIME_PREFIX = "sup_create_time"
SUPPLEMENT_EDIT_TIME_PREFIX = "sup_edit_time"
SUPPLEMENT_CATALOG_PREFIX = "sup_catalog"
SUPPLEMENT_NOTIFICATIONS_PREFIX = "sup_notifications"
SUPPLEMENT_CREATE_DAYS_PREFIX = "sup_create_days"
SUPPLEMENT_CREATE_DURATION_PREFIX = "sup_create_duration"

SUPPLEMENT_WEEK_DAYS = (
    ("mon", "Пн"),
    ("tue", "Вт"),
    ("wed", "Ср"),
    ("thu", "Чт"),
    ("fri", "Пт"),
    ("sat", "Сб"),
    ("sun", "Вс"),
)

SUPPLEMENT_DURATION_OPTIONS = (
    ("permanent", "Постоянно", "постоянно"),
    ("14_days", "14 дней", "14 дней"),
    ("30_days", "30 дней", "30 дней"),
)


def supplement_catalog_categories_inline_menu(*, rename: bool = False) -> InlineKeyboardMarkup:
    """Inline menu of supplement categories backed by stable identifiers."""
    rows: list[list[InlineKeyboardButton]] = []
    for index in range(0, len(SUPPLEMENT_CATEGORIES), 2):
        rows.append([
            InlineKeyboardButton(
                text=category.display_name,
                callback_data=f"{SUPPLEMENT_CATALOG_PREFIX}:category:{category.identifier}",
            )
            for category in SUPPLEMENT_CATEGORIES[index:index + 2]
        ])
    rows.append([
        InlineKeyboardButton(
            text="⬅️ Назад" if rename else "❌ Отменить",
            callback_data=f"{SUPPLEMENT_CATALOG_PREFIX}:{'back_to_edit' if rename else 'cancel'}",
        )
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def supplement_catalog_items_inline_menu(category: SupplementCategory) -> InlineKeyboardMarkup:
    """Inline menu of catalog items for one category."""
    rows: list[list[InlineKeyboardButton]] = []
    for index in range(0, len(category.items), 2):
        rows.append([
            InlineKeyboardButton(
                text=item.display_name,
                callback_data=f"{SUPPLEMENT_CATALOG_PREFIX}:item:{item.identifier}",
            )
            for item in category.items[index:index + 2]
        ])
    rows.append([
        InlineKeyboardButton(
            text="⬅️ К категориям",
            callback_data=f"{SUPPLEMENT_CATALOG_PREFIX}:categories",
        )
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


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


def supplement_edit_time_inline_menu(times: list[str]) -> InlineKeyboardMarkup:
    """Inline-меню выбора времени при редактировании добавки."""
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
                    callback_data=f"{SUPPLEMENT_EDIT_TIME_PREFIX}:toggle:{time_text}",
                )
            )
        rows.append(row)

    rows.append([
        InlineKeyboardButton(
            text="💾 Сохранить время",
            callback_data=f"{SUPPLEMENT_EDIT_TIME_PREFIX}:save",
        )
    ])
    rows.append([
        InlineKeyboardButton(
            text="⬅️ Назад",
            callback_data=f"{SUPPLEMENT_EDIT_TIME_PREFIX}:back",
        )
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


def supplement_delete_confirm_menu() -> ReplyKeyboardMarkup:
    """Меню подтверждения удаления добавки."""
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="✅ Да, удалить добавку")],
            [KeyboardButton(text="❌ Отменить удаление")],
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
        [KeyboardButton(text="⬅️ Назад")],
    ]
    return ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)


def time_edit_menu(times: list[str]) -> ReplyKeyboardMarkup:
    """Меню редактирования времени."""
    buttons: list[list[KeyboardButton]] = []
    for t in times:
        buttons.append([KeyboardButton(text=f"❌ {t}")])
    buttons.append([KeyboardButton(text="💾 Сохранить")])
    buttons.append([KeyboardButton(text="⬅️ Назад")])
    return ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)


def days_menu(
    selected: list[str],
    show_cancel: bool = False,
    show_skip: bool = False,
) -> ReplyKeyboardMarkup:
    """Меню выбора дней."""
    rows = []
    for _, day in SUPPLEMENT_WEEK_DAYS:
        prefix = "✅ " if day in selected else ""
        rows.append([KeyboardButton(text=f"{prefix}{day}")])
    rows.append([KeyboardButton(text="Выбрать все"), KeyboardButton(text="💾 Сохранить")])
    if show_skip:
        rows.append([KeyboardButton(text="⏭️ Пропустить")])
    if show_cancel:
        rows.append([KeyboardButton(text="⬅️ Назад"), KeyboardButton(text="❌ Отменить")])
    else:
        rows.append([KeyboardButton(text="⬅️ Назад")])
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True)


def supplement_create_days_inline_menu(selected: list[str]) -> InlineKeyboardMarkup:
    """Inline-меню выбора дней при создании добавки."""
    selected_days = set(selected or [])
    day_buttons = [
        InlineKeyboardButton(
            text=f"{'✅ ' if label in selected_days else ''}{label}",
            callback_data=f"{SUPPLEMENT_CREATE_DAYS_PREFIX}:toggle:{identifier}",
        )
        for identifier, label in SUPPLEMENT_WEEK_DAYS
    ]
    rows = [day_buttons[:3], day_buttons[3:6], day_buttons[6:]]
    rows.extend([
        [
            InlineKeyboardButton(
                text="Выбрать все",
                callback_data=f"{SUPPLEMENT_CREATE_DAYS_PREFIX}:all",
            ),
            InlineKeyboardButton(
                text="💾 Сохранить",
                callback_data=f"{SUPPLEMENT_CREATE_DAYS_PREFIX}:save",
            ),
        ],
        [
            InlineKeyboardButton(
                text="⏭️ Пропустить",
                callback_data=f"{SUPPLEMENT_CREATE_DAYS_PREFIX}:skip",
            )
        ],
        [
            InlineKeyboardButton(
                text="⬅️ Назад",
                callback_data=f"{SUPPLEMENT_CREATE_DAYS_PREFIX}:back",
            ),
            InlineKeyboardButton(
                text="❌ Отменить",
                callback_data=f"{SUPPLEMENT_CREATE_DAYS_PREFIX}:cancel",
            ),
        ],
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def supplement_create_duration_inline_menu() -> InlineKeyboardMarkup:
    """Inline-меню длительности при создании добавки."""
    duration_buttons = [
        InlineKeyboardButton(
            text=label,
            callback_data=f"{SUPPLEMENT_CREATE_DURATION_PREFIX}:set:{identifier}",
        )
        for identifier, label, _ in SUPPLEMENT_DURATION_OPTIONS
    ]
    return InlineKeyboardMarkup(
        inline_keyboard=[
            duration_buttons[:2],
            duration_buttons[2:],
            [
                InlineKeyboardButton(
                    text="⏭️ Пропустить",
                    callback_data=f"{SUPPLEMENT_CREATE_DURATION_PREFIX}:skip",
                )
            ],
            [
                InlineKeyboardButton(
                    text="⬅️ Назад",
                    callback_data=f"{SUPPLEMENT_CREATE_DURATION_PREFIX}:back",
                ),
                InlineKeyboardButton(
                    text="❌ Отменить",
                    callback_data=f"{SUPPLEMENT_CREATE_DURATION_PREFIX}:cancel",
                ),
            ],
        ]
    )


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


def supplement_notifications_inline_menu(
    *,
    creation: bool,
    notifications_enabled: bool = False,
) -> InlineKeyboardMarkup:
    """Inline-меню настройки уведомлений при создании и редактировании."""
    rows: list[list[InlineKeyboardButton]] = []

    if creation or not notifications_enabled:
        rows.append([
            InlineKeyboardButton(
                text="✅ Включить",
                callback_data=f"{SUPPLEMENT_NOTIFICATIONS_PREFIX}:enable",
            )
        ])
    else:
        rows.append([
            InlineKeyboardButton(
                text="❌ Выключить",
                callback_data=f"{SUPPLEMENT_NOTIFICATIONS_PREFIX}:disable",
            )
        ])

    if creation:
        rows.append([
            InlineKeyboardButton(
                text="⏭️ Пропустить",
                callback_data=f"{SUPPLEMENT_NOTIFICATIONS_PREFIX}:skip",
            )
        ])
        rows.append([
            InlineKeyboardButton(
                text="⬅️ Назад",
                callback_data=f"{SUPPLEMENT_NOTIFICATIONS_PREFIX}:back",
            ),
            InlineKeyboardButton(
                text="❌ Отменить",
                callback_data=f"{SUPPLEMENT_NOTIFICATIONS_PREFIX}:cancel",
            ),
        ])
    else:
        rows.append([
            InlineKeyboardButton(
                text="⬅️ Назад",
                callback_data=f"{SUPPLEMENT_NOTIFICATIONS_PREFIX}:back",
            )
        ])

    return InlineKeyboardMarkup(inline_keyboard=rows)


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
