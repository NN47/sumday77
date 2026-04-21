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

logger = logging.getLogger(__name__)


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
            lines.append("⸻")
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


def _extract_product_lines(meal: Meal) -> list[str]:
    """Извлекает строки продуктов из meals.products_json без служебного шума."""
    raw_products = getattr(meal, "products_json", None)
    products: list[dict] = []
    if raw_products:
        try:
            parsed = json.loads(raw_products)
            if isinstance(parsed, list):
                products = [item for item in parsed if isinstance(item, dict)]
        except Exception as e:
            logger.warning(f"Не смог распарсить products_json: {e}")

    if not products:
        fallback_name = _safe_product_name(
            getattr(meal, "description", None) or getattr(meal, "raw_query", None)
        )
        return [
            f"• <b>{html.escape(fallback_name)}</b>",
            f"<b>{float(getattr(meal, 'calories', 0) or 0):.0f} ккал</b> "
            f"<i>(Б {float(getattr(meal, 'protein', 0) or 0):.1f} / "
            f"Ж {float(getattr(meal, 'fat', 0) or 0):.1f} / "
            f"У {float(getattr(meal, 'carbs', 0) or 0):.1f})</i>",
        ]

    lines: list[str] = []
    for p in products:
        name = _safe_product_name(p.get("name_ru") or p.get("name"))
        cal = _safe_float(p.get("calories") or p.get("_calories") or p.get("kcal"))
        prot = _safe_float(p.get("protein_g") or p.get("_protein_g") or p.get("protein"))
        fat = _safe_float(p.get("fat_total_g") or p.get("_fat_total_g") or p.get("fat"))
        carb = _safe_float(
            p.get("carbohydrates_total_g") or p.get("_carbohydrates_total_g") or p.get("carbs")
        )
        grams = _safe_float(p.get("grams") or p.get("weight"))
        if grams > 0:
            lines.append(
                f"• <b>{html.escape(name)}</b> ({grams:.0f} г)"
            )
            lines.append(f"<b>{cal:.0f} ккал</b> <i>(Б {prot:.1f} / Ж {fat:.1f} / У {carb:.1f})</i>")
        else:
            lines.append(
                f"• <b>{html.escape(name)}</b>"
            )
            lines.append(f"<b>{cal:.0f} ккал</b> <i>(Б {prot:.1f} / Ж {fat:.1f} / У {carb:.1f})</i>")

        if bool(p.get("is_manually_corrected")):
            lines.append("✏️ <i>КБЖУ скорректированы вручную</i>")
    return lines


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
        "",
        f"🔥 <b>{totals['calories']:.0f} ккал</b>",
        f"💪 <b>Белки: {totals['protein']:.0f} г</b> 🥑 <b>Жиры: {totals['fat']:.0f} г</b> 🍩 <b>Углеводы: {totals['carbs']:.0f} г</b>",
    ]


def format_meal_block(meal_type: str, items: list[Meal]) -> list[str]:
    meal_ui = MEAL_UI.get(meal_type, MEAL_UI["snack"])
    totals = _collect_meal_totals(items)
    lines = [f"{meal_ui['emoji']} <b>{meal_ui['title']} • {totals['calories']:.0f} ккал</b>", ""]

    first_product = True
    for meal in items:
        product_lines = _extract_product_lines(meal)
        i = 0
        while i < len(product_lines):
            if not first_product:
                lines.append("")
            lines.append(product_lines[i])
            if i + 1 < len(product_lines):
                lines.append(product_lines[i + 1])
            i += 2
            while i < len(product_lines) and product_lines[i].startswith("✏️ "):
                lines.append(product_lines[i])
                i += 1
            first_product = False

    lines.append("")
    lines.append("---")
    lines.append("")
    lines.extend(format_meal_totals(meal_type, totals))
    return lines


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
        f"{label}: {current:.0f}/{target:.0f} {unit} ({percent}%)",
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
        f"🎯 Цель: {goal_label}",
        f"📊 Базовая норма: {base_calories_target:.0f} ккал",
        "",
    ]
    lines.extend(_build_goal_progress_line("🔥 Калории", calories_current, base_calories_target, "ккал"))
    lines.extend(_build_goal_progress_line("💪 Белки", protein_current, protein_target, "г"))
    lines.extend(_build_goal_progress_line("🥑 Жиры", fat_current, fat_target, "г"))
    lines.extend(_build_goal_progress_line("🍩 Углеводы", carbs_current, carbs_target, "г"))

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
