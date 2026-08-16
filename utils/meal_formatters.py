"""Функции форматирования для приёмов пищи."""
import logging
import json
import html
from datetime import date
from collections import defaultdict
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from database.models import Meal, KbjuSettings
from utils.emoji_map import EMOJI_MAP
from utils.formatters import get_kbju_goal_label
from utils.meal_types import MEAL_TYPE_ORDER, normalize_meal_type
from utils.progress_formatters import build_progress_bar
from utils.log_sanitizer import safe_exception_summary

logger = logging.getLogger(__name__)


TELEGRAM_SAFE_TEXT_LIMIT = 4_000
MEAL_SUMMARY_PRODUCTS_LIMIT = 900
MEAL_SUMMARY_PRODUCT_NAME_LIMIT = 100


_EMOJI_DIGITS = {
    "0": "0️⃣",
    "1": "1️⃣",
    "2": "2️⃣",
    "3": "3️⃣",
    "4": "4️⃣",
    "5": "5️⃣",
    "6": "6️⃣",
    "7": "7️⃣",
    "8": "8️⃣",
    "9": "9️⃣",
}


MEAL_UI = {
    "breakfast": {"emoji": "🍳", "title": "Завтрак", "totals_label": "завтрак"},
    "lunch": {"emoji": "🍲", "title": "Обед", "totals_label": "обед"},
    "dinner": {"emoji": "🍽", "title": "Ужин", "totals_label": "ужин"},
    "snack": {"emoji": "🍎", "title": "Перекус", "totals_label": "перекус"},
}


def format_food_diary_header(day_str: str) -> str:
    """Форматирует заголовок дневника питания."""
    return f"🍱 Дневник питания — {day_str}"


def format_today_meals(
    meals: list[Meal],
    daily_totals: dict,
    day_str: str,
    include_date_header: bool = True,
    settings: KbjuSettings | None = None,
) -> str:
    """Форматирует дневник питания в виде блоков по приёмам пищи + итог дня."""
    lines: list[str] = []
    if include_date_header:
        lines.append(format_food_diary_header(day_str))
        lines.append("")

    grouped: dict[str, list[Meal]] = defaultdict(list)
    for meal in meals:
        grouped[normalize_meal_type(getattr(meal, "meal_type", None))].append(meal)

    non_empty_blocks = 0
    for meal_type in MEAL_TYPE_ORDER:
        meal_group = grouped.get(meal_type, [])
        if not meal_group:
            continue
        if non_empty_blocks > 0:
            lines.append("")
        lines.extend(format_meal_block(meal_type, meal_group))
        non_empty_blocks += 1

    if non_empty_blocks > 0 and daily_totals:
        lines.append("")
        lines.append("⸻")
        lines.append("")

    lines.extend(format_daily_totals_lines(daily_totals, day_str))
    return "\n".join(lines)


def _safe_float(value: object) -> float:
    try:
        if value is None:
            return 0.0
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _safe_product_name(value: object, fallback: str = "Продукт") -> str:
    name = str(value or "").strip()
    if not name or name.lower() == "none":
        return fallback
    return name


def format_emoji_number(number: int) -> str:
    """Форматирует порядковый номер только emoji-цифрами."""
    if number == 10:
        return "🔟"
    return "".join(_EMOJI_DIGITS[digit] for digit in str(number))


def extract_product_name(product: dict, fallback: str = "Продукт") -> str:
    """Возвращает отображаемое название продукта из поддерживаемых форматов."""
    return _safe_product_name(product.get("name_ru") or product.get("name"), fallback)


def extract_product_weight(product: dict) -> float:
    """Возвращает вес продукта из поддерживаемых форматов."""
    return _safe_float(product.get("grams") or product.get("weight"))


def extract_product_macros(product: dict) -> tuple[float, float, float, float]:
    """Возвращает калории/белки/жиры/углеводы из поддерживаемых форматов."""
    calories = _safe_float(product.get("kcal") or product.get("calories") or product.get("_calories"))
    protein = _safe_float(product.get("protein") or product.get("protein_g") or product.get("_protein_g"))
    fat = _safe_float(product.get("fat") or product.get("fat_total_g") or product.get("_fat_total_g"))
    carbs = _safe_float(
        product.get("carbs")
        or product.get("carbohydrates_total_g")
        or product.get("_carbohydrates_total_g")
    )
    return calories, protein, fat, carbs


