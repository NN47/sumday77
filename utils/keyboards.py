"""Клавиатуры для бота."""
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton

# Главная кнопка меню
MAIN_MENU_BUTTON_TEXT = "🔄 Главное меню"
LEGACY_MAIN_MENU_BUTTON_TEXT = "🏠 Главное меню"
ONBOARDING_OPEN_MENU_BUTTON_TEXT = "Открыть меню"
MAIN_MENU_BUTTON_ALIASES = {MAIN_MENU_BUTTON_TEXT, LEGACY_MAIN_MENU_BUTTON_TEXT, ONBOARDING_OPEN_MENU_BUTTON_TEXT}
main_menu_button = KeyboardButton(text=MAIN_MENU_BUTTON_TEXT)
WELLBEING_BUTTON_TEXT = "🙂 Самочувствие"
WELLBEING_AND_PROCEDURES_BUTTON_TEXT = "📝 Заметки"
LEGACY_WELLBEING_AND_PROCEDURES_BUTTON_TEXT = "📝 Заметки/\n💆 Процедуры"
WEIGHT_AND_MEASUREMENTS_BUTTON_TEXT = "⚖️ Вес и замеры"
TRAINING_BUTTON_TEXT = "🚴 Активность"
LEGACY_TRAINING_BUTTON_TEXT = "🏋️ Тренировка"
MEALS_BUTTON_TEXT = "🍱 Дневник питания"
LEGACY_MEALS_BUTTON_TEXT = "🍱 КБЖУ"
MEALS_BUTTON_ALIASES = {
    MEALS_BUTTON_TEXT,
    LEGACY_MEALS_BUTTON_TEXT,
    "Дневник питания",
    "КБЖУ",
}
AI_ANALYSIS_BUTTON_TEXT = "🧠 ИИ анализ"

# Главное меню
main_menu = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text=MEALS_BUTTON_TEXT)],
        [KeyboardButton(text=TRAINING_BUTTON_TEXT), KeyboardButton(text="💧 Контроль воды")],
        [KeyboardButton(text=WELLBEING_AND_PROCEDURES_BUTTON_TEXT), KeyboardButton(text=WEIGHT_AND_MEASUREMENTS_BUTTON_TEXT)],
        [KeyboardButton(text="💊 Добавки"), KeyboardButton(text=AI_ANALYSIS_BUTTON_TEXT)],
        [KeyboardButton(text="⚙️ Настройки"), main_menu_button],
    ],
    resize_keyboard=True
)

onboarding_open_menu = ReplyKeyboardMarkup(
    keyboard=[[KeyboardButton(text=ONBOARDING_OPEN_MENU_BUTTON_TEXT)]],
    resize_keyboard=True,
)

# Меню самочувствия
wellbeing_menu = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="🟢 Быстрый опрос (20 секунд)")],
        [KeyboardButton(text="✍️ Оставить комментарий")],
        [KeyboardButton(text="📆 Календарь самочувствия")],
        [KeyboardButton(text="⬅️ Назад"), main_menu_button],
    ],
    resize_keyboard=True,
)

wellbeing_and_procedures_menu = ReplyKeyboardMarkup(
    keyboard=[[KeyboardButton(text="⬅️ Назад"), main_menu_button]],
    resize_keyboard=True,
)

notes_main_menu = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="➕ Добавить запись")],
        [KeyboardButton(text="📅 Календарь")],
        [main_menu_button],
    ],
    resize_keyboard=True,
)

notes_rating_menu = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="😄 Отлично"), KeyboardButton(text="🙂 Нормально")],
        [KeyboardButton(text="😐 Средне"), KeyboardButton(text="😞 Плохо")],
        [KeyboardButton(text="😫 Очень тяжёлый")],
        [KeyboardButton(text="⬅️ Назад"), main_menu_button],
    ],
    resize_keyboard=True,
)


def build_notes_factors_menu(factor_labels: list[str]) -> ReplyKeyboardMarkup:
    keyboard = [[KeyboardButton(text=label)] for label in factor_labels]
    keyboard.extend(
        [
            [KeyboardButton(text="✍️ Свой вариант")],
            [KeyboardButton(text="✅ Продолжить"), KeyboardButton(text="⏭ Пропустить")],
            [KeyboardButton(text="⬅️ Назад"), main_menu_button],
        ]
    )
    return ReplyKeyboardMarkup(keyboard=keyboard, resize_keyboard=True)


