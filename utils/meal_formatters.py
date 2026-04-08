"""Функции форматирования для приёмов пищи."""
import logging
import json
import html
from datetime import date
from collections import defaultdict
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from database.models import Meal
from utils.emoji_map import EMOJI_MAP
from utils.meal_types import MEAL_TYPE_ORDER, display_meal_type, normalize_meal_type

logger = logging.getLogger(__name__)


def format_today_meals(
    meals: list[Meal],
    daily_totals: dict,
    day_str: str,
    include_date_header: bool = True,
) -> str:
    """Форматирует дневник питания с группировкой по приёмам пищи."""
    lines: list[str] = []
    if include_date_header:
        lines.append(f"📅 Дневник питания за {day_str}\n")

    grouped: dict[str, list[Meal]] = defaultdict(list)
    for meal in meals:
        grouped[normalize_meal_type(getattr(meal, "meal_type", None))].append(meal)

    for meal_type in MEAL_TYPE_ORDER:
        meal_group = grouped.get(meal_type, [])
        if not meal_group:
            continue
        lines.append(display_meal_type(meal_type))
        meal_totals = {"calories": 0.0, "protein": 0.0, "fat": 0.0, "carbs": 0.0}
        for meal in meal_group:
            for product_line in _extract_product_lines(meal):
                lines.append(product_line)
            meal_totals["calories"] += float(getattr(meal, "calories", 0) or 0)
            meal_totals["protein"] += float(getattr(meal, "protein", 0) or 0)
            meal_totals["fat"] += float(getattr(meal, "fat", 0) or 0)
            meal_totals["carbs"] += float(getattr(meal, "carbs", 0) or 0)

        title = display_meal_type(meal_type).split(" ", 1)[1].lower()
        lines.append(f"\nИтого {title}:")
        lines.append(f"{EMOJI_MAP['calories']} {meal_totals['calories']:.0f} ккал")
        lines.append(f"{EMOJI_MAP['protein']} {meal_totals['protein']:.1f} г")
        lines.append(f"{EMOJI_MAP['fat']} {meal_totals['fat']:.1f} г")
        lines.append(f"{EMOJI_MAP['carbs']} {meal_totals['carbs']:.1f} г")
        lines.append("")

    lines.append("📊 Итого за день:")
    lines.append(f"{EMOJI_MAP['calories']} Калории: {daily_totals.get('calories', 0):.0f} ккал")
    lines.append(
        f"{EMOJI_MAP['protein']} Белки: {daily_totals.get('protein_g', daily_totals.get('protein', 0)):.1f} г"
    )
    lines.append(f"{EMOJI_MAP['fat']} Жиры: {daily_totals.get('fat_total_g', daily_totals.get('fat', 0)):.1f} г")
    lines.append(
        f"{EMOJI_MAP['carbs']} Углеводы: {daily_totals.get('carbohydrates_total_g', daily_totals.get('carbs', 0)):.1f} г"
    )
    
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
            f"• {html.escape(fallback_name)} — {float(getattr(meal, 'calories', 0) or 0):.0f} ккал "
            f"(Б {float(getattr(meal, 'protein', 0) or 0):.1f} / "
            f"Ж {float(getattr(meal, 'fat', 0) or 0):.1f} / "
            f"У {float(getattr(meal, 'carbs', 0) or 0):.1f})"
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
                f"• {html.escape(name)} ({grams:.0f} г) — {cal:.0f} ккал "
                f"(Б {prot:.1f} / Ж {fat:.1f} / У {carb:.1f})"
            )
        else:
            lines.append(
                f"• {html.escape(name)} — {cal:.0f} ккал (Б {prot:.1f} / Ж {fat:.1f} / У {carb:.1f})"
            )
    return lines


def build_meals_actions_keyboard(
    meals: list[Meal],
    target_date: date,
    include_back: bool = False,
) -> InlineKeyboardMarkup:
    """Строит клавиатуру с действиями для приёмов пищи."""
    rows: list[list[InlineKeyboardButton]] = []
    for idx, meal in enumerate(meals, start=1):
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"✏️ {idx}",
                    callback_data=f"meal_edit:{meal.id}:{target_date.isoformat()}",
                ),
                InlineKeyboardButton(
                    text=f"🗑 {idx}",
                    callback_data=f"meal_del:{meal.id}:{target_date.isoformat()}",
                ),
            ]
        )
    
    if include_back:
        rows.append(
            [
                InlineKeyboardButton(
                    text="➕ Добавить",
                    callback_data=f"meal_cal_add:{target_date.isoformat()}",
                )
            ]
        )
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