def _extract_products(meal: Meal) -> list[dict]:
    """Извлекает продукты из meals.products_json, сохраняя исходный порядок."""
    raw_products = getattr(meal, "products_json", None)
    products: list[dict] = []
    if raw_products:
        try:
            parsed = json.loads(raw_products)
            if isinstance(parsed, list):
                products = [item for item in parsed if isinstance(item, dict)]
        except Exception as e:
            logger.warning("Не смог распарсить products_json error_type=%s", safe_exception_summary(e))

    return products


def _collect_product_names(items: list[Meal]) -> list[str]:
    """Собирает уникальные названия продуктов в порядке записей дневника."""
    names: list[str] = []
    seen: set[str] = set()
    for meal in items:
        products = _extract_products(meal)
        meal_names = (
            [extract_product_name(product) for product in products]
            if products
            else [
                _safe_product_name(
                    getattr(meal, "description", None) or getattr(meal, "raw_query", None)
                )
            ]
        )
        for name in meal_names:
            clean_name = " ".join(name.split())
            dedupe_key = clean_name.casefold()
            if dedupe_key in seen:
                continue
            seen.add(dedupe_key)
            names.append(clean_name)
    return names


def _format_compact_product_names(names: list[str]) -> str:
    """Форматирует ограниченный, но понятный список названий для сводки."""
    if not names:
        return "Продукт"

    visible: list[str] = []
    for index, name in enumerate(names):
        shortened = name
        if len(shortened) > MEAL_SUMMARY_PRODUCT_NAME_LIMIT:
            shortened = f"{shortened[:MEAL_SUMMARY_PRODUCT_NAME_LIMIT - 1].rstrip()}…"
        escaped = html.escape(shortened)
        candidate = ", ".join([*visible, escaped])
        if len(candidate) <= MEAL_SUMMARY_PRODUCTS_LIMIT:
            visible.append(escaped)
            continue

        remaining = len(names) - index
        suffix = f"и ещё {remaining}"
        if visible and len(", ".join([*visible, suffix])) <= MEAL_SUMMARY_PRODUCTS_LIMIT:
            visible.append(suffix)
        break

    return ", ".join(visible) or f"и ещё {len(names)}"


def _normalize_totals(totals: dict | None) -> dict[str, float]:
    totals = totals or {}
    return {
        "calories": _safe_float(totals.get("calories", totals.get("kcal"))),
        "protein": _safe_float(totals.get("protein", totals.get("protein_g"))),
        "fat": _safe_float(totals.get("fat", totals.get("fat_total_g"))),
        "carbs": _safe_float(totals.get("carbs", totals.get("carbohydrates_total_g"))),
    }


def _collect_product_totals(products: list[dict]) -> dict[str, float]:
    totals = {"calories": 0.0, "protein": 0.0, "fat": 0.0, "carbs": 0.0}
    for product in products:
        calories, protein, fat, carbs = extract_product_macros(product)
        totals["calories"] += calories
        totals["protein"] += protein
        totals["fat"] += fat
        totals["carbs"] += carbs
    return totals


def format_meal_edit_details(
    meal_type: str,
    products: list[dict],
    totals: dict | None = None,
) -> str:
    """Форматирует полную детализацию продуктов перед их редактированием."""
    meal_ui = MEAL_UI.get(normalize_meal_type(meal_type), MEAL_UI["snack"])
    normalized_totals = _normalize_totals(totals) if totals is not None else _collect_product_totals(products)
    lines = [f"✏️ {meal_ui['title']} — {normalized_totals['calories']:.0f} ккал", ""]

    for index, product in enumerate(products, start=1):
        name = extract_product_name(product)
        grams = extract_product_weight(product)
        calories, protein, fat, carbs = extract_product_macros(product)
        lines.extend(
            [
                f"{format_emoji_number(index)} {name} — {grams:.0f} г",
                f"{calories:.0f} ккал · Б {protein:.1f} / Ж {fat:.1f} / У {carbs:.1f}",
            ]
        )
        if bool(product.get("is_manually_corrected")):
            lines.append("✏️ КБЖУ скорректированы вручную")
        lines.append("")

    lines.extend(
        [
            "Итого:",
            f"{normalized_totals['calories']:.0f} ккал · "
            f"Б {normalized_totals['protein']:.1f} / "
            f"Ж {normalized_totals['fat']:.1f} / "
            f"У {normalized_totals['carbs']:.1f}",
            "",
            "Выбери продукт для редактирования:",
        ]
    )
    return "\n".join(lines)