notes_text_menu = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="💾 Сохранить"), KeyboardButton(text="⏭ Пропустить")],
        [KeyboardButton(text="⬅️ Назад"), main_menu_button],
    ],
    resize_keyboard=True,
)

wellbeing_quick_mood_menu = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="😄 Отлично"), KeyboardButton(text="🙂 Нормально")],
        [KeyboardButton(text="😐 Так себе"), KeyboardButton(text="😣 Плохо")],
        [KeyboardButton(text="⬅️ Назад"), main_menu_button],
    ],
    resize_keyboard=True,
)

wellbeing_quick_influence_menu = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="Сон"), KeyboardButton(text="Питание")],
        [KeyboardButton(text="Нагрузка / тренировка"), KeyboardButton(text="Стресс")],
        [KeyboardButton(text="Всё было нормально")],
        [KeyboardButton(text="⬅️ Назад"), main_menu_button],
    ],
    resize_keyboard=True,
)

wellbeing_quick_difficulty_menu = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="Мало энергии")],
        [KeyboardButton(text="Голод / тяга к сладкому")],
        [KeyboardButton(text="Настроение / мотивация")],
        [KeyboardButton(text="Физический дискомфорт")],
        [KeyboardButton(text="Всё ок")],
        [KeyboardButton(text="⬅️ Назад"), main_menu_button],
    ],
    resize_keyboard=True,
)

wellbeing_comment_menu = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="⬅️ Назад"), main_menu_button],
    ],
    resize_keyboard=True,
)

# Inline-кнопки быстрых действий под текстом
quick_actions_inline = InlineKeyboardMarkup(
    inline_keyboard=[
        [
            InlineKeyboardButton(text="👣", callback_data="quick_steps_add"),
            InlineKeyboardButton(text="⚖️", callback_data="quick_weight"),
            InlineKeyboardButton(text="🍱", callback_data="quick_meal_add"),
            InlineKeyboardButton(text="💧+300", callback_data="quick_water_300"),
        ],
    ]
)

# Меню тренировок
training_menu = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="👣 Шаги"), KeyboardButton(text="💪 Тренировка")],
        [KeyboardButton(text="📅 Календарь активности")],
        [KeyboardButton(text="⬅️ Назад"), main_menu_button],
    ],
    resize_keyboard=True,
)

steps_menu = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="3000"), KeyboardButton(text="3500"), KeyboardButton(text="4000"), KeyboardButton(text="4500")],
        [KeyboardButton(text="5000"), KeyboardButton(text="5500"), KeyboardButton(text="6000"), KeyboardButton(text="6500")],
        [KeyboardButton(text="7000"), KeyboardButton(text="7500"), KeyboardButton(text="8000"), KeyboardButton(text="8500")],
        [KeyboardButton(text="9000"), KeyboardButton(text="9500"), KeyboardButton(text="10000"), KeyboardButton(text="10500")],
        [KeyboardButton(text="11000"), KeyboardButton(text="11500"), KeyboardButton(text="12000"), KeyboardButton(text="12500")],
        [KeyboardButton(text="13000"), KeyboardButton(text="13500"), KeyboardButton(text="14000"), KeyboardButton(text="14500")],
        [KeyboardButton(text="15000")],
        [KeyboardButton(text="✍️ Ввести вручную")],
        [KeyboardButton(text="⬅️ Назад"), main_menu_button],
    ],
    resize_keyboard=True,
)

steps_confirmation_menu = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="✅ Сохранить"), KeyboardButton(text="✏️ Изменить")],
        [KeyboardButton(text="🗑 Удалить шаги")],
        [KeyboardButton(text="⬅️ Назад")],
    ],
    resize_keyboard=True,
)

duration_menu = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="10"), KeyboardButton(text="15"), KeyboardButton(text="20")],
        [KeyboardButton(text="30"), KeyboardButton(text="45"), KeyboardButton(text="60")],
        [KeyboardButton(text="✍️ Ввести вручную")],
        [KeyboardButton(text="⬅️ Назад"), main_menu_button],
    ],
    resize_keyboard=True,
)

