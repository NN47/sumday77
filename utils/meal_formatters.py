"""Функции форматирования для приёмов пищи."""
import logging
import json
import html
import re
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


_DIARY_NAME_CONNECTORS = {"с", "со", "без", "для", "из", "в", "во", "на", "при"}
_DIARY_TECHNICAL_MODIFIERS = {
    "глазированный",
    "глазированная",
    "глазированное",
    "глазированные",
    "неглазированный",
    "неглазированная",
    "неглазированное",
    "неглазированные",
    "хрустящий",
    "хрустящая",
    "хрустящее",
    "хрустящие",
    "пищевой",
    "пищевая",
    "пищевое",
    "пищевые",
}
_RUSSIAN_ADJECTIVE_ENDINGS = (
    "ый",
    "ий",
    "ой",
    "ая",
    "яя",
    "ое",
    "ее",
    "ые",
    "ие",
    "ого",
    "его",
    "ому",
    "ему",
    "ым",
    "им",
    "ую",
    "юю",
    "ых",
    "их",
    "ыми",
    "ими",
)
_QUOTED_PRODUCT_PART_RE = re.compile(r"[«\"]\s*([^»\"]+?)\s*[»\"]")
_PRODUCT_QUANTITY_SUFFIX_RE = re.compile(
    r"(?:[,;]?\s*[-–—]?\s*)\b\d+(?:[.,]\d+)?\s*"
    r"(?:г|кг|мл|л|шт\.?|штук(?:а|и)?|порц(?:ия|ии|ий))\b.*$",
    re.IGNORECASE,
)


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


def _humanize_quoted_product_part(value: str) -> str:
    clean = " ".join(value.split()).strip(" ,.;:-–—")
    if clean.isupper():
        return clean.capitalize()
    return clean


def _looks_like_product_adjective(value: str) -> bool:
    normalized = value.casefold().strip(" ,.;:-–—")
    return any(normalized.endswith(ending) for ending in _RUSSIAN_ADJECTIVE_ENDINGS)


def format_diary_product_name(name: object, *, include_variant: bool = False) -> str:
    """Возвращает короткое человекочитаемое имя только для сводки дневника."""
    source = " ".join(_safe_product_name(name).split())
    source = _PRODUCT_QUANTITY_SUFFIX_RE.sub("", source).strip(" ,.;:-–—")
    quoted_match = _QUOTED_PRODUCT_PART_RE.search(source)
    base_source = source[: quoted_match.start()] if quoted_match else source
    raw_tokens = [token.strip(" ,.;:()[]{}") for token in base_source.split()]
    raw_tokens = [token for token in raw_tokens if token]

    if not include_variant:
        for index, token in enumerate(raw_tokens[1:], start=1):
            if token.casefold() in _DIARY_NAME_CONNECTORS:
                raw_tokens = raw_tokens[:index]
                break

    meaningful_tokens = [
        token
        for token in raw_tokens
        if token.casefold() not in _DIARY_TECHNICAL_MODIFIERS
    ]
    max_words = 6 if include_variant else 4
    base = " ".join(meaningful_tokens[:max_words]).casefold().strip()

    quoted = _humanize_quoted_product_part(quoted_match.group(1)) if quoted_match else ""
    should_include_quote = bool(
        quoted and (include_variant or not _looks_like_product_adjective(quoted))
    )
    if should_include_quote:
        return f"{base} «{quoted}»".strip()
    return base or quoted or "продукт"


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

    short_names = [format_diary_product_name(name) for name in names]
    positions_by_name: dict[str, list[int]] = defaultdict(list)
    for index, short_name in enumerate(short_names):
        positions_by_name[short_name.casefold()].append(index)

    for positions in positions_by_name.values():
        if len(positions) <= 1:
            continue
        for position in positions:
            short_names[position] = format_diary_product_name(names[position], include_variant=True)

    visible: list[str] = []
    for index, short_name in enumerate(short_names):
        escaped = html.escape(short_name)
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
    return "\n\n".join(_format_meal_edit_detail_blocks(meal_type, products, totals=totals))


def _format_meal_edit_detail_blocks(
    meal_type: str,
    products: list[dict],
    totals: dict | None = None,
) -> list[str]:
    """Строит независимые HTML-блоки, которые можно безопасно разбивать по сообщениям."""
    meal_ui = MEAL_UI.get(normalize_meal_type(meal_type), MEAL_UI["snack"])
    normalized_totals = _normalize_totals(totals) if totals is not None else _collect_product_totals(products)
    blocks = [f"<b>✏️ {meal_ui['title']} — выберите продукт для редактирования</b>"]

    for index, product in enumerate(products, start=1):
        name = html.escape(extract_product_name(product))
        grams = extract_product_weight(product)
        calories, protein, fat, carbs = extract_product_macros(product)
        lines = [
            f"{format_emoji_number(index)} <b>{name}</b> — {grams:.0f} г",
            f"{calories:.0f} ккал · Б {protein:.1f} / Ж {fat:.1f} / У {carbs:.1f}",
        ]
        if bool(product.get("is_manually_corrected")):
            lines.append("✏️ КБЖУ скорректированы вручную")
        blocks.append("\n".join(lines))

    blocks.append(
        f"<b>Итого: {normalized_totals['calories']:.0f} ккал · "
        f"Б {normalized_totals['protein']:.1f} · "
        f"Ж {normalized_totals['fat']:.1f} · "
        f"У {normalized_totals['carbs']:.1f}</b>"
    )
    return blocks


def _telegram_text_units(text: str) -> int:
    """Считает длину текста в UTF-16 code units, как Telegram Bot API."""
    return len(text.encode("utf-16-le")) // 2


def _split_plain_text_for_html(text: str, limit: int) -> list[str]:
    """Разбивает plain text так, чтобы его HTML-escaped части также укладывались в лимит."""
    chunks: list[str] = []
    current: list[str] = []
    current_units = 0
    for character in text:
        escaped_character = html.escape(character)
        character_units = _telegram_text_units(escaped_character)
        if current and current_units + character_units > limit:
            chunks.append(html.escape("".join(current)))
            current = []
            current_units = 0
        current.append(character)
        current_units += character_units
    if current:
        chunks.append(html.escape("".join(current)))
    return chunks


def format_meal_edit_chunks(
    meal_type: str,
    products: list[dict],
    totals: dict | None = None,
    *,
    limit: int = TELEGRAM_SAFE_TEXT_LIMIT,
) -> list[str]:
    """Разбивает подробный HTML edit-view только между валидными блоками."""
    if limit <= 0:
        raise ValueError("limit must be positive")

    safe_blocks: list[str] = []
    for block in _format_meal_edit_detail_blocks(meal_type, products, totals=totals):
        if _telegram_text_units(block) <= limit:
            safe_blocks.append(block)
            continue
        plain_block = html.unescape(re.sub(r"</?b>", "", block))
        safe_blocks.extend(_split_plain_text_for_html(plain_block, limit))

    chunks: list[str] = []
    current = ""
    for block in safe_blocks:
        candidate = f"{current}\n\n{block}" if current else block
        if current and _telegram_text_units(candidate) > limit:
            chunks.append(current)
            current = block
        else:
            current = candidate
    if current:
        chunks.append(current)
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
        f"<b>Б {totals['protein']:.1f} · Ж {totals['fat']:.1f} · У {totals['carbs']:.1f}</b>",
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