def _telegram_text_units(text: str) -> int:
    """Считает длину текста в UTF-16 code units, как Telegram Bot API."""
    return len(text.encode("utf-16-le")) // 2


def split_telegram_text(text: str, limit: int = TELEGRAM_SAFE_TEXT_LIMIT) -> list[str]:
    """Без потерь разбивает plain-text сообщение на безопасные для Telegram части."""
    if limit <= 0:
        raise ValueError("limit must be positive")
    if not text:
        return [""]

    chunks: list[str] = []
    current: list[str] = []
    current_units = 0
    for character in text:
        character_units = _telegram_text_units(character)
        if current and current_units + character_units > limit:
            chunks.append("".join(current))
            current = []
            current_units = 0
        current.append(character)
        current_units += character_units
    if current:
        chunks.append("".join(current))
    return chunks


def _collect_meal_totals(items: list[Meal]) -> dict[str, float]:
    totals = {"calories": 0.0, "protein": 0.0, "fat": 0.0, "carbs": 0.0}
    for meal in items:
        totals["calories"] += float(getattr(meal, "calories", 0) or 0)
        totals["protein"] += float(getattr(meal, "protein", 0) or 0)
        totals["fat"] += float(getattr(meal, "fat", 0) or 0)
        totals["carbs"] += float(getattr(meal, "carbs", 0) or 0)
    return totals


def format_meal_totals(meal_type: str, totals: dict[str, float]) -> list[str]:
    meal_ui = MEAL_UI.get(meal_type, MEAL_UI["snack"])
    return [
        f"<b>Итого {meal_ui['totals_label']}:</b>",
        f"🔥 <b>Калории:</b> {totals['calories']:.0f} ккал",
        f"🥩 <b>Белки:</b> {totals['protein']:.1f} г",
        f"🥑 <b>Жиры:</b> {totals['fat']:.1f} г",
        f"🍚 <b>Углеводы:</b> {totals['carbs']:.1f} г",
    ]


def format_meal_block(meal_type: str, items: list[Meal]) -> list[str]:
    meal_ui = MEAL_UI.get(meal_type, MEAL_UI["snack"])
    totals = _collect_meal_totals(items)
    product_names = _format_compact_product_names(_collect_product_names(items))
    return [
        f"{meal_ui['emoji']} <b>{meal_ui['title']} — {totals['calories']:.0f} ккал</b>",
        product_names,
        f"<i>Б {totals['protein']:.1f} / Ж {totals['fat']:.1f} / У {totals['carbs']:.1f}</i>",
    ]


def format_meal_message(
    meal_type: str,
    items: list[Meal],
    day_str: str | None = None,
    include_date_header: bool = False,
) -> str:
    """Собирает сообщение одного блока приёма пищи."""
    lines: list[str] = []
    if include_date_header and day_str:
        lines.append(format_food_diary_header(day_str))
        lines.append("")
    lines.extend(format_meal_block(meal_type, items))
    return "\n".join(lines)


def _build_goal_progress_line(label: str, current: float, target: float, unit: str) -> list[str]:
    percent = 0 if target <= 0 else round((current / target) * 100)
    return [
        f"<b>{label}:</b> {current:.0f}/{target:.0f} {unit} ({percent}%)",
        build_progress_bar(current, target),
    ]


def format_daily_totals_lines(
    day_totals: dict,
    day_str: str,
    settings: KbjuSettings | None = None,
    include_action_prompt: bool = False,
) -> list[str]:
    """Форматирует нижний блок прогресса КБЖУ в дневнике питания."""
    calories_current = float(day_totals.get("calories", 0) or 0)
    protein_current = float(day_totals.get("protein_g", day_totals.get("protein", 0)) or 0)
    fat_current = float(day_totals.get("fat_total_g", day_totals.get("fat", 0)) or 0)
    carbs_current = float(day_totals.get("carbohydrates_total_g", day_totals.get("carbs", 0)) or 0)

    if settings:
        goal_label = get_kbju_goal_label(settings.goal)
        base_calories_target = float(settings.calories or 0)
        protein_target = float(settings.protein or 0)
        fat_target = float(settings.fat or 0)
        carbs_target = float(settings.carbs or 0)
    else:
        goal_label = "Не задана"
        base_calories_target = 0.0
        protein_target = 0.0
        fat_target = 0.0
        carbs_target = 0.0

    lines = [
        f"🎯 <b>Цель:</b> {goal_label}",
        f"📊 <b>Базовая норма:</b> {base_calories_target:.0f} ккал",
        "",
    ]
    lines.extend(_build_goal_progress_line("🔥 Калории", calories_current, base_calories_target, "ккал"))
    lines.extend(_build_goal_progress_line("🥩 Белки", protein_current, protein_target, "г"))
    lines.extend(_build_goal_progress_line("🥑 Жиры", fat_current, fat_target, "г"))
    lines.extend(_build_goal_progress_line("🍚 Углеводы", carbs_current, carbs_target, "г"))

    if include_action_prompt:
        lines.extend(["", "Выбери действие:"])

    return lines