plank_duration_menu = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="1"), KeyboardButton(text="1,5"), KeyboardButton(text="2")],
        [KeyboardButton(text="2,5"), KeyboardButton(text="3"), KeyboardButton(text="3,5")],
        [KeyboardButton(text="4"), KeyboardButton(text="4,5"), KeyboardButton(text="5")],
        [KeyboardButton(text="✍️ Ввести вручную")],
        [KeyboardButton(text="⬅️ Назад"), main_menu_button],
    ],
    resize_keyboard=True,
)

count_menu = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text=str(n)) for n in range(1, 6)],
        [KeyboardButton(text=str(n)) for n in range(6, 11)],
        [KeyboardButton(text=str(n)) for n in range(11, 16)],
        [KeyboardButton(text=str(n)) for n in range(16, 21)],
        [KeyboardButton(text=str(n)) for n in [25, 30, 35, 40, 50]],
        [KeyboardButton(text="✏️ Ввести вручную")],
        [KeyboardButton(text="⬅️ Назад"), main_menu_button],
    ],
    resize_keyboard=True,
)

# Меню выбора даты тренировки
training_date_menu = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="📅 Сегодня"), KeyboardButton(text="📆 Другой день")],
        [KeyboardButton(text="⬅️ Назад")]
    ],
    resize_keyboard=True
)

other_day_menu = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="📅 Вчера"), KeyboardButton(text="📆 Позавчера")],
        [KeyboardButton(text="✏️ Ввести дату вручную")],
        [KeyboardButton(text="⬅️ Назад"), main_menu_button],
    ],
    resize_keyboard=True
)

# Упражнения
bodyweight_exercises = [
    "Подтягивания",
    "Отжимания",
    "Приседания",
    "Пресс",
    "Берпи",
    "Пробежка",
    "Скакалка",
    "Становая тяга без утяжелителя",
    "Румынская тяга без утяжелителя",
    "Планка",
    "Йога",
    "Другое",
]

weighted_exercises = [
    "Приседания со штангой",
    "Жим штанги лёжа",
    "Становая тяга с утяжелителем",
    "Румынская тяга с утяжелителем",
    "Тяга штанги в наклоне",
    "Жим гантелей лёжа",
    "Жим гантелей сидя",
    "Подъёмы гантелей на бицепс",
    "Тяга верхнего блока",
    "Тяга нижнего блока",
    "Жим ногами",
    "Разведения гантелей",
    "Тяга горизонтального блока",
    "Сгибание ног в тренажёре",
    "Разгибание ног в тренажёре",
    "Гиперэкстензия с утяжелителем",
    "Другое",
]

exercise_category_menu = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="Со своим весом"), KeyboardButton(text="С утяжелителем")],
        [KeyboardButton(text="⬅️ Назад")],
        [main_menu_button],
    ],
    resize_keyboard=True
)

frequent_exercises = [
    "Отжимания",
    "Подтягивания",
    "Приседания",
    "Планка",
    "Бег",
    "Йога",
]

exercise_picker_menu = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="Отжимания"), KeyboardButton(text="Подтягивания")],
        [KeyboardButton(text="Приседания"), KeyboardButton(text="Планка")],
        [KeyboardButton(text="Бег"), KeyboardButton(text="Йога")],
        [KeyboardButton(text="🕘 Недавние"), KeyboardButton(text="🔎 Поиск упражнения")],
        [KeyboardButton(text="📂 Все упражнения")],
        [KeyboardButton(text="⬅️ Назад"), main_menu_button],
    ],
    resize_keyboard=True,
)

add_another_exercise_menu = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="✅ Завершить упражнение")],
        [KeyboardButton(text="➕ Добавить другое упражнение")],
    ],
    resize_keyboard=True,
)

bodyweight_exercise_menu = ReplyKeyboardMarkup(
    keyboard=[[KeyboardButton(text=ex)] for ex in bodyweight_exercises] + [[KeyboardButton(text="⬅️ Назад"), main_menu_button]],
    resize_keyboard=True,
)

weighted_exercise_menu = ReplyKeyboardMarkup(
    keyboard=[[KeyboardButton(text=ex)] for ex in weighted_exercises] + [[KeyboardButton(text="⬅️ Назад"), main_menu_button]],
    resize_keyboard=True,
)