def format_daily_totals_message(
    day_totals: dict,
    day_str: str,
    settings: KbjuSettings | None = None,
    include_action_prompt: bool = False,
) -> str:
    """Собирает нижний блок прогресса КБЖУ в одну строку сообщения."""
    return "\n".join(
        format_daily_totals_lines(
            day_totals,
            day_str,
            settings=settings,
            include_action_prompt=include_action_prompt,
        )
    )


def build_meal_actions_keyboard(meal_type: str, target_date: date) -> InlineKeyboardMarkup:
    """Inline-кнопки действий для конкретного типа приёма пищи."""
    normalized_meal_type = normalize_meal_type(meal_type)
    iso_date = target_date.isoformat()
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="➕ Добавить",
                    callback_data=f"add_meal:{normalized_meal_type}:{iso_date}",
                ),
                InlineKeyboardButton(
                    text="✏️ Редактировать",
                    callback_data=f"edit_meal:{normalized_meal_type}:{iso_date}",
                ),
                InlineKeyboardButton(
                    text="🗑 Очистить",
                    callback_data=f"clear_meal:{normalized_meal_type}:{iso_date}",
                ),
            ]
        ]
    )


def build_daily_totals_keyboard(target_date: date, include_back: bool = False) -> InlineKeyboardMarkup | None:
    """Клавиатура для итогового сообщения дня (без кнопок редактирования приёмов пищи)."""
    if not include_back:
        return None
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="⬅️ Назад к календарю",
                    callback_data=f"meal_cal_back:{target_date.year}-{target_date.month:02d}",
                )
            ]
        ]
    )


def build_meals_actions_keyboard(
    meals: list[Meal],
    target_date: date,
    include_back: bool = False,
) -> InlineKeyboardMarkup:
    """Строит клавиатуру с действиями на уровне приёма пищи (meal_type)."""
    grouped: dict[str, list[Meal]] = defaultdict(list)
    for meal in meals:
        grouped[normalize_meal_type(getattr(meal, "meal_type", None))].append(meal)

    rows: list[list[InlineKeyboardButton]] = []
    iso_date = target_date.isoformat()
    for meal_type in MEAL_TYPE_ORDER:
        meal_group = grouped.get(meal_type, [])
        if not meal_group:
            continue
        meal_ui = MEAL_UI.get(meal_type, MEAL_UI["snack"])
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"➕ {meal_ui['title']}",
                    callback_data=f"add_meal:{meal_type}:{iso_date}",
                ),
                InlineKeyboardButton(
                    text="✏️ Редактировать",
                    callback_data=f"edit_meal:{meal_type}:{iso_date}",
                ),
                InlineKeyboardButton(
                    text="🗑 Очистить",
                    callback_data=f"clear_meal:{meal_type}:{iso_date}",
                ),
            ]
        )

    if include_back:
        rows.append(
            [
                InlineKeyboardButton(
                    text="⬅️ Назад к календарю",
                    callback_data=f"meal_cal_back:{target_date.year}-{target_date.month:02d}",
                )
            ]
        )
    
    return InlineKeyboardMarkup(inline_keyboard=rows)


def build_kbju_day_actions_keyboard(target_date: date) -> InlineKeyboardMarkup:
    """Строит клавиатуру действий для дня в календаре КБЖУ."""
    keyboard: list[list[InlineKeyboardButton]] = [
        [
            InlineKeyboardButton(
                text="➕ Добавить приём",
                callback_data=f"meal_cal_add:{target_date.isoformat()}",
            ),
        ],
        [
            InlineKeyboardButton(
                text="⬅️ Назад к календарю",
                callback_data=f"meal_cal_back:{target_date.year}-{target_date.month:02d}",
            ),
        ],
    ]
    return InlineKeyboardMarkup(inline_keyboard=keyboard)