def build_exercise_menu(category: str, custom_exercises: list[str] | None = None) -> ReplyKeyboardMarkup:
    """Строит меню упражнений с учётом пользовательских упражнений."""
    base_exercises = bodyweight_exercises if category == "bodyweight" else weighted_exercises
    custom_exercises = custom_exercises or []

    base_without_other = [ex for ex in base_exercises if ex != "Другое"]
    normalized_base = {ex.casefold() for ex in base_without_other}

    filtered_custom = []
    seen_custom = set()
    for exercise in custom_exercises:
        clean_name = exercise.strip()
        if not clean_name:
            continue
        key = clean_name.casefold()
        if key in normalized_base or key in seen_custom:
            continue
        seen_custom.add(key)
        filtered_custom.append(clean_name)

    exercise_rows = [[KeyboardButton(text=ex)] for ex in base_without_other]
    exercise_rows += [[KeyboardButton(text=ex)] for ex in filtered_custom]
    exercise_rows.append([KeyboardButton(text="Другое")])
    exercise_rows.append([KeyboardButton(text="⬅️ Назад"), main_menu_button])

    return ReplyKeyboardMarkup(keyboard=exercise_rows, resize_keyboard=True)


def build_exercise_selection_menu(exercises: list[str]) -> ReplyKeyboardMarkup:
    """Строит клавиатуру выбора упражнения из произвольного списка."""
    rows = [[KeyboardButton(text=exercise)] for exercise in exercises]
    rows.append([KeyboardButton(text="⬅️ Назад"), main_menu_button])
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True)

# Меню КБЖУ
kbju_menu = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="➕ Добавить")],
        [KeyboardButton(text="📆 Календарь КБЖУ")],
        [KeyboardButton(text="🎯 Цель / Норма КБЖУ")],
        [main_menu_button],
    ],
    resize_keyboard=True,
)

kbju_goal_view_menu = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="✏️ Редактировать")],
        [KeyboardButton(text="⬅️ Назад"), main_menu_button],
    ],
    resize_keyboard=True,
)

kbju_intro_menu = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="✅ Пройти быстрый тест КБЖУ")],
        [KeyboardButton(text="✏️ Ввести свою норму")],
        [main_menu_button],
    ],
    resize_keyboard=True,
)

kbju_gender_menu = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="🙋‍♂️ Муж"), KeyboardButton(text="🙋‍♀️ Жен")],
        [main_menu_button],
    ],
    resize_keyboard=True,
)

kbju_gender_inline = InlineKeyboardMarkup(
    inline_keyboard=[
        [
            InlineKeyboardButton(text="🙋‍♂️ Муж", callback_data="kbju_gender:male"),
            InlineKeyboardButton(text="🙋‍♀️ Жен", callback_data="kbju_gender:female"),
        ],
    ],
)

kbju_age_range_inline = InlineKeyboardMarkup(
    inline_keyboard=[
        [
            InlineKeyboardButton(text="до 18", callback_data="kbju_age:under_18"),
            InlineKeyboardButton(text="18–24", callback_data="kbju_age:18_24"),
        ],
        [
            InlineKeyboardButton(text="25–29", callback_data="kbju_age:25_29"),
            InlineKeyboardButton(text="30–34", callback_data="kbju_age:30_34"),
        ],
        [
            InlineKeyboardButton(text="35–39", callback_data="kbju_age:35_39"),
            InlineKeyboardButton(text="40–44", callback_data="kbju_age:40_44"),
        ],
        [
            InlineKeyboardButton(text="45–49", callback_data="kbju_age:45_49"),
            InlineKeyboardButton(text="50–54", callback_data="kbju_age:50_54"),
        ],
        [
            InlineKeyboardButton(text="55–59", callback_data="kbju_age:55_59"),
            InlineKeyboardButton(text="60–64", callback_data="kbju_age:60_64"),
        ],
        [
            InlineKeyboardButton(text="65+", callback_data="kbju_age:65_plus"),
        ],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="kbju_back:gender")],
    ],
)

kbju_height_range_inline = InlineKeyboardMarkup(
    inline_keyboard=[
        [
            InlineKeyboardButton(text="до 149", callback_data="kbju_height:under_149"),
            InlineKeyboardButton(text="150–154", callback_data="kbju_height:150_154"),
        ],
        [
            InlineKeyboardButton(text="155–159", callback_data="kbju_height:155_159"),
            InlineKeyboardButton(text="160–164", callback_data="kbju_height:160_164"),
        ],
        [
            InlineKeyboardButton(text="165–169", callback_data="kbju_height:165_169"),
            InlineKeyboardButton(text="170–174", callback_data="kbju_height:170_174"),
        ],
        [
            InlineKeyboardButton(text="175–179", callback_data="kbju_height:175_179"),
            InlineKeyboardButton(text="180–184", callback_data="kbju_height:180_184"),
        ],
        [
            InlineKeyboardButton(text="185–189", callback_data="kbju_height:185_189"),
            InlineKeyboardButton(text="190–194", callback_data="kbju_height:190_194"),
        ],
        [
            InlineKeyboardButton(text="195+", callback_data="kbju_height:195_plus"),
        ],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="kbju_back:age")],
    ],
)

kbju_weight_range_inline = InlineKeyboardMarkup(
    inline_keyboard=[
        [
            InlineKeyboardButton(text="до 50 кг", callback_data="kbju_weight_range:40_50"),
            InlineKeyboardButton(text="51–60 кг", callback_data="kbju_weight_range:51_60"),
        ],
        [
            InlineKeyboardButton(text="61–70 кг", callback_data="kbju_weight_range:61_70"),
            InlineKeyboardButton(text="71–80 кг", callback_data="kbju_weight_range:71_80"),
        ],
        [
            InlineKeyboardButton(text="81–90 кг", callback_data="kbju_weight_range:81_90"),
            InlineKeyboardButton(text="91–100 кг", callback_data="kbju_weight_range:91_100"),
        ],
        [
            InlineKeyboardButton(text="101–120 кг", callback_data="kbju_weight_range:101_120"),
            InlineKeyboardButton(text="121–150 кг", callback_data="kbju_weight_range:121_150"),
        ],
        [
            InlineKeyboardButton(text="151–200 кг", callback_data="kbju_weight_range:151_200"),
            InlineKeyboardButton(text="200+ кг", callback_data="kbju_weight_range:200_plus"),
        ],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="kbju_back:height")],
    ],
)


def build_kbju_weight_values_inline(
    range_min: int,
    range_max: int,
    callback_prefix: str = "kbju_weight_value",
    back_callback: str | None = None,
) -> InlineKeyboardMarkup:
    """Строит inline-клавиатуру с точным весом внутри выбранного диапазона."""
    buttons = [
        InlineKeyboardButton(text=f"{weight} кг", callback_data=f"{callback_prefix}:{weight}")
        for weight in range(range_min, range_max + 1)
    ]
    rows = [buttons[i:i + 3] for i in range(0, len(buttons), 3)]
    if back_callback:
        rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data=back_callback)])
    return InlineKeyboardMarkup(inline_keyboard=rows)

kbju_activity_menu = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="🪑 Сидячий\nОфис или учеба")],
        [KeyboardButton(text="🚶 Немного активный\nМного ходьбы")],
        [KeyboardButton(text="🏃 Активный\nРегулярные тренировки")],
        [KeyboardButton(text="🏋️ Очень активный\nФизическая работа или спорт")],
        [main_menu_button],
    ],
    resize_keyboard=True,
)

kbju_activity_inline = InlineKeyboardMarkup(
    inline_keyboard=[
        [InlineKeyboardButton(text="🪑 Сидячий\nОфис или учеба", callback_data="kbju_activity:sedentary")],
        [InlineKeyboardButton(text="🚶 Немного активный\nМного ходьбы", callback_data="kbju_activity:light")],
        [InlineKeyboardButton(text="🏃 Активный\nРегулярные тренировки", callback_data="kbju_activity:moderate")],
        [InlineKeyboardButton(text="🏋️ Очень активный\nФизическая работа или спорт", callback_data="kbju_activity:active")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="kbju_back:goal_or_speed")],
    ],
)

kbju_goal_menu = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="Похудеть")],
        [KeyboardButton(text="Поддерживать")],
        [KeyboardButton(text="Набрать")],
        [main_menu_button],
    ],
    resize_keyboard=True,
)

kbju_goal_inline = InlineKeyboardMarkup(
    inline_keyboard=[
        [InlineKeyboardButton(text="📉 Похудение", callback_data="kbju_goal:loss")],
        [InlineKeyboardButton(text="⚖️ Поддержание", callback_data="kbju_goal:maintain")],
        [InlineKeyboardButton(text="💪 Набор массы", callback_data="kbju_goal:gain")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="kbju_back:weight")],
    ],
)

kbju_goal_speed_loss_menu = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="🌿 Медленно — ~0.3 кг в неделю")],
        [KeyboardButton(text="⚖️ Стандарт — ~0.5 кг в неделю")],
        [KeyboardButton(text="🔥 Быстро — ~0.7 кг в неделю")],
        [main_menu_button],
    ],
    resize_keyboard=True,
)

kbju_goal_speed_loss_inline = InlineKeyboardMarkup(
    inline_keyboard=[
        [InlineKeyboardButton(text="🌿 Медленно — ~0.3 кг в неделю", callback_data="kbju_goal_speed:slow")],
        [InlineKeyboardButton(text="⚖️ Стандарт — ~0.5 кг в неделю", callback_data="kbju_goal_speed:normal")],
        [InlineKeyboardButton(text="🔥 Быстро — ~0.7 кг в неделю", callback_data="kbju_goal_speed:fast")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="kbju_back:target_weight")],
    ],
)

kbju_goal_speed_gain_menu = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="🌿 Медленно — ~0.3 кг в неделю")],
        [KeyboardButton(text="⚖️ Стандарт — ~0.5 кг в неделю")],
        [KeyboardButton(text="🔥 Быстро — ~0.7 кг в неделю")],
        [main_menu_button],
    ],
    resize_keyboard=True,
)

kbju_goal_speed_gain_inline = InlineKeyboardMarkup(
    inline_keyboard=[
        [InlineKeyboardButton(text="🌿 Медленно — ~0.3 кг в неделю", callback_data="kbju_goal_speed:slow")],
        [InlineKeyboardButton(text="⚖️ Стандарт — ~0.5 кг в неделю", callback_data="kbju_goal_speed:normal")],
        [InlineKeyboardButton(text="🔥 Быстро — ~0.7 кг в неделю", callback_data="kbju_goal_speed:fast")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="kbju_back:target_weight")],
    ],
)

kbju_add_menu = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="📝 Ввести приём пищи текстом (AI-анализ)")],
        [KeyboardButton(text="🧪 Ввести текст через OpenRouter")],
        [KeyboardButton(text="📷 Анализ еды по фото")],
        [KeyboardButton(text="📋 Анализ этикетки")],
        [KeyboardButton(text="📷 Этикетка через OCR (тест)")],
        [KeyboardButton(text="⬅️ Назад"), main_menu_button],
    ],
    resize_keyboard=True,
)

kbju_meal_type_menu = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="🍳 Завтрак"), KeyboardButton(text="🍲 Обед")],
        [KeyboardButton(text="🍽 Ужин"), KeyboardButton(text="🍎 Перекус")],
        [KeyboardButton(text="⬅️ Назад"), main_menu_button],
    ],
    resize_keyboard=True,
)

kbju_after_meal_menu = ReplyKeyboardMarkup(
    keyboard=[
        [
            KeyboardButton(text="➕ Внести ещё приём"),
            KeyboardButton(text="✏️ Редактировать"),
        ],
        [KeyboardButton(text="📊 Дневной отчёт")],
        [
            KeyboardButton(text="⬅️ Назад"),
            main_menu_button,
        ],
    ],
    resize_keyboard=True,
)


openrouter_confirm_menu = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="💾 Сохранить"), KeyboardButton(text="❌ Отмена")],
        [KeyboardButton(text="⬅️ Назад"), main_menu_button],
    ],
    resize_keyboard=True,
)

kbju_weight_input_menu = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="10"), KeyboardButton(text="20"), KeyboardButton(text="30"), KeyboardButton(text="40")],
        [KeyboardButton(text="50"), KeyboardButton(text="60"), KeyboardButton(text="70"), KeyboardButton(text="80")],
        [KeyboardButton(text="90"), KeyboardButton(text="100"), KeyboardButton(text="150"), KeyboardButton(text="200")],
        [KeyboardButton(text="⬅️ Назад"), main_menu_button],
    ],
    resize_keyboard=True,
)

# Меню выбора типа редактирования КБЖУ
kbju_edit_type_menu = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="⚖️ Изменить вес продукта")],
        [KeyboardButton(text="📝 Изменить состав продуктов")],
        [KeyboardButton(text="⬅️ Назад"), main_menu_button],
    ],
    resize_keyboard=True,
)

# Меню настроек
settings_menu = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="🗑 Удалить аккаунт")],
        [KeyboardButton(text="💬 Поддержка")],
        [KeyboardButton(text="🔒 Политика конфиденциальности")],
        [main_menu_button],
    ],
    resize_keyboard=True,
)

delete_account_confirm_menu = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="✅ Да, удалить аккаунт")],
        [KeyboardButton(text="❌ Отмена")],
        [main_menu_button],
    ],
    resize_keyboard=True,
)

# Меню процедур
procedures_menu = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="➕ Добавить процедуру")],
        [KeyboardButton(text="📆 Календарь процедур")],
        [KeyboardButton(text="📊 Сегодня")],
        [KeyboardButton(text="⬅️ Назад"), main_menu_button],
    ],
    resize_keyboard=True,
)

# Меню воды
water_menu = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="➕ Добавить воду")],
        [KeyboardButton(text="📆 Календарь воды")],
        [KeyboardButton(text="⬅️ Назад"), main_menu_button],
    ],
    resize_keyboard=True,
)

water_amount_menu = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="250"), KeyboardButton(text="300"), KeyboardButton(text="330"), KeyboardButton(text="500")],
        [KeyboardButton(text="550"), KeyboardButton(text="600"), KeyboardButton(text="650"), KeyboardButton(text="700")],
        [KeyboardButton(text="750"), KeyboardButton(text="800"), KeyboardButton(text="850"), KeyboardButton(text="900")],
        [KeyboardButton(text="1000"), KeyboardButton(text="-300"), KeyboardButton(text="🧹 Очистить")],
        [KeyboardButton(text="⬅️ Назад")],
    ],
    resize_keyboard=True,
)

water_quick_add_inline = InlineKeyboardMarkup(
    inline_keyboard=[
        [
            InlineKeyboardButton(text="250", callback_data="quick_water_add_250"),
            InlineKeyboardButton(text="+300", callback_data="quick_water_add_300"),
            InlineKeyboardButton(text="330", callback_data="quick_water_add_330"),
            InlineKeyboardButton(text="500", callback_data="quick_water_add_500"),
        ],
        [
            InlineKeyboardButton(text="550", callback_data="quick_water_add_550"),
            InlineKeyboardButton(text="600", callback_data="quick_water_add_600"),
            InlineKeyboardButton(text="650", callback_data="quick_water_add_650"),
            InlineKeyboardButton(text="700", callback_data="quick_water_add_700"),
        ],
        [
            InlineKeyboardButton(text="750", callback_data="quick_water_add_750"),
            InlineKeyboardButton(text="800", callback_data="quick_water_add_800"),
            InlineKeyboardButton(text="850", callback_data="quick_water_add_850"),
            InlineKeyboardButton(text="900", callback_data="quick_water_add_900"),
        ],
        [
            InlineKeyboardButton(text="1000", callback_data="quick_water_add_1000"),
            InlineKeyboardButton(text="-300", callback_data="quick_water_add_-300"),
            InlineKeyboardButton(text="🧹 Очистить", callback_data="quick_water_clear_today"),
        ],
    ]
)

# Меню анализа
activity_analysis_menu = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="📅 Анализ за день")],
        [KeyboardButton(text="📆 Анализ за неделю"), KeyboardButton(text="📊 Анализ за месяц")],
        [KeyboardButton(text="🗓 Календарь")],
        [KeyboardButton(text="⬅️ Назад"), main_menu_button],
    ],
    resize_keyboard=True,
)

# Меню для добавления еще подхода
add_another_set_menu = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="💪 Добавить еще подход")],
        [KeyboardButton(text="✅ Завершить упражнение")],
    ],
    resize_keyboard=True,
)

# Меню выбора типа хвата для подтягиваний
grip_type_menu = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="Прямой хват"), KeyboardButton(text="Обратный хват")],
        [KeyboardButton(text="Нейтральный хват")],
        [KeyboardButton(text="Пропустить")],
        [KeyboardButton(text="⬅️ Назад"), main_menu_button],
    ],
    resize_keyboard=True,
)


def push_menu_stack(bot, reply_markup):
    """Добавляет клавиатуру в стек меню."""
    if not isinstance(reply_markup, ReplyKeyboardMarkup):
        return

    stack = getattr(bot, "menu_stack", [])
    if not stack:
        stack = [main_menu]

    if stack and stack[-1] is not reply_markup:
        stack.append(reply_markup)

    bot.menu_stack = stack
