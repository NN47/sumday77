"""Обработчики для КБЖУ и питания."""
import asyncio
import logging
import json
import re
import math
import html
import secrets
from dataclasses import dataclass
from datetime import date
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove
from aiogram.exceptions import TelegramBadRequest
from utils.pagination import build_pagination_keyboard, clamp_page, total_pages_for
from typing import Iterable, Optional
from aiogram.fsm.context import FSMContext
from states.user_states import MealEntryStates
from utils.calendar_utils import show_calendar_back_button
from utils.keyboards import (
    MAIN_MENU_BUTTON_ALIASES,
    MEALS_BUTTON_TEXT,
    LEGACY_MEALS_BUTTON_TEXT,
    MEALS_BUTTON_ALIASES,
    KBJU_ADD_MEAL_BUTTON_ALIASES,
    FINISH_MEAL_BUTTON_TEXT,
    LEGACY_FINISH_MEAL_BUTTON_TEXT,
    main_menu,
    main_menu_button,
    kbju_menu,
    kbju_add_menu,
    kbju_add_method_back_menu,
    kbju_meal_type_menu,
    kbju_after_meal_menu,
    kbju_edit_type_menu,
    push_menu_stack,
)
from database.repositories import (
    AnalyticsRepository,
    DishRepository,
    MealRepository,
    MealSaveResult,
    MealSaveStatus,
    SavedProductRepository,
    WorkoutRepository,
)
from services.dish_service import (
    DishService,
    calculate_dish_totals,
    calculate_dish_weight,
    dish_to_snapshot,
    normalize_dish_display_name,
    scale_dish_snapshot,
)
from services.gemini_service import (
    gemini_service,
    GeminiServiceTemporaryUnavailableError,
    GeminiServiceQuotaError,
    GeminiServiceAuthError,
)
from services.openai_label_service import (
    openai_label_service,
    OpenAILabelServiceAPIError,
    OpenAILabelServiceConfigError,
    OpenAILabelServiceInvalidJSONError,
    OpenAILabelServiceTimeoutError,
)
from services.deepseek_service import (
    deepseek_service,
    DeepSeekServiceConfigError,
    DeepSeekServiceError,
)
from services.ai_food_parser import FoodAnalysisStatus, parse_kbju_json
from services.ai_usage_logger import log_ai_usage
from services.ai_quota_service import (
    AIFeature,
    AIAttemptLimitExceeded,
    AIGlobalLimitExceeded,
    AIOperationCooldown,
    AIOperationInProgress,
    AIQuotaAlreadyConsumed,
    AIQuotaExceeded,
    QuotaReservation,
    ai_quota_service,
    build_quota_request_id,
    format_free_ai_status_block,
    validate_ai_image,
)
from services.photo_food_validator import validate_photo_food_payload
from services.daily_analysis_preflight_service import return_to_active_daily_preflight
from utils.validators import parse_date
from utils.log_sanitizer import safe_exception_summary
from utils.sensitive_meal_text import check_sensitive_food_name, check_sensitive_meal_text
from utils.meal_formatters import (
    extract_product_macros as extract_meal_product_macros,
    extract_product_name as extract_meal_product_name,
    extract_product_weight as extract_meal_product_weight,
    format_emoji_number as format_meal_emoji_number,
    format_meal_details,
    format_meal_edit_chunks,
)
from datetime import datetime
from utils.meal_types import (
    MealType,
    MEAL_TYPE_ORDER,
    normalize_meal_type,
    display_meal_type,
    display_meal_type_with_bold_name,
)
from utils.emoji_map import EMOJI_MAP
from database.repositories.meal_completion_comment_repository import MealCompletionCommentRepository
from config import DEEPSEEK_MODEL

logger = logging.getLogger(__name__)

router = Router()

MEAL_SAVE_TOKEN_BYTES = 16
MEAL_SAVE_TOKEN_LENGTH = 22
MEAL_SAVE_CALLBACK_TOKEN_LENGTH = 12
STALE_MEAL_SAVE_TEXT = "Этот приём пищи уже сохранён или запрос устарел."


MEAL_AI_QUOTA_ALTERNATIVES = (
    (AIFeature.MEAL_TEXT, "ai", "📝 Текстовый AI-анализ"),
    (AIFeature.MEAL_PHOTO, "photo", "📷 Анализ еды по фото"),
    (AIFeature.NUTRITION_LABEL, "label", "📋 Анализ этикетки"),
)


def _build_quota_alternatives_menu(
    meal_type: str,
    feature: AIFeature | None = None,
    available_ai_quotas: dict[AIFeature, int] | None = None,
) -> InlineKeyboardMarkup:
    """Контекстные способы продолжить после исчерпания одной AI-квоты."""
    normalized = normalize_meal_type(meal_type, fallback=MealType.SNACK.value)
    rows = []
    for alternative_feature, action, title in MEAL_AI_QUOTA_ALTERNATIVES:
        remaining = int((available_ai_quotas or {}).get(alternative_feature, 0))
        if alternative_feature is feature or remaining <= 0:
            continue
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"{title} · осталось {remaining}",
                    callback_data=f"quota_alt:{action}:{normalized}",
                )
            ]
        )
    rows.append(
        [
            InlineKeyboardButton(
                text="📦 Мои продукты",
                callback_data=f"quota_alt:products:{normalized}",
            )
        ]
    )
    if feature is not AIFeature.NUTRITION_LABEL:
        rows.append(
            [
                InlineKeyboardButton(
                    text="🍽 Мои блюда",
                    callback_data=f"quota_alt:dishes:{normalized}",
                )
            ]
        )
    rows.extend(
        [
            [
                InlineKeyboardButton(
                    text="✍️ Внести вручную",
                    callback_data=f"quota_alt:manual:{normalized}",
                )
            ],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data=f"quota_alt:back:{normalized}")],
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _quota_limit_text(
    feature: AIFeature,
    available_ai_quotas: dict[AIFeature, int] | None = None,
) -> str:
    titles = {
        AIFeature.MEAL_TEXT: "текстового AI-анализа",
        AIFeature.MEAL_PHOTO: "анализа еды по фото",
        AIFeature.NUTRITION_LABEL: "анализа этикеток",
    }
    message = (
        f"Лимит бесплатного {titles.get(feature, 'AI-анализа')} на сегодня закончился.\n"
        "Он обновится в 02:00 МСК."
    )
    if any(remaining > 0 for remaining in (available_ai_quotas or {}).values()):
        return message + "\nДругие способы AI-анализа ещё доступны — выбери подходящий ниже."
    return message + "\nСейчас можно добавить продукт вручную или выбрать его из сохранённых."


def _available_meal_ai_quotas(
    user_id: str,
    exhausted_feature: AIFeature,
    *,
    exclude_same_attempt_group: bool = False,
) -> dict[AIFeature, int]:
    """Возвращает остатки других пользовательских AI-квот для меню альтернатив."""
    features = tuple(item[0] for item in MEAL_AI_QUOTA_ALTERNATIVES)
    try:
        statuses = ai_quota_service.get_statuses(user_id, features)
        blocked_attempt_group = None
        if exclude_same_attempt_group:
            current_status = statuses.get(exhausted_feature)
            if current_status is not None:
                blocked_attempt_group = ai_quota_service.entitlement(
                    current_status.plan_key,
                    exhausted_feature,
                ).attempt_group

        available = {}
        for alternative_feature, status in statuses.items():
            if alternative_feature is exhausted_feature or status.remaining <= 0:
                continue
            if blocked_attempt_group is not None:
                alternative_group = ai_quota_service.entitlement(
                    status.plan_key,
                    alternative_feature,
                ).attempt_group
                if alternative_group == blocked_attempt_group:
                    continue
            available[alternative_feature] = status.remaining
        return available
    except Exception as error:
        logger.warning(
            "Failed to load alternative AI quotas feature=%s error_type=%s",
            exhausted_feature.value,
            safe_exception_summary(error),
        )
        return {}


def _attempt_limit_text(
    feature: AIFeature,
    available_ai_quotas: dict[AIFeature, int] | None = None,
) -> str:
    if feature in {AIFeature.MEAL_PHOTO, AIFeature.NUTRITION_LABEL}:
        message = (
            "Общий дневной запас запросов для фото еды и этикеток закончился.\n"
            "Он расходуется при каждой отправке изображения, даже если AI не смог его распознать. "
            "Поэтому в отдельных лимитах фото или этикеток ещё может оставаться запас.\n"
            "Новые запросы изображений будут доступны после 02:00 МСК."
        )
    else:
        message = (
            "Общий дневной запас попыток текстового AI-анализа закончился.\n"
            "Новые запросы будут доступны после 02:00 МСК."
        )
    if any(remaining > 0 for remaining in (available_ai_quotas or {}).values()):
        return message + "\nДругие способы AI-анализа ещё доступны — выбери подходящий ниже."
    return message + "\nМожно выбрать сохранённый продукт или внести данные вручную."


async def _reserve_meal_ai_quota(
    message: Message,
    *,
    user_id: str,
    feature: AIFeature,
    request_id: str,
    meal_type: str,
) -> QuotaReservation | None:
    """Единообразно резервирует AI-квоту и показывает контролируемый отказ."""
    try:
        return ai_quota_service.reserve(user_id, feature, request_id)
    except AIQuotaExceeded:
        available_ai_quotas = _available_meal_ai_quotas(user_id, feature)
        logger.info("AI quota denied feature=%s reason=feature_limit", feature.value)
        await message.answer(
            _quota_limit_text(feature, available_ai_quotas),
            reply_markup=_build_quota_alternatives_menu(
                meal_type,
                feature,
                available_ai_quotas,
            ),
        )
    except AIAttemptLimitExceeded:
        available_ai_quotas = _available_meal_ai_quotas(
            user_id,
            feature,
            exclude_same_attempt_group=True,
        )
        logger.info("AI quota denied feature=%s reason=attempt_limit", feature.value)
        await message.answer(
            _attempt_limit_text(feature, available_ai_quotas),
            reply_markup=_build_quota_alternatives_menu(
                meal_type,
                feature,
                available_ai_quotas,
            ),
        )
    except AIGlobalLimitExceeded:
        logger.info("AI quota denied feature=%s reason=global_limit", feature.value)
        await message.answer(
            "AI-функции временно приостановлены из-за общего дневного предела. "
            "Дневник и ручное добавление продолжают работать.",
            reply_markup=_build_quota_alternatives_menu(meal_type, feature),
        )
    except AIOperationInProgress:
        logger.info("AI quota denied feature=%s reason=operation_in_progress", feature.value)
        await message.answer("Предыдущий AI-запрос ещё обрабатывается. Дождись результата.")
    except AIOperationCooldown:
        logger.info("AI quota denied feature=%s reason=cooldown", feature.value)
        await message.answer("Подожди несколько секунд перед следующим AI-запросом.")
    except AIQuotaAlreadyConsumed:
        await message.answer("Этот AI-запрос уже обработан. Новый лимит не списан.")
    return None


def _new_meal_save_token() -> str:
    """Returns an opaque 128-bit identifier without user-derived data."""
    return secrets.token_urlsafe(MEAL_SAVE_TOKEN_BYTES)


def _is_valid_meal_save_token(value: object) -> bool:
    return bool(
        isinstance(value, str)
        and len(value) == MEAL_SAVE_TOKEN_LENGTH
        and re.fullmatch(r"[A-Za-z0-9_-]+", value)
    )


def _failed_meal_save_result(error_type: str) -> MealSaveResult:
    return MealSaveResult(MealSaveStatus.FAILED, error_type=error_type)

MEAL_TYPE_BUTTONS = {
    "🍳 Завтрак": MealType.BREAKFAST.value,
    "🍲 Обед": MealType.LUNCH.value,
    "🍽 Ужин": MealType.DINNER.value,
    "🍎 Перекус": MealType.SNACK.value,
}

BACK_BUTTON_TEXTS = {"⬅️ Назад", "↩️ Назад", "Назад"}
MEAL_FINISH_BUTTON_TEXTS = {FINISH_MEAL_BUTTON_TEXT, LEGACY_FINISH_MEAL_BUTTON_TEXT}
CARBS_EMOJI = EMOJI_MAP["carbs"]
SENSITIVE_MEAL_INPUT_REJECTED_TEXT = (
    "⚠️ В сообщении обнаружена информация, не относящаяся к описанию еды.\n\n"
    "Укажите только продукты, блюда и примерное количество.\n\n"
    "Например: <code>колбаса 200 г, хлеб 30 г</code>"
)
SENSITIVE_FOOD_NAME_REJECTED_TEXT = (
    "⚠️ Это поле предназначено только для названия продукта или блюда.\n\n"
    "Введите короткое название, например: <code>гречка</code>."
)
NO_FOOD_RECOGNIZED_TEXT = (
    "⚠️ Не удалось распознать продукты или блюда.\n\n"
    "Укажите, что вы съели и примерное количество.\n\n"
    "Например: <code>карпаччо 150 г, хлеб 30 г</code>"
)
MAX_MEAL_TEXT_LENGTH = 2_000
MAX_AI_MEAL_QUERY_SUMMARY_LENGTH = 500
MEAL_TEXT_TOO_LONG_TEXT = (
    "⚠️ Сообщение слишком длинное.\n\n"
    "Укажите только продукты, блюда и примерное количество."
)
PHOTO_COMMENT_TOO_LONG_TEXT = (
    "⚠️ Сообщение слишком длинное.\n\n"
    "Укажите только краткое уточнение о продуктах или блюде на фото."
)


async def _reject_sensitive_food_name(message: Message, value: str, *, context: str) -> bool:
    """Reject a risky user-defined food name without retaining or logging its value."""
    sensitive_check = check_sensitive_food_name(value)
    if not sensitive_check.is_sensitive:
        return False
    logger.warning(
        "Sensitive food name rejected context=%s reason=%s",
        context,
        sensitive_check.reason.value if sensitive_check.reason else "unknown",
    )
    await message.answer(SENSITIVE_FOOD_NAME_REJECTED_TEXT, parse_mode="HTML")
    return True


MEAL_COMPLETION_COMMENT_SYSTEM_PROMPT = """Ты — спортивный друг пользователя, который хорошо разбирается в питании и помогает в Telegram-боте Sumday77.

Ты не диетолог, не врач, не психолог и не поэт. Пользователь завершил или отредактировал один конкретный приём пищи и как будто показал его тебе со словами: «Ну как тебе?» Ответь так, как ответил бы опытный человек, который давно занимается спортом: просто, по-человечески, конкретно, дружелюбно, позитивно, без лекций и без пустой болтовни.

Это не полный анализ дня, не медицинская рекомендация, не отчёт диетолога и не калькулятор КБЖУ. Стиль подробного AI-анализа дня не меняй: здесь нужен только короткий meal_completion_comment.

# Главный принцип

Комментарий должен быть полезным, а не просто красивым, эмоциональным или позитивным. После прочтения пользователь должен понять хотя бы одну конкретную вещь: белка достаточно или мало; приём пищи получился очень калорийным; жиров уже набралось много; овощей мало; перекус слишком лёгкий; десерт спокойно вписался; сытости должно хватить надолго; осталось много калорий на день; следующие приёмы пищи лучше сделать легче; после тренировки такой приём пищи подходит хорошо; ничего компенсировать не нужно.

Если в комментарии нет конкретной пользы, перепиши его.

# Как выбирать мысль

Всегда сначала ищи, что получилось удачно, и опирайся только на реальные данные. Реальным плюсом может быть хороший белок, овощи, фрукты, умеренная порция, удачный перекус, высокая сытость, еда после тренировки, небольшой десерт, попадание в дневную норму, калорийный приём пищи с заметным вкладом в белок или честно записанный приём пищи.

После плюса при необходимости дай одну короткую практическую рекомендацию. Максимум: один сильный момент и одна точка роста. Не пытайся одновременно разобрать всё.

Выбери только одну главную мысль: что здесь самое сильное; что больше всего влияет на калорийность; хватит ли сытости; что учесть дальше; подходит ли это после тренировки; хорошо ли набран белок; что можно было бы добавить.

# Конкретика вместо абстракций

Пиши как живой человек, будто говоришь с другом. Хорошие направления фраз: «Белка здесь почти 30 г — для обеда это хороший результат», «80 ккал — вообще небольшой перекус, день он точно не испортит», «Жиров уже набралось много, поэтому дальше можно выбрать что-то полегче», «Овощей сюда можно было бы добавить побольше — сытости было бы больше почти без лишних калорий», «Чизкейк спокойно вписался, ничего специально компенсировать не нужно», «После такого завтрака вряд ли быстро захочется есть», «Такой перекус скорее про вкус, чем про сытость».

Не используй без необходимости абстрактные слова и выражения: «баланс», «гибкий подход», «устойчивое решение», «осознанность», «правильная стратегия», «пищевое поведение», «философия», «ритуал», «якорь комфорта», «нотка игры», «гастрономическое путешествие», «приём пищи для удовольствия», «решение, которое помогает двигаться к цели», «не всё или ничего». Такие выражения допустимы только в редких случаях, когда без них нельзя передать смысл; по умолчанию используй конкретный человеческий язык.

Не философствуй о еде. Не пиши длинные рассуждения о настроении, уюте, ритуалах, отношении к еде, психологии запретов, долгосрочной устойчивости или честности перед собой. Если пользователь просто съел чизкейк, не объясняй философию питания: скажи, что именно дал чизкейк по калориям/жирам, что получилось хорошо и что можно сделать дальше.

# Честность без чувства вины

Если есть проблема, не прячь её за красивыми словами. Говори честно, но дружелюбно: калорий много; жиров уже набралось много; белка маловато; овощей почти нет; перекус лёгкий и может не дать сытости. Не используй позитивность как способ скрыть факт.

Sumday77 всегда на стороне пользователя. Никогда не создавай чувство вины, стыда, безнадёжности или мысль «я всё испортил». Не пиши и не подразумевай: «ты всё испортил», «день потерян», «баланс уже не поправить», «теперь придётся компенсировать», «нужно отработать», «лучше больше ничего не есть», «завтра начнёшь сначала», «сегодня уже поздно», «ты сорвался», «ты провалил день».

Лучше формулируй так: «Ничего специально компенсировать не нужно», «Следующий приём пищи можно сделать легче», «Один плотный приём пищи не определяет весь день», «Дальше можно просто продолжить в обычном режиме», «День ещё можно спокойно удержать в рамках цели».

Не советуй голодать, пропускать следующий приём пищи, резко урезать калории или отрабатывать еду тренировкой. Сладости не критикуй автоматически: если десерт вписался — скажи это прямо; если сладкого много — без стыда предложи дальше сделать акцент на белке, овощах или более лёгкой еде.

# Использование данных

Учитывай тип приёма пищи, состав продуктов, цель пользователя, уже съеденное за день, оставшийся запас по калориям и КБЖУ, активность текущего дня, если она есть, и предыдущие короткие комментарии сегодня, чтобы не повторять один и тот же акцент.

Комментарий должен относиться именно к завершённому или отредактированному приёму пищи. Если пользователь редактирует старый приём пищи, не привязывай ответ к моменту генерации.

Не предполагай время употребления еды по времени генерации ответа. Не используй выражения: «сейчас», «вечером», «на ночь», «перед сном», «утром», «в начале дня», «завершить день», «закончить вечер» и близкие формулировки, если эта информация не передана явно в данных приёма пищи. Ориентируйся только на данные приёма пищи, а не на текущее время генерации ответа.

Можно использовать одну или две важные цифры, если они реально помогают основной мысли: «Белка почти 30 г», «Это около половины дневной нормы калорий», «Жиров уже 83% от дневной цели», «Перекус всего на 80 ккал». Не перечисляй все КБЖУ подряд и не пиши проценты ради процентов.

# Формат ответа

1. Первая строка — короткий заголовок с эмодзи.
2. Заголовок выдели жирным через одну пару HTML-тегов <b>...</b>.
3. Затем пустая строка.
4. Далее максимум два коротких предложения.

Общий объём — 120–300 символов. Абсолютный максимум — 350 символов. Жирным выделяй только заголовок. Основной текст всегда обычным шрифтом.

Прямые правила для meal_completion_comment:
- Ответь коротко: один заголовок и максимум два коротких предложения.
- Формула ответа: реальный плюс → конкретное наблюдение → при необходимости один простой совет.
- Выбери только одну главную мысль.
- Не пиши художественный текст ради красоты.
- Не приписывай пользователю чувства, привычки или мотивы, которых нет в данных.
- Не начинай с сухого перечисления калорий, белков, жиров и углеводов.
- Жирным выделяй только заголовок. Основной текст всегда обычный.
- Общая длина ответа — не более 350 символов. Целевой объём — 120–300 символов.
- Если последнее предложение можно удалить без потери смысла, удали его.

Перед отправкой проверь: есть ли конкретная польза; понятно ли, что получилось хорошо; есть ли одна главная мысль; нет ли абстрактных слов вместо фактов; звучит ли это как живой спортивный друг; нет ли лекции, философии или чувства вины; жирным ли выделен только заголовок; уложился ли ответ в два предложения и 350 символов. Если комментарий звучит как рекламный текст, психологический пост или диетологическая статья — перепиши проще.

Не используй Markdown. Не используй символы **, __, # или списки Markdown. Используй только HTML-разметку Telegram. Разрешён только один тег: <b>...</b>, и только в первой строке заголовка. После закрывающего тега </b> весь основной текст должен быть обычным.
"""

MEAL_COMMENT_FALLBACK_TEXT = "✅ Приём пищи завершён."


def _build_local_meal_completion_text(user_id: str, target_date: date) -> str:
    """Недорогой итог, который не блокирует завершение при исчерпанной AI-квоте."""
    totals = MealRepository.get_daily_totals(user_id, target_date)
    settings = MealRepository.get_kbju_settings(user_id)
    calories = float(totals.get("calories", 0) or 0)
    if settings and float(settings.calories or 0) > 0:
        return (
            "✅ <b>Приём пищи завершён.</b>\n\n"
            f"За день набрано {calories:.0f} из {float(settings.calories):.0f} ккал."
        )
    return "✅ <b>Приём пищи завершён.</b>"

ADD_METHOD_TEXTS = {
    "ai": "📝 Ввести приём пищи текстом (AI-анализ)",
    "deepseek": "🤖 Ввести приём пищи через DeepSeek",
    "photo": "📷 Анализ еды по фото",
    "photo_openai": "🧪 Анализ еды OpenAI",
    "label": "📋 Анализ этикетки",
    "label_openai": "🧪 Анализ этикетки OpenAI",
    "custom": "✍️ Внести вручную",
}


def _get_selected_food_diary_dates(bot) -> dict:
    """Хранит выбранную дату дневника питания для reply-кнопок пользователя."""
    if not hasattr(bot, "selected_food_diary_dates"):
        bot.selected_food_diary_dates = {}
    return bot.selected_food_diary_dates


def _set_selected_food_diary_date(bot, user_id: str, target_date: date) -> None:
    _get_selected_food_diary_dates(bot)[user_id] = target_date.isoformat()


def _get_selected_food_diary_date(bot, user_id: str) -> date:
    selected_date = _get_selected_food_diary_dates(bot).get(user_id)
    if selected_date:
        try:
            return date.fromisoformat(selected_date)
        except ValueError:
            pass
    return date.today()


def _format_kbju_summary_block(totals: dict, *, bold_values: bool = False) -> str:
    """Форматирует блок КБЖУ с акцентом на названиях показателей."""
    value_template = "<b>{value}</b>" if bold_values else "{value}"
    calories = value_template.format(value=f"{float(totals.get('calories', 0) or 0):.0f} ккал")
    protein = value_template.format(value=f"{float(totals.get('protein', 0) or 0):.1f} г")
    fat = value_template.format(value=f"{float(totals.get('fat', 0) or 0):.1f} г")
    carbs = value_template.format(value=f"{float(totals.get('carbs', 0) or 0):.1f} г")
    return (
        f"🔥 <b>Калории:</b> {calories}\n"
        f"🥩 <b>Белки:</b> {protein}\n"
        f"🥑 <b>Жиры:</b> {fat}\n"
        f"🍚 <b>Углеводы:</b> {carbs}"
    )


def _format_ai_food_analysis_message(title: str, items: list, totals: dict, *, saved: bool = True) -> str:
    """Форматирует красивое отдельное сообщение результата AI-анализа продукта."""
    lines = [f"<b>{html.escape(title)}</b>", "", "📌 <b>Распознанные продукты:</b>"]
    if items:
        for item in items:
            item_name = html.escape(str(item.get("name") or "продукт"))
            grams = float(item.get("grams", 0) or 0)
            kcal = float(item.get("kcal", 0) or 0)
            protein = float(item.get("protein", 0) or 0)
            fat = float(item.get("fat", 0) or 0)
            carbs = float(item.get("carbs", 0) or 0)
            lines.append(
                f"• <b>{item_name}</b> ({grams:.0f} г) — "
                f"<b>{kcal:.0f} ккал</b> "
                f"<i>(Б {protein:.1f} / Ж {fat:.1f} / У {carbs:.1f})</i>"
            )
    else:
        lines.append("• Не удалось выделить продукты отдельно — использую общий итог.")

    lines.extend(
        [
            "",
            "📊 <b>Итого:</b>",
            _format_kbju_summary_block(totals, bold_values=True),
        ]
    )
    if saved:
        lines.extend(["", "✅ <b>Продукт сохранён.</b>"])
    else:
        lines.extend(["", "Проверьте данные перед сохранением."])
    return "\n".join(lines)


def _build_ai_meal_preview_inline_menu(save_token: str) -> InlineKeyboardMarkup:
    """Строит inline-кнопки предпросмотра текстового AI-анализа."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_ai_meal_draft"),
                InlineKeyboardButton(text="✏️ Редактировать", callback_data="edit_ai_meal_draft"),
            ],
            [
                InlineKeyboardButton(
                    text="✅ Сохранить",
                    callback_data=f"save_ai_meal_draft:{save_token}",
                )
            ],
        ]
    )


def _build_ai_meal_cancel_confirmation_menu() -> InlineKeyboardMarkup:
    """Строит безопасное подтверждение удаления текстового AI-черновика."""
    return InlineKeyboardMarkup(
        inline_keyboard=[[
            InlineKeyboardButton(text="✅ Да, отменить", callback_data="confirm_cancel_ai_meal_draft"),
            InlineKeyboardButton(text="↩️ Нет, вернуться", callback_data="back_to_ai_meal_draft"),
        ]]
    )


def _normalize_ai_items_for_edit(items: list | None) -> list[dict]:
    """Нормализует продукты текстового AI-анализа для черновика и редактора."""
    normalized = []
    for item in items or []:
        if not isinstance(item, dict):
            continue
        grams = _safe_float(item.get("grams") or item.get("weight") or item.get("amount_g"))
        calories = _safe_float(item.get("kcal") or item.get("calories"))
        protein = _safe_float(item.get("protein") or item.get("protein_g"))
        fat = _safe_float(item.get("fat") or item.get("fat_total_g"))
        carbs = _safe_float(item.get("carbs") or item.get("carbohydrates_total_g"))
        product = dict(item)
        product.update({
            "name": str(item.get("name") or item.get("title") or "Продукт"),
            "grams": grams,
            "kcal": calories,
            "calories": calories,
            "protein": protein,
            "protein_g": protein,
            "fat": fat,
            "fat_total_g": fat,
            "carbs": carbs,
            "carbohydrates_total_g": carbs,
        })
        if grams > 0:
            product.setdefault("calories_per_100g", (calories / grams) * 100 if calories else 0)
            product.setdefault("protein_per_100g", (protein / grams) * 100 if protein else 0)
            product.setdefault("fat_per_100g", (fat / grams) * 100 if fat else 0)
            product.setdefault("carbs_per_100g", (carbs / grams) * 100 if carbs else 0)
        normalized.append(product)
    return normalized


def _collect_ai_draft_totals(items: list[dict]) -> dict:
    totals, _ = _build_meal_update_payload(items)
    return {
        "calories": totals["calories"],
        "protein": totals["protein_g"],
        "fat": totals["fat_total_g"],
        "carbs": totals["carbohydrates_total_g"],
    }


def _build_ai_meal_query_summary(items: list[dict]) -> str:
    """Builds a bounded summary from validated product names, never raw input text."""
    summary = ", ".join(str(item.get("name") or "").strip() for item in items)
    return summary[:MAX_AI_MEAL_QUERY_SUMMARY_LENGTH] or "[AI-анализ]"


async def _send_ai_meal_preview(message: Message, state: FSMContext) -> None:
    """Показывает предпросмотр текстового AI-анализа без записи в дневник."""
    data = await state.get_data()
    draft = data.get("ai_pending_meal") or {}
    items = draft.get("items") or []
    title = draft.get("analysis_title") or "🧾 AI-анализ приёма пищи"
    totals = _collect_ai_draft_totals(items)
    save_token = draft.get("save_token")
    if not _is_valid_meal_save_token(save_token):
        save_token = _new_meal_save_token()
    await state.set_state(MealEntryStates.confirming_ai_meal)
    await state.update_data(
        ai_pending_meal={
            **draft,
            "items": items,
            "total": totals,
            "save_token": save_token,
        }
    )
    await message.answer(
        _format_ai_food_analysis_message(title, items, totals, saved=False),
        reply_markup=_build_ai_meal_preview_inline_menu(save_token),
        parse_mode="HTML",
    )


def _format_current_meal_after_save_message(meal_type: str, current_meal_items: list, entry_date: date) -> str:
    """Форматирует отдельное сообщение текущего состава приёма пищи после сохранения."""
    lines = [
        "🍱 <b>Уже в этом приёме пищи</b>",
        f"📅 <b>Дата:</b> {entry_date.strftime('%d.%m.%Y')}",
        "",
    ]
    if current_meal_items:
        lines.append(format_meal_details(meal_type, current_meal_items))
    else:
        lines.append(f"Пока в {display_meal_type(meal_type).lower()} нет сохранённых продуктов.")

    return "\n".join(lines)

def _format_label_result_header(source: str, product_name: str) -> str:
    """Форматирует первую строку результата анализа упаковки."""
    safe_product_name = html.escape(product_name or "Продукт")
    return f"📋 <b>Анализ этикетки:</b> {safe_product_name}\n"


def _format_label_weight_prompt(
    *,
    product_name: str,
    kcal_100g: float,
    protein_100g: float,
    fat_100g: float,
    carbs_100g: float,
    package_weight: float | None = None,
) -> str:
    """Форматирует сообщение с найденной на этикетке КБЖУ и вопросом о весе."""
    safe_product_name = html.escape(product_name or "Продукт")
    lines = [
        "✅ <b>Нашёл КБЖУ на этикетке!</b>",
        "",
        f"📦 <b>Продукт:</b> {safe_product_name}",
        "📊 <b>КБЖУ на 100 г:</b>",
        f"🔥 <b>Калории:</b> {kcal_100g:.0f} ккал",
        f"🥩 <b>Белки:</b> {protein_100g:.1f} г",
        f"🥑 <b>Жиры:</b> {fat_100g:.1f} г",
        f"🍚 <b>Углеводы:</b> {carbs_100g:.1f} г",
        "",
    ]

    if package_weight is not None and package_weight > 0:
        lines.append(f"📦 <b>В упаковке {package_weight:.0f} г, сколько Вы съели?</b>")
    else:
        lines.append("❓ <b>Вес в упаковке не найден, сколько вы съели?</b>")

    lines.append("Можешь выбрать кнопку или ввести вес вручную.")
    return "\n".join(lines)


LABEL_STANDARD_GRAM_OPTIONS = [10, 15, 20, 25, 30, 35, 40, 45, 50, 55, 60, 65, 70, 75, 80, 85, 90, 100, 150, 200, 250, 300, 350, 500]
LABEL_WEIGHT_ADJUSTMENTS = [1, 5, 10, 20, 50, 100]


def _safe_float(value, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _build_label_weight_input_menu(package_weight: float | None = None) -> ReplyKeyboardMarkup:
    """Строит меню выбора веса с весом упаковки отдельной первой кнопкой."""
    options = list(LABEL_STANDARD_GRAM_OPTIONS)
    package_weight_int = int(round(package_weight)) if package_weight and package_weight > 0 else None
    keyboard: list[list[KeyboardButton]] = []
    if package_weight_int and package_weight_int not in options:
        keyboard.append([KeyboardButton(text=str(package_weight_int))])

    for index in range(0, len(options), 4):
        keyboard.append([KeyboardButton(text=str(value)) for value in options[index:index + 4]])

    keyboard.append([KeyboardButton(text="⬅️ Назад"), main_menu_button])
    return ReplyKeyboardMarkup(keyboard=keyboard, resize_keyboard=True)


def _build_label_weight_confirm_menu(save_token: str) -> InlineKeyboardMarkup:
    """Строит единое inline-меню подтверждения результата анализа этикетки."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=f"+{step} г",
                    callback_data=f"label_wchg:{save_token}:{step}",
                )
                for step in LABEL_WEIGHT_ADJUSTMENTS
            ],
            [
                InlineKeyboardButton(
                    text=f"-{step} г",
                    callback_data=f"label_wchg:{save_token}:-{step}",
                )
                for step in LABEL_WEIGHT_ADJUSTMENTS
            ],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data=f"label_wback:{save_token}")],
            [
                InlineKeyboardButton(
                    text="✅ Сохранить",
                    callback_data=f"save_label_analysis:{save_token}",
                )
            ],
        ]
    )




PHOTO_WEIGHT_ADJUSTMENTS = LABEL_WEIGHT_ADJUSTMENTS


_NUMBER_EMOJIS = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟"]


def _number_emoji(index: int) -> str:
    return _NUMBER_EMOJIS[index] if 0 <= index < len(_NUMBER_EMOJIS) else f"{index + 1}."


def _short_product_button_name(name: str) -> str:
    clean = str(name or "Продукт").strip()
    return clean[:32]


def _build_photo_analysis_confirm_menu(
    items: list[dict] | None,
    save_token: str,
) -> InlineKeyboardMarkup:
    """Строит inline-меню подтверждения анализа еды по фото."""
    items = items or []
    rows: list[list[InlineKeyboardButton]] = []
    for idx, item in enumerate(items):
        name = _short_product_button_name(item.get("name") or "Продукт")
        rows.append([InlineKeyboardButton(text=f"✏️ {name}", callback_data=f"edit_photo_food_item:{idx}")])
    rows.append([InlineKeyboardButton(text="✏️ Название блюда", callback_data="photo_dish_name")])
    rows.append([InlineKeyboardButton(text="⚖️ Общий вес", callback_data="photo_total_weight")])
    rows.append([InlineKeyboardButton(text="❌ Отмена", callback_data="photo_cancel")])
    rows.append([
        InlineKeyboardButton(
            text="✅ Сохранить",
            callback_data=f"save_photo_food_analysis:{save_token}",
        )
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _build_food_photo_clarification_menu() -> InlineKeyboardMarkup:
    """Строит inline-меню запуска анализа фото еды без уточнения или отмены."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="⏭️ Анализировать без уточнения", callback_data="food_photo_analyze_now")],
            [InlineKeyboardButton(text="❌ Отмена", callback_data="food_photo_cancel")],
        ]
    )


def _build_photo_dish_candidates_menu(dishes: list[dict]) -> InlineKeyboardMarkup:
    """Не объединяет несколько объектов на фото без явного выбора пользователя."""
    rows = [
        [
            InlineKeyboardButton(
                text=f"{_number_emoji(index)} {_short_product_button_name(dish.get('dish_name') or 'Блюдо')}",
                callback_data=f"photo_dish_pick:{index}",
            )
        ]
        for index, dish in enumerate(dishes)
    ]
    rows.append([InlineKeyboardButton(text="🍽 Объединить в одно блюдо", callback_data="photo_dish_combine")])
    rows.append([InlineKeyboardButton(text="❌ Отмена", callback_data="photo_cancel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _format_photo_dish_candidates_text(dishes: list[dict]) -> str:
    lines = [
        "📸 <b>На фото найдено несколько отдельных блюд</b>",
        "",
        "Выбери одно из них или явно объедини всё в одну запись:",
        "",
    ]
    for index, dish in enumerate(dishes):
        name = normalize_dish_display_name(dish.get("dish_name"), dish.get("items") or [])
        weight = calculate_dish_weight(dish.get("items") or [])
        lines.append(f"{_number_emoji(index)} {html.escape(name)} — {weight:.0f} г")
    return "\n".join(lines)


def _build_photo_total_weight_editor_menu() -> InlineKeyboardMarkup:
    """Строит inline-меню редактирования общего веса блюда из анализа фото."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="−100 г", callback_data="photo_twchg:-100"),
                InlineKeyboardButton(text="−50 г", callback_data="photo_twchg:-50"),
                InlineKeyboardButton(text="+50 г", callback_data="photo_twchg:50"),
                InlineKeyboardButton(text="+100 г", callback_data="photo_twchg:100"),
            ],
            [
                InlineKeyboardButton(text="−25 г", callback_data="photo_twchg:-25"),
                InlineKeyboardButton(text="−10 г", callback_data="photo_twchg:-10"),
                InlineKeyboardButton(text="+10 г", callback_data="photo_twchg:10"),
                InlineKeyboardButton(text="+25 г", callback_data="photo_twchg:25"),
            ],
            [
                InlineKeyboardButton(text="−5 г", callback_data="photo_twchg:-5"),
                InlineKeyboardButton(text="−1 г", callback_data="photo_twchg:-1"),
                InlineKeyboardButton(text="+1 г", callback_data="photo_twchg:1"),
                InlineKeyboardButton(text="+5 г", callback_data="photo_twchg:5"),
            ],
            [InlineKeyboardButton(text="⌨️ Ввести вручную", callback_data="photo_twmanual")],
            [
                InlineKeyboardButton(text="✅ Сохранить", callback_data="photo_twsave"),
                InlineKeyboardButton(text="⬅️ Назад", callback_data="photo_twback"),
            ],
        ]
    )


def _build_photo_weight_editor_menu(product_idx: int) -> InlineKeyboardMarkup:
    """Строит inline-меню редактирования веса одного продукта из анализа фото."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text=f"+{step} г", callback_data=f"photo_wchg:{product_idx}:{step}")
                for step in PHOTO_WEIGHT_ADJUSTMENTS[:3]
            ],
            [
                InlineKeyboardButton(text=f"+{step} г", callback_data=f"photo_wchg:{product_idx}:{step}")
                for step in PHOTO_WEIGHT_ADJUSTMENTS[3:]
            ],
            [
                InlineKeyboardButton(text=f"-{step} г", callback_data=f"photo_wchg:{product_idx}:-{step}")
                for step in PHOTO_WEIGHT_ADJUSTMENTS[:3]
            ],
            [
                InlineKeyboardButton(text=f"-{step} г", callback_data=f"photo_wchg:{product_idx}:-{step}")
                for step in PHOTO_WEIGHT_ADJUSTMENTS[3:]
            ],
            [
                InlineKeyboardButton(text="✅ Готово", callback_data="photo_done"),
                InlineKeyboardButton(text="🗑 Удалить", callback_data=f"photo_delete:{product_idx}"),
            ],
        ]
    )


def _build_photo_analysis_cancel_menu() -> ReplyKeyboardMarkup:
    """Строит обычную нижнюю клавиатуру отмены анализа фото."""
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="❌ Отмена")]],
        resize_keyboard=True,
    )


async def _send_photo_analysis_confirmation(
    message: Message,
    items: list[dict],
    save_token: str,
    dish_name: str | None = None,
) -> None:
    """Показывает результат анализа и все действия в одной inline-клавиатуре."""
    await message.answer(
        _format_photo_analysis_confirmation_text(items, dish_name=dish_name),
        reply_markup=_build_photo_analysis_confirm_menu(items, save_token),
        parse_mode="HTML",
    )


async def _edit_or_send_photo_analysis_message(
    message: Message,
    text: str,
    *,
    reply_markup: InlineKeyboardMarkup | None = None,
    parse_mode: str | None = "HTML",
) -> None:
    """Обновляет сообщение анализа фото, а если Telegram не дал отредактировать — отправляет новое."""
    try:
        await message.edit_text(text, reply_markup=reply_markup, parse_mode=parse_mode)
    except TelegramBadRequest as exc:
        logger.info(
            "Could not edit photo analysis message; sending a new one error_type=%s",
            safe_exception_summary(exc),
        )
        await message.answer(text, reply_markup=reply_markup, parse_mode=parse_mode)


async def _hide_meal_reply_keyboard(message: Message) -> None:
    """Скрывает старую reply-клавиатуру без видимого служебного сообщения."""
    placeholder = await message.answer(
        "\u2063",
        reply_markup=ReplyKeyboardRemove(),
        disable_notification=True,
    )
    try:
        await placeholder.delete()
    except (AttributeError, TelegramBadRequest):
        pass


def _normalize_photo_analysis_items(items: list | None) -> list[dict]:
    """Нормализует продукты из ответа vision-модели к единому виду для черновика."""
    normalized: list[dict] = []
    for item in items or []:
        if not isinstance(item, dict):
            continue
        grams = max(0.0, _safe_float(item.get("grams") or item.get("weight") or item.get("amount_g")))
        normalized.append(
            {
                "name": str(item.get("name") or item.get("title") or "Продукт"),
                "grams": grams,
                "kcal": _safe_float(item.get("kcal") or item.get("calories")),
                "protein": _safe_float(item.get("protein") or item.get("protein_g")),
                "fat": _safe_float(item.get("fat") or item.get("fat_total_g")),
                "carbs": _safe_float(item.get("carbs") or item.get("carbohydrates_total_g")),
                "source": item.get("source") or "food_photo",
            }
        )

    return normalized


def _collect_photo_totals(items: list[dict]) -> dict:
    return {
        "calories": sum(_safe_float(item.get("kcal") or item.get("calories")) for item in items),
        "protein": sum(_safe_float(item.get("protein") or item.get("protein_g")) for item in items),
        "fat": sum(_safe_float(item.get("fat") or item.get("fat_total_g")) for item in items),
        "carbs": sum(_safe_float(item.get("carbs") or item.get("carbohydrates_total_g")) for item in items),
    }


def _scale_photo_item(item: dict, new_weight: float) -> dict:
    """Пересчитывает КБЖУ одного продукта пропорционально новому весу."""
    current_weight = _safe_float(item.get("grams"))
    if current_weight <= 0:
        current_weight = max(1.0, new_weight)
    factor = max(1.0, new_weight) / current_weight
    updated = dict(item)
    updated["grams"] = max(1.0, new_weight)
    updated["kcal"] = _safe_float(item.get("kcal") or item.get("calories")) * factor
    updated["protein"] = _safe_float(item.get("protein") or item.get("protein_g")) * factor
    updated["fat"] = _safe_float(item.get("fat") or item.get("fat_total_g")) * factor
    updated["carbs"] = _safe_float(item.get("carbs") or item.get("carbohydrates_total_g")) * factor
    return updated


def _scale_photo_items(items: list[dict], new_total_weight: float) -> list[dict]:
    """Пропорционально пересчитывает вес и КБЖУ всех блюд в черновике (legacy helper)."""
    current_total_weight = sum(_safe_float(item.get("grams")) for item in items)
    if current_total_weight <= 0:
        if not items:
            return items
        updated_items = [dict(item) for item in items]
        updated_items[0] = _scale_photo_item(updated_items[0], max(1.0, new_total_weight))
        return updated_items
    factor = max(1.0, new_total_weight) / current_total_weight
    return [_scale_photo_item(item, _safe_float(item.get("grams")) * factor) for item in items]


def _format_photo_item_block(item: dict, index: int | None = None) -> str:
    prefix = f"{_number_emoji(index)} " if index is not None else ""
    return "\n".join(
        [
            f"{prefix}{html.escape(str(item.get('name') or 'Продукт'))} — {_safe_float(item.get('grams')):.0f} г",
            f"🔥 {_safe_float(item.get('kcal') or item.get('calories')):.0f} ккал",
            f"🥩 Б: {_safe_float(item.get('protein') or item.get('protein_g')):.1f} г",
            f"🥑 Ж: {_safe_float(item.get('fat') or item.get('fat_total_g')):.1f} г",
            f"🍚 У: {_safe_float(item.get('carbs') or item.get('carbohydrates_total_g')):.1f} г",
        ]
    )


def _format_photo_analysis_confirmation_text(items: list[dict], *, dish_name: str | None = None) -> str:
    """Форматирует общий экран анализа фото до сохранения в дневник."""
    total_weight = sum(_safe_float(item.get("grams")) for item in items)
    totals = _collect_photo_totals(items)
    lines = [
        "📸 <b>Анализ фото завершён</b>",
        "",
        f"🥣 <b>{html.escape(normalize_dish_display_name(dish_name, items))}</b>",
        "",
        "🍽 <b>Состав:</b>",
        "",
    ]
    for idx, item in enumerate(items):
        lines.append(_format_photo_item_block(item, idx))
        lines.append("")
    if len(items) > 1:
        lines.extend(
            [
                "📊 <b>Итого:</b>",
                f"📦 Общий вес: {total_weight:.0f} г",
                f"🔥 Калории: {totals['calories']:.0f} ккал",
                f"🥩 Белки: {totals['protein']:.1f} г",
                f"🥑 Жиры: {totals['fat']:.1f} г",
                f"🍚 Углеводы: {totals['carbs']:.1f} г",
            ]
        )
    return "\n".join(lines).rstrip()


def _format_photo_total_weight_editor_text(total_weight: float) -> str:
    """Форматирует экран редактирования общего веса блюда."""
    return "\n".join(
        [
            "⚖️ <b>Изменение общего веса блюда</b>",
            "",
            f"Текущий общий вес: {max(1.0, total_weight):.0f} г",
            "",
            "Выбери действие:",
        ]
    )


def _format_photo_weight_editor_text(item: dict) -> str:
    """Форматирует экран редактирования веса одного продукта."""
    return "\n".join(
        [
            "✏️ <b>Редактирование веса</b>",
            "",
            f"🍽 {html.escape(str(item.get('name') or 'Продукт'))}",
            f"📦 Вес: {_safe_float(item.get('grams')):.0f} г",
            "",
            f"🔥 {_safe_float(item.get('kcal') or item.get('calories')):.0f} ккал",
            f"🥩 Б: {_safe_float(item.get('protein') or item.get('protein_g')):.1f} г",
            f"🥑 Ж: {_safe_float(item.get('fat') or item.get('fat_total_g')):.1f} г",
            f"🍚 У: {_safe_float(item.get('carbs') or item.get('carbohydrates_total_g')):.1f} г",
        ]
    )


def _calculate_label_totals(kbju_per_100g: dict | None, weight_grams: float) -> tuple[dict, dict]:
    """Возвращает итоговые КБЖУ и значения на 100 г для выбранного веса."""
    kbju_per_100g = kbju_per_100g or {}
    per_100g = {
        "kcal": _safe_float(kbju_per_100g.get("kcal")),
        "protein": _safe_float(kbju_per_100g.get("protein")),
        "fat": _safe_float(kbju_per_100g.get("fat")),
        "carbs": _safe_float(kbju_per_100g.get("carbs")),
    }
    multiplier = weight_grams / 100.0
    totals = {
        "calories": per_100g["kcal"] * multiplier,
        "protein": per_100g["protein"] * multiplier,
        "fat": per_100g["fat"] * multiplier,
        "carbs": per_100g["carbs"] * multiplier,
    }
    return totals, per_100g


def _format_label_weight_confirmation_text(data: dict, weight_grams: float) -> str:
    """Форматирует экран подтверждения выбранного веса и пересчитанных КБЖУ."""
    totals, _ = _calculate_label_totals(data.get("kbju_per_100g"), weight_grams)
    product_name = html.escape(data.get("product_name") or "Продукт")
    lines = [
        f"📦 <b>Продукт:</b> {product_name}",
        f"✅ <b>Вы выбрали:</b> {weight_grams:.0f} г",
        "",
        "<b>Итоговые КБЖУ:</b>",
        _format_kbju_summary_block(totals),
    ]
    return "\n".join(lines)


MY_PRODUCTS_PAGE_SIZE = 8
MY_PRODUCTS_HISTORY_BATCH_SIZE = 100

MY_PRODUCTS_SOURCE_FILTERS = {
    "text_ai": {"button": "📝 Из текстового AI-анализа", "title": "📝 <b>Мои продукты из текстового AI-анализа"},
    "photo_analysis": {
        "button": "📷 Из анализа еды по фото",
        "aliases": ("🗂 Старые продукты из фотоанализа",),
        "title": "📷 <b>Мои продукты из анализа еды по фото",
    },
    "label_analysis": {"button": "📋 Из анализа этикетки", "title": "📋 <b>Мои продукты из анализа этикетки"},
    "manual": {"button": "✍️ Внесённые вручную", "title": "✍️ <b>Мои продукты, внесённые вручную"},
    "all": {"button": "📦 Все продукты", "title": "📦 <b>Все мои продукты"},
}
MY_PRODUCTS_SOURCE_BUTTON_TO_FILTER = {
    button: source_filter
    for source_filter, config in MY_PRODUCTS_SOURCE_FILTERS.items()
    for button in (config["button"], *config.get("aliases", ()))
}



def _format_emoji_number(number: int) -> str:
    """Форматирует порядковый номер только emoji-цифрами."""
    return format_meal_emoji_number(number)


@dataclass(frozen=True)
class MyProductItem:
    """Продукт из истории или целое сохранённое блюдо для выбора из каталога."""

    source_meal_id: int | None
    product_index: int | None
    title: str
    amount_g: int
    calories: float
    protein: float
    fat: float
    carbs: float
    unit_weight_g: float | None = None
    unit_name: str | None = None
    package_weight_g: float | None = None
    package_units: int | None = None
    dish_id: int | None = None


def _truncate_my_product_name(name: str, limit: int = 22) -> str:
    clean = (name or "").strip()
    if len(clean) <= limit:
        return clean
    return f"{clean[:limit-1].rstrip()}…"


def _normalize_my_product_title(meal) -> str:
    """Возвращает человекочитаемое название продукта без технических префиксов."""
    raw_title = (meal.raw_query or meal.description or "Продукт").strip()
    lowered = raw_title.lower()
    if lowered.startswith("[этикетка:") and raw_title.endswith("]"):
        raw_title = raw_title[len("[Этикетка:") : -1].strip()
    return raw_title or "Продукт"


def _safe_float(value, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _parse_my_products(meal) -> list[dict]:
    raw_products = getattr(meal, "products_json", None)
    if not raw_products:
        return []
    try:
        parsed = json.loads(raw_products)
    except Exception:
        return []
    if not isinstance(parsed, list):
        return []
    return [product for product in parsed if isinstance(product, dict)]


def _my_product_title(product: dict) -> str:
    raw_name = str(product.get("name") or "").strip()
    if not raw_name or raw_name.lower() == "none":
        return "Продукт"
    return raw_name


def _my_product_value(product: dict, *keys: str) -> float:
    for key in keys:
        if key in product:
            return _safe_float(product.get(key))
    return 0.0


def _optional_positive_float(value) -> float | None:
    numeric = _safe_float(value, default=0.0)
    return numeric if numeric > 0 else None


def _optional_positive_int(value) -> int | None:
    numeric = _optional_positive_float(value)
    if numeric is None or not numeric.is_integer():
        return None
    return int(numeric)


def _optional_unit_name(value) -> str | None:
    clean = str(value or "").strip()
    return clean or None


def _extract_my_product_amount_g(product: dict) -> int:
    grams_value = _safe_float(product.get("grams"), default=0.0)
    if grams_value <= 0:
        return 100
    return max(1, int(round(grams_value)))


def _build_my_product_item_from_product(meal, product: dict, product_index: int) -> MyProductItem:
    return MyProductItem(
        source_meal_id=meal.id,
        product_index=product_index,
        title=_my_product_title(product),
        amount_g=_extract_my_product_amount_g(product),
        calories=_my_product_value(product, "kcal", "calories"),
        protein=_my_product_value(product, "protein", "protein_g"),
        fat=_my_product_value(product, "fat", "fat_total_g"),
        carbs=_my_product_value(product, "carbs", "carbohydrates_total_g"),
        unit_weight_g=_optional_positive_float(product.get("unit_weight_g")),
        unit_name=_optional_unit_name(product.get("unit_name")),
        package_weight_g=_optional_positive_float(product.get("package_weight_g")),
        package_units=_optional_positive_int(product.get("package_units")),
    )


def _build_my_product_item_from_meal(meal) -> MyProductItem:
    return MyProductItem(
        source_meal_id=meal.id,
        product_index=None,
        title=_normalize_my_product_title(meal),
        amount_g=_extract_my_product_amount_g_from_meal(meal),
        calories=float(meal.calories or 0),
        protein=float(meal.protein or 0),
        fat=float(meal.fat or 0),
        carbs=float(meal.carbs or 0),
    )


def _normalize_my_product_source(source: str | None) -> str:
    normalized = str(source or "").strip()
    aliases = {
        "ai_text": "text_ai",
        "food_photo": "photo_analysis",
        "food_photo_analysis": "photo_analysis",
        "photo": "photo_analysis",
        "gemini": "photo_analysis",
        "openai": "photo_analysis",
        "label": "label_analysis",
        "barcode": "label_analysis",
        "label_analysis_fallback": "label_analysis",
        "custom_product": "manual",
    }
    return aliases.get(normalized, normalized or "unknown")


def _get_product_source(product: dict) -> str:
    return _normalize_my_product_source(product.get("source"))


def _product_matches_source_filter(product: dict, source_filter: str | None) -> bool:
    if not source_filter or source_filter == "all":
        return True
    return _get_product_source(product) == source_filter


def _build_my_product_item_from_dish(dish) -> MyProductItem:
    snapshot = dish_to_snapshot(dish)
    totals = calculate_dish_totals(snapshot)
    return MyProductItem(
        source_meal_id=None,
        product_index=None,
        title=dish.name,
        amount_g=max(1, round(calculate_dish_weight(snapshot))),
        **totals,
        dish_id=dish.id,
    )


def _iter_my_product_items(
    my_product_meals: Iterable,
    source_filter: str | None,
    saved_dishes: dict | None = None,
) -> Iterable[MyProductItem]:
    remaining_dishes = dict(saved_dishes or {})
    for meal in my_product_meals:
        if getattr(meal, "entry_kind", "products") == "dish":
            dish = remaining_dishes.pop(getattr(meal, "dish_id", None), None)
            if dish is not None:
                yield _build_my_product_item_from_dish(dish)
            continue
        products = _parse_my_products(meal)
        meal_items = [
            _build_my_product_item_from_product(meal, product, idx)
            for idx, product in enumerate(products)
            if _my_product_title(product) != "Продукт"
            and _product_matches_source_filter(product, source_filter)
        ]
        if not meal_items and (not products) and (not source_filter or source_filter == "all"):
            meal_items = [_build_my_product_item_from_meal(meal)]

        yield from meal_items
    # Templates remain reusable even after their original diary entry is deleted.
    for dish in remaining_dishes.values():
        yield _build_my_product_item_from_dish(dish)


def _expand_my_products(
    my_product_meals: Iterable,
    limit: int | None = None,
    source_filter: str | None = None,
    *,
    saved_dishes: dict | None = None,
) -> list[MyProductItem]:
    """Собирает уникальные продукты и целые блюда, сохраняя порядок истории."""
    items: list[MyProductItem] = []
    seen: set[tuple] = set()
    for item in _iter_my_product_items(my_product_meals, source_filter, saved_dishes):
        key = ("dish", item.dish_id) if item.dish_id is not None else ("product", item.title.strip().casefold())
        if key in seen:
            continue
        seen.add(key)
        items.append(item)
        if limit is not None and len(items) >= limit:
            break
    return items


def _iter_my_products_history(user_id: str) -> Iterable:
    """Читает всю историю порциями: точный счётчик учитывает и старые записи."""
    offset = 0
    while True:
        meals_batch = MealRepository.get_user_meal_history_page(
            user_id,
            offset=offset,
            limit=MY_PRODUCTS_HISTORY_BATCH_SIZE,
        )
        if not meals_batch:
            break

        yield from meals_batch
        offset += len(meals_batch)
        if len(meals_batch) < MY_PRODUCTS_HISTORY_BATCH_SIZE:
            break


def _get_my_products_catalog(user_id: str, source_filter: str | None = None) -> list[MyProductItem]:
    dishes = {
        dish.id: dish
        for dish in DishRepository.list_active(user_id, limit=None)
        if _product_matches_source_filter({"source": dish.source}, source_filter)
    }
    return _expand_my_products(
        _iter_my_products_history(user_id), source_filter=source_filter, saved_dishes=dishes,
    )


def _get_my_products_page_items(
    user_id: str,
    page: int,
    *,
    source_filter: str | None = None,
) -> tuple[list[MyProductItem], int, int]:
    items = _get_my_products_catalog(user_id, source_filter)
    total_pages = total_pages_for(len(items), MY_PRODUCTS_PAGE_SIZE)
    page = clamp_page(page - 1, total_pages) + 1
    start = (page - 1) * MY_PRODUCTS_PAGE_SIZE
    return items[start : start + MY_PRODUCTS_PAGE_SIZE], page, total_pages


def _is_custom_product_meal(meal) -> bool:
    """Проверяет, что запись создана именно через кнопку «Внести вручную»."""
    return any(_get_product_source(product) == "manual" for product in _parse_my_products(meal))


def _get_custom_product_items(user_id: str, limit: int | None = None) -> list[MyProductItem]:
    """Возвращает только продукты, созданные пользователем через кнопку «Внести вручную»."""
    return _expand_my_products(_iter_my_products_history(user_id), limit=limit, source_filter="manual")


def _format_my_products_text(my_product_meals: list[MyProductItem], page: int, *, title: str = "🕒 <b>Недавние продукты") -> str:
    start_idx = (page - 1) * MY_PRODUCTS_PAGE_SIZE
    lines: list[str] = [f"{title} • страница {page}</b>", ""]
    for offset, item in enumerate(my_product_meals, start=start_idx + 1):
        lines.extend(
            [
                f"{_format_emoji_number(offset)} <b>{html.escape(item.title)}</b>",
                f"<b>{item.amount_g} г • {item.calories:.0f} ккал</b>",
                f"<i>Б {item.protein:.1f} / Ж {item.fat:.1f} / У {item.carbs:.1f}</i>",
                "",
            ]
        )
    return "\n".join(lines).strip()


def _format_my_products_search_results_text(query: str, items: list[MyProductItem], page: int) -> str:
    start_idx = (page - 1) * MY_PRODUCTS_PAGE_SIZE
    safe_query = html.escape((query or "").strip())
    lines: list[str] = [f"🔎 <b>Результаты поиска: {safe_query}</b>", ""]
    for offset, item in enumerate(items, start=start_idx + 1):
        lines.extend(
            [
                f"{_format_emoji_number(offset)} <b>{html.escape(item.title)}</b>",
                f"<b>{item.amount_g} г • {item.calories:.0f} ккал</b>",
                f"<i>Б {item.protein:.1f} / Ж {item.fat:.1f} / У {item.carbs:.1f}</i>",
                "",
            ]
        )
    return "\n".join(lines).strip()


def _search_my_products(items: list[MyProductItem], query: str) -> list[MyProductItem]:
    normalized_query = (query or "").strip().casefold()
    if not normalized_query:
        return []
    return [item for item in items if normalized_query in (item.title or "").casefold()]


def _my_product_pick_callback(item: MyProductItem, meal_type: str, page: int, *, origin: str = "products") -> str:
    if item.dish_id is not None:
        return f"my_dish_pick:{meal_type}:{page}:{item.dish_id}:{origin}"
    product_idx = "" if item.product_index is None else str(item.product_index)
    suffix = ":search" if origin == "search" else ""
    return f"my_product_pick:{meal_type}:{page}:{item.source_meal_id}:{product_idx}{suffix}"


def _build_my_products_keyboard(
    my_product_meals: list[MyProductItem],
    meal_type: str,
    page: int,
    total_pages: int,
    *,
    back_callback_data: str | None = None,
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for offset, item in enumerate(my_product_meals, start=1):
        title = _truncate_my_product_name(item.title)
        number = (page - 1) * MY_PRODUCTS_PAGE_SIZE + offset
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"{_format_emoji_number(number)} {title}",
                    callback_data=_my_product_pick_callback(item, meal_type, page),
                )
            ]
        )

    keyboard = build_pagination_keyboard(page - 1, total_pages, f"my_products_page:{meal_type}", rows, page_base=1)
    keyboard.inline_keyboard.append([InlineKeyboardButton(text="🔎 Поиск продукта", callback_data=f"my_products_search_start:{meal_type}")])
    if page >= 3:
        keyboard.inline_keyboard.append([InlineKeyboardButton(text="⬅️ В начало", callback_data=f"my_products_page:{meal_type}:1")])
    return keyboard


def _build_custom_products_keyboard(
    products: list[MyProductItem],
    meal_type: str,
    page: int,
    total_pages: int,
) -> InlineKeyboardMarkup:
    """Inline-выбор своих продуктов в визуальном стиле списка моих."""
    rows: list[list[InlineKeyboardButton]] = []
    for offset, item in enumerate(products, start=1):
        title = _truncate_my_product_name(item.title)
        number = (page - 1) * MY_PRODUCTS_PAGE_SIZE + offset
        product_idx = "" if item.product_index is None else str(item.product_index)
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"{_format_emoji_number(number)} {title}",
                    callback_data=f"custom_product_pick:{meal_type}:{page}:{item.source_meal_id}:{product_idx}",
                )
            ]
        )

    return build_pagination_keyboard(page - 1, total_pages, f"custom_product_page:{meal_type}", rows, page_base=1)


def _build_my_products_search_results_keyboard(
    items: list[MyProductItem],
    meal_type: str,
    page: int,
    total_pages: int,
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    start_idx = (page - 1) * MY_PRODUCTS_PAGE_SIZE
    for offset, item in enumerate(items, start=1):
        title = _truncate_my_product_name(item.title)
        number = start_idx + offset
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"{_format_emoji_number(number)} {title}",
                    callback_data=_my_product_pick_callback(item, meal_type, page, origin="search"),
                )
            ]
        )

    keyboard = build_pagination_keyboard(page - 1, total_pages, f"my_products_search_page:{meal_type}", rows, page_base=1)
    keyboard.inline_keyboard.append([InlineKeyboardButton(text="🔎 Искать ещё", callback_data=f"my_products_search_start:{meal_type}")])
    keyboard.inline_keyboard.append([InlineKeyboardButton(text="⬅️ К моим продуктам", callback_data=f"my_products_search_back:{meal_type}")])
    return keyboard


def _build_my_products_search_empty_keyboard(meal_type: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🔎 Искать ещё", callback_data=f"my_products_search_start:{meal_type}")],
            [InlineKeyboardButton(text="⬅️ К моим продуктам", callback_data=f"my_products_search_back:{meal_type}")],
        ]
    )


def _format_custom_product_step(step: int, text: str) -> str:
    """Форматирует шаг создания продукта в стиле стартового теста КБЖУ."""
    return f"<b>Шаг {step}/5</b>\n\n{text}"


def _format_custom_product_name_step() -> str:
    """Текст первого шага создания своего продукта."""
    return _format_custom_product_step(
        1,
        "<b>Сейчас мы добавим твой продукт вручную.</b>\n\n"
        "Я по шагам попрошу <b>название</b>, затем <b>калории, белки, жиры, углеводы</b> "
        "и <b>вес порции</b>. Так продукт сохранится, и в следующий раз его можно будет быстро "
        "выбрать в разделе «📦 Мои продукты».\n\n"
        "<b>Для начала введи название продукта:</b>",
    )


async def _start_custom_product_creation(message: Message, state: FSMContext, *, meal_type: str) -> None:
    """Сразу запускает мастер создания продукта вручную."""
    await state.set_state(MealEntryStates.custom_product_name)
    await state.update_data(
        meal_type=meal_type,
        custom_product={},
        custom_product_save_token=_new_meal_save_token(),
        pending_add_method=None,
        in_my_product_menu=False,
    )
    push_menu_stack(message.bot, kbju_add_menu)
    await message.answer(
        _format_custom_product_name_step(),
        reply_markup=_build_custom_product_reply_keyboard(),
        parse_mode="HTML",
    )


def _build_custom_product_reply_keyboard() -> ReplyKeyboardMarkup:
    """Нижняя клавиатура для пошагового создания своего продукта без меню добавления."""
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="⬅️ Назад")],
        ],
        resize_keyboard=True,
    )


def _render_custom_product_value_editor_text(
    *,
    step: int | None,
    title: str,
    value: float,
    unit: str,
    note: str,
) -> str:
    """Текст экрана изменения числового значения для своего продукта."""
    header = f"<b>Шаг {step}/5</b>\n\n" if step else ""
    return (
        f"{header}<b>{title}</b>\n\n"
        f"{note}\n\n"
        f"Текущее значение: <b>{value:g} {unit}</b>\n\n"
        "Измени значение кнопками или введи число сообщением.\n"
        "Когда всё верно, нажми <b>✅ Сохранить</b>."
    )


def _format_button_delta(value: float | int) -> str:
    """Форматирует шаг кнопки: дробные значения показываем через запятую."""
    return f"{value:+g}".replace(".", ",")


def _format_callback_delta(value: float | int) -> str:
    """Форматирует шаг для callback_data в машинно-читаемом виде."""
    return f"{value:g}"


def _build_custom_product_value_keyboard(
    field: str,
    *,
    unit: str,
    save_token: str | None = None,
) -> InlineKeyboardMarkup:
    """Inline-кнопки +/− для ввода КБЖУ и веса съеденного продукта."""
    if field == "calories":
        delta_rows = [
            (1, 5, 10, 20, 50, 100),
            (-1, -5, -10, -20, -50, -100),
        ]
    elif field in {"protein", "fat", "carbs"}:
        delta_rows = [
            (0.1, 0.2, 0.5, 1, 5, 10),
            (-0.1, -0.2, -0.5, -1, -5, -10),
        ]
    else:
        delta_rows = [
            (-100, -50, 50, 100),
            (-25, -10, 10, 25),
            (-5, -1, 1, 5),
        ]
    rows = [
        [
            InlineKeyboardButton(
                text=_format_button_delta(delta),
                callback_data=f"custom_vchg:{field}:{_format_callback_delta(delta)}",
            )
            for delta in delta_row
        ]
        for delta_row in delta_rows
    ]
    save_callback = f"custom_vsave:{field}"
    if field == "amount" and _is_valid_meal_save_token(save_token):
        save_callback = f"{save_callback}:{save_token}"
    rows.append([InlineKeyboardButton(text="✅ Сохранить", callback_data=save_callback)])
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data=f"custom_vback:{field}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


CUSTOM_PRODUCT_FIELDS = {
    "calories": {
        "state": MealEntryStates.custom_product_calories,
        "step": 2,
        "title": "🔥 Введите калории продукта",
        "unit": "ккал",
        "note": "Укажи <b>калорийность на 100 г продукта</b>.",
        "next_field": "protein",
    },
    "protein": {
        "state": MealEntryStates.custom_product_protein,
        "step": 3,
        "title": "🥩 Введите белки продукта",
        "unit": "г",
        "note": "Укажи <b>белки на 100 г продукта</b>.",
        "next_field": "fat",
    },
    "fat": {
        "state": MealEntryStates.custom_product_fat,
        "step": 4,
        "title": "🥑 Введите жиры продукта",
        "unit": "г",
        "note": "Укажи <b>жиры на 100 г продукта</b>.",
        "next_field": "carbs",
    },
    "carbs": {
        "state": MealEntryStates.custom_product_carbs,
        "step": 5,
        "title": "🍚 Введите углеводы продукта",
        "unit": "г",
        "note": "Укажи <b>углеводы на 100 г продукта</b>.",
        "next_field": "amount",
    },
    "amount": {
        "state": MealEntryStates.custom_product_amount,
        "step": None,
        "title": "⚖️ Сколько продукта ты съел(а) в этом приёме пищи?",
        "unit": "г",
        "note": "Укажи <b>вес порции</b>, а я пересчитаю КБЖУ из значений на 100 г.",
        "next_field": None,
    },
}


async def _show_custom_product_value_editor(message: Message, state: FSMContext, field: str, value: float = 0) -> None:
    """Показывает редактор значения для шага создания своего продукта."""
    config = CUSTOM_PRODUCT_FIELDS[field]
    data = await state.get_data()
    save_token = data.get("custom_product_save_token")
    if not _is_valid_meal_save_token(save_token):
        save_token = _new_meal_save_token()
    await state.set_state(config["state"])
    await state.update_data(
        custom_product_current_field=field,
        custom_product_draft_value=float(value),
        custom_product_save_token=save_token,
    )
    await message.answer(
        _render_custom_product_value_editor_text(
            step=config["step"],
            title=config["title"],
            value=float(value),
            unit=config["unit"],
            note=config["note"],
        ),
        reply_markup=_build_custom_product_value_keyboard(
            field,
            unit=config["unit"],
            save_token=save_token,
        ),
        parse_mode="HTML",
    )


async def _go_to_previous_custom_product_step(message: Message, state: FSMContext) -> None:
    """Возвращает пользователя на предыдущий шаг создания своего продукта."""
    data = await state.get_data()
    current_field = data.get("custom_product_current_field")
    product = dict(data.get("custom_product") or {})
    if current_field == "amount":
        await _show_custom_product_value_editor(message, state, "carbs", float(product.get("carbs", 0)))
    elif current_field == "carbs":
        await _show_custom_product_value_editor(message, state, "fat", float(product.get("fat", 0)))
    elif current_field == "fat":
        await _show_custom_product_value_editor(message, state, "protein", float(product.get("protein", 0)))
    elif current_field == "protein":
        await _show_custom_product_value_editor(message, state, "calories", float(product.get("calories", 0)))
    else:
        await state.set_state(MealEntryStates.custom_product_name)
        await message.answer(
            _format_custom_product_name_step(),
            reply_markup=_build_custom_product_reply_keyboard(),
            parse_mode="HTML",
        )


async def _advance_custom_product_after_save(
    message: Message,
    state: FSMContext,
    field: str,
    value: float,
    *,
    user_id: str | None = None,
) -> None:
    """Сохраняет значение текущего поля и переводит к следующему шагу."""
    data = await state.get_data()
    product = dict(data.get("custom_product") or {})
    product[field] = value
    await state.update_data(custom_product=product, custom_product_draft_value=None)
    next_field = CUSTOM_PRODUCT_FIELDS[field]["next_field"]
    if next_field:
        start_value = float(product.get(next_field, 0))
        await _show_custom_product_value_editor(message, state, next_field, start_value)
        return
    await _save_custom_product(message, state, user_id=user_id)


@router.callback_query(lambda c: c.data.startswith("custom_vchg:"))
async def custom_product_value_change(callback: CallbackQuery, state: FSMContext):
    """Меняет черновое значение КБЖУ/веса кнопками +/−."""
    _, field, delta_str = callback.data.split(":", maxsplit=2)
    config = CUSTOM_PRODUCT_FIELDS.get(field)
    if not config:
        await callback.answer("Неизвестное поле", show_alert=True)
        return
    data = await state.get_data()
    value = max(0.0, float(data.get("custom_product_draft_value") or 0) + float(delta_str))
    if field == "amount":
        value = max(1.0, value)
    await state.update_data(custom_product_draft_value=value, custom_product_current_field=field)
    await callback.answer()
    await callback.message.edit_text(
        _render_custom_product_value_editor_text(
            step=config["step"],
            title=config["title"],
            value=value,
            unit=config["unit"],
            note=config["note"],
        ),
        reply_markup=_build_custom_product_value_keyboard(
            field,
            unit=config["unit"],
            save_token=data.get("custom_product_save_token"),
        ),
        parse_mode="HTML",
    )


@router.callback_query(lambda c: c.data.startswith("custom_vback:"))
async def custom_product_value_back(callback: CallbackQuery, state: FSMContext):
    """Возвращает на предыдущий шаг создания продукта из inline-редактора."""
    parts = callback.data.split(":", maxsplit=2)
    _, field = parts[:2]
    if field not in CUSTOM_PRODUCT_FIELDS:
        await callback.answer("Неизвестное поле", show_alert=True)
        return
    await state.update_data(custom_product_current_field=field)
    await callback.answer()
    await _go_to_previous_custom_product_step(callback.message, state)


@router.callback_query(lambda c: c.data.startswith("custom_vsave:"))
async def custom_product_value_save(callback: CallbackQuery, state: FSMContext):
    """Фиксирует введённое значение и только после этого переводит к следующему шагу."""
    parts = callback.data.split(":", maxsplit=2)
    _, field = parts[:2]
    if field not in CUSTOM_PRODUCT_FIELDS:
        await callback.answer("Неизвестное поле", show_alert=True)
        return
    data = await state.get_data()
    if field == "amount":
        callback_token = parts[2] if len(parts) == 3 else None
        current_token = data.get("custom_product_save_token")
        if (
            not _is_valid_meal_save_token(callback_token)
            or callback_token != current_token
        ):
            await callback.answer(STALE_MEAL_SAVE_TEXT, show_alert=True)
            return
    value = float(data.get("custom_product_draft_value") or 0)
    if field == "amount" and value <= 0:
        await callback.answer("Вес должен быть больше 0 г", show_alert=True)
        return
    await callback.answer()
    await _advance_custom_product_after_save(callback.message, state, field, value, user_id=str(callback.from_user.id))


def _build_my_product_keyboard(meal_type: str) -> ReplyKeyboardMarkup:
    """Обычные кнопки нижней части экрана «Внести вручную»."""
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="➕ Создать продукт")],
            [KeyboardButton(text="⬅️ Назад")],
        ],
        resize_keyboard=True,
    )


def _build_my_products_source_filter_reply_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            *[[KeyboardButton(text=config["button"])] for config in MY_PRODUCTS_SOURCE_FILTERS.values()],
            [KeyboardButton(text="⬅️ Назад")],
        ],
        resize_keyboard=True,
    )


async def _show_my_products_source_filter_block(message: Message) -> None:
    await message.answer(
        "📂 <b>Показать продукты по источнику:</b>",
        reply_markup=_build_my_products_source_filter_reply_keyboard(),
        parse_mode="HTML",
    )


def _parse_non_negative_number(raw_text: str) -> float | None:
    """Парсит неотрицательное число из пользовательского ввода."""
    try:
        value = float((raw_text or "").strip().replace(",", "."))
    except (TypeError, ValueError):
        return None
    if value < 0:
        return None
    return value


async def _show_my_product_menu(
    message: Message,
    state: FSMContext,
    *,
    meal_type: str,
    user_id: str,
) -> None:
    """Совместимость со старыми вызовами: теперь «Внести вручную» сразу создаёт продукт."""
    await _start_custom_product_creation(message, state, meal_type=meal_type)

AI_TEMPORARY_UNAVAILABLE_TEXT = "Сервис AI сейчас временно перегружен. Попробуй ещё раз чуть позже."
AI_QUOTA_UNAVAILABLE_TEXT = "⚠️ AI временно недоступен из-за лимита запросов."
AI_CONFIG_UNAVAILABLE_TEXT = "⚠️ AI временно недоступен из-за ошибки настройки."
AI_TIMEOUT_UNAVAILABLE_TEXT = "⏱️ AI отвечает слишком долго. Попробуй ещё раз чуть позже."

def _meal_comment_keyboard(meal_id: int, target_date: date) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="➡️ Продолжить", callback_data=f"meal_comment_continue:{meal_id}:{target_date.isoformat()}")]])


def _truncate_meal_comment(text: str, limit: int = 350) -> str:
    if len(text) <= limit:
        return text
    cut = text[:limit].rstrip()
    last_space = max(cut.rfind(" "), cut.rfind("\n"))
    if last_space > 0:
        cut = cut[:last_space].rstrip()
    while cut.count("<b>") > cut.count("</b>"):
        cut = cut.rsplit("<b>", 1)[0].rstrip()
    while cut.count("</b>") > cut.count("<b>"):
        cut = cut.rsplit("</b>", 1)[0].rstrip()
    return cut.rstrip(".,;:—- ") or text[:limit].rstrip()


def _sanitize_meal_comment_html(text: str) -> str:
    text = (text or "").strip().replace("**", "").replace("__", "").replace("#", "")
    text = re.sub(r"<\/?(?!b\b)[^>]+>", "", text)
    text = html.escape(text, quote=False).replace("&lt;b&gt;", "<b>").replace("&lt;/b&gt;", "</b>")
    text = text.replace("\r", "")
    text = "\n".join(re.sub(r"[ \t]+", " ", line).strip() for line in text.split("\n"))
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    if text.count("<b>") > text.count("</b>"):
        first_line_end = text.find("\n")
        if first_line_end != -1:
            text = text[:first_line_end] + "</b>" + text[first_line_end:]
        else:
            text += "</b>"
    while text.count("</b>") > text.count("<b>"):
        text = text.replace("</b>", "", 1)

    first_line = text.split("\n", 1)[0].strip()
    if "<b>" in first_line or "</b>" in first_line:
        title_source = re.sub(r"</?b>", "", first_line).strip()
    else:
        bold_match = re.search(r"<b>(.*?)</b>", text, flags=re.DOTALL)
        if bold_match:
            title_source = bold_match.group(1).strip()
        else:
            title_source = text.split(". ", 1)[0].strip()

    title = re.sub(r"</?b>", "", title_source).split("\n", 1)[0].strip()
    plain = re.sub(r"</?b>", "", text).strip()
    first_plain_line = plain.split("\n", 1)[0].strip()
    if first_plain_line and plain.startswith(first_plain_line):
        body = plain[len(first_plain_line):].strip()
    elif title and plain.startswith(title):
        body = plain[len(title):].strip()
    else:
        body = plain
    body = re.sub(r"^[-–—.:;\s]+", "", body).strip()

    sentences = [part.strip() for part in re.split(r"(?<=[.!?…])\s+", body) if part.strip()]
    if sentences:
        body = " ".join(sentences[:2])
    if title and not title.endswith(('.', '!', '?', '…')):
        title += "."

    if not title:
        return MEAL_COMMENT_FALLBACK_TEXT
    result = f"<b>{title}</b>"
    if body:
        result += f"\n\n{body}"
    return _truncate_meal_comment(result, 350).strip() or MEAL_COMMENT_FALLBACK_TEXT


def _format_product_line(item: dict) -> str:
    name = item.get("name") or item.get("product") or "продукт"
    grams = _safe_float(item.get("grams") or item.get("amount_g") or item.get("weight"))
    kcal = _safe_float(item.get("kcal") or item.get("calories"))
    protein = _safe_float(item.get("protein"))
    fat = _safe_float(item.get("fat"))
    carbs = _safe_float(item.get("carbs"))
    return f"- {name}: {grams:.0f} г, {kcal:.0f} ккал, Б {protein:.1f} г, Ж {fat:.1f} г, У {carbs:.1f} г"


def _extract_meal_products(meal) -> list[dict]:
    """Возвращает нормализованный список продуктов из одной записи Meal."""
    try:
        parsed = json.loads(getattr(meal, "products_json", None) or "[]")
    except (TypeError, json.JSONDecodeError):
        parsed = []
    if not isinstance(parsed, list):
        parsed = []
    products: list[dict] = []
    for item in parsed:
        if isinstance(item, dict):
            products.append(item)
    if not products:
        products.append(
            {
                "name": getattr(meal, "description", None) or getattr(meal, "raw_query", None) or "приём пищи",
                "grams": 0,
                "kcal": float(getattr(meal, "calories", 0) or 0),
                "protein": float(getattr(meal, "protein", 0) or 0),
                "fat": float(getattr(meal, "fat", 0) or 0),
                "carbs": float(getattr(meal, "carbs", 0) or 0),
            }
        )
    return products


def _load_completed_meal_context(user_id: str, meal_id: int) -> tuple[object | None, date, list[object], list[dict], dict]:
    """Загружает из БД весь текущий приём пищи по якорной записи meal_id."""
    anchor_meal = MealRepository.get_meal_by_id(meal_id, user_id)
    if not anchor_meal:
        return None, date.today(), [], [], {"calories": 0.0, "protein": 0.0, "fat": 0.0, "carbs": 0.0}

    target_date = getattr(anchor_meal, "date", None) or date.today()
    meal_type = normalize_meal_type(getattr(anchor_meal, "meal_type", None), fallback=MealType.SNACK.value)
    meal_rows = MealRepository.get_meals_for_type_for_date(user_id, target_date, meal_type)
    products: list[dict] = []
    for meal_row in meal_rows:
        products.extend(_extract_meal_products(meal_row))

    totals = {
        "calories": sum(float(getattr(meal_row, "calories", 0) or 0) for meal_row in meal_rows),
        "protein": sum(float(getattr(meal_row, "protein", 0) or 0) for meal_row in meal_rows),
        "fat": sum(float(getattr(meal_row, "fat", 0) or 0) for meal_row in meal_rows),
        "carbs": sum(float(getattr(meal_row, "carbs", 0) or 0) for meal_row in meal_rows),
    }
    return anchor_meal, target_date, meal_rows, products, totals


def _build_meal_completion_prompt(user_id: str, meal, target_date: date, products: list[dict] | None = None, meal_totals: dict | None = None) -> str:
    if products is None:
        products = _extract_meal_products(meal)
    if meal_totals is None:
        meal_totals = {
            "calories": float(getattr(meal, "calories", 0) or 0),
            "protein": float(getattr(meal, "protein", 0) or 0),
            "fat": float(getattr(meal, "fat", 0) or 0),
            "carbs": float(getattr(meal, "carbs", 0) or 0),
        }
    settings = MealRepository.get_kbju_settings(user_id)
    totals = MealRepository.get_daily_totals(user_id, target_date)
    workouts = WorkoutRepository.get_workouts_for_day(user_id, target_date)
    previous = MealCompletionCommentRepository.get_success_for_date(user_id, target_date)
    left = {
        "calories": (settings.calories if settings else 0) - totals.get("calories", 0),
        "protein": (settings.protein if settings else 0) - totals.get("protein", 0),
        "fat": (settings.fat if settings else 0) - totals.get("fat", 0),
        "carbs": (settings.carbs if settings else 0) - totals.get("carbs", 0),
    }
    product_lines = "\n".join(_format_product_line(item) for item in products) or f"- {meal.description or meal.raw_query or 'приём пищи'}"
    activity_lines = "\n".join(f"- {w.exercise}: {w.count} {w.variant or ''}, ~{float(w.calories or 0):.0f} ккал" for w in workouts) or "нет записанной активности"
    previous_lines = "\n".join(f"- {c.comment_text}" for c in previous if c.meal_id != meal.id and c.comment_text) or "нет"
    goal = getattr(settings, "goal", None) or "не указана"
    norm = f"{float(settings.calories):.0f} ккал, Б {float(settings.protein):.1f}, Ж {float(settings.fat):.1f}, У {float(settings.carbs):.1f}" if settings else "не указана"
    return (
        f"Тип приёма пищи: {display_meal_type(meal.meal_type)}\n"
        f"Дата: {target_date.isoformat()}\n"
        f"Количество продуктов в завершённом приёме: {len(products)}\n"
        f"Временной контекст: используй только тип приёма пищи ({display_meal_type(meal.meal_type)}); время употребления и время генерации комментария не переданы и не должны использоваться для выводов.\n"
        f"Время приёма пищи: не сохраняется отдельно; не предполагай его по текущему времени.\n\n"
        f"Продукты:\n{product_lines}\n\n"
        f"Итог приёма: {meal_totals['calories']:.0f} ккал, Б {meal_totals['protein']:.1f}, Ж {meal_totals['fat']:.1f}, У {meal_totals['carbs']:.1f}\n"
        f"Цель пользователя: {goal}\n"
        f"Дневная норма: {norm}\n"
        f"Уже съедено за день после этого приёма: {totals['calories']:.0f} ккал, Б {totals['protein']:.1f}, Ж {totals['fat']:.1f}, У {totals['carbs']:.1f}\n"
        f"Осталось: {left['calories']:.0f} ккал, Б {left['protein']:.1f}, Ж {left['fat']:.1f}, У {left['carbs']:.1f}\n"
        f"Активность сегодня:\n{activity_lines}\n\n"
        f"Предыдущие короткие комментарии сегодня:\n{previous_lines}"
    )


async def _finish_current_meal_and_return_to_diary(message: Message, state: FSMContext) -> None:
    """Завершает заполнение текущего приёма пищи и показывает короткий комментарий перед дневником."""
    data = await state.get_data()
    user_id = str(message.from_user.id)
    fallback_date_str = data.get("entry_date")
    try:
        fallback_date = date.fromisoformat(fallback_date_str) if isinstance(fallback_date_str, str) else date.today()
    except ValueError:
        fallback_date = date.today()

    meal_id = getattr(message.bot, "last_meal_ids", {}).get(user_id)
    meal_id = int(meal_id) if meal_id else None
    await state.clear()
    if not meal_id:
        await message.answer("✅ Приём пищи завершён.", reply_markup=ReplyKeyboardRemove())
        await _return_to_food_diary(message, user_id, fallback_date)
        return

    meal, target_date, meal_rows, products, meal_totals = _load_completed_meal_context(user_id, meal_id)
    if not meal:
        await message.answer("✅ Приём пищи завершён.", reply_markup=ReplyKeyboardRemove())
        await _return_to_food_diary(message, user_id, fallback_date)
        return

    existing = MealCompletionCommentRepository.get_by_meal(user_id, meal.id)
    if existing and existing.status in {"success", "fallback"} and existing.comment_text:
        if existing.status == "success" and existing.quota_request_id:
            try:
                ai_quota_service.consume(existing.quota_request_id, outcome="success", result_ref=f"meal_comment:{existing.id}")
            except Exception:
                logger.warning("Meal comment quota repair failed meal_id=%s", existing.meal_id)
        loading = await message.answer("🧠 Смотрю, что получилось...", reply_markup=ReplyKeyboardRemove())
        try:
            await message.bot.edit_message_text(
                chat_id=message.chat.id,
                message_id=loading.message_id,
                text=existing.comment_text,
                parse_mode="HTML",
                reply_markup=_meal_comment_keyboard(meal.id, target_date),
            )
        except TelegramBadRequest:
            await message.answer(existing.comment_text, parse_mode="HTML", reply_markup=_meal_comment_keyboard(meal.id, target_date))
        return

    logical_existing = MealCompletionCommentRepository.get_for_logical_meal(
        user_id,
        target_date,
        meal.meal_type,
    )
    if logical_existing and logical_existing.comment_text:
        if logical_existing.status == "success" and logical_existing.quota_request_id:
            try:
                ai_quota_service.consume(logical_existing.quota_request_id, outcome="success", result_ref=f"meal_comment:{logical_existing.id}")
            except Exception:
                logger.warning("Logical meal comment quota repair failed meal_id=%s", logical_existing.meal_id)
        await message.answer(
            logical_existing.comment_text,
            parse_mode="HTML",
            reply_markup=_meal_comment_keyboard(meal.id, target_date),
        )
        return

    if not hasattr(message.bot, "meal_comment_in_progress"):
        message.bot.meal_comment_in_progress = set()
    in_progress_key = (user_id, meal.id)
    if in_progress_key in message.bot.meal_comment_in_progress:
        await message.answer("🧠 Смотрю, что получилось...", reply_markup=ReplyKeyboardRemove())
        return
    message.bot.meal_comment_in_progress.add(in_progress_key)

    loading = await message.answer("🧠 Смотрю, что получилось...", reply_markup=ReplyKeyboardRemove())
    text = _build_local_meal_completion_text(user_id, target_date)
    metadata = {}
    status = "fallback"
    error_message = None
    prompt = ""
    quota_request_id = build_quota_request_id(
        "meal_comment",
        user_id,
        target_date.isoformat(),
        meal.meal_type,
    )
    reservation = None
    try:
        try:
            reservation = ai_quota_service.reserve(
                user_id,
                AIFeature.MEAL_COMPLETION_COMMENT,
                quota_request_id,
            )
        except (
            AIQuotaExceeded,
            AIAttemptLimitExceeded,
            AIGlobalLimitExceeded,
            AIOperationInProgress,
            AIOperationCooldown,
            AIQuotaAlreadyConsumed,
        ):
            reservation = None

        if reservation is None:
            raise RuntimeError("meal_comment_quota_unavailable")
        if len(meal_rows) > 1 and len(products) <= 1:
            raise ValueError(
                f"Incomplete meal completion context: meal_rows={len(meal_rows)}, products={len(products)}"
            )
        prompt = _build_meal_completion_prompt(user_id, meal, target_date, products, meal_totals)
        logger.info(
            "meal_completion_comment meal_id=%s meal_type=%s product_count=%s input_chars=%s status=requesting",
            meal.id,
            meal.meal_type,
            len(products),
            len(prompt),
        )
        raw_text, metadata = await asyncio.wait_for(asyncio.to_thread(deepseek_service.generate_meal_completion_comment, prompt, user_id=user_id, system_prompt=MEAL_COMPLETION_COMMENT_SYSTEM_PROMPT), timeout=75.0)
        text = _sanitize_meal_comment_html(raw_text)
        status = "success"
    except Exception as exc:  # не блокируем завершение приёма пищи
        if reservation is not None and status != "success":
            ai_quota_service.release(quota_request_id, outcome="comment_error")
        if str(exc) != "meal_comment_quota_unavailable":
            logger.warning(
                "Meal completion comment failed meal_id=%s error_type=%s",
                meal.id,
                safe_exception_summary(exc),
            )
            error_message = safe_exception_summary(exc)
    finally:
        logger.info(
            "meal_completion_comment meal_id=%s meal_type=%s product_count=%s input_chars=%s status=%s",
            meal.id,
            meal.meal_type,
            len(products),
            len(prompt),
            status,
        )
    try:
        saved_comment = MealCompletionCommentRepository.save(user_id, meal.id, target_date, meal.meal_type, comment_text=text, model=metadata.get("model") or DEEPSEEK_MODEL, status=status, input_tokens=metadata.get("input_tokens"), output_tokens=metadata.get("output_tokens"), total_tokens=metadata.get("total_tokens"), estimated_cost_usd=metadata.get("estimated_cost_usd"), error_message=error_message, quota_request_id=quota_request_id if status == "success" else None)
    except Exception as save_error:
        if reservation is not None:
            ai_quota_service.release(quota_request_id, outcome="comment_storage_error")
        message.bot.meal_comment_in_progress.discard(in_progress_key)
        logger.warning(
            "Meal completion comment storage failed meal_id=%s error_type=%s",
            meal.id,
            safe_exception_summary(save_error),
        )
        text = _build_local_meal_completion_text(user_id, target_date)
        await message.answer(text, parse_mode="HTML", reply_markup=_meal_comment_keyboard(meal.id, target_date))
        return
    if status == "success":
        try:
            ai_quota_service.consume(
                quota_request_id,
                outcome="success",
                result_ref=f"meal_comment:{saved_comment.id}",
            )
        except Exception as quota_commit_error:
            logger.warning(
                "Meal comment quota commit failed meal_id=%s error_type=%s",
                meal.id,
                safe_exception_summary(quota_commit_error),
            )
    message.bot.meal_comment_in_progress.discard(in_progress_key)
    try:
        await message.bot.edit_message_text(chat_id=message.chat.id, message_id=loading.message_id, text=text, parse_mode="HTML", reply_markup=_meal_comment_keyboard(meal.id, target_date))
    except TelegramBadRequest:
        await message.answer(text, parse_mode="HTML", reply_markup=_meal_comment_keyboard(meal.id, target_date))


async def _restore_current_meal_entry_screen(
    message: Message,
    state: FSMContext,
    data: dict | None = None,
    *,
    user_id: str | None = None,
) -> None:
    """Полностью восстанавливает экран текущего открытого приёма пищи."""
    data = data if data is not None else await state.get_data()
    meal_type = normalize_meal_type(data.get("meal_type"), fallback=MealType.SNACK.value)
    entry_date_str = data.get("entry_date")
    try:
        entry_date = date.fromisoformat(entry_date_str) if isinstance(entry_date_str, str) else date.today()
    except ValueError:
        entry_date = date.today()

    await _keep_meal_entry_open_after_save(
        message,
        state,
        user_id=user_id or str(message.from_user.id),
        entry_date=entry_date,
        meal_type=meal_type,
    )


async def _return_to_add_methods_from_method_input(
    message: Message,
    state: FSMContext,
    *,
    user_id: str | None = None,
) -> None:
    """Очищает черновик выбранного способа и возвращает к способам добавления."""
    data = await state.get_data()
    meal_type = normalize_meal_type(data.get("meal_type"), fallback=MealType.SNACK.value)
    entry_date_str = data.get("entry_date")
    meal_entry_open = bool(data.get("meal_entry_open"))

    # Черновики разных способов содержат много временных ключей. Полностью очищаем
    # FSM и восстанавливаем только навигационный контекст выбранного приёма пищи.
    await state.clear()
    await state.update_data(
        meal_type=meal_type,
        entry_date=entry_date_str,
        pending_add_method=None,
        meal_entry_open=meal_entry_open,
    )

    resolved_user_id = user_id or str(message.from_user.id)
    if data.get("meal_entry_open"):
        await _restore_current_meal_entry_screen(
            message,
            state,
            data,
            user_id=resolved_user_id,
        )
        return

    await state.set_state(MealEntryStates.choosing_meal_type)
    await _show_input_methods(message, state, user_id=resolved_user_id)


async def _select_meal_type_button_if_needed(message: Message, state: FSMContext, text: str) -> bool:
    """Обрабатывает кнопки выбора приёма пищи вне основного шага выбора."""
    if text not in MEAL_TYPE_BUTTONS:
        return False

    meal_type = MEAL_TYPE_BUTTONS[text]
    await state.update_data(meal_type=meal_type, pending_add_method=None)
    await message.answer(
        f"Выбрано: {display_meal_type(meal_type)}. Теперь отправь фото для анализа.",
        reply_markup=kbju_add_method_back_menu,
    )
    return True


async def _reroute_add_method_button_if_needed(message: Message, state: FSMContext, text: str) -> bool:
    """Перенаправляет на выбранный способ добавления, даже если активен другой state."""
    if text in MEAL_FINISH_BUTTON_TEXTS:
        await _finish_current_meal_and_return_to_diary(message, state)
        return True
    if text == ADD_METHOD_TEXTS["ai"]:
        await kbju_add_via_ai(message, state)
        return True
    if text == ADD_METHOD_TEXTS["deepseek"]:
        await kbju_add_via_deepseek(message, state)
        return True
    if text == ADD_METHOD_TEXTS["photo"]:
        await kbju_add_via_photo(message, state)
        return True
    if text == ADD_METHOD_TEXTS["photo_openai"]:
        await kbju_add_via_photo_openai(message, state)
        return True
    if text == ADD_METHOD_TEXTS["label"]:
        await kbju_add_via_label(message, state)
        return True
    if text == ADD_METHOD_TEXTS["label_openai"]:
        await kbju_add_via_label_openai(message, state)
        return True
    if text == ADD_METHOD_TEXTS["custom"]:
        await kbju_add_via_custom_product(message, state)
        return True
    return False


def _get_food_diary_message_store(bot) -> dict:
    """Возвращает хранилище message_id для дневника питания."""
    if not hasattr(bot, "food_diary_message_ids"):
        bot.food_diary_message_ids = {}
    return bot.food_diary_message_ids


def _get_food_diary_day_store(bot, user_id: str, target_date: date) -> dict:
    """Возвращает/создаёт хранилище message_id для пользователя и даты."""
    store = _get_food_diary_message_store(bot)
    user_store = store.setdefault(user_id, {})
    return user_store.setdefault(target_date.isoformat(), {"meals": {}, "summary": None})


async def _safe_edit_or_send_message(
    message: Message,
    *,
    stored_message_id: int | None,
    text: str,
    inline_keyboard: InlineKeyboardMarkup | None = None,
) -> int | None:
    """Пытается отредактировать сообщение, при ошибке отправляет новое."""
    if stored_message_id:
        try:
            await message.bot.edit_message_text(
                chat_id=message.chat.id,
                message_id=stored_message_id,
                text=text,
                parse_mode="HTML",
                reply_markup=inline_keyboard,
            )
            return stored_message_id
        except TelegramBadRequest as exc:
            if "message is not modified" in str(exc).lower():
                return stored_message_id
            logger.info(
                "Не удалось отредактировать сообщение message_id=%s error_type=%s",
                stored_message_id,
                safe_exception_summary(exc),
            )

    try:
        sent = await message.answer(text, parse_mode="HTML", reply_markup=inline_keyboard)
    except TelegramBadRequest:
        sent = await message.answer(text, reply_markup=inline_keyboard)
    return sent.message_id if sent else None


async def _delete_stored_message(message: Message, stored_message_id: int | None) -> None:
    """Удаляет ранее сохранённое сообщение дневника, если оно существует."""
    if not stored_message_id:
        return
    try:
        await message.bot.delete_message(chat_id=message.chat.id, message_id=stored_message_id)
    except TelegramBadRequest as exc:
        logger.info(
            "Не удалось удалить сообщение message_id=%s error_type=%s",
            stored_message_id,
            safe_exception_summary(exc),
        )


async def _render_day_meals_messages(
    message: Message,
    user_id: str,
    target_date: date,
    *,
    include_back: bool = False,
    changed_meal_type: str | None = None,
    force_refresh: bool = False,
) -> None:
    """Точечно рендерит сообщения по приёмам пищи + отдельное сообщение итогов дня."""
    from collections import defaultdict
    from utils.meal_formatters import (
        format_food_diary_header,
        format_meal_message,
        format_daily_totals_message,
        build_meal_actions_keyboard,
        build_daily_totals_keyboard,
    )

    _set_selected_food_diary_date(message.bot, user_id, target_date)
    meals = MealRepository.get_meals_for_date(user_id, target_date)
    daily_totals = MealRepository.get_daily_totals(user_id, target_date)
    settings = MealRepository.get_kbju_settings(user_id)
    day_str = target_date.strftime("%d.%m.%Y")

    grouped: dict[str, list] = defaultdict(list)
    for meal in meals:
        grouped[normalize_meal_type(getattr(meal, "meal_type", None))].append(meal)

    day_store = _get_food_diary_day_store(message.bot, user_id, target_date)
    meal_messages: dict[str, int] = day_store.setdefault("meals", {})

    if force_refresh:
        for stored_id in list(meal_messages.values()):
            await _delete_stored_message(message, stored_id)
        meal_messages.clear()
        if day_store.get("summary"):
            await _delete_stored_message(message, day_store.get("summary"))
            day_store["summary"] = None

    if not meals:
        for stored_id in list(meal_messages.values()):
            await _delete_stored_message(message, stored_id)
        meal_messages.clear()
        if day_store.get("summary"):
            await _delete_stored_message(message, day_store.get("summary"))
            day_store["summary"] = None

        empty_text = (
            f"{format_food_diary_header(day_str)}\n\n"
            "Пока нет записей за выбранный день. Добавь приём пищи 👇"
        )
        keyboard = build_daily_totals_keyboard(target_date, include_back=include_back)
        day_store["summary"] = await _safe_edit_or_send_message(
            message,
            stored_message_id=None,
            text=empty_text,
            inline_keyboard=keyboard,
        )
        if include_back:
            push_menu_stack(message.bot, kbju_menu)
            await message.answer("⬇️ Кнопки управления", reply_markup=kbju_menu)
        return

    meal_types_to_update = (
        [normalize_meal_type(changed_meal_type)]
        if changed_meal_type
        else [meal_type for meal_type in MEAL_TYPE_ORDER if grouped.get(meal_type)]
    )

    first_meal_type = next((mt for mt in MEAL_TYPE_ORDER if grouped.get(mt)), None)
    for meal_type in meal_types_to_update:
        items = grouped.get(meal_type, [])
        if not items:
            if meal_type in meal_messages:
                await _delete_stored_message(message, meal_messages.get(meal_type))
                meal_messages.pop(meal_type, None)
            continue

        include_header = meal_type == first_meal_type
        meal_text = format_meal_message(
            meal_type,
            items,
            day_str=day_str,
            include_date_header=include_header,
        )
        meal_messages[meal_type] = await _safe_edit_or_send_message(
            message,
            stored_message_id=meal_messages.get(meal_type),
            text=meal_text,
            inline_keyboard=build_meal_actions_keyboard(meal_type, target_date),
        )

    for stale_type in [mt for mt in list(meal_messages.keys()) if mt not in grouped]:
        await _delete_stored_message(message, meal_messages.get(stale_type))
        meal_messages.pop(stale_type, None)

    if not include_back:
        if day_store.get("summary"):
            await _delete_stored_message(message, day_store.get("summary"))
            day_store["summary"] = None

        sent = await message.answer("⬇️ Кнопки управления", reply_markup=kbju_menu)
        day_store["summary"] = sent.message_id if sent else None
        return

    summary_text = format_daily_totals_message(
        daily_totals,
        day_str,
        settings=settings,
        include_action_prompt=False,
    )
    day_store["summary"] = await _safe_edit_or_send_message(
        message,
        stored_message_id=day_store.get("summary"),
        text=summary_text,
        inline_keyboard=build_daily_totals_keyboard(target_date, include_back=include_back),
    )
    push_menu_stack(message.bot, kbju_menu)
    await message.answer("⬇️ Кнопки управления", reply_markup=kbju_menu)


async def _send_ai_error_message(message: Message, error: Exception) -> None:
    if isinstance(error, GeminiServiceTemporaryUnavailableError):
        await message.answer(AI_TEMPORARY_UNAVAILABLE_TEXT)
        return
    if isinstance(error, GeminiServiceQuotaError):
        await message.answer(AI_QUOTA_UNAVAILABLE_TEXT)
        return
    if isinstance(error, GeminiServiceAuthError):
        await message.answer(AI_CONFIG_UNAVAILABLE_TEXT)
        return
    await message.answer("⚠️ Не удалось получить ответ AI. Попробуй ещё раз позже.")


async def _send_openai_label_error_message(message: Message, error: Exception) -> None:
    if isinstance(error, OpenAILabelServiceConfigError):
        await message.answer("OpenAI API key не настроен на сервере.")
        return
    if isinstance(error, OpenAILabelServiceTimeoutError):
        await message.answer("⚠️ OpenAI не успел обработать фото. Попробуй ещё раз позже.")
        return
    if isinstance(error, OpenAILabelServiceInvalidJSONError):
        await message.answer("⚠️ OpenAI вернул невалидный JSON. Попробуй ещё раз или используй обычный анализ этикетки.")
        return
    if isinstance(error, OpenAILabelServiceAPIError):
        await message.answer("⚠️ OpenAI API вернул ошибку. Попробуй ещё раз позже.")
        return
    await message.answer("⚠️ Не удалось получить ответ OpenAI. Попробуй ещё раз позже.")


async def _send_openai_food_error_message(message: Message, error: Exception) -> None:
    if isinstance(error, OpenAILabelServiceConfigError):
        await message.answer("OpenAI API key не настроен на сервере.")
        return
    if isinstance(error, OpenAILabelServiceTimeoutError):
        await message.answer("⚠️ OpenAI не успел обработать фото. Попробуй ещё раз позже.")
        return
    if isinstance(error, OpenAILabelServiceInvalidJSONError):
        await message.answer("⚠️ OpenAI вернул невалидный JSON. Попробуй ещё раз или используй обычный анализ еды по фото.")
        return
    if isinstance(error, OpenAILabelServiceAPIError):
        await message.answer("⚠️ OpenAI API вернул ошибку. Попробуй ещё раз позже.")
        return
    await message.answer("⚠️ Не удалось получить ответ OpenAI. Попробуй ещё раз позже.")


async def _run_gemini_task(func, *args, timeout_seconds: float = 45.0, **kwargs):
    """Запускает синхронный Gemini-вызов в отдельном потоке с timeout."""
    if gemini_service is None:
        raise GeminiServiceTemporaryUnavailableError("Gemini service is not initialized")
    try:
        return await asyncio.wait_for(asyncio.to_thread(func, *args, **kwargs), timeout=timeout_seconds)
    except asyncio.TimeoutError as exc:
        raise GeminiServiceTemporaryUnavailableError(AI_TIMEOUT_UNAVAILABLE_TEXT) from exc


async def _run_openai_label_task(func, *args, timeout_seconds: float = 45.0, **kwargs):
    """Запускает синхронный OpenAI-вызов анализа этикетки в отдельном потоке с timeout."""
    try:
        return await asyncio.wait_for(asyncio.to_thread(func, *args, **kwargs), timeout=timeout_seconds)
    except asyncio.TimeoutError as exc:
        service = getattr(func, "__self__", None)
        log_ai_usage(
            provider="openai",
            feature=kwargs.get("feature") or "label_analysis",
            model=getattr(service, "model", "unknown"),
            status="error",
            user_id=kwargs.get("user_id"),
            latency_ms=int(timeout_seconds * 1000),
            error_message="OpenAI API request timed out",
        )
        raise OpenAILabelServiceTimeoutError("OpenAI API request timed out") from exc


class AllProvidersUnavailableError(RuntimeError):
    """Все AI-провайдеры анализа изображения недоступны."""


@dataclass(frozen=True)
class ProviderAnalysisResult:
    """Результат AI-анализа с провайдером, который реально дал ответ."""

    payload: dict
    provider: str


async def _analyze_image_with_openai(
    openai_analyzer,
    image_data: bytes,
    *,
    user_id: str | int | None = None,
    feature: str,
    operation_log_name: str,
    comment: str | None = None,
) -> Optional[dict]:
    """Выполняет анализ изображения через OpenAI и логирует fallback-вызов."""
    try:
        kwargs = {"user_id": user_id, "feature": feature}
        if comment:
            kwargs["comment"] = comment
        result = await _run_openai_label_task(
            openai_analyzer,
            image_data,
            **kwargs,
        )
    except Exception as exc:
        logger.error(
            "[OpenAI] %s failed error_type=%s",
            operation_log_name,
            safe_exception_summary(exc),
            exc_info=True,
        )
        raise

    logger.info("[OpenAI] %s completed successfully", operation_log_name)
    return result


async def _analyze_label_with_openai(image_data: bytes, *, user_id: str | int | None = None) -> Optional[dict]:
    """Выполняет анализ этикетки через OpenAI и логирует результат fallback-вызова."""
    result = await _analyze_image_with_openai(
        openai_label_service.extract_kbju_from_label,
        image_data,
        user_id=user_id,
        feature="label_analysis_fallback",
        operation_log_name="label analysis",
    )
    logger.info("[Fallback] OpenAI fallback used for label analysis")
    return result


async def _run_image_analysis_with_openai_fallback(
    gemini_analyzer,
    image_data: bytes,
    *,
    user_id: str | int | None = None,
    openai_analyzer,
    openai_feature: str,
    operation_type: str,
    success_validator=None,
    comment: str | None = None,
    quota_request_id: str | None = None,
) -> ProviderAnalysisResult:
    """Запускает анализ изображения через Gemini, а при недоступности/пустом ответе — через OpenAI."""
    logger.info("Gemini attempt for %s", operation_type)
    try:
        if comment:
            gemini_result = await _run_gemini_task(gemini_analyzer, image_data, comment)
        else:
            gemini_result = await _run_gemini_task(gemini_analyzer, image_data)
        if success_validator is None or success_validator(gemini_result):
            logger.info("%s completed successfully via Gemini", operation_type)
            return ProviderAnalysisResult(payload=gemini_result, provider="gemini")
        logger.warning("Gemini returned no usable result for %s", operation_type)
    except Exception as gemini_error:
        logger.error(
            "Gemini error for %s error_type=%s",
            operation_type,
            safe_exception_summary(gemini_error),
            exc_info=True,
        )

    logger.info("Fallback: переход на OpenAI для %s", operation_type)
    if quota_request_id:
        ai_quota_service.register_additional_provider_attempt(quota_request_id)
    try:
        openai_kwargs = {
            "user_id": user_id,
            "feature": openai_feature,
            "operation_log_name": operation_type,
        }
        if comment:
            openai_kwargs["comment"] = comment
        openai_result = await _analyze_image_with_openai(
            openai_analyzer,
            image_data,
            **openai_kwargs,
        )
        if success_validator is None or success_validator(openai_result):
            logger.info("OpenAI success for %s", operation_type)
            logger.info("%s completed successfully via OpenAI", operation_type)
            return ProviderAnalysisResult(payload=openai_result, provider="openai")
        logger.error("OpenAI returned no usable result for %s", operation_type)
    except Exception as openai_error:
        logger.error(
            "OpenAI error for %s error_type=%s",
            operation_type,
            safe_exception_summary(openai_error),
            exc_info=True,
        )
        raise AllProvidersUnavailableError("All providers unavailable") from openai_error

    logger.error("%s failed: all providers unavailable", operation_type)
    raise AllProvidersUnavailableError("All providers unavailable")


async def _run_label_analysis_with_openai_fallback(
    analyzer,
    image_data: bytes,
    *,
    user_id: str | int | None = None,
    quota_request_id: str | None = None,
):
    """Запускает анализ этикетки через Gemini, а при недоступности всех ключей — через OpenAI."""
    try:
        return await _run_gemini_task(analyzer, image_data)
    except Exception as gemini_error:
        logger.error(
            "Gemini error for label analysis error_type=%s",
            safe_exception_summary(gemini_error),
            exc_info=True,
        )
        logger.info("[Fallback] All Gemini keys failed. Switching to OpenAI.")
        if quota_request_id:
            ai_quota_service.register_additional_provider_attempt(quota_request_id)
        try:
            return await _analyze_label_with_openai(image_data, user_id=user_id)
        except Exception as openai_error:
            logger.error("[Error] All providers unavailable")
            raise AllProvidersUnavailableError("All providers unavailable") from openai_error


def _has_food_photo_result(payload: Optional[dict]) -> bool:
    """Проверяет, что AI вернул пригодный результат анализа еды по фото."""
    return validate_photo_food_payload(payload) is not None


async def _run_food_photo_analysis_with_openai_fallback(
    analyzer,
    image_data: bytes,
    *,
    user_id: str | int | None = None,
    comment: str | None = None,
    quota_request_id: str | None = None,
) -> ProviderAnalysisResult:
    """Запускает анализ еды по фото через Gemini с fallback на OpenAI."""
    try:
        result = await _run_image_analysis_with_openai_fallback(
            analyzer,
            image_data,
            user_id=user_id,
            openai_analyzer=openai_label_service.analyze_food_photo_openai,
            openai_feature="food_photo_analysis",
            operation_type="анализа еды по фото",
            success_validator=_has_food_photo_result,
            comment=comment,
            quota_request_id=quota_request_id,
        )
        if result.provider == "gemini":
            logger.info("Анализ еды по фото завершён успешно через Gemini")
        elif result.provider == "openai":
            logger.info("Анализ еды по фото завершён успешно через OpenAI")
        logger.info("final_food_photo_analysis_provider=%s", result.provider)
        return result
    except AllProvidersUnavailableError as error:
        logger.error("Анализ еды по фото завершён ошибкой: все провайдеры недоступны")
        raise AllProvidersUnavailableError("All providers unavailable") from error


def reset_user_state(message: Message, *, keep_supplements: bool = False):
    """Сбрасывает состояние пользователя."""
    # TODO: Заменить на FSM clear
    pass


async def _prompt_meal_type_selection(message: Message, state: FSMContext, pending_add_method: str | None = None):
    """Просит выбрать тип приёма пищи и сохраняет контекст добавления."""
    payload = {"entry_date": date.today().isoformat()}
    if pending_add_method:
        payload["pending_add_method"] = pending_add_method
    await state.set_state(MealEntryStates.choosing_meal_type)
    await state.update_data(**payload)
    push_menu_stack(message.bot, kbju_meal_type_menu)
    await message.answer(
        "Сначала выбери, к какому приёму пищи добавить запись:",
        reply_markup=kbju_meal_type_menu,
    )


async def _ensure_meal_type_selected(
    message: Message,
    state: FSMContext,
    pending_add_method: str,
) -> bool:
    """Проверяет, что meal_type выбран; иначе запрашивает выбор."""
    data = await state.get_data()
    raw_meal_type = str(data.get("meal_type") or "").strip().lower()
    if raw_meal_type in {
        MealType.BREAKFAST.value,
        MealType.LUNCH.value,
        MealType.DINNER.value,
        MealType.SNACK.value,
    }:
        return True
    await _prompt_meal_type_selection(message, state, pending_add_method=pending_add_method)
    return False


def _build_my_products_entry_keyboard(meal_type: str) -> InlineKeyboardMarkup:
    normalized_meal_type = normalize_meal_type(meal_type, fallback=MealType.SNACK.value)
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🍽 Мои блюда",
                    callback_data=f"meal_entry_my_dishes:{normalized_meal_type}:1",
                )
            ],
            [
                InlineKeyboardButton(
                    text="📦 Мои продукты",
                    callback_data=f"meal_entry_my_products:{normalized_meal_type}:1",
                )
            ]
        ]
    )


async def _show_input_methods(message: Message, state: FSMContext, *, user_id: str | None = None) -> None:
    """Показывает выбранный приём пищи или меню способов добавления еды."""
    await state.set_state(MealEntryStates.choosing_meal_type)
    data = await state.get_data()
    meal_type = normalize_meal_type(data.get("meal_type"), fallback=MealType.SNACK.value)
    entry_date_str = data.get("entry_date")
    try:
        entry_date = date.fromisoformat(entry_date_str) if isinstance(entry_date_str, str) else date.today()
    except ValueError:
        entry_date = date.today()

    current_meal_items = [
        meal
        for meal in MealRepository.get_meals_for_date(user_id or str(message.from_user.id), entry_date)
        if normalize_meal_type(getattr(meal, "meal_type", None)) == meal_type
    ]
    if current_meal_items:
        await _keep_meal_entry_open_after_save(
            message,
            state,
            user_id=user_id or str(message.from_user.id),
            entry_date=entry_date,
            meal_type=meal_type,
            current_meal_items=current_meal_items,
        )
        return

    await state.update_data(
        entry_date=entry_date.isoformat(),
        meal_type=meal_type,
        pending_add_method=None,
        meal_entry_open=True,
    )
    text = (
        "Теперь выбери способ добавления приёма пищи.\n\n"
        "💡 Для повторного добавления используй «📦 Мои продукты» или «🍽 Мои блюда».\n\n"
        f"{format_free_ai_status_block(user_id or str(message.from_user.id))}"
    )
    push_menu_stack(message.bot, kbju_add_menu)
    await message.answer(text, reply_markup=_build_my_products_entry_keyboard(meal_type))
    await message.answer("⬇️ Кнопки управления", reply_markup=kbju_add_menu)


async def _show_my_products_page(
    message: Message,
    state: FSMContext,
    meal_type: str,
    page: int,
    *,
    user_id: str | None = None,
    edit_message: bool = False,
    back_callback_data: str | None = None,
    source_filter: str | None = None,
    show_source_filter_block: bool = False,
) -> bool:
    user_id = user_id or str(message.from_user.id)
    page_items, page, total_pages = _get_my_products_page_items(
        user_id,
        page,
        source_filter=source_filter,
    )
    await state.update_data(
        my_products_page=page,
        meal_type=meal_type,
        my_products_source_filter=source_filter,
        in_my_products_section=True,
    )
    if not page_items:
        return False
    title = MY_PRODUCTS_SOURCE_FILTERS.get(source_filter or "", {}).get("title", "🕒 <b>Недавние продукты")
    text = _format_my_products_text(page_items, page, title=title)
    reply_markup = _build_my_products_keyboard(
        page_items,
        meal_type,
        page,
        total_pages,
        back_callback_data=back_callback_data,
    )
    if edit_message:
        await message.edit_text(text, reply_markup=reply_markup, parse_mode="HTML")
    else:
        await message.answer(text, reply_markup=reply_markup, parse_mode="HTML")
    if show_source_filter_block:
        await _show_my_products_source_filter_block(message)
    return True


def _build_meal_entry_post_save_keyboard(meal_type: str, entry_date: date) -> InlineKeyboardMarkup:
    """Inline-действия под сообщением после сохранения продукта в текущий приём пищи."""
    normalized_meal_type = normalize_meal_type(meal_type, fallback=MealType.SNACK.value)
    iso_date = entry_date.isoformat()
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✏️ Редактировать",
                    callback_data=f"edit_meal:{normalized_meal_type}:{iso_date}",
                ),
                InlineKeyboardButton(
                    text="🤖 AI-лимиты",
                    callback_data="meal_entry_ai_limits",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="📦 Мои продукты",
                    callback_data=f"meal_entry_my_products:{normalized_meal_type}:1",
                ),
                InlineKeyboardButton(
                    text="🍽 Мои блюда",
                    callback_data=f"meal_entry_my_dishes:{normalized_meal_type}:1",
                )
            ],
        ]
    )


def _build_meal_entry_edit_keyboard(meal_type: str, entry_date: date) -> InlineKeyboardMarkup:
    """Inline-действие редактирования под сообщением о сохранённом продукте."""
    normalized_meal_type = normalize_meal_type(meal_type, fallback=MealType.SNACK.value)
    iso_date = entry_date.isoformat()
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✏️ Редактировать",
                    callback_data=f"edit_meal:{normalized_meal_type}:{iso_date}",
                )
            ]
        ]
    )

def _get_all_my_products_for_search(user_id: str, source_filter: str | None = None) -> list[MyProductItem]:
    return _get_my_products_catalog(user_id, source_filter)


async def _show_my_products_search_results(
    message: Message,
    state: FSMContext,
    *,
    user_id: str,
    meal_type: str,
    query: str,
    page: int = 1,
    edit_message: bool = False,
) -> None:
    data = await state.get_data()
    source_filter = data.get("my_products_source_filter")
    all_items = _get_all_my_products_for_search(user_id, source_filter=source_filter)
    matched_items = _search_my_products(all_items, query)
    await state.update_data(my_products_search_query=query, meal_type=meal_type, my_products_source_filter=source_filter)

    if not matched_items:
        send = message.edit_text if edit_message else message.answer
        await send(
            "Ничего не нашёл 😕\n"
            "Попробуй ввести другое название или часть названия.",
            reply_markup=_build_my_products_search_empty_keyboard(meal_type),
        )
        return

    total_pages = total_pages_for(len(matched_items), MY_PRODUCTS_PAGE_SIZE)
    page = clamp_page(page - 1, total_pages) + 1
    start = (page - 1) * MY_PRODUCTS_PAGE_SIZE
    page_items = matched_items[start : start + MY_PRODUCTS_PAGE_SIZE]
    await state.update_data(my_products_search_page=page)
    text = _format_my_products_search_results_text(query, page_items, page)
    reply_markup = _build_my_products_search_results_keyboard(
        page_items,
        meal_type,
        page,
        total_pages,
    )
    if edit_message:
        await message.edit_text(text, reply_markup=reply_markup, parse_mode="HTML")
    else:
        await message.answer(text, reply_markup=reply_markup, parse_mode="HTML")


def _format_product_weight_g(value: float | int) -> str:
    numeric = float(value)
    return f"{numeric:.0f}" if numeric.is_integer() else f"{numeric:.2f}".rstrip("0").rstrip(".")


def _render_my_product_confirm_text(meal_type: str, meal, amount_g: int = 100) -> str:
    meal_ui = display_meal_type_with_bold_name(meal_type)
    if isinstance(meal, MyProductItem):
        title = meal.title
        calories = meal.calories
        protein = meal.protein
        fat = meal.fat
        carbs = meal.carbs
        unit_weight_g = meal.unit_weight_g
        unit_name = meal.unit_name
        package_weight_g = meal.package_weight_g
        package_units = meal.package_units
    else:
        title = _normalize_my_product_title(meal)
        calories = float(meal.calories or 0)
        protein = float(meal.protein or 0)
        fat = float(meal.fat or 0)
        carbs = float(meal.carbs or 0)
        unit_weight_g = None
        unit_name = None
        package_weight_g = None
        package_units = None
    safe_title = html.escape(title or "Продукт")
    lines = [
        f"{meal_ui} • <b>Добавить продукт?</b>\n\n"
        f"<b>Продукт:</b> {safe_title}",
        "",
        f"⚖️ <b>Последняя порция:</b> {_format_product_weight_g(amount_g)} г",
    ]
    if unit_weight_g is not None:
        safe_unit_name = html.escape(unit_name or "шт.")
        lines.append(
            f"1️⃣ <b>1 {safe_unit_name}:</b> {_format_product_weight_g(unit_weight_g)} г"
        )
    if package_weight_g is not None or package_units is not None:
        package_parts = []
        if package_weight_g is not None:
            package_parts.append(f"{_format_product_weight_g(package_weight_g)} г")
        if package_units is not None:
            package_parts.append(f"{package_units} шт.")
        lines.append(f"📦 <b>Упаковка:</b> {' • '.join(package_parts)}")
    lines.extend(
        [
            "",
            f"🔥 <b>Калории:</b> {calories:.0f} ккал",
            f"🥩 <b>Белки:</b> {protein:.1f} г",
            f"🥑 <b>Жиры:</b> {fat:.1f} г",
            f"🍚 <b>Углеводы:</b> {carbs:.1f} г",
            "",
            "<b>Выбери действие:</b>",
        ]
    )
    return "\n".join(lines)


def _enrich_my_product_from_reference(item: MyProductItem, user_id: str | None) -> MyProductItem:
    """Дополняет JSON старой/новой записи постоянными данными saved_products."""
    if not user_id:
        return item
    try:
        saved = SavedProductRepository.get_by_name(str(user_id), item.title)
    except Exception as exc:
        logger.warning(
            "Не удалось прочитать saved_products error_type=%s",
            safe_exception_summary(exc),
        )
        return item
    if saved is None:
        return item

    amount_g = item.amount_g
    calories = item.calories
    protein = item.protein
    fat = item.fat
    carbs = item.carbs
    if saved.last_weight_g and round(float(saved.last_weight_g), 6) != round(float(item.amount_g), 6):
        ratio = float(saved.last_weight_g) / float(item.amount_g or 100)
        amount_g = max(1, int(round(float(saved.last_weight_g))))
        calories *= ratio
        protein *= ratio
        fat *= ratio
        carbs *= ratio

    return MyProductItem(
        source_meal_id=item.source_meal_id,
        product_index=item.product_index,
        title=item.title,
        amount_g=amount_g,
        calories=calories,
        protein=protein,
        fat=fat,
        carbs=carbs,
        unit_weight_g=saved.unit_weight_g or item.unit_weight_g,
        unit_name=saved.unit_name or item.unit_name,
        package_weight_g=saved.package_weight_g or item.package_weight_g,
        package_units=saved.package_units or item.package_units,
    )


def _sync_saved_product_reference(
    user_id: str,
    item: MyProductItem,
    *,
    last_weight_g: float,
) -> None:
    """Синхронизирует справочник, не влияя на уже сохранённый приём пищи."""
    try:
        SavedProductRepository.upsert(
            user_id=str(user_id),
            name=item.title,
            last_weight_g=last_weight_g,
            unit_weight_g=item.unit_weight_g,
            unit_name=item.unit_name,
            package_weight_g=item.package_weight_g,
            package_units=item.package_units,
        )
    except Exception as exc:
        logger.warning(
            "Приём пищи сохранён, но saved_products не обновлён error_type=%s",
            safe_exception_summary(exc),
        )


def _get_my_product_from_source_meal(
    meal,
    product_index: int | None,
    *,
    user_id: str | None = None,
) -> MyProductItem:
    if product_index is not None:
        products = _parse_my_products(meal)
        if 0 <= product_index < len(products):
            item = _build_my_product_item_from_product(meal, products[product_index], product_index)
            return _enrich_my_product_from_reference(item, user_id)
    return _enrich_my_product_from_reference(_build_my_product_item_from_meal(meal), user_id)


def _single_product_json_for_my_product(meal, item: MyProductItem, ratio: float = 1.0, amount_g: float | None = None) -> str | None:
    if item.product_index is None:
        return meal.products_json
    products = _parse_my_products(meal)
    if 0 <= item.product_index < len(products):
        product = dict(products[item.product_index])
        if amount_g is not None:
            product["grams"] = amount_g
        reference_fields = {
            "unit_weight_g": item.unit_weight_g,
            "unit_name": item.unit_name,
            "package_weight_g": item.package_weight_g,
            "package_units": item.package_units,
        }
        for key, value in reference_fields.items():
            if value is not None:
                product[key] = value
        scaled_values = {
            "kcal": float(item.calories) * ratio,
            "calories": float(item.calories) * ratio,
            "protein": float(item.protein) * ratio,
            "protein_g": float(item.protein) * ratio,
            "fat": float(item.fat) * ratio,
            "fat_total_g": float(item.fat) * ratio,
            "carbs": float(item.carbs) * ratio,
            "carbohydrates_total_g": float(item.carbs) * ratio,
        }
        for key, value in scaled_values.items():
            if key in product:
                product[key] = value
        return json.dumps([product], ensure_ascii=False)
    return None


def _parse_my_product_index(raw_index: str | None) -> int | None:
    if raw_index in (None, ""):
        return None
    try:
        return int(raw_index)
    except (TypeError, ValueError):
        return None

def _extract_my_product_amount_g_from_meal(meal) -> int:
    """Возвращает исходную граммовку продукта из products_json, если доступно."""
    raw_products = getattr(meal, "products_json", None)
    if not raw_products:
        return 100
    try:
        parsed = json.loads(raw_products)
    except Exception:
        return 100
    if not isinstance(parsed, list) or not parsed:
        return 100
    grams = parsed[0].get("grams")
    try:
        grams_value = float(grams)
    except (TypeError, ValueError):
        return 100
    if grams_value <= 0:
        return 100
    return max(1, int(round(grams_value)))


def _build_my_product_confirm_keyboard(
    source_meal_id: int,
    meal_type: str,
    page: int,
    product_index: int | None = None,
    *,
    save_token: str,
    include_delete: bool = False,
    item: MyProductItem | None = None,
) -> InlineKeyboardMarkup:
    product_idx = "" if product_index is None else str(product_index)
    callback_token = save_token[:MEAL_SAVE_CALLBACK_TOKEN_LENGTH]
    rows = [
        [
            InlineKeyboardButton(
                text="✅ Добавить",
                callback_data=(
                    f"mpc:{meal_type}:{page}:{source_meal_id}:"
                    f"{product_idx}:{callback_token}"
                ),
            )
        ],
    ]
    if item is not None and item.unit_weight_g is not None:
        unit_label = _truncate_my_product_name(item.unit_name or "шт.", limit=20)
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"1️⃣ Добавить 1 {unit_label}",
                    callback_data=(
                        f"mpu:{meal_type}:{page}:{source_meal_id}:"
                        f"{product_idx}:{callback_token}"
                    ),
                )
            ]
        )
    rows.append(
        [InlineKeyboardButton(text="✏️ Изменить вес", callback_data=f"my_product_edit_weight:{meal_type}:{page}:{source_meal_id}:{product_idx}")]
    )
    if include_delete:
        rows.append(
            [
                InlineKeyboardButton(
                    text="🗑 Удалить",
                    callback_data=f"custom_product_delete_ask:{meal_type}:{page}:{source_meal_id}:{product_idx}",
                )
            ]
        )
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data=f"my_product_back:{meal_type}:{page}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def _ensure_my_product_save_token(
    state: FSMContext,
    data: dict | None = None,
    *,
    renew: bool = False,
) -> str:
    current_data = data if data is not None else await state.get_data()
    save_token = current_data.get("my_product_save_token")
    if renew or not _is_valid_meal_save_token(save_token):
        save_token = _new_meal_save_token()
        await state.update_data(my_product_save_token=save_token)
    return save_token


def _render_my_product_weight_editor_text(item: MyProductItem, draft_amount_g: int | None = None) -> str:
    """Текст экрана быстрого изменения веса продукта из истории."""
    current_amount = int(item.amount_g or 100)
    new_amount = int(draft_amount_g or current_amount)
    ratio = new_amount / float(current_amount or 100)
    lines = [
        "<b>✏️ Изменение веса продукта</b>",
        "",
        f"<b>Продукт:</b> {html.escape(item.title or 'Продукт')}",
        "",
        _format_kbju_summary_block(
            {
                "calories": float(item.calories) * ratio,
                "protein": float(item.protein) * ratio,
                "fat": float(item.fat) * ratio,
                "carbs": float(item.carbs) * ratio,
            }
        ),
        "",
        f"⚖️ <b>Текущий вес:</b> {current_amount} г",
    ]
    if new_amount != current_amount:
        lines.append(f"⚖️ <b>Новый вес:</b> {new_amount} г")
    lines.extend(["", "<b>Выбери действие или введи вручную:</b>"])
    return "\n".join(lines)


def _build_my_product_weight_editor_keyboard() -> InlineKeyboardMarkup:
    """Клавиатура изменения веса продукта из истории по аналогии с редактором приёма пищи."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="−100 г", callback_data="my_product_wchg:-100"),
                InlineKeyboardButton(text="−50 г", callback_data="my_product_wchg:-50"),
                InlineKeyboardButton(text="+50 г", callback_data="my_product_wchg:50"),
                InlineKeyboardButton(text="+100 г", callback_data="my_product_wchg:100"),
            ],
            [
                InlineKeyboardButton(text="−25 г", callback_data="my_product_wchg:-25"),
                InlineKeyboardButton(text="−10 г", callback_data="my_product_wchg:-10"),
                InlineKeyboardButton(text="+10 г", callback_data="my_product_wchg:10"),
                InlineKeyboardButton(text="+25 г", callback_data="my_product_wchg:25"),
            ],
            [
                InlineKeyboardButton(text="−5 г", callback_data="my_product_wchg:-5"),
                InlineKeyboardButton(text="−1 г", callback_data="my_product_wchg:-1"),
                InlineKeyboardButton(text="+1 г", callback_data="my_product_wchg:1"),
                InlineKeyboardButton(text="+5 г", callback_data="my_product_wchg:5"),
            ],
            [InlineKeyboardButton(text="⌨️ Ввести вручную", callback_data="my_product_wmanual")],
            [InlineKeyboardButton(text="✅ Сохранить", callback_data="my_product_wsave")],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="my_product_wback")],
        ]
    )


def _build_adjusted_my_product_item(item: MyProductItem, amount_g: int) -> MyProductItem:
    """Возвращает копию продукта из истории с пересчитанными КБЖУ под новый вес."""
    ratio = amount_g / float(item.amount_g or 100)
    return MyProductItem(
        source_meal_id=item.source_meal_id,
        product_index=item.product_index,
        title=item.title,
        amount_g=amount_g,
        calories=float(item.calories) * ratio,
        protein=float(item.protein) * ratio,
        fat=float(item.fat) * ratio,
        carbs=float(item.carbs) * ratio,
        unit_weight_g=item.unit_weight_g,
        unit_name=item.unit_name,
        package_weight_g=item.package_weight_g,
        package_units=item.package_units,
    )


MY_DISHES_PAGE_SIZE = 8


def _format_saved_dishes_page(dishes: list, page: int) -> str:
    lines = ["🍽 <b>Мои блюда</b>", "", "Выбери блюдо, чтобы посмотреть состав и добавить порцию:", ""]
    for index, dish in enumerate(dishes, start=1):
        snapshot = dish_to_snapshot(dish)
        totals = calculate_dish_totals(snapshot)
        lines.append(
            f"{_format_emoji_number(index)} {html.escape(dish.name)} — "
            f"{calculate_dish_weight(snapshot):.0f} г, {totals['calories']:.0f} ккал"
        )
    lines.extend(["", f"Страница {page}"])
    return "\n".join(lines)


def _build_saved_dishes_keyboard(
    dishes: list,
    meal_type: str,
    page: int,
    total_pages: int,
) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                text=f"{_format_emoji_number(index)} {_truncate_product_name(dish.name)}",
                callback_data=f"my_dish_pick:{meal_type}:{page}:{dish.id}",
            )
        ]
        for index, dish in enumerate(dishes, start=1)
    ]
    keyboard = build_pagination_keyboard(page - 1, total_pages, f"meal_entry_my_dishes:{meal_type}", rows, page_base=1)
    keyboard.inline_keyboard.append([InlineKeyboardButton(text="⬅️ К приёму пищи", callback_data="my_dishes_back_to_current_meal")])
    return keyboard


def _format_saved_dish_card(dish, items: list[dict]) -> str:
    totals = calculate_dish_totals(items)
    lines = [
        f"🥣 <b>{html.escape(dish.name)}</b>",
        "",
        "<b>Состав:</b>",
    ]
    for index, item in enumerate(items):
        lines.append(
            f"{_number_emoji(index)} {html.escape(str(item.get('name') or 'Ингредиент'))} — "
            f"{_safe_float(item.get('grams')):.0f} г"
        )
    lines.extend(
        [
            "",
            f"📦 <b>Общий вес:</b> {calculate_dish_weight(items):.0f} г",
            f"🔥 <b>Калории:</b> {totals['calories']:.0f} ккал",
            f"🥩 <b>Белки:</b> {totals['protein']:.1f} г",
            f"🥑 <b>Жиры:</b> {totals['fat']:.1f} г",
            f"🍚 <b>Углеводы:</b> {totals['carbs']:.1f} г",
        ]
    )
    return "\n".join(lines)


def _build_saved_dish_card_keyboard(
    meal_type: str, page: int, dish_id: int, *, return_to_products: bool = False,
) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="✅ Добавить порцию", callback_data=f"my_dish_add:{dish_id}")],
            [InlineKeyboardButton(text="⚖️ Изменить общий вес", callback_data=f"my_dish_weight:{dish_id}")],
            [InlineKeyboardButton(text="🗑 Убрать из моих блюд", callback_data=f"my_dish_archive_ask:{dish_id}")],
            [InlineKeyboardButton(
                text="⬅️ Назад",
                callback_data="my_dish_catalog_back" if return_to_products else f"meal_entry_my_dishes:{meal_type}:{page}",
            )],
        ]
    )


def _build_saved_dish_weight_keyboard(dish_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="−100 г", callback_data=f"my_dish_wchg:{dish_id}:-100"),
                InlineKeyboardButton(text="−50 г", callback_data=f"my_dish_wchg:{dish_id}:-50"),
                InlineKeyboardButton(text="+50 г", callback_data=f"my_dish_wchg:{dish_id}:50"),
                InlineKeyboardButton(text="+100 г", callback_data=f"my_dish_wchg:{dish_id}:100"),
            ],
            [
                InlineKeyboardButton(text="−10 г", callback_data=f"my_dish_wchg:{dish_id}:-10"),
                InlineKeyboardButton(text="+10 г", callback_data=f"my_dish_wchg:{dish_id}:10"),
            ],
            [InlineKeyboardButton(text="⌨️ Ввести вручную", callback_data=f"my_dish_wmanual:{dish_id}")],
            [InlineKeyboardButton(text="✅ Применить", callback_data=f"my_dish_wsave:{dish_id}")],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data=f"my_dish_wback:{dish_id}")],
        ]
    )


def _format_saved_dish_weight_text(dish, items: list[dict]) -> str:
    totals = calculate_dish_totals(items)
    return "\n".join(
        [
            f"⚖️ <b>Порция: {html.escape(dish.name)}</b>",
            "",
            f"Общий вес: {calculate_dish_weight(items):.0f} г",
            f"🔥 {totals['calories']:.0f} ккал · Б {totals['protein']:.1f} · "
            f"Ж {totals['fat']:.1f} · У {totals['carbs']:.1f}",
            "",
            "Ингредиенты и КБЖУ пересчитываются пропорционально.",
        ]
    )


async def _show_saved_dishes_page(
    message: Message,
    state: FSMContext,
    *,
    user_id: str,
    meal_type: str,
    page: int,
    edit_message: bool = False,
) -> bool:
    total = DishRepository.count_active(user_id)
    if total <= 0:
        return False
    total_pages = total_pages_for(total, MY_DISHES_PAGE_SIZE)
    page = clamp_page(page - 1, total_pages) + 1
    dishes = DishRepository.list_active(
        user_id,
        offset=(page - 1) * MY_DISHES_PAGE_SIZE,
        limit=MY_DISHES_PAGE_SIZE,
    )
    await state.update_data(my_dishes_page=page, meal_type=meal_type, in_my_dishes_section=True)
    text = _format_saved_dishes_page(dishes, page)
    keyboard = _build_saved_dishes_keyboard(dishes, meal_type, page, total_pages)
    if edit_message:
        await _edit_or_send_photo_analysis_message(message, text, reply_markup=keyboard, parse_mode="HTML")
    else:
        await message.answer(text, reply_markup=keyboard, parse_mode="HTML")
    return True


@router.callback_query(lambda c: c.data.startswith("meal_entry_my_dishes:"))
async def meal_entry_my_dishes(callback: CallbackQuery, state: FSMContext):
    """Открывает отдельный каталог сохранённых блюд."""
    await callback.answer()
    parts = callback.data.split(":")
    meal_type = normalize_meal_type(parts[1] if len(parts) > 1 else None, fallback=MealType.SNACK.value)
    try:
        page = int(parts[2]) if len(parts) > 2 else 1
    except (TypeError, ValueError):
        page = 1
    data = await state.get_data()
    entry_date_raw = str(data.get("entry_date") or date.today().isoformat())
    await state.update_data(
        my_dishes_return_meal_type=meal_type,
        my_dishes_return_entry_date=entry_date_raw,
    )
    shown = await _show_saved_dishes_page(
        callback.message,
        state,
        user_id=str(callback.from_user.id),
        meal_type=meal_type,
        page=page,
        edit_message=bool(data.get("in_my_dishes_section")),
    )
    if not shown:
        await callback.message.answer(
            "Пока нет сохранённых блюд. Они появятся здесь после сохранения анализа еды по фото."
        )


@router.callback_query(lambda c: c.data == "my_dishes_back_to_current_meal")
async def my_dishes_back_to_current_meal(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    data = await state.get_data()
    meal_type = normalize_meal_type(data.get("my_dishes_return_meal_type"), fallback=MealType.SNACK.value)
    try:
        entry_date = date.fromisoformat(str(data.get("my_dishes_return_entry_date") or ""))
    except ValueError:
        entry_date = date.today()
    await state.update_data(in_my_dishes_section=False)
    await _keep_meal_entry_open_after_save(
        callback.message,
        state,
        user_id=str(callback.from_user.id),
        entry_date=entry_date,
        meal_type=meal_type,
    )


@router.callback_query(lambda c: c.data.startswith("my_dish_pick:"))
async def my_dish_pick(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    _, meal_type, raw_page, raw_dish_id, *origin_parts = callback.data.split(":")
    origin = origin_parts[0] if origin_parts else "dishes"
    data = await state.get_data()
    return_context = {
        "origin": origin,
        "page": int(raw_page),
        "source_filter": data.get("my_products_source_filter"),
        "query": data.get("my_products_search_query"),
    } if origin in {"products", "search"} else None
    dish = DishRepository.get_by_id(str(callback.from_user.id), int(raw_dish_id))
    if dish is None:
        await callback.answer("Блюдо не найдено", show_alert=True)
        return
    items = dish_to_snapshot(dish)
    save_token = _new_meal_save_token()
    return_date_key = "my_products_return_entry_date" if return_context else "my_dishes_return_entry_date"
    await state.update_data(
        my_dish_id=dish.id,
        my_dish_items=items,
        my_dish_original_items=items,
        my_dish_save_token=save_token,
        my_dish_return_context=return_context,
        my_dish_weight_draft=None,
        my_dishes_return_entry_date=data.get(return_date_key) or data.get("entry_date"),
        my_dishes_page=int(raw_page),
        meal_type=meal_type,
    )
    await _edit_or_send_photo_analysis_message(
        callback.message,
        _format_saved_dish_card(dish, items),
        reply_markup=_build_saved_dish_card_keyboard(
            meal_type, int(raw_page), dish.id, return_to_products=bool(return_context),
        ),
        parse_mode="HTML",
    )


async def _show_my_dish_catalog_origin(message: Message, state: FSMContext, *, user_id: str) -> None:
    data = await state.get_data()
    context = data.get("my_dish_return_context") or {}
    meal_type = normalize_meal_type(data.get("meal_type"), fallback=MealType.SNACK.value)
    await state.set_state(MealEntryStates.choosing_meal_type)
    await state.update_data(my_products_source_filter=context.get("source_filter"))
    if context.get("origin") == "search" and context.get("query"):
        await _show_my_products_search_results(
            message, state, user_id=user_id, meal_type=meal_type,
            query=context["query"], page=context["page"], edit_message=True,
        )
        return
    shown = await _show_my_products_page(
        message, state, user_id=user_id, meal_type=meal_type,
        page=int(context.get("page") or 1), source_filter=context.get("source_filter"), edit_message=True,
    )
    if not shown:
        await message.edit_text(
            "В этом фильтре пока нет продуктов и блюд.",
            reply_markup=_build_my_products_search_empty_keyboard(meal_type),
        )


@router.callback_query(lambda c: c.data == "my_dish_catalog_back")
async def my_dish_catalog_back(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await _show_my_dish_catalog_origin(callback.message, state, user_id=str(callback.from_user.id))


@router.callback_query(lambda c: c.data.startswith("my_dish_weight:"))
async def my_dish_weight_open(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    dish_id = int(callback.data.split(":", 1)[1])
    data = await state.get_data()
    dish = DishRepository.get_by_id(str(callback.from_user.id), dish_id)
    items = data.get("my_dish_items") or (dish_to_snapshot(dish) if dish else [])
    if dish is None or not items:
        await callback.answer("Блюдо не найдено", show_alert=True)
        return
    await state.update_data(my_dish_weight_draft=[dict(item) for item in items])
    await callback.message.edit_text(
        _format_saved_dish_weight_text(dish, items),
        reply_markup=_build_saved_dish_weight_keyboard(dish_id),
        parse_mode="HTML",
    )


@router.callback_query(lambda c: c.data.startswith("my_dish_wchg:"))
async def my_dish_weight_change(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    _, raw_dish_id, raw_delta = callback.data.split(":", 2)
    dish_id = int(raw_dish_id)
    data = await state.get_data()
    draft = data.get("my_dish_weight_draft") or data.get("my_dish_items") or []
    dish = DishRepository.get_by_id(str(callback.from_user.id), dish_id)
    if dish is None or not draft:
        await callback.answer("Блюдо не найдено", show_alert=True)
        return
    new_weight = max(1.0, calculate_dish_weight(draft) + float(raw_delta))
    updated = scale_dish_snapshot(draft, new_weight)
    await state.update_data(my_dish_weight_draft=updated)
    await callback.message.edit_text(
        _format_saved_dish_weight_text(dish, updated),
        reply_markup=_build_saved_dish_weight_keyboard(dish_id),
        parse_mode="HTML",
    )


@router.callback_query(lambda c: c.data.startswith("my_dish_wmanual:"))
async def my_dish_weight_manual_start(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    dish_id = int(callback.data.split(":", 1)[1])
    await state.set_state(MealEntryStates.editing_saved_dish_total_weight_input)
    await state.update_data(my_dish_id=dish_id)
    await callback.message.answer(
        "Введи новый общий вес порции в граммах:",
        reply_markup=_build_photo_analysis_cancel_menu(),
    )


@router.message(MealEntryStates.editing_saved_dish_total_weight_input)
async def my_dish_weight_manual_apply(message: Message, state: FSMContext):
    raw = (message.text or "").strip().replace(",", ".")
    if raw == "❌ Отмена":
        data = await state.get_data()
        dish_id = int(data.get("my_dish_id") or 0)
        dish = DishRepository.get_by_id(str(message.from_user.id), dish_id)
        items = data.get("my_dish_items") or []
        await state.set_state(MealEntryStates.choosing_meal_type)
        if dish is not None and items:
            await message.answer(
                _format_saved_dish_card(dish, items),
                reply_markup=_build_saved_dish_card_keyboard(
                    normalize_meal_type(data.get("meal_type"), fallback=MealType.SNACK.value),
                    int(data.get("my_dishes_page") or 1),
                    dish_id,
                    return_to_products=bool(data.get("my_dish_return_context")),
                ),
                parse_mode="HTML",
            )
        else:
            await message.answer("Изменение порции отменено.")
        return
    try:
        new_weight = float(raw)
    except ValueError:
        await message.answer("Введи вес числом, например: 350")
        return
    if not math.isfinite(new_weight) or new_weight < 1:
        await message.answer("Вес должен быть не меньше 1 г.")
        return
    data = await state.get_data()
    dish_id = int(data.get("my_dish_id") or 0)
    dish = DishRepository.get_by_id(str(message.from_user.id), dish_id)
    draft = data.get("my_dish_weight_draft") or data.get("my_dish_items") or []
    if dish is None or not draft:
        await message.answer("Блюдо не найдено.")
        return
    updated = scale_dish_snapshot(draft, new_weight)
    await state.set_state(MealEntryStates.choosing_meal_type)
    await state.update_data(my_dish_weight_draft=updated)
    await message.answer(
        _format_saved_dish_weight_text(dish, updated),
        reply_markup=_build_saved_dish_weight_keyboard(dish_id),
        parse_mode="HTML",
    )


@router.callback_query(lambda c: c.data.startswith("my_dish_wsave:"))
async def my_dish_weight_save(callback: CallbackQuery, state: FSMContext):
    await callback.answer("Порция изменена")
    dish_id = int(callback.data.split(":", 1)[1])
    data = await state.get_data()
    dish = DishRepository.get_by_id(str(callback.from_user.id), dish_id)
    draft = data.get("my_dish_weight_draft") or []
    if dish is None or not draft:
        await callback.answer("Черновик порции не найден", show_alert=True)
        return
    await state.update_data(my_dish_items=draft, my_dish_weight_draft=None)
    await callback.message.edit_text(
        _format_saved_dish_card(dish, draft),
        reply_markup=_build_saved_dish_card_keyboard(
            normalize_meal_type(data.get("meal_type"), fallback=MealType.SNACK.value),
            int(data.get("my_dishes_page") or 1),
            dish_id,
            return_to_products=bool(data.get("my_dish_return_context")),
        ),
        parse_mode="HTML",
    )


@router.callback_query(lambda c: c.data.startswith("my_dish_wback:"))
async def my_dish_weight_back(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    dish_id = int(callback.data.split(":", 1)[1])
    data = await state.get_data()
    dish = DishRepository.get_by_id(str(callback.from_user.id), dish_id)
    items = data.get("my_dish_items") or []
    if dish is None or not items:
        await callback.answer("Блюдо не найдено", show_alert=True)
        return
    await state.update_data(my_dish_weight_draft=None)
    await callback.message.edit_text(
        _format_saved_dish_card(dish, items),
        reply_markup=_build_saved_dish_card_keyboard(
            normalize_meal_type(data.get("meal_type"), fallback=MealType.SNACK.value),
            int(data.get("my_dishes_page") or 1),
            dish_id,
            return_to_products=bool(data.get("my_dish_return_context")),
        ),
        parse_mode="HTML",
    )


@router.callback_query(lambda c: c.data.startswith("my_dish_add:"))
async def my_dish_add(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    dish_id = int(callback.data.split(":", 1)[1])
    token = data.get("my_dish_save_token")
    if not _is_valid_meal_save_token(token) or dish_id != int(data.get("my_dish_id") or 0):
        await callback.answer(STALE_MEAL_SAVE_TEXT, show_alert=True)
        return
    meal_type = normalize_meal_type(data.get("meal_type"), fallback=MealType.SNACK.value)
    try:
        entry_date = date.fromisoformat(str(data.get("my_dishes_return_entry_date") or data.get("entry_date") or ""))
    except ValueError:
        entry_date = date.today()
    result = DishService.add_saved_dish_to_diary(
        save_token=token,
        user_id=str(callback.from_user.id),
        dish_id=dish_id,
        entry_date=entry_date,
        meal_type=meal_type,
        items=data.get("my_dish_items") or None,
    )
    if result.status is MealSaveStatus.FAILED or result.meal is None:
        await callback.answer("Не удалось добавить блюдо", show_alert=True)
        return
    await callback.answer("Блюдо добавлено")
    if not hasattr(callback.message.bot, "last_meal_ids"):
        callback.message.bot.last_meal_ids = {}
    callback.message.bot.last_meal_ids[str(callback.from_user.id)] = result.meal.id
    await state.update_data(my_dish_save_token=None, my_dish_items=None, in_my_dishes_section=False)
    await _keep_meal_entry_open_after_save(
        callback.message,
        state,
        user_id=str(callback.from_user.id),
        entry_date=entry_date,
        meal_type=meal_type,
    )


@router.callback_query(lambda c: c.data.startswith("my_dish_archive_ask:"))
async def my_dish_archive_ask(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    dish_id = int(callback.data.split(":", 1)[1])
    await callback.message.edit_text(
        "Убрать сохранённое блюдо из каталогов? Уже добавленные записи дневника сохранятся.",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="🗑 Убрать", callback_data=f"my_dish_archive:{dish_id}")],
                [InlineKeyboardButton(text="⬅️ Назад", callback_data=f"my_dish_wback:{dish_id}")],
            ]
        ),
    )


@router.callback_query(lambda c: c.data.startswith("my_dish_archive:"))
async def my_dish_archive(callback: CallbackQuery, state: FSMContext):
    dish_id = int(callback.data.split(":", 1)[1])
    if not DishRepository.archive(str(callback.from_user.id), dish_id):
        await callback.answer("Блюдо не найдено", show_alert=True)
        return
    await callback.answer("Блюдо убрано")
    data = await state.get_data()
    if data.get("my_dish_return_context"):
        await _show_my_dish_catalog_origin(callback.message, state, user_id=str(callback.from_user.id))
        return
    shown = await _show_saved_dishes_page(
        callback.message,
        state,
        user_id=str(callback.from_user.id),
        meal_type=normalize_meal_type(data.get("meal_type"), fallback=MealType.SNACK.value),
        page=int(data.get("my_dishes_page") or 1),
        edit_message=True,
    )
    if not shown:
        await callback.message.edit_text("В «Моих блюдах» пока пусто.")


@router.callback_query(lambda c: c.data == "meal_entry_ai_limits")
async def show_meal_entry_ai_limits(callback: CallbackQuery):
    """Показывает актуальные остатки AI-квот без нового сообщения в чате."""
    await callback.answer(
        format_free_ai_status_block(str(callback.from_user.id), compact=True),
        show_alert=True,
    )


@router.callback_query(lambda c: c.data.startswith("meal_entry_my_products:"))
async def meal_entry_my_products(callback: CallbackQuery, state: FSMContext):
    """Открывает список моих продуктов из inline-кнопки под текущим приёмом пищи."""
    await callback.answer()
    parts = callback.data.split(":")
    meal_type = normalize_meal_type(parts[1] if len(parts) > 1 else None, fallback=MealType.SNACK.value)
    try:
        page = int(parts[2]) if len(parts) > 2 else 1
    except (TypeError, ValueError):
        page = 1

    data = await state.get_data()
    entry_date_raw = str(data.get("entry_date") or "").strip()
    opened_from_current_meal = bool(entry_date_raw and data.get("meal_type") == meal_type)
    if opened_from_current_meal:
        await state.update_data(
            my_products_return_to_meal_entry=True,
            my_products_return_meal_type=meal_type,
            my_products_return_entry_date=entry_date_raw,
        )
        await callback.message.answer("Открываю «Мои продукты».", reply_markup=ReplyKeyboardRemove())

    shown = await _show_my_products_page(
        callback.message,
        state,
        meal_type=meal_type,
        page=page,
        user_id=str(callback.from_user.id),
        back_callback_data="my_products_back_to_current_meal" if opened_from_current_meal else None,
        source_filter=None,
        show_source_filter_block=True,
    )
    if not shown:
        await callback.message.answer(
            "Пока нет моих продуктов. Добавь продукт любым способом — он появится здесь."
        )


@router.callback_query(lambda c: c.data == "my_products_back_to_current_meal")
async def my_products_back_to_current_meal(callback: CallbackQuery, state: FSMContext):
    """Возвращает из «Моих продуктов» в открытый текущий приём пищи."""
    await callback.answer()
    data = await state.get_data()
    await state.update_data(in_my_products_section=False, my_products_source_filter=None)
    meal_type = normalize_meal_type(
        data.get("my_products_return_meal_type") or data.get("meal_type"),
        fallback=MealType.SNACK.value,
    )
    entry_date_raw = str(data.get("my_products_return_entry_date") or data.get("entry_date") or "").strip()
    try:
        entry_date = date.fromisoformat(entry_date_raw) if entry_date_raw else date.today()
    except ValueError:
        entry_date = date.today()

    await _keep_meal_entry_open_after_save(
        callback.message,
        state,
        user_id=str(callback.from_user.id),
        entry_date=entry_date,
        meal_type=meal_type,
    )


@router.message(lambda m: (m.text or "") in MY_PRODUCTS_SOURCE_BUTTON_TO_FILTER)
async def my_products_source_filter_selected(message: Message, state: FSMContext):
    source_filter = MY_PRODUCTS_SOURCE_BUTTON_TO_FILTER[message.text]
    data = await state.get_data()
    meal_type = normalize_meal_type(data.get("meal_type"), fallback=MealType.SNACK.value)
    back_callback_data = "my_products_back_to_main"
    shown = await _show_my_products_page(
        message,
        state,
        meal_type=meal_type,
        page=1,
        user_id=str(message.from_user.id),
        source_filter=source_filter,
        back_callback_data=back_callback_data,
    )
    if not shown:
        await state.update_data(my_products_source_filter=source_filter, meal_type=meal_type, my_products_page=1)
        await message.answer(
            "В этом фильтре пока нет продуктов.",
            reply_markup=_build_my_products_search_empty_keyboard(meal_type),
        )


@router.callback_query(lambda c: c.data == "my_products_back_to_main")
async def my_products_back_to_main(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    data = await state.get_data()
    meal_type = normalize_meal_type(data.get("meal_type"), fallback=MealType.SNACK.value)
    back_callback_data = "my_products_back_to_current_meal" if data.get("my_products_return_to_meal_entry") else None
    await _show_my_products_page(
        callback.message,
        state,
        meal_type=meal_type,
        page=1,
        user_id=str(callback.from_user.id),
        edit_message=True,
        back_callback_data=back_callback_data,
        source_filter=None,
        show_source_filter_block=True,
    )


@router.callback_query(lambda c: c.data.startswith("my_products_search_start:"))
async def my_products_search_start(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    _, meal_type = callback.data.split(":", maxsplit=1)
    await state.set_state(MealEntryStates.waiting_for_my_products_search)
    await state.update_data(meal_type=meal_type, my_products_search_page=1)
    await callback.message.answer(
        "<b>Введите название продукта или часть названия 👇</b>\n\n"
        "Например:\n"
        "сыр\n"
        "йог\n"
        "кур"
    )


@router.message(MealEntryStates.waiting_for_my_products_search)
async def handle_my_products_search_query(message: Message, state: FSMContext):
    text = (message.text or "").strip()
    data = await state.get_data()
    meal_type = normalize_meal_type(data.get("meal_type"), fallback=MealType.SNACK.value)

    if text in MAIN_MENU_BUTTON_ALIASES:
        await state.clear()
        from handlers.common import go_main_menu

        await go_main_menu(message, state)
        return

    if text in BACK_BUTTON_TEXTS:
        await state.set_state(MealEntryStates.choosing_meal_type)
        await _show_my_products_page(
            message,
            state,
            meal_type=meal_type,
            page=int(data.get("my_products_page") or 1),
            source_filter=data.get("my_products_source_filter"),
            show_source_filter_block=not data.get("my_products_source_filter"),
        )
        return

    if not text:
        await message.answer("Введите название продукта или часть названия 👇")
        return

    await state.set_state(MealEntryStates.choosing_meal_type)
    await _show_my_products_search_results(
        message,
        state,
        user_id=str(message.from_user.id),
        meal_type=meal_type,
        query=text,
        page=1,
    )


@router.callback_query(lambda c: c.data.startswith("my_products_search_page:"))
async def my_products_search_page(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    _, meal_type, page_str = callback.data.split(":", maxsplit=2)
    data = await state.get_data()
    query = str(data.get("my_products_search_query") or "").strip()
    if not query:
        await state.set_state(MealEntryStates.waiting_for_my_products_search)
        await callback.message.answer("Введите название продукта или часть названия 👇")
        return
    await _show_my_products_search_results(
        callback.message,
        state,
        user_id=str(callback.from_user.id),
        meal_type=meal_type,
        query=query,
        page=int(page_str),
        edit_message=True,
    )


@router.callback_query(lambda c: c.data.startswith("my_products_search_back:"))
async def my_products_search_back(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    _, meal_type = callback.data.split(":", maxsplit=1)
    data = await state.get_data()
    await state.set_state(MealEntryStates.choosing_meal_type)
    await _show_my_products_page(
        callback.message,
        state,
        meal_type=meal_type,
        page=int(data.get("my_products_page") or 1),
        user_id=str(callback.from_user.id),
        edit_message=True,
        source_filter=data.get("my_products_source_filter"),
        show_source_filter_block=not data.get("my_products_source_filter"),
    )


@router.callback_query(lambda c: c.data == "my_products_search_main_menu")
async def my_products_search_main_menu(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.clear()
    push_menu_stack(callback.message.bot, main_menu)
    await callback.message.answer("⬇️ Кнопки управления", reply_markup=main_menu)


@router.callback_query(lambda c: c.data.startswith("my_product_pick:"))
async def my_product_pick(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    user_id = str(callback.from_user.id)
    parts = callback.data.split(":")
    _, meal_type, page_str, source_meal_id_str, *product_idx_parts = parts
    product_index = _parse_my_product_index(product_idx_parts[0] if product_idx_parts else None)
    pick_origin = product_idx_parts[1] if len(product_idx_parts) > 1 else "my_products"
    source_meal_id = int(source_meal_id_str)
    page = int(page_str)
    source_meal = MealRepository.get_meal_by_id(source_meal_id, user_id)
    if not source_meal:
        await callback.message.answer("❌ Не удалось найти продукт в истории.")
        return
    my_product_item = _get_my_product_from_source_meal(
        source_meal, product_index, user_id=user_id
    )
    save_token = await _ensure_my_product_save_token(state, renew=True)
    await state.update_data(
        my_product_source_meal_id=source_meal_id,
        my_product_source_product_idx=product_index,
        my_product_custom_amount_g=None,
        my_products_page=page,
        my_product_pick_origin="search" if pick_origin == "search" else "my_products",
        meal_type=meal_type,
    )
    await callback.message.answer(
        _render_my_product_confirm_text(meal_type, my_product_item, amount_g=my_product_item.amount_g),
        reply_markup=_build_my_product_confirm_keyboard(
            source_meal_id,
            meal_type,
            page,
            product_index,
            save_token=save_token,
            item=my_product_item,
        ),
        parse_mode="HTML",
    )


@router.callback_query(lambda c: c.data.startswith("my_product_back:"))
async def my_product_back(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    _, meal_type, page_str = callback.data.split(":", maxsplit=2)
    data = await state.get_data()
    if data.get("my_product_pick_origin") == "search":
        query = str(data.get("my_products_search_query") or "").strip()
        if query:
            await _show_my_products_search_results(
                callback.message,
                state,
                user_id=str(callback.from_user.id),
                meal_type=meal_type,
                query=query,
                page=int(data.get("my_products_search_page") or page_str),
                edit_message=True,
            )
            return

    if data.get("my_product_pick_origin") == "custom" or data.get("in_my_product_menu"):
        await _show_my_product_menu(
            callback.message,
            state,
            meal_type=meal_type,
            user_id=str(callback.from_user.id),
        )
        return

    await _show_my_products_page(
        callback.message,
        state,
        meal_type=meal_type,
        page=int(page_str),
        user_id=str(callback.from_user.id),
        edit_message=True,
        **({"source_filter": data.get("my_products_source_filter")} if data.get("my_products_source_filter") else {}),
    )


@router.callback_query(lambda c: c.data.startswith("my_products_page:"))
async def my_products_page(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    _, meal_type, page_str = callback.data.split(":", maxsplit=2)
    data = await state.get_data()
    source_filter = data.get("my_products_source_filter")
    back_callback_data = (
        "my_products_back_to_main"
        if source_filter
        else "my_products_back_to_current_meal"
        if data.get("my_products_return_to_meal_entry")
        else None
    )
    await _show_my_products_page(
        callback.message,
        state,
        meal_type=meal_type,
        page=int(page_str),
        user_id=str(callback.from_user.id),
        edit_message=True,
        back_callback_data=back_callback_data,
        source_filter=source_filter,
        show_source_filter_block=False,
    )


@router.callback_query(lambda c: c.data.startswith("my_product_edit_weight:"))
async def my_product_edit_weight(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    data = await state.get_data()
    parts = callback.data.split(":")
    _, meal_type, page_str, source_meal_id_str, *product_idx_parts = parts
    product_index = _parse_my_product_index(product_idx_parts[0] if product_idx_parts else None)
    source_meal_id = int(source_meal_id_str)
    user_id = str(callback.from_user.id)
    source_meal = MealRepository.get_meal_by_id(source_meal_id, user_id)
    if not source_meal:
        await callback.message.answer("❌ Не удалось найти продукт в истории.")
        return
    my_product_item = _get_my_product_from_source_meal(
        source_meal, product_index, user_id=user_id
    )
    current_amount = int(my_product_item.amount_g or 100)
    custom_amount = int(data.get("my_product_custom_amount_g") or current_amount)
    pick_origin = (
        "custom"
        if data.get("my_product_pick_origin") == "custom" or data.get("in_my_product_menu")
        else data.get("my_product_pick_origin", "my_products")
    )
    await state.update_data(
        my_product_source_meal_id=source_meal_id,
        my_product_source_product_idx=product_index,
        my_product_weight_edit_mode=True,
        my_product_weight_draft_g=custom_amount,
        my_products_page=int(page_str),
        my_product_pick_origin=pick_origin,
        in_my_product_menu=pick_origin == "custom",
        meal_type=meal_type,
    )
    await callback.message.answer(
        _render_my_product_weight_editor_text(my_product_item, draft_amount_g=custom_amount),
        reply_markup=_build_my_product_weight_editor_keyboard(),
        parse_mode="HTML",
    )


@router.callback_query(lambda c: c.data.startswith("my_product_wchg:"))
async def my_product_weight_change_draft(callback: CallbackQuery, state: FSMContext):
    """Меняет временный вес продукта из истории кнопками +/−."""
    delta = int(callback.data.split(":", maxsplit=1)[1])
    data = await state.get_data()
    source_meal_id = data.get("my_product_source_meal_id")
    if not source_meal_id:
        await callback.message.answer("❌ Не удалось найти продукт из истории.")
        return

    source_meal = MealRepository.get_meal_by_id(int(source_meal_id), str(callback.from_user.id))
    if not source_meal:
        await callback.message.answer("❌ Не удалось найти продукт из истории.")
        return

    product_index = _parse_my_product_index(data.get("my_product_source_product_idx"))
    my_product_item = _get_my_product_from_source_meal(
        source_meal, product_index, user_id=str(callback.from_user.id)
    )
    base_amount = int(data.get("my_product_weight_draft_g") or my_product_item.amount_g or 100)
    new_amount = base_amount + delta
    if new_amount < 1:
        await callback.answer("Вес не может быть меньше 1 г", show_alert=True)
        return

    await state.update_data(my_product_weight_draft_g=new_amount, my_product_weight_edit_mode=True)
    await callback.answer()
    await callback.message.edit_text(
        _render_my_product_weight_editor_text(my_product_item, draft_amount_g=new_amount),
        reply_markup=_build_my_product_weight_editor_keyboard(),
        parse_mode="HTML",
    )


@router.callback_query(lambda c: c.data == "my_product_wmanual")
async def my_product_weight_manual_input_start(callback: CallbackQuery, state: FSMContext):
    """Запрашивает ручной ввод веса для продукта из истории."""
    await callback.answer()
    await state.set_state(MealEntryStates.editing_meal_weight_manual_input)
    await state.update_data(my_product_weight_edit_mode=True)
    await callback.message.answer("Введи новый вес (в граммах), например: 180")


@router.callback_query(lambda c: c.data == "my_product_wsave")
async def my_product_weight_save_draft(callback: CallbackQuery, state: FSMContext):
    """Сохраняет выбранный вес продукта из истории и возвращает экран подтверждения."""
    await callback.answer()
    data = await state.get_data()
    source_meal_id = data.get("my_product_source_meal_id")
    if not source_meal_id:
        await callback.message.answer("❌ Не удалось найти продукт из истории.")
        return

    source_meal = MealRepository.get_meal_by_id(int(source_meal_id), str(callback.from_user.id))
    if not source_meal:
        await callback.message.answer("❌ Не удалось найти продукт из истории.")
        return

    meal_type = normalize_meal_type(data.get("meal_type"), fallback=MealType.SNACK.value)
    product_index = _parse_my_product_index(data.get("my_product_source_product_idx"))
    my_product_item = _get_my_product_from_source_meal(
        source_meal, product_index, user_id=str(callback.from_user.id)
    )
    new_amount = int(data.get("my_product_weight_draft_g") or my_product_item.amount_g or 100)
    adjusted = _build_adjusted_my_product_item(my_product_item, new_amount)
    save_token = await _ensure_my_product_save_token(state, data)

    await state.set_state(MealEntryStates.choosing_meal_type)
    await state.update_data(
        my_product_custom_amount_g=new_amount,
        my_product_weight_edit_mode=False,
        my_product_weight_draft_g=None,
    )
    await callback.message.edit_text(
        _render_my_product_confirm_text(meal_type, adjusted, amount_g=new_amount),
        reply_markup=_build_my_product_confirm_keyboard(
            int(source_meal_id),
            meal_type,
            int(data.get("my_products_page") or 1),
            product_index,
            save_token=save_token,
            include_delete=data.get("my_product_pick_origin") == "custom" or data.get("in_my_product_menu"),
            item=adjusted,
        ),
        parse_mode="HTML",
    )


@router.callback_query(lambda c: c.data == "my_product_wback")
async def my_product_weight_back(callback: CallbackQuery, state: FSMContext):
    """Возвращает экран подтверждения продукта из истории без сохранения черновика веса."""
    await callback.answer()
    data = await state.get_data()
    source_meal_id = data.get("my_product_source_meal_id")
    if not source_meal_id:
        await callback.message.answer("❌ Не удалось найти продукт из истории.")
        return

    source_meal = MealRepository.get_meal_by_id(int(source_meal_id), str(callback.from_user.id))
    if not source_meal:
        await callback.message.answer("❌ Не удалось найти продукт из истории.")
        return

    meal_type = normalize_meal_type(data.get("meal_type"), fallback=MealType.SNACK.value)
    product_index = _parse_my_product_index(data.get("my_product_source_product_idx"))
    my_product_item = _get_my_product_from_source_meal(
        source_meal, product_index, user_id=str(callback.from_user.id)
    )
    custom_amount = data.get("my_product_custom_amount_g")
    display_item = _build_adjusted_my_product_item(my_product_item, int(custom_amount)) if custom_amount else my_product_item
    display_amount = int(custom_amount or my_product_item.amount_g or 100)
    save_token = await _ensure_my_product_save_token(state, data)

    await state.set_state(MealEntryStates.choosing_meal_type)
    await state.update_data(my_product_weight_edit_mode=False, my_product_weight_draft_g=None)
    await callback.message.edit_text(
        _render_my_product_confirm_text(meal_type, display_item, amount_g=display_amount),
        reply_markup=_build_my_product_confirm_keyboard(
            int(source_meal_id),
            meal_type,
            int(data.get("my_products_page") or 1),
            product_index,
            save_token=save_token,
            include_delete=data.get("my_product_pick_origin") == "custom" or data.get("in_my_product_menu"),
            item=display_item,
        ),
        parse_mode="HTML",
    )


async def _save_my_product_from_callback(
    callback: CallbackQuery,
    state: FSMContext,
    *,
    add_single_unit: bool,
) -> None:
    """Общий путь добавления последней порции или одной единицы продукта."""
    user_id = str(callback.from_user.id)
    data = await state.get_data()
    parts = callback.data.split(":")
    if len(parts) != 6:
        await callback.answer(STALE_MEAL_SAVE_TEXT, show_alert=True)
        return
    _, meal_type_raw, _page_str, source_meal_id_str, product_idx_raw, callback_token = parts
    save_token = data.get("my_product_save_token")
    if (
        not _is_valid_meal_save_token(save_token)
        or callback_token != save_token[:MEAL_SAVE_CALLBACK_TOKEN_LENGTH]
    ):
        await callback.answer(STALE_MEAL_SAVE_TEXT, show_alert=True)
        return
    await callback.answer()
    product_index = _parse_my_product_index(
        product_idx_raw or data.get("my_product_source_product_idx")
    )
    source_meal_id = int(source_meal_id_str)
    source_meal = MealRepository.get_meal_by_id(source_meal_id, user_id)
    if not source_meal:
        await callback.message.answer("❌ Продукт не найден.")
        return
    meal_type = normalize_meal_type(meal_type_raw or data.get("meal_type"), fallback=MealType.SNACK.value)
    entry_date_str = data.get("entry_date")
    try:
        entry_date = date.fromisoformat(entry_date_str) if isinstance(entry_date_str, str) else date.today()
    except ValueError:
        entry_date = date.today()
    my_product_item = _get_my_product_from_source_meal(
        source_meal, product_index, user_id=user_id
    )
    custom_amount = data.get("my_product_custom_amount_g")
    old_amount = float(my_product_item.amount_g or 100)
    if add_single_unit:
        if my_product_item.unit_weight_g is None:
            await callback.message.answer("❌ Вес одной единицы для этого продукта не указан.")
            return
        selected_amount = float(my_product_item.unit_weight_g)
    else:
        selected_amount = float(custom_amount) if custom_amount else old_amount
    ratio = selected_amount / old_amount
    stored_amount = int(selected_amount) if selected_amount.is_integer() else selected_amount
    save_result = MealRepository.save_meal_idempotent(
        save_token=save_token,
        user_id=user_id,
        raw_query=my_product_item.title,
        description=my_product_item.title,
        calories=float(my_product_item.calories) * ratio,
        protein=float(my_product_item.protein) * ratio,
        fat=float(my_product_item.fat) * ratio,
        carbs=float(my_product_item.carbs) * ratio,
        entry_date=entry_date,
        products_json=_single_product_json_for_my_product(
            source_meal,
            my_product_item,
            ratio=ratio,
            amount_g=stored_amount,
        ),
        api_details=source_meal.api_details,
        meal_type=meal_type,
    )
    if save_result.status is MealSaveStatus.FAILED or save_result.meal is None:
        await callback.message.answer(
            "Не удалось добавить продукт. Черновик сохранён — попробуй ещё раз."
        )
        return

    new_meal = save_result.meal
    if not hasattr(callback.message.bot, "last_meal_ids"):
        callback.message.bot.last_meal_ids = {}
    callback.message.bot.last_meal_ids[user_id] = new_meal.id
    _sync_saved_product_reference(
        user_id,
        my_product_item,
        last_weight_g=selected_amount,
    )
    await state.update_data(my_product_save_token=None)
    await _keep_meal_entry_open_after_save(
        callback.message,
        state,
        user_id=user_id,
        entry_date=entry_date,
        meal_type=meal_type,
        intro_lines=[
            "✅ Приём пищи уже сохранён."
            if save_result.status is MealSaveStatus.ALREADY_SAVED
            else "✅ Добавил продукт."
        ],
        parse_mode="HTML",
    )


@router.callback_query(lambda c: c.data.startswith("mpc:"))
async def my_product_confirm(callback: CallbackQuery, state: FSMContext):
    await _save_my_product_from_callback(
        callback,
        state,
        add_single_unit=False,
    )


@router.callback_query(lambda c: c.data.startswith("mpu:"))
async def my_product_add_single_unit(callback: CallbackQuery, state: FSMContext):
    await _save_my_product_from_callback(
        callback,
        state,
        add_single_unit=True,
    )


@router.callback_query(lambda c: c.data.startswith("my_product_confirm:"))
async def reject_legacy_my_product_confirm(callback: CallbackQuery):
    """Rejects old unbound Add callbacks instead of applying current FSM data."""
    await callback.answer(STALE_MEAL_SAVE_TEXT, show_alert=True)


@router.message(lambda m: (m.text or "").strip() in MEALS_BUTTON_ALIASES)
async def calories(message: Message, state: FSMContext):
    """Открывает дневник питания с актуальной сводкой за сегодня."""
    user_id = str(message.from_user.id)
    logger.info("Food diary opened")
    AnalyticsRepository.track_event(user_id, "open_kbju", section="kbju")
    await state.clear()
    await send_today_results(message, user_id)


@router.message(lambda m: m.text == "🍱 Быстрый перекус")
async def quick_snack(message: Message, state: FSMContext):
    """Упрощённый вход в добавление перекуса через ИИ одним нажатием."""
    user_id = str(message.from_user.id)
    logger.info("Quick snack flow started source=reply")
    
    await state.update_data(meal_type=MealType.SNACK.value, pending_add_method=None, entry_date=date.today().isoformat())
    # Начинаем поток как для ИИ-ввода, но с более короткими подсказками под перекус
    await state.set_state(MealEntryStates.waiting_for_ai_food_input)
    
    text = (
        "🍱 Быстрый перекус\n\n"
        "Напиши коротко, чем перекусил(а) — одним сообщением.\n\n"
        "Примеры:\n"
        "• йогурт 150 г и горсть орехов\n"
        "• яблоко и протеиновый батончик\n"
        "• творог 100 г с ягодами\n\n"
        "Я оценю КБЖУ с помощью ИИ и добавлю это как приём пищи."
    )
    
    push_menu_stack(message.bot, kbju_add_menu)
    await message.answer(text, reply_markup=kbju_add_menu)


@router.callback_query(lambda c: c.data == "quick_snack")
async def quick_snack_cb(callback: CallbackQuery, state: FSMContext):
    """Упрощённый вход в добавление перекуса через ИИ по inline-кнопке."""
    await callback.answer()
    message = callback.message
    user_id = str(callback.from_user.id)
    logger.info("Quick snack flow started source=inline")
    
    await state.update_data(meal_type=MealType.SNACK.value, pending_add_method=None, entry_date=date.today().isoformat())
    await state.set_state(MealEntryStates.waiting_for_ai_food_input)
    
    text = (
        "🍱 Быстрый перекус\n\n"
        "Напиши коротко, чем перекусил(а) — одним сообщением.\n\n"
        "Примеры:\n"
        "• йогурт 150 г и горсть орехов\n"
        "• яблоко и протеиновый батончик\n"
        "• творог 100 г с ягодами\n\n"
        "Я оценю КБЖУ с помощью ИИ и добавлю это как приём пищи."
    )
    
    push_menu_stack(message.bot, kbju_add_menu)
    await message.answer(text, reply_markup=kbju_add_menu)


@router.callback_query(lambda c: c.data == "quick_meal_add")
async def quick_meal_add(callback: CallbackQuery, state: FSMContext):
    """Быстрый переход в добавление приёма пищи через inline-кнопку."""
    await callback.answer()
    reset_user_state(callback.message)
    await start_kbju_add_flow(callback.message, date.today(), state)


@router.message(lambda m: m.text == "🎯 Цель / Норма КБЖУ")
async def show_kbju_goal(message: Message, state: FSMContext):
    """Показывает текущую цель КБЖУ и варианты её настройки."""
    from utils.formatters import format_current_kbju_goal
    from utils.keyboards import kbju_intro_menu

    user_id = str(message.from_user.id)
    logger.info("KBJU goal settings opened")

    await state.clear()
    settings = MealRepository.get_kbju_settings(user_id)

    if settings:
        text = format_current_kbju_goal(settings)
        text += "\n\nВыбери, как хочешь обновить цель 👇"
        await message.answer(text, parse_mode="HTML", reply_markup=kbju_intro_menu)
        return

    await message.answer(
        "Цель по КБЖУ пока не настроена.\n"
        "Выбери удобный вариант: быстрый тест или ручной ввод 👇",
        reply_markup=kbju_intro_menu,
    )


@router.message(lambda m: m.text in KBJU_ADD_MEAL_BUTTON_ALIASES)
async def calories_add(message: Message, state: FSMContext):
    """Начинает процесс добавления приёма пищи."""
    # Проверяем, что пользователь НЕ находится в состоянии редактирования добавок
    from states.user_states import SupplementStates
    current_state = await state.get_state()
    
    # Если пользователь редактирует время добавки, не обрабатываем эту кнопку здесь
    # В aiogram get_state() возвращает строку вида "SupplementStates:entering_time"
    # Сравниваем строки, используя строковое представление состояния
    if current_state and "entering_time" in str(current_state) and "SupplementStates" in str(current_state):
        return  # Пропускаем обработку, чтобы более специфичный обработчик в supplements.py мог обработать
    
    reset_user_state(message)
    user_id = str(message.from_user.id)
    entry_date = _get_selected_food_diary_date(message.bot, user_id)
    await start_kbju_add_flow(message, entry_date, state)


async def start_kbju_add_flow(message: Message, entry_date: date, state: FSMContext):
    """Запускает поток добавления приёма пищи."""
    _ = str(message.from_user.id)
    await state.set_state(MealEntryStates.choosing_meal_type)
    await state.update_data(entry_date=entry_date.isoformat(), pending_add_method=None)
    push_menu_stack(message.bot, kbju_meal_type_menu)
    await message.answer(
        "Выбери приём пищи, к которому нужно добавить продукты:",
        reply_markup=kbju_meal_type_menu,
    )


@router.message(MealEntryStates.choosing_meal_type, lambda m: m.text in MEAL_TYPE_BUTTONS)
async def select_meal_type(message: Message, state: FSMContext):
    """Сохраняет выбранный тип приёма пищи и продолжает сценарий добавления."""
    meal_type = MEAL_TYPE_BUTTONS[message.text]
    data = await state.get_data()
    pending_method = data.get("pending_add_method")
    await state.update_data(meal_type=meal_type)

    if pending_method in ADD_METHOD_TEXTS:
        await message.answer(f"Выбрано: {display_meal_type(meal_type)}")
        if pending_method == "ai":
            await kbju_add_via_ai(message, state)
            return
        if pending_method == "deepseek":
            await kbju_add_via_deepseek(message, state)
            return
        if pending_method == "photo":
            await kbju_add_via_photo(message, state)
            return
        if pending_method == "photo_openai":
            await kbju_add_via_photo_openai(message, state)
            return
        if pending_method == "label":
            await kbju_add_via_label(message, state)
            return
        if pending_method == "label_openai":
            await kbju_add_via_label_openai(message, state)
            return
        if pending_method == "custom":
            await kbju_add_via_custom_product(message, state)
            return

    await message.answer(f"Отлично! {display_meal_type(meal_type)}.")
    await _show_input_methods(message, state)


@router.message(MealEntryStates.choosing_meal_type, lambda m: (m.text or "").strip() == "➕ Создать продукт")
async def custom_product_create_from_reply(message: Message, state: FSMContext):
    """Начинает создание своего продукта с обычной кнопки."""
    data = await state.get_data()
    if not data.get("in_my_product_menu"):
        return
    meal_type = normalize_meal_type(data.get("meal_type"), fallback=MealType.SNACK.value)
    await state.set_state(MealEntryStates.custom_product_name)
    await state.update_data(
        meal_type=meal_type,
        custom_product={},
        custom_product_save_token=_new_meal_save_token(),
        in_my_product_menu=False,
    )
    await message.answer(
        _format_custom_product_name_step(),
        reply_markup=_build_custom_product_reply_keyboard(),
        parse_mode="HTML",
    )


@router.message(MealEntryStates.choosing_meal_type, lambda m: (m.text or "").strip() in BACK_BUTTON_TEXTS or (m.text or "").strip() in MEAL_FINISH_BUTTON_TEXTS or (m.text or "").strip() in MAIN_MENU_BUTTON_ALIASES)
async def handle_meal_type_menu_navigation(message: Message, state: FSMContext):
    """Обрабатывает навигационные кнопки на шаге выбора приёма пищи."""
    text = (message.text or "").strip()
    if text in MAIN_MENU_BUTTON_ALIASES:
        await state.clear()
        from handlers.common import go_main_menu

        await go_main_menu(message, state)
        return

    if text in MEAL_FINISH_BUTTON_TEXTS:
        await _finish_current_meal_and_return_to_diary(message, state)
        return

    data = await state.get_data()
    if data.get("in_my_products_section"):
        meal_type = normalize_meal_type(data.get("meal_type"), fallback=MealType.SNACK.value)
        await state.update_data(in_my_products_section=False, my_products_source_filter=None, meal_type=meal_type)
        if data.get("my_products_return_to_meal_entry") or data.get("meal_entry_open"):
            await _restore_current_meal_entry_screen(message, state, data, user_id=str(message.from_user.id))
            return
        await _show_input_methods(message, state, user_id=str(message.from_user.id))
        return

    if data.get("in_my_product_menu"):
        meal_type = normalize_meal_type(data.get("meal_type"), fallback=MealType.SNACK.value)
        await state.update_data(in_my_product_menu=False, meal_type=meal_type, pending_add_method=None)
        if data.get("meal_entry_open"):
            await _restore_current_meal_entry_screen(message, state, data)
            return
        await _show_input_methods(message, state, user_id=str(message.from_user.id))
        return

    selected_meal_type = normalize_meal_type(data.get("meal_type"), fallback="")
    if selected_meal_type in MEAL_TYPE_ORDER:
        await state.update_data(meal_type=None, pending_add_method=None)
        await state.set_state(MealEntryStates.choosing_meal_type)
        push_menu_stack(message.bot, kbju_meal_type_menu)
        await message.answer(
            "Выбери приём пищи, к которому нужно добавить продукты:",
            reply_markup=kbju_meal_type_menu,
        )
        return

    await state.clear()
    from handlers.common import go_back

    await go_back(message, state)


@router.message(lambda m: m.text == "📝 Ввести приём пищи текстом (AI-анализ)")
async def kbju_add_via_ai(message: Message, state: FSMContext):
    """Обработчик добавления через AI-анализ на DeepSeek."""
    if not await _ensure_meal_type_selected(message, state, "ai"):
        return
    await state.update_data(pending_add_method=None)
    await state.set_state(MealEntryStates.waiting_for_ai_food_input)
    
    text = (
        "<b>📝 Ввести приём пищи текстом (AI-анализ)</b>\n\n"
        "<b>Просто напиши обычным человеческим языком, что ты съел — бот сам разберётся и посчитает КБЖУ</b>\n\n"
        "Можно писать как удобно:\n\n"
        "✔ Список продуктов\n"
        "200 г курицы, 100 г йогурта, 30 г орехов\n\n"
        "✔ Описание блюда\n"
        "Я приготовил запеканку: творог 500 г, 3 яйца, 2 ложки сметаны, 3 ложки муки, 1 мерный стакан протеина. Съел 1/3 от неё\n\n"
        "✔ Обычный разговорный текст\n"
        "Сделал бутерброд из хлеба, масла, огурца и колбасы, съел половину\n\n"
        "✔ Даже без точного веса\n"
        "Тарелка борща и кусок хлеба\n\n"
        "✔ Готовое блюдо с известным КБЖУ\n"
        "Творожно-протеиновые вафли, 200 г\n\n"
        "На 100 г готовых вафель:\n"
        "• 137 ккал\n"
        "• 14,5 г белка\n"
        "• 3,5 г жиров\n"
        "• 11,7 г углеводов\n\n"
        "Бот сам:\n"
        " • распознает продукты\n"
        " • оценит примерный вес\n"
        " • посчитает калории, белки, жиры и углеводы"
    )
    
    push_menu_stack(message.bot, kbju_add_menu)
    await message.answer(text, reply_markup=kbju_add_method_back_menu, parse_mode="HTML")


@router.message(lambda m: m.text == "🤖 Ввести приём пищи через DeepSeek")
async def kbju_add_via_deepseek(message: Message, state: FSMContext):
    """Обработчик отдельного сценария DeepSeek."""
    if not await _ensure_meal_type_selected(message, state, "deepseek"):
        return
    await state.update_data(pending_add_method=None)
    await state.set_state(MealEntryStates.waiting_for_deepseek_food_input)

    push_menu_stack(message.bot, kbju_add_menu)
    await message.answer(
        "🤖 DeepSeek\n\n"
        "Отправь продукты и количество одним сообщением — как в AI-анализе текстом. "
        "Я разберу описание через DeepSeek и сохраню приём пищи.",
        reply_markup=kbju_add_menu,
    )


@router.message(lambda m: m.text == "✍️ Внести вручную")
async def kbju_add_via_custom_product(message: Message, state: FSMContext):
    """Сразу запускает сценарий создания нового продукта вручную."""
    if not await _ensure_meal_type_selected(message, state, "custom"):
        return
    data = await state.get_data()
    meal_type = normalize_meal_type(data.get("meal_type"), fallback=MealType.SNACK.value)
    await _start_custom_product_creation(message, state, meal_type=meal_type)


@router.callback_query(lambda c: c.data and c.data.startswith("quota_alt:"))
async def handle_quota_alternative(callback: CallbackQuery, state: FSMContext):
    """Открывает бесплатную альтернативу после исчерпания AI-квоты."""
    try:
        _, action, meal_type_raw = callback.data.split(":", maxsplit=2)
    except ValueError:
        await callback.answer("Действие устарело", show_alert=True)
        return
    meal_type = normalize_meal_type(meal_type_raw, fallback=MealType.SNACK.value)
    user_id = str(callback.from_user.id)
    await callback.answer()
    await state.update_data(meal_type=meal_type, meal_entry_open=True)
    if action == "products":
        await _show_my_products_page(
            callback.message,
            state,
            meal_type=meal_type,
            page=1,
            user_id=user_id,
            back_callback_data="my_products_back_to_current_meal",
        )
        return
    if action == "dishes":
        shown = await _show_saved_dishes_page(
            callback.message,
            state,
            user_id=user_id,
            meal_type=meal_type,
            page=1,
        )
        if not shown:
            await callback.message.answer("Сохранённых блюд пока нет. Можно внести продукт вручную.")
        return
    if action == "manual":
        await _start_custom_product_creation(callback.message, state, meal_type=meal_type)
        return
    if action == "ai":
        await kbju_add_via_ai(callback.message, state)
        return
    if action == "photo":
        await kbju_add_via_photo(callback.message, state)
        return
    if action == "label":
        await kbju_add_via_label(callback.message, state)
        return
    await _show_input_methods(callback.message, state, user_id=user_id)


@router.callback_query(lambda c: c.data.startswith("custom_product_back:"))
async def custom_product_back(callback: CallbackQuery, state: FSMContext):
    """Возвращает из «Моего продукта» к способам добавления."""
    await callback.answer()
    _, meal_type = callback.data.split(":", maxsplit=1)
    data = await state.get_data()
    await state.set_state(MealEntryStates.choosing_meal_type)
    await state.update_data(
        meal_type=meal_type,
        pending_add_method=None,
        custom_product_save_token=None,
        in_my_product_menu=False,
    )
    if data.get("meal_entry_open"):
        await _restore_current_meal_entry_screen(callback.message, state, data, user_id=str(callback.from_user.id))
        return
    await _show_input_methods(callback.message, state, user_id=str(callback.from_user.id))


@router.callback_query(lambda c: c.data.startswith("custom_product_create:"))
async def custom_product_create(callback: CallbackQuery, state: FSMContext):
    """Начинает пошаговое создание продукта."""
    await callback.answer()
    _, meal_type = callback.data.split(":", maxsplit=1)
    await state.set_state(MealEntryStates.custom_product_name)
    await state.update_data(
        meal_type=meal_type,
        custom_product={},
        custom_product_save_token=_new_meal_save_token(),
        in_my_product_menu=False,
    )
    await callback.message.answer(
        _format_custom_product_name_step(),
        reply_markup=_build_custom_product_reply_keyboard(),
        parse_mode="HTML",
    )


@router.callback_query(lambda c: c.data.startswith("custom_product_page:"))
async def custom_product_page(callback: CallbackQuery, state: FSMContext):
    """Переключает страницы списка своих продуктов."""
    await callback.answer()
    _, meal_type, page_str = callback.data.split(":", maxsplit=2)
    products = _get_custom_product_items(str(callback.from_user.id))
    if not products:
        await callback.message.answer(
            "Здесь ты можешь сам внести свой продукт. Нажми «➕ Создать продукт», чтобы добавить первый."
        )
        return
    total_pages = total_pages_for(len(products), MY_PRODUCTS_PAGE_SIZE)
    page = clamp_page(int(page_str) - 1, total_pages) + 1
    start = (page - 1) * MY_PRODUCTS_PAGE_SIZE
    page_items = products[start : start + MY_PRODUCTS_PAGE_SIZE]
    await state.update_data(my_products_page=page, meal_type=meal_type, in_my_product_menu=True)
    await callback.message.edit_text(
        _format_my_products_text(page_items, page, title="🧺 <b>Мои продукты"),
        reply_markup=_build_custom_products_keyboard(
            page_items,
            meal_type,
            page,
            total_pages,
        ),
        parse_mode="HTML",
    )


@router.callback_query(lambda c: c.data.startswith("custom_product_pick:"))
async def custom_product_pick(callback: CallbackQuery, state: FSMContext):
    """Открывает подтверждение добавления своего продукта."""
    await callback.answer()
    parts = callback.data.split(":")
    _, meal_type, page_str, source_meal_id_str, *product_idx_parts = parts
    product_index = _parse_my_product_index(product_idx_parts[0] if product_idx_parts else None)
    source_meal_id = int(source_meal_id_str)
    source_meal = MealRepository.get_meal_by_id(source_meal_id, str(callback.from_user.id))
    if not source_meal or not _is_custom_product_meal(source_meal):
        await callback.message.answer("❌ Не удалось найти свой продукт.")
        return
    my_product_item = _get_my_product_from_source_meal(
        source_meal,
        product_index,
        user_id=str(callback.from_user.id),
    )
    save_token = await _ensure_my_product_save_token(state, renew=True)
    await state.update_data(
        my_product_source_meal_id=source_meal_id,
        my_product_source_product_idx=product_index,
        my_product_custom_amount_g=None,
        my_products_page=int(page_str),
        my_product_pick_origin="custom",
        meal_type=meal_type,
        in_my_product_menu=True,
    )
    await callback.message.answer(
        _render_my_product_confirm_text(meal_type, my_product_item, amount_g=my_product_item.amount_g),
        reply_markup=_build_my_product_confirm_keyboard(
            source_meal_id,
            meal_type,
            int(page_str),
            product_index,
            save_token=save_token,
            include_delete=True,
            item=my_product_item,
        ),
        parse_mode="HTML",
    )


def _build_custom_product_delete_confirm_keyboard(
    source_meal_id: int,
    meal_type: str,
    page: int,
    product_index: int | None = None,
) -> InlineKeyboardMarkup:
    product_idx = "" if product_index is None else str(product_index)
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🗑 Да, удалить",
                    callback_data=f"custom_product_delete:{meal_type}:{page}:{source_meal_id}:{product_idx}",
                )
            ],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data=f"custom_product_pick:{meal_type}:{page}:{source_meal_id}:{product_idx}")],
        ]
    )


@router.callback_query(lambda c: c.data.startswith("custom_product_delete_ask:"))
async def custom_product_delete_ask(callback: CallbackQuery, state: FSMContext):
    """Запрашивает подтверждение удаления своего продукта из списка."""
    await callback.answer()
    parts = callback.data.split(":")
    _, meal_type, page_str, source_meal_id_str, *product_idx_parts = parts
    product_index = _parse_my_product_index(product_idx_parts[0] if product_idx_parts else None)
    source_meal_id = int(source_meal_id_str)
    source_meal = MealRepository.get_meal_by_id(source_meal_id, str(callback.from_user.id))
    if not source_meal or not _is_custom_product_meal(source_meal):
        await callback.message.answer("❌ Не удалось найти свой продукт.")
        return

    my_product_item = _get_my_product_from_source_meal(
        source_meal,
        product_index,
        user_id=str(callback.from_user.id),
    )
    await callback.message.edit_text(
        f'Удалить продукт «{html.escape(my_product_item.title or "Продукт")}» из списка своих продуктов?',
        reply_markup=_build_custom_product_delete_confirm_keyboard(source_meal_id, meal_type, int(page_str), product_index),
        parse_mode="HTML",
    )


@router.callback_query(lambda c: c.data.startswith("custom_product_delete:"))
async def custom_product_delete(callback: CallbackQuery, state: FSMContext):
    """Удаляет свой продукт и возвращает список оставшихся продуктов."""
    await callback.answer()
    parts = callback.data.split(":")
    _, meal_type, _page_str, source_meal_id_str, *product_idx_parts = parts
    product_index = _parse_my_product_index(product_idx_parts[0] if product_idx_parts else None)
    source_meal_id = int(source_meal_id_str)
    user_id = str(callback.from_user.id)
    source_meal = MealRepository.get_meal_by_id(source_meal_id, user_id)
    if not source_meal or not _is_custom_product_meal(source_meal):
        await callback.message.answer("❌ Не удалось найти свой продукт.")
        return

    products = _parse_my_products(source_meal)
    if product_index is not None and len(products) > 1:
        products.pop(product_index)
        totals, api_details = _build_meal_update_payload(products)
        success = MealRepository.update_meal(
            meal_id=source_meal_id,
            user_id=user_id,
            description=getattr(source_meal, "raw_query", None),
            calories=totals["calories"],
            protein=totals["protein_g"],
            fat=totals["fat_total_g"],
            carbs=totals["carbohydrates_total_g"],
            products_json=json.dumps(products, ensure_ascii=False),
            api_details=api_details,
            is_manually_corrected=True,
        )
    else:
        success = MealRepository.delete_meal(source_meal_id, user_id)

    if not success:
        await callback.message.answer("❌ Не удалось удалить продукт. Попробуй позже.")
        return

    await state.update_data(my_product_source_meal_id=None, my_product_source_product_idx=None, my_product_custom_amount_g=None)
    await callback.message.answer("✅ Продукт удалён из списка своих продуктов.")
    await _show_my_product_menu(callback.message, state, meal_type=meal_type, user_id=user_id)


@router.message(MealEntryStates.custom_product_name)
async def handle_custom_product_name(message: Message, state: FSMContext):
    text = (message.text or "").strip()
    if await _reroute_add_method_button_if_needed(message, state, text):
        return
    if text in BACK_BUTTON_TEXTS or text == "❌ Отмена":
        await _return_to_add_methods_from_method_input(message, state)
        return
    if len(text) < 2:
        await message.answer("Название слишком короткое. Введите название продукта:", reply_markup=_build_custom_product_reply_keyboard())
        return
    if len(text) > 80:
        await message.answer("Название слишком длинное. Введите до 80 символов.", reply_markup=_build_custom_product_reply_keyboard())
        return
    if await _reject_sensitive_food_name(message, text, context="custom_product_create"):
        return
    await state.update_data(custom_product={"name": text})
    await _show_custom_product_value_editor(message, state, "calories", 0)


async def _handle_custom_product_macro(
    message: Message,
    state: FSMContext,
    *,
    field: str,
    next_state,
    next_step: int,
    next_prompt: str,
) -> None:
    text = (message.text or "").strip()
    if await _reroute_add_method_button_if_needed(message, state, text):
        return
    if text in BACK_BUTTON_TEXTS:
        await _go_to_previous_custom_product_step(message, state)
        return
    if text == "❌ Отмена":
        await _return_to_add_methods_from_method_input(message, state)
        return
    value = _parse_non_negative_number(text)
    if value is None:
        await message.answer("Введите число 0 или больше. Можно использовать запятую или точку.")
        return
    await _advance_custom_product_after_save(message, state, field, value, user_id=str(message.from_user.id))


@router.message(MealEntryStates.custom_product_calories)
async def handle_custom_product_calories(message: Message, state: FSMContext):
    await _handle_custom_product_macro(
        message,
        state,
        field="calories",
        next_state=MealEntryStates.custom_product_protein,
        next_step=3,
        next_prompt="Введите белки продукта (г):",
    )


@router.message(MealEntryStates.custom_product_protein)
async def handle_custom_product_protein(message: Message, state: FSMContext):
    await _handle_custom_product_macro(
        message,
        state,
        field="protein",
        next_state=MealEntryStates.custom_product_fat,
        next_step=4,
        next_prompt="Введите жиры продукта (г):",
    )


@router.message(MealEntryStates.custom_product_fat)
async def handle_custom_product_fat(message: Message, state: FSMContext):
    await _handle_custom_product_macro(
        message,
        state,
        field="fat",
        next_state=MealEntryStates.custom_product_carbs,
        next_step=5,
        next_prompt="Введите углеводы продукта (г):",
    )


@router.message(MealEntryStates.custom_product_carbs)
async def handle_custom_product_carbs(message: Message, state: FSMContext):
    await _handle_custom_product_macro(
        message,
        state,
        field="carbs",
        next_state=MealEntryStates.custom_product_amount,
        next_step=0,
        next_prompt="",
    )


@router.message(MealEntryStates.custom_product_amount)
async def handle_custom_product_amount(message: Message, state: FSMContext):
    await _handle_custom_product_macro(
        message,
        state,
        field="amount",
        next_state=None,
        next_step=0,
        next_prompt="",
    )


async def _save_custom_product(
    message: Message,
    state: FSMContext,
    *,
    user_id: str | None = None,
) -> MealSaveResult:
    """Сохраняет свой продукт и добавляет съеденную порцию в приём пищи."""
    data = await state.get_data()
    product = dict(data.get("custom_product") or {})
    save_token = data.get("custom_product_save_token")
    if not product or not _is_valid_meal_save_token(save_token):
        await message.answer(STALE_MEAL_SAVE_TEXT)
        return _failed_meal_save_result("stale_draft")
    name = str(product.get("name") or "Продукт").strip()
    amount_g = max(1.0, float(product.get("amount") or 100))
    ratio = amount_g / 100.0
    meal_type = normalize_meal_type(data.get("meal_type"), fallback=MealType.SNACK.value)
    entry_date_str = data.get("entry_date")
    try:
        entry_date = date.fromisoformat(entry_date_str) if isinstance(entry_date_str, str) else date.today()
    except ValueError:
        entry_date = date.today()

    calories = float(product.get("calories", 0)) * ratio
    protein = float(product.get("protein", 0)) * ratio
    fat = float(product.get("fat", 0)) * ratio
    carbs = float(product.get("carbs", 0)) * ratio
    products_json = json.dumps(
        [
            {
                "name": name,
                "grams": amount_g,
                "kcal": calories,
                "protein": protein,
                "fat": fat,
                "carbs": carbs,
                "calories": calories,
                "protein_g": protein,
                "fat_total_g": fat,
                "carbohydrates_total_g": carbs,
                "per_100g": {
                    "calories": float(product.get("calories", 0)),
                    "protein": float(product.get("protein", 0)),
                    "fat": float(product.get("fat", 0)),
                    "carbs": float(product.get("carbs", 0)),
                },
                "source": "manual",
            }
        ],
        ensure_ascii=False,
    )
    resolved_user_id = user_id or str(message.from_user.id)
    save_result = MealRepository.save_meal_idempotent(
        save_token=save_token,
        user_id=resolved_user_id,
        raw_query=name,
        description=name,
        calories=calories,
        protein=protein,
        fat=fat,
        carbs=carbs,
        entry_date=entry_date,
        products_json=products_json,
        meal_type=meal_type,
        is_manually_corrected=True,
    )
    if save_result.status is MealSaveStatus.FAILED or save_result.meal is None:
        await message.answer(
            "Не удалось сохранить продукт. Черновик сохранён — попробуй ещё раз."
        )
        return save_result

    saved_meal = save_result.meal
    if not hasattr(message.bot, "last_meal_ids"):
        message.bot.last_meal_ids = {}
    message.bot.last_meal_ids[resolved_user_id] = saved_meal.id

    await state.update_data(
        custom_product=None,
        custom_product_save_token=None,
        custom_product_draft_value=None,
        custom_product_current_field=None,
    )

    await _keep_meal_entry_open_after_save(
        message,
        state,
        user_id=resolved_user_id,
        entry_date=entry_date,
        meal_type=meal_type,
        intro_lines=[
            (
                "✅ <b>Приём пищи уже сохранён.</b>"
                if save_result.status is MealSaveStatus.ALREADY_SAVED
                else "✅ <b>Свой продукт создан и добавлен.</b>"
            ),
            "",
            f"<b>Продукт:</b> {html.escape(name)}",
            f"<b>Порция:</b> {amount_g:g} г",
            "",
            "<b>КБЖУ на 100 г:</b>",
            _format_kbju_summary_block(
                {
                    "calories": float(product.get("calories", 0)),
                    "protein": float(product.get("protein", 0)),
                    "fat": float(product.get("fat", 0)),
                    "carbs": float(product.get("carbs", 0)),
                }
            ),
            "",
            "<b>В этом приёме пищи:</b>",
            _format_kbju_summary_block({"calories": calories, "protein": protein, "fat": fat, "carbs": carbs}),
        ],
        parse_mode="HTML",
    )
    return save_result


async def _handle_provider_food_input(
    message: Message,
    state: FSMContext,
    *,
    provider_name: str,
    provider_title: str,
    analyzer,
) -> None:
    """Общая логика обработки текстового ввода еды через AI-провайдера."""
    user_text = (message.text or "").strip()
    if await _reroute_add_method_button_if_needed(message, state, user_text):
        return
    if user_text in BACK_BUTTON_TEXTS:
        await _return_to_add_methods_from_method_input(message, state)
        return
    if not user_text:
        await message.answer("Напиши, пожалуйста, что ты съел(а) 🙏")
        return
    if len(user_text) > MAX_MEAL_TEXT_LENGTH:
        logger.info(
            "Meal text input rejected reason=too_long input_chars=%s",
            len(user_text),
        )
        await message.answer(MEAL_TEXT_TOO_LONG_TEXT)
        return

    sensitive_check = check_sensitive_meal_text(user_text)
    if sensitive_check.is_sensitive:
        logger.warning(
            "Sensitive meal input rejected reason=%s",
            sensitive_check.reason.value if sensitive_check.reason else "unknown",
        )
        await message.answer(
            SENSITIVE_MEAL_INPUT_REJECTED_TEXT,
            parse_mode="HTML",
        )
        return

    user_id = str(message.from_user.id)
    data = await state.get_data()
    meal_type = normalize_meal_type(data.get("meal_type"), fallback=MealType.SNACK.value)
    request_id = build_quota_request_id(
        "meal_text",
        user_id,
        getattr(message, "message_id", None) or id(message),
    )
    reservation = await _reserve_meal_ai_quota(
        message,
        user_id=user_id,
        feature=AIFeature.MEAL_TEXT,
        request_id=request_id,
        meal_type=meal_type,
    )
    if reservation is None:
        return
    await message.answer("Обрабатываю…", reply_markup=ReplyKeyboardRemove())
    try:
        raw = await asyncio.to_thread(
            analyzer,
            user_text,
            user_id=user_id,
            feature=AIFeature.MEAL_TEXT.value,
        )
        kbju_data = parse_kbju_json(raw)
    except DeepSeekServiceConfigError:
        ai_quota_service.release(request_id, outcome="provider_config_error")
        logger.exception("%s: API key is not configured", provider_name)
        await message.answer("⚠️ DeepSeek временно недоступен: не настроен DEEPSEEK_API_KEY.")
        await message.answer("Можешь выбрать другой способ добавления или попробовать позже.")
        return
    except (DeepSeekServiceError, ValueError, json.JSONDecodeError):
        ai_quota_service.release(request_id, outcome="provider_or_parse_error")
        logger.exception("%s: failed to process user text", provider_name)
        await message.answer(f"Не удалось обработать через {provider_name}: пустой ответ или ошибка API. Попробуй позже.")
        await message.answer("Можешь отправить текст ещё раз.")
        return
    except Exception:
        ai_quota_service.release(request_id, outcome="unexpected_error")
        raise

    if not kbju_data:
        ai_quota_service.release(request_id, outcome="invalid_result")
        logger.error("%s: parse error, empty or incompatible payload", provider_name)
        await message.answer(f"Не удалось обработать через {provider_name}. Попробуй позже.")
        await message.answer("Можешь отправить текст ещё раз.")
        return

    analysis_status = kbju_data.get("status")
    if analysis_status == FoodAnalysisStatus.NO_FOOD.value:
        ai_quota_service.release(request_id, outcome="no_food")
        logger.info("Meal text analysis returned no_food")
        await message.answer(NO_FOOD_RECOGNIZED_TEXT, parse_mode="HTML")
        return
    if analysis_status != FoodAnalysisStatus.OK.value or "total" not in kbju_data:
        ai_quota_service.release(request_id, outcome="invalid_result")
        logger.error("%s: parse error, invalid food analysis status", provider_name)
        await message.answer(f"Не удалось обработать через {provider_name}. Попробуй позже.")
        await message.answer("Можешь отправить текст ещё раз.")
        return

    items = _normalize_ai_items_for_edit(kbju_data.get("items", []))
    if not items:
        ai_quota_service.release(request_id, outcome="no_food")
        logger.info("Meal text analysis returned no_food")
        await message.answer(NO_FOOD_RECOGNIZED_TEXT, parse_mode="HTML")
        return

    ai_quota_service.consume(request_id, outcome="success", result_ref="meal_text_draft")

    analysis_title = (
        provider_title
        if provider_title == "📝 AI-анализ приёма пищи"
        else f"{provider_title}: оценка приёма пищи"
    )
    entry_date_str = data.get("entry_date")
    try:
        entry_date = date.fromisoformat(entry_date_str) if isinstance(entry_date_str, str) else date.today()
    except ValueError:
        entry_date = date.today()

    # Для текстового AI-анализа больше не пишем в дневник автоматически:
    # сохраняем распознавание только во временный FSM-черновик до подтверждения.
    await state.update_data(
        ai_pending_meal={
            "raw_query": _build_ai_meal_query_summary(items),
            "items": items,
            "total": _collect_ai_draft_totals(items),
            "meal_type": meal_type,
            "entry_date": entry_date.isoformat(),
            "analysis_title": analysis_title,
            "quota_request_id": request_id,
        }
    )
    await _send_ai_meal_preview(message, state)


async def _keep_meal_entry_open_after_save(
    message: Message,
    state: FSMContext,
    *,
    user_id: str,
    entry_date: date,
    meal_type: str,
    intro_lines: list[str] | None = None,
    parse_mode: str | None = None,
    show_my_product_before_intro: bool = False,
    current_meal_items: list | None = None,
) -> None:
    """Оставляет пользователя внутри выбранного приёма пищи после сохранения продукта."""
    normalized_meal_type = normalize_meal_type(meal_type, fallback=MealType.SNACK.value)
    await state.set_state(MealEntryStates.choosing_meal_type)
    await state.update_data(
        entry_date=entry_date.isoformat(),
        meal_type=normalized_meal_type,
        pending_add_method=None,
        meal_entry_open=True,
    )

    if current_meal_items is None:
        current_meal_items = [
            meal
            for meal in MealRepository.get_meals_for_date(user_id, entry_date)
            if normalize_meal_type(getattr(meal, "meal_type", None)) == normalized_meal_type
        ]

    if show_my_product_before_intro:
        await _show_my_products_page(
            message,
            state,
            meal_type=normalized_meal_type,
            page=1,
            user_id=user_id,
        )

    if intro_lines:
        await message.answer(
            "\n".join(intro_lines),
            reply_markup=_build_meal_entry_edit_keyboard(normalized_meal_type, entry_date),
            parse_mode=parse_mode,
        )

    current_meal_text = _format_current_meal_after_save_message(
        normalized_meal_type,
        current_meal_items,
        entry_date,
    )

    push_menu_stack(message.bot, kbju_add_menu)
    await message.answer(
        current_meal_text,
        reply_markup=_build_meal_entry_post_save_keyboard(normalized_meal_type, entry_date),
        parse_mode="HTML",
    )
    await message.answer(
        "Можно добавить ещё продукт в этот приём пищи или завершить его.",
        reply_markup=kbju_add_menu,
    )
    await return_to_active_daily_preflight(message, user_id, entry_date)

@router.message(MealEntryStates.waiting_for_deepseek_food_input)
async def handle_deepseek_food_input(message: Message, state: FSMContext):
    """Обрабатывает текст пользователя через DeepSeek с автосохранением."""
    await _handle_provider_food_input(
        message,
        state,
        provider_name="DeepSeek",
        provider_title="🤖 DeepSeek",
        analyzer=deepseek_service.analyze_food_text,
    )


@router.message(lambda m: m.text == "📷 Анализ еды по фото")
async def kbju_add_via_photo(message: Message, state: FSMContext):
    """Обработчик анализа еды по фото."""
    if not await _ensure_meal_type_selected(message, state, "photo"):
        return
    reset_user_state(message)
    await state.update_data(pending_add_method=None)
    await state.set_state(MealEntryStates.waiting_for_photo)
    
    text = (
        "📷 Анализ еды по фото\n\n"
        "Отправь мне фото еды, и я определю КБЖУ с помощью ИИ!\n\n"
        "Сделай фото так, чтобы еда была хорошо видна на изображении."
    )
    
    push_menu_stack(message.bot, kbju_add_menu)
    await message.answer(text, reply_markup=kbju_add_method_back_menu)


@router.message(lambda m: m.text == "🧪 Анализ еды OpenAI")
async def kbju_add_via_photo_openai(message: Message, state: FSMContext):
    """Тестовый обработчик анализа еды по фото через OpenAI."""
    if not await _ensure_meal_type_selected(message, state, "photo_openai"):
        return
    reset_user_state(message)
    await state.update_data(pending_add_method=None)
    await state.set_state(MealEntryStates.waiting_for_openai_food_photo)

    text = (
        "📷 Анализ еды по фото\n\n"
        "Отправь мне фото еды, и я определю КБЖУ с помощью ИИ!\n\n"
        "Сделай фото так, чтобы еда была хорошо видна на изображении."
    )

    push_menu_stack(message.bot, kbju_add_menu)
    await message.answer(text, reply_markup=kbju_add_method_back_menu)


@router.message(MealEntryStates.waiting_for_ai_food_input)
async def handle_ai_food_input(message: Message, state: FSMContext):
    """Обрабатывает основной AI-анализ текста еды через DeepSeek."""
    logger.info("AI text meal analysis provider=deepseek")
    await _handle_provider_food_input(
        message,
        state,
        provider_name="DeepSeek",
        provider_title="📝 AI-анализ приёма пищи",
        analyzer=deepseek_service.analyze_food_text,
    )


@router.message(MealEntryStates.confirming_ai_meal)
async def handle_ai_confirm(message: Message, state: FSMContext):
    """Обрабатывает reply-сохранение предпросмотра текстового AI-анализа."""
    text = (message.text or "").strip()

    if text in MAIN_MENU_BUTTON_ALIASES:
        await state.clear()
        from handlers.common import go_main_menu

        await go_main_menu(message, state)
        return

    if text == "❌ Отмена":
        await state.set_state(MealEntryStates.confirming_ai_meal_cancel)
        await message.answer(
            "Отменить добавление приёма пищи? Все распознанные данные будут удалены.",
            reply_markup=_build_ai_meal_cancel_confirmation_menu(),
        )
        return

    if text == "✅ Сохранить":
        await _save_ai_meal_draft(message, state, user_id=str(message.from_user.id))
        return

    await message.answer("Проверь данные и выбери действие: ✅ Сохранить, ✏️ Редактировать или ❌ Отмена.")


@router.message(MealEntryStates.confirming_ai_meal_cancel)
async def handle_ai_cancel_confirmation_message(message: Message):
    """Не позволяет reply-кнопке обойти inline-подтверждение отмены."""
    await message.answer("Подтверди отмену кнопкой под сообщением или вернись к черновику.")


@router.callback_query(MealEntryStates.confirming_ai_meal, F.data == "cancel_ai_meal_draft")
async def request_cancel_ai_meal_draft(callback: CallbackQuery, state: FSMContext):
    """Запрашивает подтверждение перед удалением текстового AI-черновика."""
    data = await state.get_data()
    if not data.get("ai_pending_meal"):
        await callback.answer("Черновик уже недоступен", show_alert=True)
        return

    await callback.answer()
    await state.set_state(MealEntryStates.confirming_ai_meal_cancel)
    await callback.message.edit_text(
        "Отменить добавление приёма пищи? Все распознанные данные будут удалены.",
        reply_markup=_build_ai_meal_cancel_confirmation_menu(),
    )


@router.callback_query(MealEntryStates.confirming_ai_meal_cancel, F.data == "confirm_cancel_ai_meal_draft")
async def confirm_cancel_ai_meal_draft(callback: CallbackQuery, state: FSMContext):
    """Удаляет текстовый AI-черновик после явного подтверждения пользователя."""
    await callback.answer()
    await callback.message.edit_text("❌ Добавление приёма пищи отменено.")
    await _return_to_add_methods_from_method_input(
        callback.message,
        state,
        user_id=str(callback.from_user.id),
    )


@router.callback_query(MealEntryStates.confirming_ai_meal_cancel, F.data == "back_to_ai_meal_draft")
async def back_to_ai_meal_draft(callback: CallbackQuery, state: FSMContext):
    """Возвращает неизменённый текстовый AI-черновик из подтверждения отмены."""
    data = await state.get_data()
    draft = data.get("ai_pending_meal") or {}
    items = draft.get("items") or []
    if not draft:
        await callback.answer("Черновик уже недоступен", show_alert=True)
        return

    totals = _collect_ai_draft_totals(items)
    title = draft.get("analysis_title") or "🧾 AI-анализ приёма пищи"
    save_token = draft.get("save_token")
    if not _is_valid_meal_save_token(save_token):
        save_token = _new_meal_save_token()
        draft = {**draft, "save_token": save_token}
    await callback.answer()
    await state.set_state(MealEntryStates.confirming_ai_meal)
    await state.update_data(ai_pending_meal={**draft, "items": items, "total": totals})
    await callback.message.edit_text(
        _format_ai_food_analysis_message(title, items, totals, saved=False),
        reply_markup=_build_ai_meal_preview_inline_menu(save_token),
        parse_mode="HTML",
    )


async def _save_ai_meal_draft(
    message: Message,
    state: FSMContext,
    *,
    user_id: str,
) -> MealSaveResult:
    """Сохраняет подтверждённый текстовый AI-черновик без привязки к типу Telegram-события."""
    data = await state.get_data()
    pending = data.get("ai_pending_meal") or {}
    items = pending.get("items") or []
    save_token = pending.get("save_token")
    if not items or not _is_valid_meal_save_token(save_token):
        await message.answer(STALE_MEAL_SAVE_TEXT)
        return _failed_meal_save_result("stale_draft")
    total = _collect_ai_draft_totals(items)
    raw_query = pending.get("raw_query") or "[AI-анализ]"
    meal_type = normalize_meal_type(pending.get("meal_type") or data.get("meal_type"), fallback=MealType.SNACK.value)
    entry_date_str = pending.get("entry_date") or data.get("entry_date")
    try:
        entry_date = date.fromisoformat(entry_date_str) if isinstance(entry_date_str, str) else date.today()
    except ValueError:
        entry_date = date.today()

    _, api_details = _build_meal_update_payload(items)
    save_result = MealRepository.save_meal_idempotent(
        save_token=save_token,
        user_id=user_id,
        raw_query=raw_query,
        calories=total["calories"],
        protein=total["protein"],
        fat=total["fat"],
        carbs=total["carbs"],
        entry_date=entry_date,
        products_json=json.dumps([{**item, "source": item.get("source") or "text_ai"} for item in items], ensure_ascii=False),
        api_details=api_details,
        meal_type=meal_type,
        is_manually_corrected=bool(any(bool(p.get("is_manually_corrected")) for p in items)),
    )
    if save_result.status is MealSaveStatus.FAILED or save_result.meal is None:
        await message.answer(
            "Не удалось сохранить приём пищи. Черновик сохранён — попробуй ещё раз."
        )
        return save_result

    saved_meal = save_result.meal
    if not hasattr(message.bot, "last_meal_ids"):
        message.bot.last_meal_ids = {}
    message.bot.last_meal_ids[user_id] = saved_meal.id

    await state.update_data(ai_pending_meal=None)
    await _keep_meal_entry_open_after_save(
        message,
        state,
        user_id=user_id,
        entry_date=entry_date,
        meal_type=meal_type,
        intro_lines=[
            "✅ <b>Приём пищи уже сохранён.</b>"
            if save_result.status is MealSaveStatus.ALREADY_SAVED
            else "✅ <b>Продукт сохранён.</b>"
        ],
        parse_mode="HTML",
    )
    return save_result


@router.callback_query(
    MealEntryStates.confirming_ai_meal,
    lambda c: c.data and c.data.startswith("save_ai_meal_draft:"),
)
async def save_ai_meal_draft_from_inline(callback: CallbackQuery, state: FSMContext):
    """Сохраняет текущий текстовый AI-черновик по привязанной inline-кнопке."""
    data = await state.get_data()
    pending = data.get("ai_pending_meal") or {}
    callback_token = callback.data.split(":", maxsplit=1)[1]
    current_token = pending.get("save_token")
    if (
        not _is_valid_meal_save_token(callback_token)
        or callback_token != current_token
    ):
        await callback.answer(STALE_MEAL_SAVE_TEXT, show_alert=True)
        return

    await callback.answer()
    save_result = await _save_ai_meal_draft(
        callback.message,
        state,
        user_id=str(callback.from_user.id),
    )
    if save_result.status is not MealSaveStatus.FAILED:
        try:
            await callback.message.edit_reply_markup(reply_markup=None)
        except (AttributeError, TelegramBadRequest):
            pass


@router.callback_query(F.data == "save_ai_meal_draft")
async def reject_legacy_ai_meal_save_callback(callback: CallbackQuery):
    """Rejects pre-token Save buttons instead of applying them to a newer FSM draft."""
    await callback.answer(STALE_MEAL_SAVE_TEXT, show_alert=True)


@router.callback_query(MealEntryStates.confirming_ai_meal, F.data == "edit_ai_meal_draft")
async def edit_ai_meal_draft(callback: CallbackQuery, state: FSMContext):
    """Открывает существующий редактор продукта для FSM-черновика AI-анализа."""
    await callback.answer()
    data = await state.get_data()
    pending = data.get("ai_pending_meal") or {}
    products = pending.get("items") or []
    if not products:
        await callback.answer("Не нашёл продукты для редактирования", show_alert=True)
        return
    await state.update_data(
        ai_text_draft_mode=True,
        saved_products=products,
        weight_drafts={},
        kbju_drafts={},
        editing_product_idx=0 if len(products) == 1 else None,
    )
    await state.set_state(MealEntryStates.editing_meal_weight)
    if len(products) == 1:
        await callback.message.answer(
            "⬇️ Убираю нижнюю клавиатуру на время редактирования",
            reply_markup=ReplyKeyboardRemove(),
        )
        await callback.message.answer(
            _render_product_actions_text(products[0]),
            reply_markup=_build_product_actions_keyboard(0),
        )
    else:
        await _send_weight_products_list(
            callback.message,
            "<b>✏️ Выбери продукт для редактирования:</b>",
            products,
        )

@router.message(lambda m: m.text == "📋 Анализ этикетки")
async def kbju_add_via_label(message: Message, state: FSMContext):
    """Обработчик анализа этикетки."""
    if not await _ensure_meal_type_selected(message, state, "label"):
        return
    reset_user_state(message)
    await state.update_data(pending_add_method=None)
    await state.set_state(MealEntryStates.waiting_for_label_photo)
    
    text = (
        "<b>📋 Анализ этикетки/упаковки</b>\n\n"
        "<b>Отправь мне фото этикетки или упаковки продукта, и я найду КБЖУ в тексте! 📸</b>\n\n"
        "Я прочитаю информацию о пищевой ценности и извлеку точные данные о калориях, белках, жирах и углеводах.\n\n"
        "После анализа уточню у тебя, сколько грамм ты съел(а)."
    )
    
    push_menu_stack(message.bot, kbju_add_menu)
    await message.answer(text, reply_markup=kbju_add_method_back_menu, parse_mode="HTML")


@router.message(lambda m: m.text == "🧪 Анализ этикетки OpenAI")
async def kbju_add_via_label_openai(message: Message, state: FSMContext):
    """Тестовый обработчик анализа этикетки через OpenAI."""
    if not await _ensure_meal_type_selected(message, state, "label_openai"):
        return
    reset_user_state(message)
    await state.update_data(pending_add_method=None)
    await state.set_state(MealEntryStates.waiting_for_openai_label_photo)

    text = (
        "<b>📋 Анализ этикетки/упаковки</b>\n\n"
        "<b>Отправь мне фото этикетки или упаковки продукта, и я найду КБЖУ в тексте! 📸</b>\n\n"
        "Я прочитаю информацию о пищевой ценности и извлеку точные данные о калориях, белках, жирах и углеводах.\n\n"
        "После анализа уточню у тебя, сколько грамм ты съел(а)."
    )

    push_menu_stack(message.bot, kbju_add_menu)
    await message.answer(text, reply_markup=kbju_add_method_back_menu, parse_mode="HTML")


async def _clear_photo_comment_fields(state: FSMContext) -> None:
    """Remove legacy raw photo-comment fields while preserving the rest of FSM data."""
    data = await state.get_data()
    cleaned_data = {
        key: value
        for key, value in data.items()
        if key not in {"food_photo_comment", "photo_analysis_comment"}
    }
    if len(cleaned_data) == len(data):
        return

    await state.set_data(cleaned_data)


async def _handle_food_photo_analysis(
    message: Message,
    state: FSMContext,
    *,
    provider: str,
    analyzer,
    runner,
    error_sender,
    raw_query: str = "[Анализ по фото]",
    image_file_id: str | None = None,
    comment: str | None = None,
    user_id: str | None = None,
):
    """Общая логика обработки фото еды для Gemini и OpenAI."""
    await _clear_photo_comment_fields(state)
    user_id = str(user_id or message.from_user.id)
    data = await state.get_data()
    meal_type = normalize_meal_type(data.get("meal_type"), fallback=MealType.SNACK.value)
    entry_date_str = data.get("entry_date")
    if entry_date_str:
        if isinstance(entry_date_str, str):
            try:
                entry_date = date.fromisoformat(entry_date_str)
            except ValueError:
                parsed = parse_date(entry_date_str)
                entry_date = parsed.date() if isinstance(parsed, datetime) else date.today()
        else:
            entry_date = date.today()
    else:
        entry_date = date.today()

    logger.info("food_photo_analysis_started provider=%s", provider)
    await message.answer(
        "📷 Анализирую фото с помощью ИИ, секунду...",
        reply_markup=ReplyKeyboardRemove(),
    )

    if image_file_id:
        file_id = image_file_id
    else:
        photo = message.photo[-1]  # Берём самое большое разрешение
        file_id = photo.file_id
    file = await message.bot.get_file(file_id)
    image_bytes = await message.bot.download_file(file.file_path)
    image_data = image_bytes.read()

    try:
        validate_ai_image(image_data)
    except ValueError as error:
        code = str(error)
        if code == "image_too_large":
            await message.answer("Фото слишком большое. Максимальный размер — 10 МБ.")
        else:
            await message.answer("Поддерживаются фотографии JPEG, PNG и WebP.")
        return

    source_message_id = data.get("food_photo_message_id") or getattr(message, "message_id", None) or id(message)
    request_id = build_quota_request_id("meal_photo", user_id, source_message_id)
    reservation = await _reserve_meal_ai_quota(
        message,
        user_id=user_id,
        feature=AIFeature.MEAL_PHOTO,
        request_id=request_id,
        meal_type=meal_type,
    )
    if reservation is None:
        return

    try:
        if provider == "openai":
            openai_kwargs = {"user_id": user_id, "feature": "food_photo_analysis"}
            if comment:
                openai_kwargs["comment"] = comment
            kbju_data = await runner(
                analyzer,
                image_data,
                **openai_kwargs,
            )
            final_provider = "openai"
        elif provider == "gemini":
            analysis_result = await _run_food_photo_analysis_with_openai_fallback(
                analyzer,
                image_data,
                user_id=user_id,
                comment=comment,
                quota_request_id=request_id,
            )
            kbju_data = analysis_result.payload
            final_provider = analysis_result.provider
        else:
            if comment:
                kbju_data = await runner(analyzer, image_data, comment)
            else:
                kbju_data = await runner(analyzer, image_data)
            final_provider = provider
    except AllProvidersUnavailableError:
        ai_quota_service.release(request_id, outcome="providers_unavailable")
        await message.answer(
            "⚠️ Не получилось определить КБЖУ по фото.\n"
            "Попробуй сделать фото получше или используй другой способ."
        )
        return
    except Exception as e:
        ai_quota_service.release(request_id, outcome="provider_error")
        await error_sender(message, e)
        return

    logger.info("food_photo_analysis_completed provider=%s", final_provider)

    validated_payload = validate_photo_food_payload(kbju_data)
    if not validated_payload:
        ai_quota_service.release(request_id, outcome="no_food")
        await message.answer(
            "⚠️ Не получилось определить КБЖУ по фото.\n"
            "Попробуй сделать фото получше или используй другой способ."
        )
        return

    raw_dishes = validated_payload.get("dishes") or [
        {
            "dish_name": validated_payload.get("dish_name"),
            "confidence": validated_payload.get("confidence"),
            "ingredients": validated_payload.get("items") or [],
        }
    ]
    candidates = []
    for dish in raw_dishes:
        candidate_items = _normalize_photo_analysis_items(dish.get("ingredients") or dish.get("items"))
        if not candidate_items:
            continue
        candidates.append(
            {
                "dish_name": normalize_dish_display_name(dish.get("dish_name"), candidate_items),
                "confidence": dish.get("confidence"),
                "items": candidate_items,
            }
        )

    if not candidates:
        ai_quota_service.release(request_id, outcome="no_food")
        await message.answer(
            "⚠️ Не получилось определить продукты на фото.\n"
            "Попробуй сделать фото получше или используй другой способ."
        )
        return

    ai_quota_service.consume(request_id, outcome="success", result_ref="meal_photo_draft")
    save_token = _new_meal_save_token()
    await state.set_state(MealEntryStates.confirming_photo_analysis)
    await state.update_data(
        photo_analysis_candidates=candidates if len(candidates) > 1 else None,
        photo_analysis_items=candidates[0]["items"] if len(candidates) == 1 else None,
        photo_analysis_dish_name=candidates[0]["dish_name"] if len(candidates) == 1 else None,
        photo_analysis_raw_query=raw_query,
        photo_analysis_provider=final_provider,
        entry_date=entry_date.isoformat(),
        meal_type=meal_type,
        photo_save_token=save_token,
        photo_quota_request_id=request_id,
    )
    if len(candidates) > 1:
        await message.answer(
            _format_photo_dish_candidates_text(candidates),
            reply_markup=_build_photo_dish_candidates_menu(candidates),
            parse_mode="HTML",
        )
        return
    await _send_photo_analysis_confirmation(
        message,
        candidates[0]["items"],
        save_token,
        candidates[0]["dish_name"],
    )


async def _activate_photo_dish_candidate(
    message: Message,
    state: FSMContext,
    *,
    items: list[dict],
    dish_name: str,
) -> None:
    data = await state.get_data()
    save_token = data.get("photo_save_token")
    if not items or not _is_valid_meal_save_token(save_token):
        await message.answer(STALE_MEAL_SAVE_TEXT)
        return
    normalized_name = normalize_dish_display_name(dish_name, items)
    await state.update_data(
        photo_analysis_items=[dict(item) for item in items],
        photo_analysis_dish_name=normalized_name,
        photo_analysis_candidates=None,
    )
    await _edit_or_send_photo_analysis_message(
        message,
        _format_photo_analysis_confirmation_text(items, dish_name=normalized_name),
        reply_markup=_build_photo_analysis_confirm_menu(items, save_token),
        parse_mode="HTML",
    )


@router.callback_query(lambda c: c.data.startswith("photo_dish_pick:"))
async def photo_analysis_pick_dish(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    candidates = data.get("photo_analysis_candidates") or []
    try:
        index = int(callback.data.split(":", 1)[1])
    except (TypeError, ValueError, IndexError):
        await callback.answer("Не удалось выбрать блюдо", show_alert=True)
        return
    if index < 0 or index >= len(candidates):
        await callback.answer("Блюдо не найдено", show_alert=True)
        return
    candidate = candidates[index]
    await callback.answer()
    await _activate_photo_dish_candidate(
        callback.message,
        state,
        items=candidate.get("items") or [],
        dish_name=candidate.get("dish_name") or "",
    )


@router.callback_query(lambda c: c.data == "photo_dish_combine")
async def photo_analysis_combine_dishes(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    candidates = data.get("photo_analysis_candidates") or []
    items = [dict(item) for candidate in candidates for item in candidate.get("items") or []]
    if not items:
        await callback.answer("Черновик анализа не найден", show_alert=True)
        return
    combined_name = " + ".join(
        normalize_dish_display_name(candidate.get("dish_name"), candidate.get("items") or [])
        for candidate in candidates
    )
    await callback.answer()
    await _activate_photo_dish_candidate(
        callback.message,
        state,
        items=items,
        dish_name=combined_name,
    )


@router.message(MealEntryStates.waiting_for_photo, F.photo)
async def handle_photo_input(message: Message, state: FSMContext):
    """Сохраняет фото еды и сразу ждёт уточнение перед анализом."""
    photo = message.photo[-1]
    await _clear_photo_comment_fields(state)
    await state.update_data(
        food_photo_file_id=photo.file_id,
        food_photo_message_id=getattr(message, "message_id", None),
    )
    await state.set_state(MealEntryStates.waiting_for_food_photo_comment)
    await message.answer(
        "📷 Фото получено.\n\n"
        "Если хотите, можете сразу написать уточнение к блюду одним сообщением.\n\n"
        "Например:\n"
        "• общий вес 500 г\n"
        "• помидоры, огурцы, фета, авокадо, лук\n"
        "• съел половину порции\n"
        "• без масла / с майонезом / со сметаной\n\n"
        "Если уточнений нет — нажмите «⏭️ Анализировать без уточнения».",
        reply_markup=_build_food_photo_clarification_menu(),
    )


async def _run_pending_food_photo_analysis(
    message: Message,
    state: FSMContext,
    *,
    comment: str | None = None,
    user_id: str | None = None,
) -> None:
    """Запускает анализ ранее полученного фото еды с опциональным уточнением."""
    data = await state.get_data()
    file_id = data.get("food_photo_file_id")
    if not file_id:
        await state.set_state(MealEntryStates.waiting_for_photo)
        await message.answer(
            "Фото не найдено. Отправь фото еды ещё раз.",
            reply_markup=kbju_add_method_back_menu,
        )
        return

    await _handle_food_photo_analysis(
        message,
        state,
        provider="gemini",
        analyzer=gemini_service.estimate_kbju_from_photo,
        runner=_run_gemini_task,
        error_sender=_send_ai_error_message,
        image_file_id=str(file_id),
        comment=comment,
        user_id=user_id,
    )


@router.callback_query(lambda c: c.data == "food_photo_analyze_now")
async def analyze_food_photo_without_comment(callback: CallbackQuery, state: FSMContext):
    """Запускает анализ сохранённого фото без дополнительного контекста."""
    await callback.answer()
    await callback.message.edit_reply_markup(reply_markup=None)
    await _run_pending_food_photo_analysis(
        callback.message,
        state,
        user_id=str(callback.from_user.id),
    )


@router.callback_query(lambda c: c.data == "food_photo_cancel")
async def cancel_pending_food_photo_analysis(callback: CallbackQuery, state: FSMContext):
    """Отменяет ожидание уточнения к фото еды через inline-кнопку."""
    await callback.answer()
    await callback.message.edit_reply_markup(reply_markup=None)
    await _return_to_add_methods_from_method_input(
        callback.message,
        state,
        user_id=str(callback.from_user.id),
    )


@router.message(MealEntryStates.waiting_for_food_photo_comment)
async def handle_food_photo_comment(message: Message, state: FSMContext):
    """Получает текстовое уточнение и запускает анализ фото еды."""
    text = (message.text or "").strip()
    await _clear_photo_comment_fields(state)
    if text in BACK_BUTTON_TEXTS or text == "❌ Отмена":
        await _return_to_add_methods_from_method_input(message, state)
        return
    if not text:
        await message.answer("Пожалуйста, введите уточнение текстом или нажмите «❌ Отмена».")
        return
    if len(text) > MAX_MEAL_TEXT_LENGTH:
        logger.info(
            "Photo food comment rejected reason=too_long input_chars=%s",
            len(text),
        )
        await message.answer(PHOTO_COMMENT_TOO_LONG_TEXT)
        return

    sensitive_check = check_sensitive_meal_text(text)
    if sensitive_check.is_sensitive:
        logger.warning(
            "Photo food comment rejected reason=%s",
            sensitive_check.reason.value if sensitive_check.reason else "unknown",
        )
        await message.answer(SENSITIVE_MEAL_INPUT_REJECTED_TEXT, parse_mode="HTML")
        return

    await _run_pending_food_photo_analysis(
        message,
        state,
        comment=text,
        user_id=str(message.from_user.id),
    )


@router.message(MealEntryStates.waiting_for_photo)
async def handle_food_photo_non_photo(message: Message, state: FSMContext):
    """Просит прислать фото еды или обрабатывает отмену сценария."""
    text = (message.text or "").strip()
    if text in BACK_BUTTON_TEXTS or text == "❌ Отмена":
        await _return_to_add_methods_from_method_input(message, state)
        return
    await message.answer("Пожалуйста, отправь фото еды для анализа или нажми «⬅️ Назад».")


@router.message(MealEntryStates.waiting_for_openai_food_photo, F.photo)
async def handle_openai_food_photo(message: Message, state: FSMContext):
    """Обрабатывает фото еды через OpenAI."""
    await _handle_food_photo_analysis(
        message,
        state,
        provider="openai",
        analyzer=openai_label_service.analyze_food_photo_openai,
        runner=_run_openai_label_task,
        error_sender=_send_openai_food_error_message,
        raw_query="[Анализ по фото OpenAI]",
    )


@router.message(MealEntryStates.waiting_for_openai_food_photo)
async def handle_openai_food_non_photo(message: Message, state: FSMContext):
    """Просит прислать именно фото для OpenAI-анализа еды."""
    if (message.text or "").strip() in BACK_BUTTON_TEXTS:
        await _return_to_add_methods_from_method_input(message, state)
        return
    await message.answer("Пожалуйста, отправь фото еды для OpenAI-анализа.")


async def _cancel_photo_analysis_confirmation(
    message: Message,
    state: FSMContext,
    *,
    user_id: str | None = None,
):
    """Удаляет черновик анализа фото и возвращает к способам добавления."""
    await message.answer("❌ Анализ блюда отменён.")
    await _return_to_add_methods_from_method_input(
        message,
        state,
        user_id=user_id,
    )


async def _save_photo_analysis_confirmation(
    message: Message,
    state: FSMContext,
    user_id: str,
    data: dict,
) -> MealSaveResult:
    items = data.get("photo_analysis_items") or []
    save_token = data.get("photo_save_token")
    if not items or not _is_valid_meal_save_token(save_token):
        await message.answer(STALE_MEAL_SAVE_TEXT)
        return _failed_meal_save_result("stale_draft")
    meal_type = normalize_meal_type(data.get("meal_type"), fallback=MealType.SNACK.value)
    entry_date_str = data.get("entry_date")
    try:
        entry_date = date.fromisoformat(entry_date_str) if isinstance(entry_date_str, str) else date.today()
    except ValueError:
        parsed = parse_date(entry_date_str)
        entry_date = parsed.date() if isinstance(parsed, datetime) else date.today()

    dish_name = normalize_dish_display_name(data.get("photo_analysis_dish_name"), items)
    save_result = DishService.save_photo_dish_entry(
        save_token=save_token,
        user_id=user_id,
        dish_name=dish_name,
        items=items,
        entry_date=entry_date,
        meal_type=meal_type,
        provider=data.get("photo_analysis_provider"),
    )
    if save_result.status is MealSaveStatus.FAILED or save_result.meal is None:
        await message.answer(
            "Не удалось сохранить приём пищи. Черновик сохранён — попробуй ещё раз."
        )
        return save_result

    saved_meal = save_result.meal

    if not hasattr(message.bot, "last_meal_ids"):
        message.bot.last_meal_ids = {}
    message.bot.last_meal_ids[user_id] = saved_meal.id

    await state.update_data(
        photo_analysis_items=None,
        photo_analysis_raw_query=None,
        photo_analysis_provider=None,
        photo_analysis_dish_name=None,
        photo_analysis_candidates=None,
        photo_analysis_editing_idx=None,
        photo_total_weight_draft_items=None,
        photo_total_weight_original_items=None,
        photo_save_token=None,
    )
    await _clear_photo_comment_fields(state)
    await _keep_meal_entry_open_after_save(
        message,
        state,
        user_id=user_id,
        entry_date=entry_date,
        meal_type=meal_type,
        intro_lines=(
            ["✅ Приём пищи уже сохранён."]
            if save_result.status is MealSaveStatus.ALREADY_SAVED
            else None
        ),
    )
    return save_result


@router.callback_query(lambda c: c.data.startswith("edit_photo_food_item:") or c.data.startswith("photo_edit:"))
async def photo_analysis_edit_product(callback: CallbackQuery, state: FSMContext):
    """Открывает общий редактор ингредиента из анализа фото."""
    data = await state.get_data()
    items = data.get("photo_analysis_items") or []
    try:
        product_idx = int(callback.data.split(":", 1)[1])
    except (TypeError, ValueError, IndexError):
        await callback.answer("Не удалось открыть продукт", show_alert=True)
        return
    if product_idx < 0 or product_idx >= len(items):
        await callback.answer("Продукт не найден", show_alert=True)
        return

    await state.set_state(MealEntryStates.editing_meal_weight)
    await state.update_data(
        photo_analysis_editing_idx=product_idx,
        photo_draft_edit_mode=True,
        saved_products=[dict(item) for item in items],
        editing_product_idx=product_idx,
        weight_drafts={},
        kbju_drafts={},
    )
    await callback.message.edit_text(
        _render_product_actions_text(items[product_idx]),
        reply_markup=_build_product_actions_keyboard(product_idx),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(lambda c: c.data == "photo_dish_name")
async def photo_analysis_dish_name_start(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    if not data.get("photo_analysis_items"):
        await callback.answer("Черновик анализа не найден", show_alert=True)
        return
    await callback.answer()
    await state.set_state(MealEntryStates.editing_photo_dish_name_input)
    await callback.message.answer(
        "Введи короткое название блюда (до 80 символов):",
        reply_markup=ReplyKeyboardRemove(),
    )


@router.message(MealEntryStates.editing_photo_dish_name_input)
async def photo_analysis_dish_name_apply(message: Message, state: FSMContext):
    value = (message.text or "").strip()
    if value == "❌ Отмена":
        data = await state.get_data()
        items = data.get("photo_analysis_items") or []
        save_token = data.get("photo_save_token")
        await state.set_state(MealEntryStates.confirming_photo_analysis)
        if items and _is_valid_meal_save_token(save_token):
            await message.answer(
                _format_photo_analysis_confirmation_text(
                    items,
                    dish_name=data.get("photo_analysis_dish_name"),
                ),
                reply_markup=_build_photo_analysis_confirm_menu(items, save_token),
                parse_mode="HTML",
            )
        else:
            await message.answer(STALE_MEAL_SAVE_TEXT)
        return
    if not value or not any(character.isalpha() for character in value):
        await message.answer("Название должно содержать буквы. Попробуй ещё раз.")
        return
    if len(value) > 80:
        await message.answer("Название слишком длинное. Введи до 80 символов.")
        return
    if await _reject_sensitive_food_name(message, value, context="photo_dish_rename"):
        return
    data = await state.get_data()
    items = data.get("photo_analysis_items") or []
    save_token = data.get("photo_save_token")
    if not items or not _is_valid_meal_save_token(save_token):
        await state.set_state(MealEntryStates.confirming_photo_analysis)
        await message.answer(STALE_MEAL_SAVE_TEXT)
        return
    await state.set_state(MealEntryStates.confirming_photo_analysis)
    await state.update_data(photo_analysis_dish_name=value)
    await message.answer(
        _format_photo_analysis_confirmation_text(items, dish_name=value),
        reply_markup=_build_photo_analysis_confirm_menu(items, save_token),
        parse_mode="HTML",
    )


@router.callback_query(lambda c: c.data.startswith("photo_wchg:"))
async def photo_analysis_weight_change(callback: CallbackQuery, state: FSMContext):
    """Корректирует вес выбранного продукта через inline-кнопки."""
    data = await state.get_data()
    items = data.get("photo_analysis_items") or []
    if not items:
        await callback.answer("Черновик анализа фото не найден", show_alert=True)
        return

    parts = callback.data.split(":")
    if len(parts) == 2:
        # Backward-compatible path for old callback payloads: edit first product only.
        product_idx = int(data.get("photo_analysis_editing_idx") or 0)
        delta = float(parts[1])
    else:
        try:
            product_idx = int(parts[1])
            delta = float(parts[2])
        except (TypeError, ValueError, IndexError):
            await callback.answer("Не удалось изменить вес", show_alert=True)
            return

    if product_idx < 0 or product_idx >= len(items):
        await callback.answer("Продукт не найден", show_alert=True)
        return

    current_weight = _safe_float(items[product_idx].get("grams"))
    new_weight = current_weight + delta
    if new_weight < 1:
        await callback.answer(
            "Минимальный вес продукта — 1 г. Чтобы убрать продукт, нажмите 🗑 Удалить.",
            show_alert=False,
        )
        return

    updated_items = [dict(item) for item in items]
    updated_items[product_idx] = _scale_photo_item(updated_items[product_idx], new_weight)
    await state.update_data(photo_analysis_items=updated_items, photo_analysis_editing_idx=product_idx)
    await _edit_or_send_photo_analysis_message(
        callback.message,
        _format_photo_weight_editor_text(updated_items[product_idx]),
        reply_markup=_build_photo_weight_editor_menu(product_idx),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(lambda c: c.data.startswith("photo_delete:"))
async def photo_analysis_delete_product(callback: CallbackQuery, state: FSMContext):
    """Удаляет выбранный продукт из текущего результата анализа фото."""
    data = await state.get_data()
    items = data.get("photo_analysis_items") or []
    try:
        product_idx = int(callback.data.split(":", 1)[1])
    except (TypeError, ValueError, IndexError):
        await callback.answer("Не удалось удалить продукт", show_alert=True)
        return

    if product_idx < 0 or product_idx >= len(items):
        await callback.answer("Продукт не найден", show_alert=True)
        return

    updated_items = [dict(item) for idx, item in enumerate(items) if idx != product_idx]
    await state.update_data(
        photo_analysis_items=updated_items,
        photo_analysis_editing_idx=None,
        photo_total_weight_draft_items=None,
        photo_total_weight_original_items=None,
    )
    await callback.answer("Продукт удалён")

    if not updated_items:
        await _edit_or_send_photo_analysis_message(
            callback.message,
            "Все продукты удалены. Добавьте продукт вручную или отмените действие.",
            reply_markup=None,
            parse_mode=None,
        )
        return

    await _edit_or_send_photo_analysis_message(
        callback.message,
        _format_photo_analysis_confirmation_text(
            updated_items,
            dish_name=data.get("photo_analysis_dish_name"),
        ),
        reply_markup=_build_photo_analysis_confirm_menu(
            updated_items,
            data.get("photo_save_token"),
        ),
        parse_mode="HTML",
    )


@router.callback_query(lambda c: c.data == "photo_done")
async def photo_analysis_weight_done(callback: CallbackQuery, state: FSMContext):
    """Возвращает с редактора веса на общий экран анализа фото."""
    data = await state.get_data()
    items = data.get("photo_analysis_items") or []
    if not items:
        await callback.answer("Черновик анализа фото не найден", show_alert=True)
        return
    await state.update_data(photo_analysis_editing_idx=None)
    await _edit_or_send_photo_analysis_message(
        callback.message,
        _format_photo_analysis_confirmation_text(
            items,
            dish_name=data.get("photo_analysis_dish_name"),
        ),
        reply_markup=_build_photo_analysis_confirm_menu(
            items,
            data.get("photo_save_token"),
        ),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(lambda c: c.data == "photo_total_weight")
async def photo_analysis_total_weight_open(callback: CallbackQuery, state: FSMContext):
    """Открывает редактор общего веса блюда из анализа фото."""
    data = await state.get_data()
    items = data.get("photo_analysis_items") or []
    if not items:
        await callback.answer("Черновик анализа фото не найден", show_alert=True)
        return

    draft_items = [dict(item) for item in items]
    total_weight = sum(_safe_float(item.get("grams")) for item in draft_items)
    await state.update_data(
        photo_total_weight_original_items=[dict(item) for item in items],
        photo_total_weight_draft_items=draft_items,
    )
    await callback.message.edit_text(
        _format_photo_total_weight_editor_text(total_weight),
        reply_markup=_build_photo_total_weight_editor_menu(),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(lambda c: c.data.startswith("photo_twchg:"))
async def photo_analysis_total_weight_change(callback: CallbackQuery, state: FSMContext):
    """Меняет общий вес блюда и пропорционально пересчитывает все продукты в черновике."""
    data = await state.get_data()
    draft_items = data.get("photo_total_weight_draft_items") or data.get("photo_analysis_items") or []
    if not draft_items:
        await callback.answer("Черновик анализа фото не найден", show_alert=True)
        return
    try:
        delta = float(callback.data.split(":", 1)[1])
    except (TypeError, ValueError, IndexError):
        await callback.answer("Не удалось изменить общий вес", show_alert=True)
        return

    current_total_weight = sum(_safe_float(item.get("grams")) for item in draft_items)
    new_total_weight = max(1.0, current_total_weight + delta)
    updated_items = _scale_photo_items([dict(item) for item in draft_items], new_total_weight)
    await state.update_data(photo_total_weight_draft_items=updated_items)
    await callback.message.edit_text(
        _format_photo_total_weight_editor_text(sum(_safe_float(item.get("grams")) for item in updated_items)),
        reply_markup=_build_photo_total_weight_editor_menu(),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(lambda c: c.data == "photo_twmanual")
async def photo_analysis_total_weight_manual_request(callback: CallbackQuery, state: FSMContext):
    """Запрашивает ручной ввод общего веса блюда."""
    data = await state.get_data()
    if not (data.get("photo_total_weight_draft_items") or data.get("photo_analysis_items")):
        await callback.answer("Черновик анализа фото не найден", show_alert=True)
        return
    await state.set_state(MealEntryStates.editing_photo_total_weight_manual_input)
    await callback.message.answer(
        "Введи общий вес блюда числом в граммах, например: 500",
        reply_markup=ReplyKeyboardRemove(),
    )
    await callback.answer()


@router.message(MealEntryStates.editing_photo_total_weight_manual_input)
async def photo_analysis_total_weight_manual_apply(message: Message, state: FSMContext):
    """Применяет ручной ввод общего веса блюда."""
    text = (message.text or "").strip().replace(",", ".")
    if text == "❌ Отмена":
        await _cancel_photo_analysis_confirmation(message, state)
        return
    try:
        new_total_weight = float(text)
    except (TypeError, ValueError):
        await message.answer("Пожалуйста, введи вес числом в граммах, например: 500")
        return
    if new_total_weight < 1:
        await message.answer("Общий вес должен быть не меньше 1 г. Введи число в граммах.")
        return

    data = await state.get_data()
    draft_items = data.get("photo_total_weight_draft_items") or data.get("photo_analysis_items") or []
    if not draft_items:
        await state.set_state(MealEntryStates.confirming_photo_analysis)
        await message.answer("Черновик анализа фото не найден. Можно попробовать ещё раз.", reply_markup=kbju_add_menu)
        return

    updated_items = _scale_photo_items([dict(item) for item in draft_items], new_total_weight)
    await state.set_state(MealEntryStates.confirming_photo_analysis)
    await state.update_data(photo_total_weight_draft_items=updated_items)
    await message.answer(
        _format_photo_total_weight_editor_text(sum(_safe_float(item.get("grams")) for item in updated_items)),
        reply_markup=_build_photo_total_weight_editor_menu(),
        parse_mode="HTML",
    )


@router.callback_query(lambda c: c.data == "photo_twsave")
async def photo_analysis_total_weight_save(callback: CallbackQuery, state: FSMContext):
    """Сохраняет черновик общего веса и возвращает экран результата анализа фото."""
    data = await state.get_data()
    draft_items = data.get("photo_total_weight_draft_items") or []
    if not draft_items:
        await callback.answer("Черновик общего веса не найден", show_alert=True)
        return
    await state.update_data(
        photo_analysis_items=draft_items,
        photo_total_weight_draft_items=None,
        photo_total_weight_original_items=None,
    )
    await callback.message.edit_text(
        _format_photo_analysis_confirmation_text(
            draft_items,
            dish_name=data.get("photo_analysis_dish_name"),
        ),
        reply_markup=_build_photo_analysis_confirm_menu(
            draft_items,
            data.get("photo_save_token"),
        ),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(lambda c: c.data == "photo_twback")
async def photo_analysis_total_weight_back(callback: CallbackQuery, state: FSMContext):
    """Возвращает к результату анализа фото без сохранения черновика общего веса."""
    data = await state.get_data()
    items = data.get("photo_total_weight_original_items") or data.get("photo_analysis_items") or []
    if not items:
        await callback.answer("Черновик анализа фото не найден", show_alert=True)
        return
    await state.update_data(
        photo_total_weight_draft_items=None,
        photo_total_weight_original_items=None,
    )
    await callback.message.edit_text(
        _format_photo_analysis_confirmation_text(
            items,
            dish_name=data.get("photo_analysis_dish_name"),
        ),
        reply_markup=_build_photo_analysis_confirm_menu(
            items,
            data.get("photo_save_token"),
        ),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(lambda c: c.data == "photo_cancel")
async def photo_analysis_cancel(callback: CallbackQuery, state: FSMContext):
    """Legacy: отменяет сохранение анализа фото через inline-кнопку."""
    await callback.answer()
    await callback.message.edit_reply_markup(reply_markup=None)
    await _cancel_photo_analysis_confirmation(
        callback.message,
        state,
        user_id=str(callback.from_user.id),
    )


@router.callback_query(lambda c: c.data.startswith("save_photo_food_analysis:"))
async def photo_analysis_save(callback: CallbackQuery, state: FSMContext):
    """Сохраняет анализ фото через inline-кнопку."""
    data = await state.get_data()
    callback_token = callback.data.split(":", maxsplit=1)[1]
    current_token = data.get("photo_save_token")
    if (
        not _is_valid_meal_save_token(callback_token)
        or callback_token != current_token
    ):
        await callback.answer(STALE_MEAL_SAVE_TEXT, show_alert=True)
        return
    if not data.get("photo_analysis_items"):
        await callback.answer(STALE_MEAL_SAVE_TEXT, show_alert=True)
        return
    await callback.answer()
    save_result = await _save_photo_analysis_confirmation(
        callback.message,
        state,
        str(callback.from_user.id),
        data,
    )
    if save_result.status is not MealSaveStatus.FAILED:
        try:
            await callback.message.edit_reply_markup(reply_markup=None)
        except TelegramBadRequest:
            pass


@router.callback_query(lambda c: c.data in {"save_photo_food_analysis", "photo_save"})
async def reject_legacy_photo_meal_save_callback(callback: CallbackQuery):
    """Rejects old unbound Save buttons after deployment."""
    await callback.answer(STALE_MEAL_SAVE_TEXT, show_alert=True)


@router.message(MealEntryStates.confirming_photo_analysis)
async def handle_photo_analysis_confirmation(message: Message, state: FSMContext):
    """Подтверждает, корректирует или отменяет сохранение еды после анализа фото."""
    text = (message.text or "").strip()
    if await _reroute_add_method_button_if_needed(message, state, text):
        return

    data = await state.get_data()
    items = data.get("photo_analysis_items") or []
    if not items:
        await state.set_state(MealEntryStates.choosing_meal_type)
        await state.update_data(
            photo_analysis_items=None,
            photo_analysis_raw_query=None,
            photo_analysis_provider=None,
            photo_analysis_editing_idx=None,
            photo_total_weight_draft_items=None,
            photo_total_weight_original_items=None,
        )
        await message.answer("Черновик анализа фото не найден. Можно попробовать ещё раз.", reply_markup=kbju_add_menu)
        return

    if text == "❌ Отмена":
        await _cancel_photo_analysis_confirmation(message, state)
        return

    return


async def _handle_label_photo_analysis(
    message: Message,
    state: FSMContext,
    *,
    provider: str,
    analyzer,
    error_sender,
    runner,
    meal_source: str | None = None,
):
    """Общая логика обработки фото этикетки для Gemini и OpenAI."""
    user_id = str(message.from_user.id)
    data = await state.get_data()
    meal_type = normalize_meal_type(data.get("meal_type"), fallback=MealType.SNACK.value)
    entry_date_str = data.get("entry_date")
    if entry_date_str:
        if isinstance(entry_date_str, str):
            try:
                entry_date = date.fromisoformat(entry_date_str)
            except ValueError:
                parsed = parse_date(entry_date_str)
                entry_date = parsed.date() if isinstance(parsed, datetime) else date.today()
        else:
            entry_date = date.today()
    else:
        entry_date = date.today()

    logger.info("label_analysis_started provider=%s", provider)
    await message.answer("📋 Анализирую этикетку с помощью ИИ, секунду...")

    photo = message.photo[-1]
    file = await message.bot.get_file(photo.file_id)
    image_bytes = await message.bot.download_file(file.file_path)
    image_data = image_bytes.read()

    try:
        validate_ai_image(image_data)
    except ValueError as error:
        code = str(error)
        if code == "image_too_large":
            await message.answer("Фото слишком большое. Максимальный размер — 10 МБ.")
        else:
            await message.answer("Поддерживаются фотографии JPEG, PNG и WebP.")
        return

    request_id = build_quota_request_id(
        "nutrition_label",
        user_id,
        getattr(message, "message_id", None) or id(message),
    )
    reservation = await _reserve_meal_ai_quota(
        message,
        user_id=user_id,
        feature=AIFeature.NUTRITION_LABEL,
        request_id=request_id,
        meal_type=meal_type,
    )
    if reservation is None:
        return

    try:
        if provider == "openai":
            label_data = await runner(analyzer, image_data, user_id=user_id, feature="label_analysis")
        elif provider == "gemini_fallback":
            label_data = await runner(
                analyzer,
                image_data,
                user_id=user_id,
                quota_request_id=request_id,
            )
        else:
            label_data = await runner(analyzer, image_data)
    except Exception as e:
        ai_quota_service.release(request_id, outcome="provider_error")
        await error_sender(message, e)
        return

    if not label_data or "kbju_per_100g" not in label_data:
        ai_quota_service.release(request_id, outcome="no_label_data")
        await message.answer(
            "⚠️ Не удалось найти КБЖУ на этикетке.\n"
            "Попробуй сделать фото более чётким или используй другой способ."
        )
        return

    ai_quota_service.consume(request_id, outcome="success", result_ref="nutrition_label_draft")
    kbju_per_100g = label_data["kbju_per_100g"]
    package_weight = label_data.get("package_weight")
    found_weight = label_data.get("found_weight", False)
    product_name = label_data.get("product_name", "Продукт")
    unit_weight_g = _optional_positive_float(label_data.get("unit_weight_g"))
    unit_name = _optional_unit_name(label_data.get("unit_name"))
    package_units = _optional_positive_int(label_data.get("package_units"))

    def safe_float(value) -> float:
        try:
            if value is None:
                return 0.0
            return float(value)
        except (TypeError, ValueError):
            return 0.0

    kcal_100g = safe_float(kbju_per_100g.get("kcal"))
    protein_100g = safe_float(kbju_per_100g.get("protein"))
    fat_100g = safe_float(kbju_per_100g.get("fat"))
    carbs_100g = safe_float(kbju_per_100g.get("carbs"))

    await state.set_state(MealEntryStates.waiting_for_weight_input)
    update_payload = {
        "kbju_per_100g": kbju_per_100g,
        "product_name": product_name,
        "entry_date": entry_date.isoformat(),
        "label_save_token": _new_meal_save_token(),
        "label_quota_request_id": request_id,
        "unit_weight_g": unit_weight_g,
        "unit_name": unit_name,
        "package_units": package_units,
        "package_weight": None,
        "package_weight_g": None,
    }
    if meal_source:
        update_payload["meal_source"] = meal_source

    prompt_package_weight = None
    if found_weight and package_weight is not None:
        weight = safe_float(package_weight)
        if weight > 0:
            prompt_package_weight = weight
            update_payload["package_weight"] = weight
            update_payload["package_weight_g"] = weight

    await state.update_data(**update_payload)
    weight_input_menu = _build_label_weight_input_menu(prompt_package_weight)
    push_menu_stack(message.bot, weight_input_menu)

    await message.answer(
        _format_label_weight_prompt(
            product_name=product_name,
            kcal_100g=kcal_100g,
            protein_100g=protein_100g,
            fat_100g=fat_100g,
            carbs_100g=carbs_100g,
            package_weight=prompt_package_weight,
        ),
        reply_markup=weight_input_menu,
        parse_mode="HTML",
    )


@router.message(MealEntryStates.waiting_for_label_photo, F.photo)
async def handle_label_photo(message: Message, state: FSMContext):
    """Обрабатывает фото этикетки через Gemini."""
    await _handle_label_photo_analysis(
        message,
        state,
        provider="gemini_fallback",
        analyzer=gemini_service.extract_kbju_from_label,
        error_sender=_send_ai_error_message,
        runner=_run_label_analysis_with_openai_fallback,
    )


@router.message(MealEntryStates.waiting_for_label_photo)
async def handle_label_non_photo(message: Message, state: FSMContext):
    """Просит прислать фото этикетки или переключает способ добавления."""
    text = (message.text or "").strip()
    if await _select_meal_type_button_if_needed(message, state, text):
        return
    if await _reroute_add_method_button_if_needed(message, state, text):
        return
    if text in BACK_BUTTON_TEXTS:
        await _return_to_add_methods_from_method_input(message, state)
        return
    await message.answer("Пожалуйста, отправь фото этикетки или выбери другой способ добавления.")


@router.message(MealEntryStates.waiting_for_openai_label_photo, F.photo)
async def handle_openai_label_photo(message: Message, state: FSMContext):
    """Обрабатывает фото этикетки через OpenAI."""
    await _handle_label_photo_analysis(
        message,
        state,
        provider="openai",
        analyzer=openai_label_service.extract_kbju_from_label,
        error_sender=_send_openai_label_error_message,
        runner=_run_openai_label_task,
        meal_source="openai",
    )


@router.message(MealEntryStates.waiting_for_openai_label_photo)
async def handle_openai_label_non_photo(message: Message, state: FSMContext):
    """Просит прислать именно фото для OpenAI-анализа этикетки."""
    text = (message.text or "").strip()
    if await _select_meal_type_button_if_needed(message, state, text):
        return
    if await _reroute_add_method_button_if_needed(message, state, text):
        return
    if text in BACK_BUTTON_TEXTS:
        await _return_to_add_methods_from_method_input(message, state)
        return
    await message.answer("Пожалуйста, отправь фото этикетки или упаковки продукта.")


@router.message(MealEntryStates.waiting_for_weight_input)
async def handle_weight_input(message: Message, state: FSMContext):
    """Обрабатывает выбор/ввод веса и открывает подтверждение без сохранения."""
    text = (message.text or "").strip()
    if await _reroute_add_method_button_if_needed(message, state, text):
        return
    if text == "⬅️ Назад":
        from handlers.common import go_back
        await go_back(message, state)
        return

    try:
        weight_grams = float(text.replace(",", "."))
        if weight_grams <= 0:
            raise ValueError
    except (ValueError, AttributeError):
        await message.answer("Вес должен быть больше нуля. Введи правильное число (например: 50 или 100):")
        return

    await state.update_data(selected_label_weight=weight_grams)
    await state.set_state(MealEntryStates.confirming_label_weight)
    data = await state.get_data()
    save_token = data.get("label_save_token")
    if not _is_valid_meal_save_token(save_token):
        save_token = _new_meal_save_token()
        await state.update_data(label_save_token=save_token)
        data["label_save_token"] = save_token
    await _hide_meal_reply_keyboard(message)
    await message.answer(
        _format_label_weight_confirmation_text(data, weight_grams),
        reply_markup=_build_label_weight_confirm_menu(save_token),
        parse_mode="HTML",
    )


async def _save_label_analysis_draft(
    message: Message,
    state: FSMContext,
    *,
    user_id: str,
    data: dict,
    weight_grams: float,
) -> MealSaveResult:
    """Сохраняет подтверждённый результат этикетки для message- и callback-сценариев."""
    save_token = data.get("label_save_token")
    if not _is_valid_meal_save_token(save_token):
        await message.answer(STALE_MEAL_SAVE_TEXT)
        return _failed_meal_save_result("stale_draft")

    meal_type = normalize_meal_type(data.get("meal_type"), fallback=MealType.SNACK.value)
    entry_date_str = data.get("entry_date")
    if entry_date_str:
        try:
            entry_date = date.fromisoformat(entry_date_str) if isinstance(entry_date_str, str) else date.today()
        except ValueError:
            parsed = parse_date(entry_date_str)
            entry_date = parsed.date() if isinstance(parsed, datetime) else date.today()
    else:
        entry_date = date.today()

    kbju_per_100g = data.get("kbju_per_100g")
    product_name = data.get("product_name", "Продукт")
    meal_source = data.get("meal_source")
    totals_for_db, per_100g = _calculate_label_totals(kbju_per_100g, weight_grams)
    unit_weight_g = _optional_positive_float(data.get("unit_weight_g"))
    unit_name = _optional_unit_name(data.get("unit_name"))
    package_weight_g = _optional_positive_float(
        data.get("package_weight_g") or data.get("package_weight")
    )
    package_units = _optional_positive_int(data.get("package_units"))

    if meal_source == "openai":
        lines = [_format_label_result_header("label", product_name)]
        raw_query = f"[Этикетка OpenAI: {product_name}]"
    else:
        lines = [_format_label_result_header("label", product_name)]
        raw_query = f"[Этикетка: {product_name}]"

    lines.append(f"📦 <b>Вес:</b> {weight_grams:.0f} г\n")
    lines.append("<b>КБЖУ:</b>")
    lines.append(_format_kbju_summary_block(totals_for_db))
    product_payload = {
        "name": product_name,
        "grams": weight_grams,
        "kcal": totals_for_db["calories"],
        "protein": totals_for_db["protein"],
        "fat": totals_for_db["fat"],
        "carbs": totals_for_db["carbs"],
        "calories": totals_for_db["calories"],
        "protein_g": totals_for_db["protein"],
        "fat_total_g": totals_for_db["fat"],
        "carbohydrates_total_g": totals_for_db["carbs"],
        "calories_per_100g": per_100g["kcal"],
        "protein_per_100g": per_100g["protein"],
        "fat_per_100g": per_100g["fat"],
        "carbs_per_100g": per_100g["carbs"],
        "source": "label_analysis",
        "unit_weight_g": unit_weight_g,
        "unit_name": unit_name,
        "package_weight_g": package_weight_g,
        "package_units": package_units,
    }
    products_json = json.dumps([product_payload])

    save_result = MealRepository.save_meal_idempotent(
        save_token=save_token,
        user_id=user_id,
        raw_query=raw_query,
        calories=totals_for_db["calories"],
        protein=totals_for_db["protein"],
        fat=totals_for_db["fat"],
        carbs=totals_for_db["carbs"],
        entry_date=entry_date,
        products_json=products_json,
        meal_type=meal_type,
    )
    if save_result.status is MealSaveStatus.FAILED or save_result.meal is None:
        await message.answer(
            "Не удалось сохранить приём пищи. Данные этикетки сохранены — попробуй ещё раз."
        )
        return save_result

    saved_meal = save_result.meal

    try:
        SavedProductRepository.upsert_from_product(user_id, product_payload)
    except Exception as exc:
        logger.warning(
            "Этикетка сохранена, но saved_products не обновлён error_type=%s",
            safe_exception_summary(exc),
        )

    if not hasattr(message.bot, "last_meal_ids"):
        message.bot.last_meal_ids = {}
    message.bot.last_meal_ids[user_id] = saved_meal.id

    await state.update_data(
        label_save_token=None,
        selected_label_weight=None,
        unit_weight_g=None,
        unit_name=None,
        package_weight=None,
        package_weight_g=None,
        package_units=None,
    )

    await _keep_meal_entry_open_after_save(
        message,
        state,
        user_id=user_id,
        entry_date=entry_date,
        meal_type=meal_type,
        intro_lines=(
            ["✅ Приём пищи уже сохранён."]
            if save_result.status is MealSaveStatus.ALREADY_SAVED
            else lines
        ),
        parse_mode="HTML",
    )
    return save_result


@router.message(MealEntryStates.confirming_label_weight)
async def handle_label_weight_confirmation(message: Message, state: FSMContext):
    """Поддерживает ввод со старой reply-клавиатуры для уже открытых сценариев."""
    text = (message.text or "").strip()
    if await _reroute_add_method_button_if_needed(message, state, text):
        return

    data = await state.get_data()
    current_weight = max(1.0, _safe_float(data.get("selected_label_weight"), 1.0))

    if text == "⬅️ Назад":
        package_weight = _safe_float(data.get("package_weight")) or None
        weight_input_menu = _build_label_weight_input_menu(package_weight)
        await state.set_state(MealEntryStates.waiting_for_weight_input)
        await message.answer(
            _format_label_weight_prompt(
                product_name=data.get("product_name", "Продукт"),
                kcal_100g=_safe_float((data.get("kbju_per_100g") or {}).get("kcal")),
                protein_100g=_safe_float((data.get("kbju_per_100g") or {}).get("protein")),
                fat_100g=_safe_float((data.get("kbju_per_100g") or {}).get("fat")),
                carbs_100g=_safe_float((data.get("kbju_per_100g") or {}).get("carbs")),
                package_weight=package_weight,
            ),
            reply_markup=weight_input_menu,
            parse_mode="HTML",
        )
        return

    if re.fullmatch(r"[+-](1|5|10|20|50|100)", text):
        current_weight = max(1.0, current_weight + float(text))
        await state.update_data(selected_label_weight=current_weight)
        save_token = data.get("label_save_token")
        await message.answer(
            _format_label_weight_confirmation_text(data, current_weight),
            reply_markup=_build_label_weight_confirm_menu(save_token),
            parse_mode="HTML",
        )
        return

    if text != "✅ Сохранить":
        await message.answer("Скорректируй вес кнопками или нажми ✅ Сохранить / ⬅️ Назад.")
        return

    await _save_label_analysis_draft(
        message,
        state,
        user_id=str(message.from_user.id),
        data=data,
        weight_grams=current_weight,
    )


def _is_current_label_callback(data: dict, callback_token: str) -> bool:
    return bool(
        _is_valid_meal_save_token(callback_token)
        and callback_token == data.get("label_save_token")
    )


@router.callback_query(
    MealEntryStates.confirming_label_weight,
    lambda c: c.data and c.data.startswith("label_wchg:"),
)
async def label_weight_change_inline(callback: CallbackQuery, state: FSMContext):
    """Корректирует вес этикетки без создания новых сообщений."""
    try:
        _, callback_token, raw_delta = callback.data.split(":", maxsplit=2)
        delta = float(raw_delta)
    except (TypeError, ValueError):
        await callback.answer("Не удалось изменить вес", show_alert=True)
        return
    data = await state.get_data()
    if not _is_current_label_callback(data, callback_token):
        await callback.answer(STALE_MEAL_SAVE_TEXT, show_alert=True)
        return

    current_weight = max(1.0, _safe_float(data.get("selected_label_weight"), 1.0))
    current_weight = max(1.0, current_weight + delta)
    await state.update_data(selected_label_weight=current_weight)
    await callback.message.edit_text(
        _format_label_weight_confirmation_text(data, current_weight),
        reply_markup=_build_label_weight_confirm_menu(callback_token),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(
    MealEntryStates.confirming_label_weight,
    lambda c: c.data and c.data.startswith("label_wback:"),
)
async def label_weight_back_inline(callback: CallbackQuery, state: FSMContext):
    """Возвращает к выбору веса этикетки."""
    data = await state.get_data()
    callback_token = callback.data.split(":", maxsplit=1)[1]
    if not _is_current_label_callback(data, callback_token):
        await callback.answer(STALE_MEAL_SAVE_TEXT, show_alert=True)
        return

    package_weight = _safe_float(data.get("package_weight")) or None
    await state.set_state(MealEntryStates.waiting_for_weight_input)
    await callback.answer()
    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except TelegramBadRequest:
        pass
    await callback.message.answer(
        _format_label_weight_prompt(
            product_name=data.get("product_name", "Продукт"),
            kcal_100g=_safe_float((data.get("kbju_per_100g") or {}).get("kcal")),
            protein_100g=_safe_float((data.get("kbju_per_100g") or {}).get("protein")),
            fat_100g=_safe_float((data.get("kbju_per_100g") or {}).get("fat")),
            carbs_100g=_safe_float((data.get("kbju_per_100g") or {}).get("carbs")),
            package_weight=package_weight,
        ),
        reply_markup=_build_label_weight_input_menu(package_weight),
        parse_mode="HTML",
    )


@router.callback_query(
    MealEntryStates.confirming_label_weight,
    lambda c: c.data and c.data.startswith("save_label_analysis:"),
)
async def save_label_analysis_inline(callback: CallbackQuery, state: FSMContext):
    """Сохраняет результат этикетки по последней inline-кнопке."""
    data = await state.get_data()
    callback_token = callback.data.split(":", maxsplit=1)[1]
    if not _is_current_label_callback(data, callback_token):
        await callback.answer(STALE_MEAL_SAVE_TEXT, show_alert=True)
        return

    await callback.answer()
    save_result = await _save_label_analysis_draft(
        callback.message,
        state,
        user_id=str(callback.from_user.id),
        data=data,
        weight_grams=max(1.0, _safe_float(data.get("selected_label_weight"), 1.0)),
    )
    if save_result.status is not MealSaveStatus.FAILED:
        try:
            await callback.message.edit_reply_markup(reply_markup=None)
        except TelegramBadRequest:
            pass

@router.message(lambda m: m.text == "📊 Дневной отчёт")
async def calories_today_results(message: Message):
    """Показывает дневной отчёт по КБЖУ."""
    reset_user_state(message)
    user_id = str(message.from_user.id)
    await send_today_results(message, user_id)


async def _return_to_food_diary(message: Message, user_id: str, target_date: date) -> None:
    """Возвращает пользователя в дневник питания после действия с приёмом пищи."""
    push_menu_stack(message.bot, kbju_menu)
    await message.answer("🍱 Дневник питания", reply_markup=kbju_menu)
    await _render_day_meals_messages(
        message,
        user_id,
        target_date,
        include_back=target_date != date.today(),
        force_refresh=True,
    )


async def send_today_results(message: Message, user_id: str):
    """Отправляет результаты за сегодня и возвращает меню раздела в reply-клавиатуру."""
    await _return_to_food_diary(message, user_id, date.today())


@router.callback_query(lambda c: c.data.startswith("meal_comment_continue:"))
async def continue_after_meal_comment(callback: CallbackQuery):
    """Открывает дневник после короткого комментария к завершённому приёму пищи."""
    user_id = str(callback.from_user.id)
    parts = callback.data.split(":")
    try:
        target_date = date.fromisoformat(parts[2]) if len(parts) > 2 else date.today()
    except ValueError:
        target_date = date.today()
    if callback.message:
        try:
            await callback.message.edit_reply_markup(reply_markup=None)
        except TelegramBadRequest as exc:
            logger.info("Не удалось убрать кнопку продолжения error_type=%s", safe_exception_summary(exc))
        await _return_to_food_diary(callback.message, user_id, target_date)
    await callback.answer()


@router.message(lambda m: m.text == "📆 Календарь КБЖУ")
async def calories_calendar(message: Message):
    """Показывает календарь КБЖУ."""
    reset_user_state(message)
    user_id = str(message.from_user.id)
    await show_calendar_back_button(message)
    await show_kbju_calendar(message, user_id)


async def show_kbju_calendar(message: Message, user_id: str, year: Optional[int] = None, month: Optional[int] = None):
    """Показывает календарь КБЖУ."""
    today = date.today()
    if year is None:
        year = today.year
    if month is None:
        month = today.month
    
    from utils.calendar_utils import build_kbju_calendar_keyboard
    keyboard = build_kbju_calendar_keyboard(user_id, year, month)
    
    await message.answer(
        f"📆 Календарь КБЖУ\n\nВыбери день:",
        reply_markup=keyboard,
    )


@router.callback_query(lambda c: c.data.startswith("meal_cal_nav:"))
async def navigate_kbju_calendar(callback: CallbackQuery):
    """Навигация по календарю КБЖУ."""
    await callback.answer()
    parts = callback.data.split(":")
    year, month = map(int, parts[1].split("-"))
    user_id = str(callback.from_user.id)
    await show_kbju_calendar(callback.message, user_id, year, month)


@router.callback_query(lambda c: c.data.startswith("meal_cal_back:"))
async def back_to_kbju_calendar(callback: CallbackQuery):
    """Возврат к календарю КБЖУ."""
    await callback.answer()
    parts = callback.data.split(":")
    year, month = map(int, parts[1].split("-"))
    user_id = str(callback.from_user.id)
    await show_kbju_calendar(callback.message, user_id, year, month)


@router.callback_query(lambda c: c.data.startswith("meal_cal_day:"))
async def select_kbju_calendar_day(callback: CallbackQuery):
    """Выбор дня в календаре КБЖУ."""
    await callback.answer()
    parts = callback.data.split(":")
    target_date = date.fromisoformat(parts[1])
    user_id = str(callback.from_user.id)
    await show_day_meals(callback.message, user_id, target_date)


async def show_day_meals(message: Message, user_id: str, target_date: date):
    """Показывает приёмы пищи за день."""
    await _render_day_meals_messages(message, user_id, target_date, include_back=True)


def _truncate_product_name(name: str, limit: int = 28) -> str:
    """Аккуратно обрезает длинные названия для inline-кнопок."""
    clean_name = (name or "продукт").strip()
    if len(clean_name) <= limit:
        return clean_name
    return f"{clean_name[:limit - 1].rstrip()}…"


def _build_weight_products_keyboard(products: list[dict]) -> InlineKeyboardMarkup:
    """Клавиатура выбора продукта для редактирования."""
    rows: list[list[InlineKeyboardButton]] = []
    for idx, product in enumerate(products, start=1):
        name = _truncate_product_name(extract_meal_product_name(product, fallback="продукт"))
        grams = extract_meal_product_weight(product)
        corrected_badge = " ✏️" if bool(product.get("is_manually_corrected")) else ""
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"{_format_emoji_number(idx)} {name} — {grams:.0f} г{corrected_badge}",
                    callback_data=f"meal_wsel:{idx - 1}",
                )
            ]
        )

    rows.append(
        [
            InlineKeyboardButton(text="✅ Готово", callback_data="meal_wdone"),
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def _send_weight_products_list(
    message: Message,
    text: str,
    products: list[dict],
    *,
    remove_reply_keyboard: bool = True,
) -> None:
    """Открывает список продуктов для редактирования и скрывает старую reply-клавиатуру."""
    if remove_reply_keyboard:
        await message.answer(
            "⬇️ Убираю нижнюю клавиатуру на время редактирования",
            reply_markup=ReplyKeyboardRemove(),
        )
    await message.answer(text, reply_markup=_build_weight_products_keyboard(products))


async def _show_meal_edit_products_list(
    message: Message,
    products: list[dict],
    meal_type: str,
    *,
    totals: dict | None = None,
    remove_reply_keyboard: bool = False,
    edit_existing: bool = False,
) -> None:
    """Показывает полную детализацию и клавиатуру того же упорядоченного списка."""
    chunks = format_meal_edit_chunks(meal_type, products, totals=totals)
    keyboard = _build_weight_products_keyboard(products)

    if edit_existing:
        try:
            await message.edit_text(
                chunks[0],
                reply_markup=keyboard if len(chunks) == 1 else None,
                parse_mode="HTML",
            )
        except TelegramBadRequest:
            await message.answer(
                chunks[0],
                reply_markup=keyboard if len(chunks) == 1 else None,
                parse_mode="HTML",
            )
        chunks = chunks[1:]

    for index, chunk in enumerate(chunks):
        is_first_chunk = index == 0
        is_last_chunk = index == len(chunks) - 1
        reply_markup = (
            ReplyKeyboardRemove()
            if remove_reply_keyboard and is_first_chunk
            else keyboard if is_last_chunk else None
        )
        sent = await message.answer(
            chunk,
            reply_markup=reply_markup,
            parse_mode="HTML",
        )
        if remove_reply_keyboard and is_first_chunk and is_last_chunk:
            try:
                await sent.edit_reply_markup(reply_markup=keyboard)
            except (AttributeError, TelegramBadRequest):
                await message.answer(
                    "Выберите продукт для редактирования:",
                    reply_markup=keyboard,
                )


def _format_product_macro_summary(
    calories: float,
    protein: float,
    fat: float,
    carbs: float,
) -> str:
    """Форматирует блок КБЖУ для карточки редактирования продукта."""
    return (
        f"🔥 <b>Калории:</b> {calories:.0f} ккал\n"
        f"🥩 <b>Белки:</b> {protein:.1f} г\n"
        f"🥑 <b>Жиры:</b> {fat:.1f} г\n"
        f"{CARBS_EMOJI} <b>Углеводы:</b> {carbs:.1f} г"
    )


def _render_product_actions_text(product: dict) -> str:
    name = html.escape(str(product.get("name") or "продукт"))
    grams = float(product.get("grams") or 0)
    calories, protein, fat, carbs = _extract_product_macros(product)
    lines = [
        "<b>✏️ Редактирование продукта</b>",
        "",
        f"<b>Продукт:</b> {name}",
        "",
        f"⚖️ <b>Вес:</b> {grams:.0f} г",
        _format_product_macro_summary(calories, protein, fat, carbs),
    ]
    if bool(product.get("is_manually_corrected")):
        lines.append("✏️ КБЖУ скорректированы вручную")
    lines.extend(["", "Выбери действие:"])
    return "\n".join(lines)


def _build_product_actions_keyboard(product_idx: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="✏️ Изменить название", callback_data=f"meal_pact_name:{product_idx}")],
            [InlineKeyboardButton(text="⚖️ Изменить вес", callback_data=f"meal_pact_weight:{product_idx}")],
            [InlineKeyboardButton(text="🧮 Изменить КБЖУ", callback_data=f"meal_pact_kbju:{product_idx}")],
            [InlineKeyboardButton(text="🗑 Удалить", callback_data=f"meal_wdelask:{product_idx}")],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="meal_wback_list")],
        ]
    )


def _build_name_input_keyboard(product_idx: int) -> InlineKeyboardMarkup:
    """Inline-клавиатура выхода из режима ввода нового названия."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="⬅️ Назад", callback_data=f"meal_pact_name_back:{product_idx}")],
        ]
    )


def _build_weight_editor_reply_keyboard() -> ReplyKeyboardMarkup:
    """Reply-клавиатура для отмены режима редактирования продукта."""
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="❌ Отмена")]],
        resize_keyboard=True,
    )


def _build_weight_editor_keyboard(product_idx: int, *, has_changes: bool = True) -> InlineKeyboardMarkup:
    """Клавиатура изменения веса одного продукта."""
    rows = [
        [
            InlineKeyboardButton(text="−100 г", callback_data=f"meal_wchg:{product_idx}:-100"),
            InlineKeyboardButton(text="−50 г", callback_data=f"meal_wchg:{product_idx}:-50"),
            InlineKeyboardButton(text="+50 г", callback_data=f"meal_wchg:{product_idx}:50"),
            InlineKeyboardButton(text="+100 г", callback_data=f"meal_wchg:{product_idx}:100"),
        ],
        [
            InlineKeyboardButton(text="−25 г", callback_data=f"meal_wchg:{product_idx}:-25"),
            InlineKeyboardButton(text="−10 г", callback_data=f"meal_wchg:{product_idx}:-10"),
            InlineKeyboardButton(text="+10 г", callback_data=f"meal_wchg:{product_idx}:10"),
            InlineKeyboardButton(text="+25 г", callback_data=f"meal_wchg:{product_idx}:25"),
        ],
        [
            InlineKeyboardButton(text="−5 г", callback_data=f"meal_wchg:{product_idx}:-5"),
            InlineKeyboardButton(text="−1 г", callback_data=f"meal_wchg:{product_idx}:-1"),
            InlineKeyboardButton(text="+1 г", callback_data=f"meal_wchg:{product_idx}:1"),
            InlineKeyboardButton(text="+5 г", callback_data=f"meal_wchg:{product_idx}:5"),
        ],
        [InlineKeyboardButton(text="⌨️ Ввести вручную", callback_data=f"meal_wmanual:{product_idx}")],
    ]
    action_row = []
    if has_changes:
        action_row.append(InlineKeyboardButton(text="✅ Сохранить", callback_data=f"meal_wsave:{product_idx}"))
    action_row.append(InlineKeyboardButton(text="🗑 Удалить", callback_data=f"meal_wdelask:{product_idx}"))
    rows.append(action_row)
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data=f"meal_wback_product:{product_idx}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _build_weight_delete_confirm_keyboard(product_idx: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🗑 Да, удалить", callback_data=f"meal_wdel:{product_idx}")],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data=f"meal_wdelback:{product_idx}")],
        ]
    )


def _extract_product_macros(product: dict) -> tuple[float, float, float, float]:
    """Возвращает калории/белки/жиры/углеводы из любого поддерживаемого формата."""
    return extract_meal_product_macros(product)


def _ensure_per_100g(product: dict) -> tuple[float, float, float, float]:
    """Гарантирует наличие КБЖУ на 100 г для пересчётов."""
    calories_per_100g = float(product.get("calories_per_100g") or 0)
    protein_per_100g = float(product.get("protein_per_100g") or 0)
    fat_per_100g = float(product.get("fat_per_100g") or 0)
    carbs_per_100g = float(product.get("carbs_per_100g") or 0)

    if calories_per_100g or protein_per_100g or fat_per_100g or carbs_per_100g:
        return calories_per_100g, protein_per_100g, fat_per_100g, carbs_per_100g

    grams = float(product.get("grams") or 0)
    if grams <= 0:
        return 0, 0, 0, 0

    calories, protein, fat, carbs = _extract_product_macros(product)
    return (
        (calories / grams) * 100 if calories else 0,
        (protein / grams) * 100 if protein else 0,
        (fat / grams) * 100 if fat else 0,
        (carbs / grams) * 100 if carbs else 0,
    )


def _apply_product_weight(product: dict, new_weight: float) -> bool:
    """Обновляет вес и пересчитывает КБЖУ продукта. Возвращает False, если данных недостаточно."""
    calories_per_100g, protein_per_100g, fat_per_100g, carbs_per_100g = _ensure_per_100g(product)
    if not calories_per_100g and not protein_per_100g and not fat_per_100g and not carbs_per_100g:
        return False

    new_calories = (calories_per_100g * new_weight) / 100 if calories_per_100g else 0
    new_protein = (protein_per_100g * new_weight) / 100 if protein_per_100g else 0
    new_fat = (fat_per_100g * new_weight) / 100 if fat_per_100g else 0
    new_carbs = (carbs_per_100g * new_weight) / 100 if carbs_per_100g else 0

    product["grams"] = new_weight
    product["kcal"] = new_calories
    product["protein"] = new_protein
    product["fat"] = new_fat
    product["carbs"] = new_carbs
    product["calories"] = new_calories
    product["protein_g"] = new_protein
    product["fat_total_g"] = new_fat
    product["carbohydrates_total_g"] = new_carbs
    product["calories_per_100g"] = calories_per_100g
    product["protein_per_100g"] = protein_per_100g
    product["fat_per_100g"] = fat_per_100g
    product["carbs_per_100g"] = carbs_per_100g
    return True


def _apply_product_manual_macros(
    product: dict,
    *,
    calories: Optional[float] = None,
    protein: Optional[float] = None,
    fat: Optional[float] = None,
    carbs: Optional[float] = None,
) -> bool:
    """Обновляет выбранные поля КБЖУ без изменения веса и пересчитывает значения на 100 г."""
    grams = float(product.get("grams") or 0)
    if grams <= 0:
        return False

    current_calories, current_protein, current_fat, current_carbs = _extract_product_macros(product)
    new_calories = current_calories if calories is None else float(calories)
    new_protein = current_protein if protein is None else float(protein)
    new_fat = current_fat if fat is None else float(fat)
    new_carbs = current_carbs if carbs is None else float(carbs)

    product["kcal"] = new_calories
    product["protein"] = new_protein
    product["fat"] = new_fat
    product["carbs"] = new_carbs
    product["calories"] = new_calories
    product["protein_g"] = new_protein
    product["fat_total_g"] = new_fat
    product["carbohydrates_total_g"] = new_carbs
    product["calories_per_100g"] = (new_calories / grams) * 100
    product["protein_per_100g"] = (new_protein / grams) * 100
    product["fat_per_100g"] = (new_fat / grams) * 100
    product["carbs_per_100g"] = (new_carbs / grams) * 100
    product["is_manually_corrected"] = True
    return True


def _parse_kbju_bulk_input(raw_text: str) -> Optional[tuple[float, float, float, float]]:
    normalized = (raw_text or "").strip().replace(",", ".")
    chunks = [part for part in normalized.split() if part]
    if len(chunks) != 4:
        return None
    try:
        values = tuple(float(part) for part in chunks)
    except ValueError:
        return None
    if any(value < 0 for value in values):
        return None
    return values  # calories, protein, fat, carbs


def _build_meal_update_payload(products: list[dict]) -> tuple[dict, str]:
    """Формирует суммарные КБЖУ и api_details для сохранения приёма пищи."""
    totals = {
        "calories": 0.0,
        "protein_g": 0.0,
        "fat_total_g": 0.0,
        "carbohydrates_total_g": 0.0,
    }
    api_details_lines = []

    for product in products:
        name = product.get("name", "продукт")
        grams = float(product.get("grams", 0))
        calories, protein, fat, carbs = _extract_product_macros(product)

        totals["calories"] += calories
        totals["protein_g"] += protein
        totals["fat_total_g"] += fat
        totals["carbohydrates_total_g"] += carbs

        api_details_lines.append(
            f"• {name} ({grams:.0f} г) — {calories:.0f} ккал "
            f"(Б {protein:.1f} / Ж {fat:.1f} / У {carbs:.1f})"
        )

    return totals, "\n".join(api_details_lines) if api_details_lines else None


def _build_product_preview_for_weight(product: dict, draft_weight: Optional[float]) -> dict:
    """Возвращает копию продукта с КБЖУ, пересчитанными под черновой вес."""
    preview = dict(product)
    current_weight = float(product.get("grams") or 0)
    if draft_weight is not None and round(float(draft_weight), 2) != round(current_weight, 2):
        _apply_product_weight(preview, float(draft_weight))
    return preview


def _render_weight_editor_text(product: dict, draft_weight: Optional[float] = None) -> str:
    """Текст экрана изменения веса конкретного продукта."""
    name = html.escape(str(product.get("name") or "продукт"))
    current_weight = float(product.get("grams") or 0)
    has_changes = draft_weight is not None and round(float(draft_weight), 2) != round(current_weight, 2)
    preview = _build_product_preview_for_weight(product, draft_weight if has_changes else None)
    calories, protein, fat, carbs = _extract_product_macros(preview)
    lines = [
        "<b>✏️ Изменение веса продукта</b>",
        "",
        f"<b>Продукт:</b> {name}",
        "",
        _format_product_macro_summary(calories, protein, fat, carbs),
        "",
        f"<b>Текущий вес:</b> {current_weight:.0f} г",
    ]
    if has_changes:
        lines.append(f"<b>Новый вес:</b> {float(draft_weight):.0f} г")

    lines.extend(["", "<b>Выбери действие или введи вручную:</b>"])
    return "\n".join(lines)


def _build_kbju_editor_keyboard(product_idx: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="🔥 Калории", callback_data=f"meal_kfield:{product_idx}:calories"),
                InlineKeyboardButton(text="🥩 Белки", callback_data=f"meal_kfield:{product_idx}:protein"),
            ],
            [
                InlineKeyboardButton(text="🥑 Жиры", callback_data=f"meal_kfield:{product_idx}:fat"),
                InlineKeyboardButton(text=f"{CARBS_EMOJI} Углеводы", callback_data=f"meal_kfield:{product_idx}:carbs"),
            ],
            [InlineKeyboardButton(text="↩️ Назад", callback_data=f"meal_kback:{product_idx}")],
        ]
    )


def _render_kbju_editor_text(product: dict, draft: Optional[dict] = None) -> str:
    name = product.get("name") or "продукт"
    grams = float(product.get("grams") or 0)
    calories, protein, fat, carbs = _extract_product_macros(product)
    if draft:
        calories = float(draft.get("calories", calories))
        protein = float(draft.get("protein", protein))
        fat = float(draft.get("fat", fat))
        carbs = float(draft.get("carbs", carbs))
    lines = [
        "🧮 <b>Ручная правка КБЖУ</b>",
        "",
        f"<b>Продукт:</b> {html.escape(str(name))}",
        "",
        f"<b>Текущий вес:</b> {grams:.0f} г",
        "",
        f"🔥 <b>Калории:</b> {calories:.0f} ккал",
        f"🥩 <b>Белки:</b> {protein:.1f} г",
        f"🥑 <b>Жиры:</b> {fat:.1f} г",
        f"{CARBS_EMOJI} <b>Углеводы:</b> {carbs:.1f} г",
    ]
    if bool(product.get("is_manually_corrected")) or draft:
        lines.append("✏️ КБЖУ скорректированы вручную")
    return "\n".join(lines)


def _build_kbju_field_editor_keyboard(product_idx: int, field: str) -> InlineKeyboardMarkup:
    step_map = {
        "calories": (1, 5, 10, 25, 50, 100),
        "protein": (0.5, 1, 5, 10, 25, 50),
        "fat": (0.5, 1, 5, 10, 25, 50),
        "carbs": (0.5, 1, 5, 10, 25, 50),
    }
    small, medium, large, xmedium, xlarge, xxlarge = step_map[field]
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text=_format_button_delta(-xxlarge), callback_data=f"meal_kdelta:{product_idx}:{field}:{_format_callback_delta(-xxlarge)}"),
                InlineKeyboardButton(text=_format_button_delta(-xlarge), callback_data=f"meal_kdelta:{product_idx}:{field}:{_format_callback_delta(-xlarge)}"),
                InlineKeyboardButton(text=_format_button_delta(xlarge), callback_data=f"meal_kdelta:{product_idx}:{field}:{_format_callback_delta(xlarge)}"),
                InlineKeyboardButton(text=_format_button_delta(xxlarge), callback_data=f"meal_kdelta:{product_idx}:{field}:{_format_callback_delta(xxlarge)}"),
            ],
            [
                InlineKeyboardButton(text=_format_button_delta(-xmedium), callback_data=f"meal_kdelta:{product_idx}:{field}:{_format_callback_delta(-xmedium)}"),
                InlineKeyboardButton(text=_format_button_delta(-large), callback_data=f"meal_kdelta:{product_idx}:{field}:{_format_callback_delta(-large)}"),
                InlineKeyboardButton(text=_format_button_delta(large), callback_data=f"meal_kdelta:{product_idx}:{field}:{_format_callback_delta(large)}"),
                InlineKeyboardButton(text=_format_button_delta(xmedium), callback_data=f"meal_kdelta:{product_idx}:{field}:{_format_callback_delta(xmedium)}"),
            ],
            [
                InlineKeyboardButton(text=_format_button_delta(-medium), callback_data=f"meal_kdelta:{product_idx}:{field}:{_format_callback_delta(-medium)}"),
                InlineKeyboardButton(text=_format_button_delta(-small), callback_data=f"meal_kdelta:{product_idx}:{field}:{_format_callback_delta(-small)}"),
                InlineKeyboardButton(text=_format_button_delta(small), callback_data=f"meal_kdelta:{product_idx}:{field}:{_format_callback_delta(small)}"),
                InlineKeyboardButton(text=_format_button_delta(medium), callback_data=f"meal_kdelta:{product_idx}:{field}:{_format_callback_delta(medium)}"),
            ],
            [InlineKeyboardButton(text="⌨️ Ввести вручную", callback_data=f"meal_kmanual:{product_idx}:{field}")],
            [InlineKeyboardButton(text="✅ Сохранить", callback_data=f"meal_kfsave:{product_idx}:{field}")],
            [InlineKeyboardButton(text="↩️ Назад", callback_data=f"meal_kfback:{product_idx}")],
        ]
    )


def _render_kbju_field_editor_text(product: dict, field: str, current_value: float) -> str:
    name = product.get("name") or "продукт"
    grams = float(product.get("grams") or 0)
    field_meta = {
        "calories": ("🔥", "калорий", "ккал", 0),
        "protein": ("🥩", "белков", "г", 1),
        "fat": ("🥑", "жиров", "г", 1),
        "carbs": (CARBS_EMOJI, "углеводов", "г", 1),
    }
    emoji, title, unit, precision = field_meta[field]
    formatted_value = f"{current_value:.0f}" if precision == 0 else f"{current_value:.1f}"
    return "\n".join(
        [
            f"{emoji} <b>Изменение {title}</b>",
            "",
            f"<b>Продукт:</b> {html.escape(str(name))}",
            "",
            f"<b>Текущий вес:</b> {grams:.0f} г",
            "",
            f"<b>Текущее значение:</b> {formatted_value} {unit}",
            "",
            "<b>Выбери действие:</b>",
        ]
    )


@router.callback_query(lambda c: c.data.startswith("meal_cal_add:"))
async def add_meal_from_calendar(callback: CallbackQuery, state: FSMContext):
    """Добавляет приём пищи из календаря."""
    await callback.answer()
    parts = callback.data.split(":")
    target_date = date.fromisoformat(parts[1])
    await start_kbju_add_flow(callback.message, target_date, state)


@router.message(F.text == "➕ Внести ещё приём")
async def kbju_add_more_meal(message: Message, state: FSMContext):
    """Добавляет ещё один приём пищи."""
    await start_kbju_add_flow(message, date.today(), state)


@router.message(F.text == "✏️ Редактировать")
async def edit_last_meal(message: Message, state: FSMContext):
    """Редактирует последний добавленный приём пищи."""
    user_id = str(message.from_user.id)
    
    # Получаем ID последнего приёма
    if not hasattr(message.bot, "last_meal_ids"):
        message.bot.last_meal_ids = {}
    
    last_meal_id = message.bot.last_meal_ids.get(user_id)
    if not last_meal_id:
        await message.answer(
            "❌ Не найден последний приём пищи для редактирования.\n"
            "Попробуй добавить приём пищи, а затем отредактировать его."
        )
        return
    
    # Получаем приём пищи
    meal = MealRepository.get_meal_by_id(last_meal_id, user_id)
    if not meal:
        await message.answer("❌ Не нашёл запись для изменения.")
        return
    
    products = _extract_products_for_edit(meal)
    
    if not products:
        await message.answer(
            "❌ Не удалось извлечь список продуктов из этой записи.\n"
            "Попробуй удалить и создать запись заново."
        )
        return
    
    meal_type = normalize_meal_type(getattr(meal, "meal_type", None), fallback=MealType.SNACK.value)
    totals = {
        "calories": float(getattr(meal, "calories", 0) or 0),
        "protein": float(getattr(meal, "protein", 0) or 0),
        "fat": float(getattr(meal, "fat", 0) or 0),
        "carbs": float(getattr(meal, "carbs", 0) or 0),
    }

    # Сохраняем данные в FSM для редактирования продукта
    await state.set_state(MealEntryStates.editing_meal_weight)
    await state.update_data(
        meal_id=last_meal_id,
        target_date=meal.date.isoformat(),
        saved_products=products,
        weight_drafts={},
        kbju_drafts={},
        editing_product_idx=None,
        editing_meal_type=meal_type,
    )

    await _show_meal_edit_products_list(
        message,
        products,
        meal_type,
        totals=totals,
        remove_reply_keyboard=True,
    )


@router.callback_query(lambda c: c.data.startswith("meal_edit:"))
async def start_meal_edit(callback: CallbackQuery, state: FSMContext):
    """Начинает редактирование приёма пищи."""
    await callback.answer()
    parts = callback.data.split(":")
    meal_id = int(parts[1])
    target_date = date.fromisoformat(parts[2]) if len(parts) > 2 else date.today()
    user_id = str(callback.from_user.id)

    await _start_meal_edit_flow(callback.message, state, user_id, meal_id, target_date)


async def _start_meal_edit_flow(
    message: Message,
    state: FSMContext,
    user_id: str,
    meal_id: int,
    target_date: date,
    *,
    return_to_meal_entry: bool = False,
    return_meal_type: str | None = None,
    meal_type: str | None = None,
) -> None:
    """Общий сценарий запуска редактирования конкретной записи приёма пищи."""
    meal = MealRepository.get_meal_by_id(meal_id, user_id)
    if not meal:
        await message.answer("❌ Не нашёл запись для изменения.")
        return

    products = _extract_products_for_edit(meal)
    if not products:
        await message.answer(
            "❌ Не удалось извлечь список продуктов из этой записи.\n"
            "Попробуй удалить и создать запись заново."
        )
        return

    editing_meal_type = normalize_meal_type(
        meal_type or getattr(meal, "meal_type", None),
        fallback=MealType.SNACK.value,
    )
    totals = {
        "calories": float(getattr(meal, "calories", 0) or 0),
        "protein": float(getattr(meal, "protein", 0) or 0),
        "fat": float(getattr(meal, "fat", 0) or 0),
        "carbs": float(getattr(meal, "carbs", 0) or 0),
    }
    
    # Сохраняем данные в FSM и открываем меню действий продукта
    await state.update_data(
        meal_id=meal_id,
        target_date=target_date.isoformat(),
        saved_products=products,
        weight_drafts={},
        kbju_drafts={},
        editing_product_idx=None,
        editing_meal_type=editing_meal_type,
        return_to_meal_entry=return_to_meal_entry,
        return_meal_type=return_meal_type,
    )
    await state.set_state(MealEntryStates.editing_meal_weight)

    await _show_meal_edit_products_list(
        message,
        products,
        editing_meal_type,
        totals=totals,
        remove_reply_keyboard=True,
    )


def _extract_products_for_edit(meal) -> list[dict]:
    """Извлекает список продуктов из meal для режима редактирования веса."""
    products: list[dict] = []
    if meal.products_json:
        try:
            parsed = json.loads(meal.products_json)
            if isinstance(parsed, list):
                products = [item for item in parsed if isinstance(item, dict)]
        except Exception:
            products = []

    if not products and meal.api_details:
        lines = meal.api_details.split("\n")
        for line in lines:
            if not line.strip().startswith("•"):
                continue
            match = re.match(r"•\s*(.+?)\s*\((\d+(?:\.\d+)?)\s*г\)", line)
            if not match:
                continue
            name = match.group(1).strip()
            grams = float(match.group(2))
            kbju_match = re.search(
                r"(\d+(?:\.\d+)?)\s*ккал.*?Б\s*(\d+(?:\.\d+)?).*?Ж\s*(\d+(?:\.\d+)?).*?У\s*(\d+(?:\.\d+)?)",
                line,
            )
            if not kbju_match or grams <= 0:
                continue
            cal = float(kbju_match.group(1))
            prot = float(kbju_match.group(2))
            fat = float(kbju_match.group(3))
            carbs = float(kbju_match.group(4))
            products.append(
                {
                    "name": name,
                    "grams": grams,
                    "calories": cal,
                    "protein_g": prot,
                    "fat_total_g": fat,
                    "carbohydrates_total_g": carbs,
                    "calories_per_100g": (cal / grams) * 100,
                    "protein_per_100g": (prot / grams) * 100,
                    "fat_per_100g": (fat / grams) * 100,
                    "carbs_per_100g": (carbs / grams) * 100,
                }
            )
    return products


def _strip_source_meta(product: dict) -> dict:
    return {k: v for k, v in product.items() if not str(k).startswith("_source_")}


@router.callback_query(lambda c: c.data.startswith("add_meal:"))
async def add_meal_from_diary_block(callback: CallbackQuery, state: FSMContext):
    """Добавляет приём в выбранный блок meal_type/дата из дневника."""
    await callback.answer()
    parts = callback.data.split(":")
    if len(parts) < 3:
        await callback.message.answer("❌ Не удалось открыть добавление: некорректные данные.")
        return
    meal_type = normalize_meal_type(parts[1], fallback=MealType.SNACK.value)
    target_date = date.fromisoformat(parts[2]) if len(parts) > 2 else date.today()
    await state.update_data(meal_type=meal_type, entry_date=target_date.isoformat(), pending_add_method=None)
    await callback.message.answer(
        f"Добавляем в приём пищи: {display_meal_type(meal_type)} ({target_date.strftime('%d.%m.%Y')})"
    )
    await _show_input_methods(callback.message, state, user_id=str(callback.from_user.id))


@router.callback_query(lambda c: c.data.startswith("edit_meal:"))
async def edit_meal_from_diary_block(callback: CallbackQuery, state: FSMContext):
    """Редактирует последний приём выбранного meal_type за дату."""
    await callback.answer()
    parts = callback.data.split(":")
    if len(parts) < 3:
        await callback.message.answer("❌ Не удалось открыть редактирование: некорректные данные.")
        return
    meal_type = normalize_meal_type(parts[1], fallback=MealType.SNACK.value)
    target_date = date.fromisoformat(parts[2]) if len(parts) > 2 else date.today()
    user_id = str(callback.from_user.id)
    current_data = await state.get_data()
    return_to_meal_entry = (
        current_data.get("meal_type") == meal_type
        and current_data.get("entry_date") == target_date.isoformat()
    )
    meals_for_type = MealRepository.get_meals_for_type_for_date(user_id, target_date, meal_type)
    if not meals_for_type:
        await callback.message.answer("❌ В этом приёме пищи пока нечего редактировать.")
        return
    if len(meals_for_type) == 1:
        await _start_meal_edit_flow(
            callback.message,
            state,
            user_id,
            meals_for_type[-1].id,
            target_date,
            return_to_meal_entry=return_to_meal_entry,
            return_meal_type=meal_type,
            meal_type=meal_type,
        )
        return

    merged_products: list[dict] = []
    for meal in meals_for_type:
        meal_products = _extract_products_for_edit(meal)
        for product in meal_products:
            enriched = dict(product)
            enriched["_source_meal_id"] = meal.id
            merged_products.append(enriched)

    if not merged_products:
        await callback.message.answer("❌ Не удалось извлечь продукты для редактирования.")
        return

    await state.update_data(
        meal_id=None,
        target_date=target_date.isoformat(),
        saved_products=merged_products,
        weight_drafts={},
        kbju_drafts={},
        editing_product_idx=None,
        grouped_meal_ids=[m.id for m in meals_for_type],
        grouped_meal_type=meal_type,
        editing_meal_type=meal_type,
        return_to_meal_entry=return_to_meal_entry,
        return_meal_type=meal_type,
    )
    await state.set_state(MealEntryStates.editing_meal_weight)

    totals = {
        "calories": sum(float(getattr(meal, "calories", 0) or 0) for meal in meals_for_type),
        "protein": sum(float(getattr(meal, "protein", 0) or 0) for meal in meals_for_type),
        "fat": sum(float(getattr(meal, "fat", 0) or 0) for meal in meals_for_type),
        "carbs": sum(float(getattr(meal, "carbs", 0) or 0) for meal in meals_for_type),
    }
    await _show_meal_edit_products_list(
        callback.message,
        merged_products,
        meal_type,
        totals=totals,
        remove_reply_keyboard=True,
    )


@router.callback_query(lambda c: c.data.startswith("clear_meal:"))
async def clear_meal_from_diary_block(callback: CallbackQuery):
    """Запрашивает подтверждение очистки выбранного приёма пищи (meal_type) за дату."""
    await callback.answer()
    parts = callback.data.split(":")
    if len(parts) < 3:
        await callback.message.answer("❌ Не удалось очистить приём пищи: некорректные данные.")
        return

    meal_type = normalize_meal_type(parts[1], fallback=MealType.SNACK.value)
    target_date = date.fromisoformat(parts[2]) if len(parts) > 2 else date.today()
    meal_title = display_meal_type(meal_type).lower()
    target_date_iso = target_date.isoformat()

    confirm_keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Да, удалить",
                    callback_data=f"clear_meal_confirm:{meal_type}:{target_date_iso}",
                ),
                InlineKeyboardButton(
                    text="❌ Отмена",
                    callback_data=f"clear_meal_cancel:{meal_type}:{target_date_iso}",
                ),
            ]
        ]
    )
    await callback.message.answer(
        f"❓ Вы точно хотите удалить все данные за {meal_title}?",
        reply_markup=confirm_keyboard,
    )


@router.callback_query(lambda c: c.data.startswith("clear_meal_confirm:"))
async def clear_meal_from_diary_block_confirmed(callback: CallbackQuery):
    """Очищает выбранный приём пищи (meal_type) за дату после подтверждения."""
    await callback.answer()
    parts = callback.data.split(":")
    if len(parts) < 3:
        await callback.message.answer("❌ Не удалось очистить приём пищи: некорректные данные.")
        return

    meal_type = normalize_meal_type(parts[1], fallback=MealType.SNACK.value)
    target_date = date.fromisoformat(parts[2]) if len(parts) > 2 else date.today()
    user_id = str(callback.from_user.id)
    deleted_count = MealRepository.delete_meals_by_type_for_date(user_id, target_date, meal_type)
    if deleted_count <= 0:
        await callback.message.answer("ℹ️ В этом приёме пищи уже нет записей.")
        return

    await callback.message.answer(f"✅ {display_meal_type(meal_type)} очищен: удалено записей — {deleted_count}.")
    await _render_day_meals_messages(
        callback.message,
        user_id,
        target_date,
        include_back=True,
        changed_meal_type=meal_type,
    )


@router.callback_query(lambda c: c.data.startswith("clear_meal_cancel:"))
async def clear_meal_from_diary_block_cancelled(callback: CallbackQuery):
    """Отменяет очистку выбранного приёма пищи и возвращает отчёт за день."""
    await callback.answer("Очистка отменена")
    parts = callback.data.split(":")
    if len(parts) < 3:
        return

    target_date = date.fromisoformat(parts[2]) if len(parts) > 2 else date.today()
    user_id = str(callback.from_user.id)
    await callback.message.answer("👌 Очистку отменили.")
    await _render_day_meals_messages(
        callback.message,
        user_id,
        target_date,
        include_back=True,
    )


@router.message(MealEntryStates.choosing_edit_type)
async def handle_edit_type_choice(message: Message, state: FSMContext):
    """Обрабатывает выбор типа редактирования."""
    user_id = str(message.from_user.id)
    text = message.text.strip()
    
    # Проверяем, не является ли это кнопкой меню
    menu_buttons = ["⬅️ Назад"]
    if text in menu_buttons or text in MAIN_MENU_BUTTON_ALIASES:
        await state.clear()
        if text == "⬅️ Назад":
            from handlers.common import go_back
            await go_back(message, state)
        elif text in MAIN_MENU_BUTTON_ALIASES:
            from handlers.common import go_main_menu
            await go_main_menu(message, state)
        return
    
    data = await state.get_data()
    saved_products = data.get("saved_products", [])
    
    if not saved_products:
        await message.answer("❌ Не удалось найти сохраненные данные продуктов.")
        await state.clear()
        return
    
    if text in {"⚖️ Изменить вес", "🧮 Изменить КБЖУ"}:
        await state.set_state(MealEntryStates.editing_meal_weight)
        await state.update_data(weight_drafts={}, kbju_drafts={}, editing_product_idx=None)

        await _show_meal_edit_products_list(
            message,
            saved_products,
            normalize_meal_type(
                data.get("editing_meal_type") or data.get("grouped_meal_type") or data.get("meal_type"),
                fallback=MealType.SNACK.value,
            ),
            remove_reply_keyboard=True,
        )
    else:
        await message.answer("Пожалуйста, выбери вариант с кнопки.")


@router.message(MealEntryStates.editing_meal_weight)
async def handle_meal_weight_edit(message: Message, state: FSMContext):
    """Обрабатывает сообщения в режиме изменения веса."""
    text = (message.text or "").strip()
    if text == "❌ Отмена":
        data = await state.get_data()
        product_idx = data.get("editing_product_idx")
        saved_products = data.get("saved_products", [])
        drafts = data.get("weight_drafts", {})
        if product_idx is not None and 0 <= product_idx < len(saved_products):
            drafts.pop(str(product_idx), None)
            await state.set_state(MealEntryStates.editing_meal_weight)
            await state.update_data(weight_drafts=drafts, editing_product_idx=product_idx)
            await message.answer(
                _render_product_actions_text(saved_products[product_idx]),
                reply_markup=_build_product_actions_keyboard(product_idx),
            )
            return

        await state.set_state(MealEntryStates.choosing_edit_type)
        push_menu_stack(message.bot, kbju_edit_type_menu)
        await message.answer(
            "✏️ Редактирование приёма пищи\n\nВыбери, что хочешь изменить:",
            reply_markup=kbju_edit_type_menu,
        )
        return

    if text == "⬅️ Назад":
        await state.set_state(MealEntryStates.choosing_edit_type)
        push_menu_stack(message.bot, kbju_edit_type_menu)
        await message.answer(
            "✏️ Редактирование приёма пищи\n\nВыбери, что хочешь изменить:",
            reply_markup=kbju_edit_type_menu,
        )
        return

    await message.answer("Используй кнопки ниже для редактирования продукта 👇")


@router.callback_query(lambda c: c.data == "meal_wback_edit")
async def meal_weight_back_to_edit_type(callback: CallbackQuery, state: FSMContext):
    """Возврат к выбору типа редактирования."""
    await callback.answer()
    await state.set_state(MealEntryStates.choosing_edit_type)
    await callback.message.answer(
        "✏️ Редактирование приёма пищи\n\nВыбери, что хочешь изменить:",
        reply_markup=kbju_edit_type_menu,
    )



@router.callback_query(lambda c: c.data == "meal_wdone")
async def meal_weight_done(callback: CallbackQuery, state: FSMContext):
    """Завершает редактирование веса и возвращает пользователя в нужный контекст."""
    await callback.answer("Изменения сохранены")
    data = await state.get_data()
    target_date_raw = data.get("target_date")
    try:
        target_date = date.fromisoformat(target_date_raw) if isinstance(target_date_raw, str) else date.today()
    except ValueError:
        target_date = date.today()

    user_id = str(callback.from_user.id)
    return_to_meal_entry = bool(data.get("return_to_meal_entry"))
    return_meal_type = normalize_meal_type(
        data.get("return_meal_type") or data.get("grouped_meal_type") or data.get("meal_type"),
        fallback=MealType.SNACK.value,
    )

    if data.get("ai_text_draft_mode"):
        pending = data.get("ai_pending_meal") or {}
        await state.update_data(
            ai_pending_meal={**pending, "items": data.get("saved_products") or []},
            ai_text_draft_mode=False,
            meal_id=None,
            weight_drafts={},
            kbju_drafts={},
        )
        await _send_ai_meal_preview(callback.message, state)
        return

    if data.get("photo_draft_edit_mode"):
        items = data.get("saved_products") or []
        dish_name = normalize_dish_display_name(data.get("photo_analysis_dish_name"), items)
        save_token = data.get("photo_save_token")
        await state.set_state(MealEntryStates.confirming_photo_analysis)
        await state.update_data(
            photo_analysis_items=items,
            photo_analysis_dish_name=dish_name,
            photo_draft_edit_mode=False,
            saved_products=None,
            meal_id=None,
            weight_drafts={},
            kbju_drafts={},
        )
        await _edit_or_send_photo_analysis_message(
            callback.message,
            _format_photo_analysis_confirmation_text(items, dish_name=dish_name),
            reply_markup=_build_photo_analysis_confirm_menu(items, save_token),
            parse_mode="HTML",
        )
        return

    await state.clear()
    await callback.message.answer("✅ Изменения выполнены")

    if return_to_meal_entry:
        await _keep_meal_entry_open_after_save(
            callback.message,
            state,
            user_id=user_id,
            entry_date=target_date,
            meal_type=return_meal_type,
        )
        return

    await _return_to_food_diary(callback.message, user_id, target_date)

@router.callback_query(lambda c: c.data == "meal_wcancel")
async def meal_weight_cancel(callback: CallbackQuery, state: FSMContext):
    """Отмена редактирования веса."""
    await callback.answer("Редактирование отменено")
    await state.clear()
    await callback.message.answer("Ок, отменил изменение веса 👌", reply_markup=kbju_after_meal_menu)


@router.callback_query(lambda c: c.data.startswith("meal_wback_product:"))
async def meal_weight_back_to_product_actions(callback: CallbackQuery, state: FSMContext):
    """Возврат с экрана изменения веса к действиям выбранного продукта без сохранения черновика."""
    await callback.answer()
    product_idx = int(callback.data.split(":")[1])
    data = await state.get_data()
    saved_products = data.get("saved_products", [])
    drafts = data.get("weight_drafts", {})
    if product_idx < 0 or product_idx >= len(saved_products):
        await callback.answer("Не нашёл продукт", show_alert=True)
        return

    drafts.pop(str(product_idx), None)
    await state.set_state(MealEntryStates.editing_meal_weight)
    await state.update_data(weight_drafts=drafts, editing_product_idx=product_idx)
    await callback.message.edit_text(
        _render_product_actions_text(saved_products[product_idx]),
        reply_markup=_build_product_actions_keyboard(product_idx),
    )


@router.callback_query(lambda c: c.data == "meal_wback_list")
async def meal_weight_back_to_products(callback: CallbackQuery, state: FSMContext):
    """Возврат к списку продуктов."""
    await callback.answer()
    data = await state.get_data()
    saved_products = data.get("saved_products", [])
    await state.set_state(MealEntryStates.editing_meal_weight)
    if data.get("ai_text_draft_mode"):
        await callback.message.edit_text(
            "<b>✏️ Выбери продукт для редактирования:</b>",
            reply_markup=_build_weight_products_keyboard(saved_products),
        )
        return
    if data.get("photo_draft_edit_mode"):
        await callback.message.edit_text(
            "<b>✏️ Выбери ингредиент для редактирования:</b>",
            reply_markup=_build_weight_products_keyboard(saved_products),
        )
        return

    meal_type = normalize_meal_type(
        data.get("editing_meal_type") or data.get("grouped_meal_type") or data.get("meal_type"),
        fallback=MealType.SNACK.value,
    )
    await _show_meal_edit_products_list(
        callback.message,
        saved_products,
        meal_type,
        edit_existing=True,
    )


@router.callback_query(lambda c: c.data.startswith("meal_wsel:"))
async def meal_weight_select_product(callback: CallbackQuery, state: FSMContext):
    """Открывает меню действий для выбранного продукта."""
    await callback.answer()
    product_idx = int(callback.data.split(":")[1])
    data = await state.get_data()
    saved_products = data.get("saved_products", [])

    if product_idx < 0 or product_idx >= len(saved_products):
        await callback.answer("Не нашёл продукт", show_alert=True)
        return

    product = saved_products[product_idx]
    await state.set_state(MealEntryStates.editing_meal_weight)
    await state.update_data(editing_product_idx=product_idx)

    try:
        await callback.message.edit_text(
            _render_product_actions_text(product),
            reply_markup=_build_product_actions_keyboard(product_idx),
        )
    except TelegramBadRequest:
        await callback.message.answer(
            _render_product_actions_text(product),
            reply_markup=_build_product_actions_keyboard(product_idx),
        )


@router.callback_query(lambda c: c.data.startswith("meal_pact_name:"))
async def meal_product_name_input_start(callback: CallbackQuery, state: FSMContext):
    """Запрашивает новое название для выбранного продукта."""
    await callback.answer()
    product_idx = int(callback.data.split(":")[1])
    data = await state.get_data()
    saved_products = data.get("saved_products", [])
    if product_idx < 0 or product_idx >= len(saved_products):
        await callback.answer("Не нашёл продукт", show_alert=True)
        return

    product_name = saved_products[product_idx].get("name") or "продукт"
    await state.set_state(MealEntryStates.editing_meal_name_input)
    await state.update_data(editing_product_idx=product_idx)
    await callback.message.answer(
        f'Введи новое название для продукта "{html.escape(str(product_name))}":',
        reply_markup=_build_name_input_keyboard(product_idx),
    )


@router.callback_query(lambda c: c.data.startswith("meal_pact_name_back:"))
async def meal_product_name_input_back(callback: CallbackQuery, state: FSMContext):
    """Отменяет ввод нового названия и возвращает карточку продукта."""
    await callback.answer()
    product_idx = int(callback.data.split(":")[1])
    data = await state.get_data()
    saved_products = data.get("saved_products", [])
    if product_idx < 0 or product_idx >= len(saved_products):
        await callback.answer("Не нашёл продукт", show_alert=True)
        return

    await state.set_state(MealEntryStates.editing_meal_weight)
    await state.update_data(editing_product_idx=product_idx)
    try:
        await callback.message.edit_text(
            _render_product_actions_text(saved_products[product_idx]),
            reply_markup=_build_product_actions_keyboard(product_idx),
        )
    except TelegramBadRequest:
        await callback.message.answer(
            _render_product_actions_text(saved_products[product_idx]),
            reply_markup=_build_product_actions_keyboard(product_idx),
        )


@router.message(MealEntryStates.editing_meal_name_input)
async def meal_product_name_input_value(message: Message, state: FSMContext):
    """Сохраняет новое название продукта."""
    new_name = (message.text or "").strip()
    if new_name == "❌ Отмена":
        data = await state.get_data()
        product_idx = data.get("editing_product_idx")
        saved_products = data.get("saved_products", [])
        if product_idx is not None and 0 <= product_idx < len(saved_products):
            await state.set_state(MealEntryStates.editing_meal_weight)
            await state.update_data(editing_product_idx=product_idx)
            await message.answer(
                _render_product_actions_text(saved_products[product_idx]),
                reply_markup=_build_product_actions_keyboard(product_idx),
            )
            return
        await state.set_state(MealEntryStates.editing_meal_weight)
        await message.answer("Редактирование названия отменено.")
        return
    if not new_name:
        await message.answer("Пожалуйста, введи непустое название продукта.")
        return
    if len(new_name) > 80:
        await message.answer("Название слишком длинное. Введи до 80 символов.")
        return
    if await _reject_sensitive_food_name(message, new_name, context="meal_product_rename"):
        return

    data = await state.get_data()
    product_idx = data.get("editing_product_idx")
    meal_id = data.get("meal_id")
    saved_products = data.get("saved_products", [])
    if product_idx is None or product_idx < 0 or product_idx >= len(saved_products):
        await message.answer("❌ Не удалось найти продукт для редактирования.")
        await state.set_state(MealEntryStates.editing_meal_weight)
        return

    product = saved_products[product_idx]
    product["name"] = new_name
    if "name_ru" in product:
        product["name_ru"] = new_name

    if data.get("ai_text_draft_mode") or data.get("photo_draft_edit_mode"):
        pending = data.get("ai_pending_meal") or {}
        await state.set_state(MealEntryStates.editing_meal_weight)
        payload = dict(
            saved_products=saved_products,
            editing_product_idx=product_idx,
        )
        if data.get("ai_text_draft_mode"):
            payload["ai_pending_meal"] = {**pending, "items": saved_products}
        else:
            payload["photo_analysis_items"] = saved_products
        await state.update_data(**payload)
        await message.answer("✅ Название продукта обновлено")
        await message.answer(
            _render_product_actions_text(product),
            reply_markup=_build_product_actions_keyboard(product_idx),
        )
        return


    source_meal_id = int(product.get("_source_meal_id") or meal_id or 0)
    if not source_meal_id:
        await message.answer("❌ Не удалось определить запись для обновления.")
        await state.set_state(MealEntryStates.editing_meal_weight)
        return

    if not source_meal_id:
        await callback.answer("Не удалось определить запись для удаления", show_alert=True)
        return

    if not source_meal_id:
        await callback.answer("Не удалось определить запись для удаления", show_alert=True)
        return

    source_products = [
        _strip_source_meta(p)
        for p in saved_products
        if int(p.get("_source_meal_id") or source_meal_id) == source_meal_id
    ]
    totals, api_details = _build_meal_update_payload(source_products)
    user_id = str(message.from_user.id)
    meal = MealRepository.get_meal_by_id(source_meal_id, user_id)
    raw_query = meal.raw_query if meal and hasattr(meal, "raw_query") else None
    success = MealRepository.update_meal(
        meal_id=source_meal_id,
        user_id=user_id,
        description=raw_query,
        calories=totals["calories"],
        protein=totals["protein_g"],
        fat=totals["fat_total_g"],
        carbs=totals["carbohydrates_total_g"],
        products_json=json.dumps(source_products),
        api_details=api_details,
        is_manually_corrected=bool(
            any(bool(p.get("is_manually_corrected")) for p in source_products)
        ),
    )
    if not success:
        await message.answer("❌ Не удалось обновить название продукта.")
        await state.set_state(MealEntryStates.editing_meal_weight)
        return

    await state.set_state(MealEntryStates.editing_meal_weight)
    await state.update_data(saved_products=saved_products, editing_product_idx=product_idx)
    await message.answer(
        "✅ Название продукта обновлено",
    )
    await message.answer(
        _render_product_actions_text(product),
        reply_markup=_build_product_actions_keyboard(product_idx),
    )
    meal_date = getattr(meal, "date", None)
    if meal_date is not None:
        await return_to_active_daily_preflight(message, user_id, meal_date)


@router.callback_query(lambda c: c.data.startswith("meal_pact_weight:"))
async def meal_product_open_weight_editor(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    product_idx = int(callback.data.split(":")[1])
    data = await state.get_data()
    saved_products = data.get("saved_products", [])
    drafts = data.get("weight_drafts", {})
    if product_idx < 0 or product_idx >= len(saved_products):
        await callback.answer("Не нашёл продукт", show_alert=True)
        return
    product = saved_products[product_idx]
    drafts.pop(str(product_idx), None)
    draft_weight = None
    await state.set_state(MealEntryStates.editing_meal_weight)
    await state.update_data(
        editing_product_idx=product_idx,
        weight_drafts=drafts,
        weight_editor_message_id=callback.message.message_id,
    )
    await callback.message.edit_text(
        _render_weight_editor_text(product, draft_weight=draft_weight),
        reply_markup=_build_weight_editor_keyboard(product_idx, has_changes=False),
    )


@router.callback_query(lambda c: c.data.startswith("meal_pact_kbju:"))
async def meal_product_open_kbju_editor(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    product_idx = int(callback.data.split(":")[1])
    data = await state.get_data()
    saved_products = data.get("saved_products", [])
    kbju_drafts = data.get("kbju_drafts", {})
    if product_idx < 0 or product_idx >= len(saved_products):
        await callback.answer("Не нашёл продукт", show_alert=True)
        return
    product = saved_products[product_idx]
    draft = kbju_drafts.get(str(product_idx))
    await state.set_state(MealEntryStates.edit_kbju_menu)
    await state.update_data(editing_product_idx=product_idx)
    await callback.message.edit_text(
        _render_kbju_editor_text(product, draft=draft),
        reply_markup=_build_kbju_editor_keyboard(product_idx),
    )


@router.callback_query(lambda c: c.data.startswith("meal_wchg:"))
async def meal_weight_change_draft(callback: CallbackQuery, state: FSMContext):
    """Меняет временный вес продукта кнопками +/−."""
    _, raw_idx, raw_delta = callback.data.split(":")
    product_idx = int(raw_idx)
    delta = int(raw_delta)

    data = await state.get_data()
    saved_products = data.get("saved_products", [])
    drafts = data.get("weight_drafts", {})
    if product_idx < 0 or product_idx >= len(saved_products):
        await callback.answer("Не нашёл продукт", show_alert=True)
        return

    product = saved_products[product_idx]
    base_weight = float(drafts.get(str(product_idx), product.get("grams", 0)))
    new_weight = base_weight + delta
    if new_weight < 1:
        await callback.answer("Вес не может быть меньше 1 г")
        return

    drafts[str(product_idx)] = int(new_weight)
    await state.update_data(weight_drafts=drafts, editing_product_idx=product_idx)
    await callback.answer()

    await callback.message.edit_text(
        _render_weight_editor_text(product, draft_weight=new_weight),
        reply_markup=_build_weight_editor_keyboard(product_idx, has_changes=True),
    )


@router.callback_query(lambda c: c.data.startswith("meal_wmanual:"))
async def meal_weight_manual_input_start(callback: CallbackQuery, state: FSMContext):
    """Запрашивает ручной ввод веса для конкретного продукта."""
    await callback.answer()
    product_idx = int(callback.data.split(":")[1])
    data = await state.get_data()
    saved_products = data.get("saved_products", [])
    if product_idx < 0 or product_idx >= len(saved_products):
        await callback.answer("Не нашёл продукт", show_alert=True)
        return

    product_name = saved_products[product_idx].get("name") or "продукт"
    await state.set_state(MealEntryStates.editing_meal_weight_manual_input)
    await state.update_data(editing_product_idx=product_idx)
    await callback.message.answer(
        f'Введи новый вес для продукта "{product_name}" в граммах:',
        reply_markup=ReplyKeyboardRemove(),
    )


@router.message(MealEntryStates.editing_meal_weight_manual_input)
async def meal_weight_manual_input_value(message: Message, state: FSMContext):
    """Обрабатывает ручной ввод нового веса."""
    if (message.text or "").strip() == "❌ Отмена":
        data = await state.get_data()
        product_idx = data.get("editing_product_idx")
        saved_products = data.get("saved_products", [])
        drafts = data.get("weight_drafts", {})
        if product_idx is not None and 0 <= product_idx < len(saved_products):
            drafts.pop(str(product_idx), None)
            await state.set_state(MealEntryStates.editing_meal_weight)
            await state.update_data(weight_drafts=drafts, editing_product_idx=product_idx)
            await message.answer(
                _render_product_actions_text(saved_products[product_idx]),
                reply_markup=_build_product_actions_keyboard(product_idx),
            )
        else:
            await state.set_state(MealEntryStates.editing_meal_weight)
            await message.answer("Используй кнопки ниже для редактирования продукта 👇")
        return

    raw_value = (message.text or "").strip().replace(",", ".")
    if not raw_value.isdigit():
        await message.answer("Пожалуйста, введи вес числом в граммах, например: 180")
        return

    new_weight = int(raw_value)
    if new_weight < 1:
        await message.answer("Вес должен быть не меньше 1 г.")
        return

    data = await state.get_data()
    if data.get("my_product_weight_edit_mode"):
        source_meal_id = data.get("my_product_source_meal_id")
        if not source_meal_id:
            await message.answer("❌ Не удалось найти продукт из истории.")
            await state.clear()
            return
        user_id = str(message.from_user.id)
        source_meal = MealRepository.get_meal_by_id(int(source_meal_id), user_id)
        if not source_meal:
            await message.answer("❌ Не удалось найти продукт из истории.")
            await state.clear()
            return
        meal_type = normalize_meal_type(data.get("meal_type"), fallback=MealType.SNACK.value)
        product_index = _parse_my_product_index(data.get("my_product_source_product_idx"))
        my_product_item = _get_my_product_from_source_meal(
            source_meal, product_index, user_id=user_id
        )
        adjusted = _build_adjusted_my_product_item(my_product_item, new_weight)
        save_token = await _ensure_my_product_save_token(state, data)
        await state.set_state(MealEntryStates.choosing_meal_type)
        await state.update_data(
            my_product_custom_amount_g=new_weight,
            my_product_weight_edit_mode=False,
            my_product_weight_draft_g=None,
        )
        await message.answer(
            _render_my_product_confirm_text(meal_type, adjusted, amount_g=new_weight),
            reply_markup=_build_my_product_confirm_keyboard(
                int(source_meal_id),
                meal_type,
                int(data.get("my_products_page") or 1),
                product_index,
                save_token=save_token,
                item=adjusted,
            ),
            parse_mode="HTML",
        )
        return

    product_idx = data.get("editing_product_idx")
    saved_products = data.get("saved_products", [])
    drafts = data.get("weight_drafts", {})
    if product_idx is None or product_idx < 0 or product_idx >= len(saved_products):
        await message.answer("❌ Не удалось найти продукт для редактирования.")
        await state.set_state(MealEntryStates.editing_meal_weight)
        return

    drafts[str(product_idx)] = new_weight
    product = saved_products[product_idx]
    await state.set_state(MealEntryStates.editing_meal_weight)
    await state.update_data(weight_drafts=drafts)

    editor_message_id = data.get("weight_editor_message_id")
    if editor_message_id:
        try:
            await message.delete()
        except TelegramBadRequest:
            pass
        await message.bot.edit_message_text(
            chat_id=message.chat.id,
            message_id=editor_message_id,
            text=_render_weight_editor_text(product, draft_weight=new_weight),
            reply_markup=_build_weight_editor_keyboard(product_idx, has_changes=True),
        )
    else:
        await message.answer(
            _render_weight_editor_text(product, draft_weight=new_weight),
            reply_markup=_build_weight_editor_keyboard(product_idx, has_changes=True),
        )


@router.message(MealEntryStates.edit_kbju_menu)
async def handle_meal_kbju_edit(message: Message, state: FSMContext):
    if (message.text or "").strip() == "❌ Отмена":
        data = await state.get_data()
        product_idx = data.get("editing_product_idx")
        saved_products = data.get("saved_products", [])
        if product_idx is not None and 0 <= product_idx < len(saved_products):
            await state.set_state(MealEntryStates.editing_meal_weight)
            await state.update_data(editing_product_idx=product_idx)
            await message.answer(
                _render_product_actions_text(saved_products[product_idx]),
                reply_markup=_build_product_actions_keyboard(product_idx),
            )
            return
    await message.answer("Используй кнопки ниже, чтобы изменить КБЖУ 👇")


@router.callback_query(lambda c: c.data.startswith("meal_kfield:"))
async def meal_kbju_edit_single_field_start(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    _, raw_idx, field = callback.data.split(":")
    product_idx = int(raw_idx)
    if field not in {"calories", "protein", "fat", "carbs"}:
        await callback.answer("Неизвестное поле", show_alert=True)
        return
    data = await state.get_data()
    saved_products = data.get("saved_products", [])
    kbju_drafts = data.get("kbju_drafts", {})
    if product_idx < 0 or product_idx >= len(saved_products):
        await callback.answer("Не нашёл продукт", show_alert=True)
        return
    product = saved_products[product_idx]
    calories, protein, fat, carbs = _extract_product_macros(product)
    draft = kbju_drafts.get(str(product_idx), {})
    current_value = float(draft.get(field, {"calories": calories, "protein": protein, "fat": fat, "carbs": carbs}[field]))
    await state.set_state(MealEntryStates.edit_kbju_field)
    await state.update_data(
        editing_product_idx=product_idx,
        editing_macro_field=field,
        editing_macro_current_value=current_value,
        kbju_field_message_id=callback.message.message_id,
    )
    await callback.message.edit_text(
        _render_kbju_field_editor_text(product, field, current_value),
        reply_markup=_build_kbju_field_editor_keyboard(product_idx, field),
    )


@router.callback_query(lambda c: c.data.startswith("meal_kdelta:"))
async def meal_kbju_edit_single_field_delta(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    _, raw_idx, field, raw_delta = callback.data.split(":")
    product_idx = int(raw_idx)
    delta = float(raw_delta)
    data = await state.get_data()
    saved_products = data.get("saved_products", [])
    if product_idx < 0 or product_idx >= len(saved_products):
        await callback.answer("Не нашёл продукт", show_alert=True)
        return
    current_value = float(data.get("editing_macro_current_value", 0))
    new_value = max(0.0, current_value + delta)
    await state.update_data(editing_macro_current_value=new_value)
    await callback.message.edit_text(
        _render_kbju_field_editor_text(saved_products[product_idx], field, new_value),
        reply_markup=_build_kbju_field_editor_keyboard(product_idx, field),
    )


@router.callback_query(lambda c: c.data.startswith("meal_kmanual:"))
async def meal_kbju_edit_single_field_manual_start(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    _, raw_idx, field = callback.data.split(":")
    product_idx = int(raw_idx)
    await state.set_state(MealEntryStates.edit_kbju_manual_input)
    await state.update_data(
        editing_product_idx=product_idx,
        editing_macro_field=field,
        kbju_field_message_id=callback.message.message_id,
    )
    await callback.message.edit_text(
        "⌨️ Введи новое значение числом.\n"
        "Пример: 12.5",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="↩️ Назад", callback_data=f"meal_kfback:{product_idx}")]]
        ),
    )


@router.message(MealEntryStates.edit_kbju_field)
async def handle_meal_kbju_field_edit(message: Message, state: FSMContext):
    if (message.text or "").strip() != "❌ Отмена":
        await message.answer("Используй кнопки ниже, чтобы изменить значение КБЖУ 👇")
        return

    data = await state.get_data()
    product_idx = data.get("editing_product_idx")
    saved_products = data.get("saved_products", [])
    kbju_drafts = data.get("kbju_drafts", {})
    if product_idx is not None and 0 <= product_idx < len(saved_products):
        await state.set_state(MealEntryStates.edit_kbju_menu)
        await state.update_data(editing_product_idx=product_idx)
        await message.answer(
            _render_kbju_editor_text(saved_products[product_idx], draft=kbju_drafts.get(str(product_idx))),
            reply_markup=_build_kbju_editor_keyboard(product_idx),
        )
        return

    await state.set_state(MealEntryStates.editing_meal_weight)
    await message.answer("Редактирование значения отменено.")


@router.message(MealEntryStates.edit_kbju_manual_input)
async def meal_kbju_edit_single_field_value(message: Message, state: FSMContext):
    if (message.text or "").strip() == "❌ Отмена":
        data = await state.get_data()
        product_idx = data.get("editing_product_idx")
        saved_products = data.get("saved_products", [])
        kbju_drafts = data.get("kbju_drafts", {})
        if product_idx is not None and 0 <= product_idx < len(saved_products):
            await state.set_state(MealEntryStates.edit_kbju_menu)
            await state.update_data(editing_product_idx=product_idx)
            await message.answer(
                _render_kbju_editor_text(saved_products[product_idx], draft=kbju_drafts.get(str(product_idx))),
                reply_markup=_build_kbju_editor_keyboard(product_idx),
            )
            return
    raw_value = (message.text or "").strip().replace(",", ".")
    try:
        value = float(raw_value)
    except ValueError:
        await message.answer("Пожалуйста, введи число. Пример: 12.5")
        return
    if value < 0:
        await message.answer("Значение не может быть отрицательным.")
        return

    data = await state.get_data()
    product_idx = data.get("editing_product_idx")
    field = data.get("editing_macro_field")
    saved_products = data.get("saved_products", [])
    field_message_id = data.get("kbju_field_message_id")
    if product_idx is None or product_idx < 0 or product_idx >= len(saved_products) or not field:
        await message.answer("❌ Не удалось найти продукт для редактирования.")
        return

    await state.set_state(MealEntryStates.edit_kbju_field)
    await state.update_data(editing_macro_current_value=value)
    await message.delete()
    await message.bot.edit_message_text(
        chat_id=message.chat.id,
        message_id=field_message_id,
        text=_render_kbju_field_editor_text(saved_products[product_idx], field, value),
        reply_markup=_build_kbju_field_editor_keyboard(product_idx, field),
    )


@router.callback_query(lambda c: c.data.startswith("meal_kall:"))
async def meal_kbju_edit_all_start(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    product_idx = int(callback.data.split(":")[1])
    await state.set_state(MealEntryStates.editing_meal_kbju_all_input)
    await state.update_data(editing_product_idx=product_idx)
    await callback.message.answer(
        "Введи 4 значения в формате:\n"
        "ккал белки жиры углеводы\n"
        "Пример: 120 14 5 3",
        reply_markup=ReplyKeyboardRemove(),
    )


@router.message(MealEntryStates.editing_meal_kbju_all_input)
async def meal_kbju_edit_all_value(message: Message, state: FSMContext):
    parsed = _parse_kbju_bulk_input(message.text or "")
    if not parsed:
        await message.answer(
            "Не понял формат.\n"
            "Нужно: ккал белки жиры углеводы\n"
            "Пример: 120 14 5 3"
        )
        return

    calories, protein, fat, carbs = parsed
    data = await state.get_data()
    product_idx = data.get("editing_product_idx")
    saved_products = data.get("saved_products", [])
    kbju_drafts = data.get("kbju_drafts", {})
    if product_idx is None or product_idx < 0 or product_idx >= len(saved_products):
        await message.answer("❌ Не удалось найти продукт для редактирования.")
        return
    product = saved_products[product_idx]
    kbju_drafts[str(product_idx)] = {
        "calories": calories,
        "protein": protein,
        "fat": fat,
        "carbs": carbs,
    }
    await state.set_state(MealEntryStates.edit_kbju_menu)
    await state.update_data(kbju_drafts=kbju_drafts)
    await message.answer(
        _render_kbju_editor_text(product, draft=kbju_drafts[str(product_idx)]),
        reply_markup=_build_kbju_editor_keyboard(product_idx),
    )


@router.callback_query(lambda c: c.data.startswith("meal_kfback:"))
async def meal_kbju_field_back_to_menu(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    product_idx = int(callback.data.split(":")[1])
    data = await state.get_data()
    saved_products = data.get("saved_products", [])
    kbju_drafts = data.get("kbju_drafts", {})
    if product_idx < 0 or product_idx >= len(saved_products):
        await callback.answer("Не нашёл продукт", show_alert=True)
        return
    await state.set_state(MealEntryStates.edit_kbju_menu)
    await callback.message.edit_text(
        _render_kbju_editor_text(saved_products[product_idx], draft=kbju_drafts.get(str(product_idx))),
        reply_markup=_build_kbju_editor_keyboard(product_idx),
    )


@router.callback_query(lambda c: c.data.startswith("meal_kback:"))
async def meal_kbju_back_to_product_actions(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    product_idx = int(callback.data.split(":")[1])
    data = await state.get_data()
    saved_products = data.get("saved_products", [])
    if product_idx < 0 or product_idx >= len(saved_products):
        await callback.answer("Не нашёл продукт", show_alert=True)
        return
    await state.set_state(MealEntryStates.editing_meal_weight)
    await callback.message.edit_text(
        _render_product_actions_text(saved_products[product_idx]),
        reply_markup=_build_product_actions_keyboard(product_idx),
    )


async def _save_kbju_changes_for_product(
    callback: CallbackQuery,
    state: FSMContext,
    product_idx: int,
) -> bool:
    user_id = str(callback.from_user.id)
    data = await state.get_data()
    meal_id = data.get("meal_id")
    saved_products = data.get("saved_products", [])
    kbju_drafts = data.get("kbju_drafts", {})

    if product_idx < 0 or product_idx >= len(saved_products):
        await callback.answer("Не удалось сохранить", show_alert=True)
        return False
    draft = kbju_drafts.get(str(product_idx))
    if not draft:
        await callback.answer("Сначала измени хотя бы одно значение", show_alert=True)
        return False

    product = saved_products[product_idx]
    if not _apply_product_manual_macros(
        product,
        calories=draft.get("calories"),
        protein=draft.get("protein"),
        fat=draft.get("fat"),
        carbs=draft.get("carbs"),
    ):
        await callback.answer("Не удалось обновить КБЖУ", show_alert=True)
        return False

    if data.get("ai_text_draft_mode") or data.get("photo_draft_edit_mode"):
        kbju_drafts.pop(str(product_idx), None)
        pending = data.get("ai_pending_meal") or {}
        await state.set_state(MealEntryStates.edit_kbju_menu)
        payload = dict(
            saved_products=saved_products,
            kbju_drafts=kbju_drafts,
        )
        if data.get("ai_text_draft_mode"):
            payload["ai_pending_meal"] = {**pending, "items": saved_products}
        else:
            payload["photo_analysis_items"] = saved_products
        await state.update_data(**payload)
        await callback.message.edit_text(
            _render_kbju_editor_text(product),
            reply_markup=_build_kbju_editor_keyboard(product_idx),
        )
        await callback.answer("✅ КБЖУ обновлены в черновике")
        return True

    source_meal_id = int(product.get("_source_meal_id") or meal_id or 0)
    if not source_meal_id:
        await callback.answer("Не удалось определить запись для обновления", show_alert=True)
        return False
    source_products = [
        _strip_source_meta(p)
        for p in saved_products
        if int(p.get("_source_meal_id") or source_meal_id) == source_meal_id
    ]
    totals, api_details = _build_meal_update_payload(source_products)
    meal = MealRepository.get_meal_by_id(source_meal_id, user_id)
    raw_query = meal.raw_query if meal and hasattr(meal, "raw_query") else None
    success = MealRepository.update_meal(
        meal_id=source_meal_id,
        user_id=user_id,
        description=raw_query,
        calories=totals["calories"],
        protein=totals["protein_g"],
        fat=totals["fat_total_g"],
        carbs=totals["carbohydrates_total_g"],
        products_json=json.dumps(source_products),
        api_details=api_details,
        is_manually_corrected=True,
    )
    if not success:
        await callback.answer("Не удалось обновить запись", show_alert=True)
        return False

    kbju_drafts.pop(str(product_idx), None)
    await state.set_state(MealEntryStates.edit_kbju_menu)
    await state.update_data(saved_products=saved_products, kbju_drafts=kbju_drafts)
    await callback.message.edit_text(
        _render_kbju_editor_text(product),
        reply_markup=_build_kbju_editor_keyboard(product_idx),
    )
    await callback.answer("✅ КБЖУ сохранены")
    meal_date = getattr(meal, "date", None)
    if meal_date is not None:
        await return_to_active_daily_preflight(callback.message, user_id, meal_date)
    return True


@router.callback_query(lambda c: c.data.startswith("meal_kfsave:"))
async def meal_kbju_field_save(callback: CallbackQuery, state: FSMContext):
    _, raw_idx, field = callback.data.split(":")
    product_idx = int(raw_idx)
    data = await state.get_data()
    kbju_drafts = data.get("kbju_drafts", {})
    draft = dict(kbju_drafts.get(str(product_idx), {}))
    draft[field] = max(0.0, float(data.get("editing_macro_current_value", 0)))
    kbju_drafts[str(product_idx)] = draft
    await state.update_data(kbju_drafts=kbju_drafts)
    await _save_kbju_changes_for_product(callback, state, product_idx)


@router.callback_query(lambda c: c.data.startswith("meal_ksave:"))
async def meal_kbju_save(callback: CallbackQuery, state: FSMContext):
    product_idx = int(callback.data.split(":")[1])
    await _save_kbju_changes_for_product(callback, state, product_idx)


@router.callback_query(lambda c: c.data.startswith("meal_wsave:"))
async def meal_weight_save(callback: CallbackQuery, state: FSMContext):
    """Сохраняет новый вес выбранного продукта и пересчитывает КБЖУ."""
    product_idx = int(callback.data.split(":")[1])
    user_id = str(callback.from_user.id)
    data = await state.get_data()
    meal_id = data.get("meal_id")
    saved_products = data.get("saved_products", [])
    drafts = data.get("weight_drafts", {})

    if product_idx < 0 or product_idx >= len(saved_products):
        await callback.answer("Не удалось сохранить", show_alert=True)
        return

    product = saved_products[product_idx]
    if str(product_idx) not in drafts:
        await callback.answer("Сначала измени вес", show_alert=True)
        return
    draft_weight = float(drafts.get(str(product_idx), product.get("grams", 0)))
    if round(draft_weight, 2) == round(float(product.get("grams") or 0), 2):
        await callback.answer("Вес не изменён", show_alert=True)
        return
    if draft_weight < 1:
        await callback.answer("Вес не может быть меньше 1 г", show_alert=True)
        return

    if not _apply_product_weight(product, draft_weight):
        await callback.answer("Не удалось пересчитать КБЖУ", show_alert=True)
        return

    if data.get("ai_text_draft_mode") or data.get("photo_draft_edit_mode"):
        drafts.pop(str(product_idx), None)
        pending = data.get("ai_pending_meal") or {}
        payload = dict(
            saved_products=saved_products,
            weight_drafts=drafts,
        )
        if data.get("ai_text_draft_mode"):
            payload["ai_pending_meal"] = {**pending, "items": saved_products}
        else:
            payload["photo_analysis_items"] = saved_products
        await state.update_data(**payload)
        await callback.answer("✅ Вес обновлён в черновике")
        await callback.message.edit_text(
            "<b>✏️ Выбери продукт для редактирования:</b>",
            reply_markup=_build_weight_products_keyboard(saved_products),
        )
        return

    source_meal_id = int(product.get("_source_meal_id") or meal_id or 0)
    if not source_meal_id:
        await callback.answer("Не удалось определить запись для обновления", show_alert=True)
        return

    source_products = [
        _strip_source_meta(p)
        for p in saved_products
        if int(p.get("_source_meal_id") or source_meal_id) == source_meal_id
    ]
    totals, api_details = _build_meal_update_payload(source_products)
    meal = MealRepository.get_meal_by_id(source_meal_id, user_id)
    raw_query = meal.raw_query if meal and hasattr(meal, "raw_query") else None

    success = MealRepository.update_meal(
        meal_id=source_meal_id,
        user_id=user_id,
        description=raw_query,
        calories=totals["calories"],
        protein=totals["protein_g"],
        fat=totals["fat_total_g"],
        carbs=totals["carbohydrates_total_g"],
        products_json=json.dumps(source_products),
        api_details=api_details,
        is_manually_corrected=bool(
            any(bool(p.get("is_manually_corrected")) for p in source_products)
        ),
    )
    if not success:
        await callback.answer("Не удалось обновить запись", show_alert=True)
        return

    drafts.pop(str(product_idx), None)
    await state.update_data(saved_products=saved_products, weight_drafts=drafts)
    await callback.answer("✅ Вес продукта обновлён")
    await _show_meal_edit_products_list(
        callback.message,
        saved_products,
        normalize_meal_type(
            data.get("editing_meal_type") or data.get("grouped_meal_type") or data.get("meal_type"),
            fallback=MealType.SNACK.value,
        ),
        edit_existing=True,
    )
    meal_date = getattr(meal, "date", None)
    if meal_date is not None:
        await return_to_active_daily_preflight(callback.message, user_id, meal_date)


@router.callback_query(lambda c: c.data.startswith("meal_wdelask:"))
async def meal_weight_delete_confirm_with_state(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    product_idx = int(callback.data.split(":")[1])
    data = await state.get_data()
    saved_products = data.get("saved_products", [])
    if product_idx < 0 or product_idx >= len(saved_products):
        await callback.answer("Не нашёл продукт", show_alert=True)
        return

    product_name = saved_products[product_idx].get("name") or "продукт"
    await callback.message.edit_text(
        f'Удалить продукт "{product_name}" из этого приёма пищи?',
        reply_markup=_build_weight_delete_confirm_keyboard(product_idx),
    )


@router.callback_query(lambda c: c.data.startswith("meal_wdelback:"))
async def meal_weight_delete_back(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    product_idx = int(callback.data.split(":")[1])
    data = await state.get_data()
    saved_products = data.get("saved_products", [])
    if product_idx < 0 or product_idx >= len(saved_products):
        await callback.answer("Не нашёл продукт", show_alert=True)
        return

    product = saved_products[product_idx]
    await state.set_state(MealEntryStates.editing_meal_weight)
    await callback.message.edit_text(
        _render_product_actions_text(product),
        reply_markup=_build_product_actions_keyboard(product_idx),
    )


@router.callback_query(lambda c: c.data.startswith("meal_wdel:"))
async def meal_weight_delete(callback: CallbackQuery, state: FSMContext):
    """Удаляет продукт из приёма пищи после подтверждения."""
    await callback.answer()
    product_idx = int(callback.data.split(":")[1])
    user_id = str(callback.from_user.id)
    data = await state.get_data()
    meal_id = data.get("meal_id")
    saved_products = data.get("saved_products", [])
    drafts = data.get("weight_drafts", {})

    if product_idx < 0 or product_idx >= len(saved_products):
        await callback.answer("Не удалось удалить продукт", show_alert=True)
        return
    if data.get("photo_draft_edit_mode") and len(saved_products) == 1:
        await callback.answer("В блюде должен остаться хотя бы один ингредиент", show_alert=True)
        return
    source_meal_id = int(saved_products[product_idx].get("_source_meal_id") or meal_id or 0)

    saved_products.pop(product_idx)
    drafts = {
        str(int(idx) - 1 if int(idx) > product_idx else int(idx)): value
        for idx, value in drafts.items()
        if int(idx) != product_idx
    }

    if data.get("ai_text_draft_mode") or data.get("photo_draft_edit_mode"):
        pending = data.get("ai_pending_meal") or {}
        payload = dict(
            saved_products=saved_products,
            weight_drafts=drafts,
        )
        if data.get("ai_text_draft_mode"):
            payload["ai_pending_meal"] = {**pending, "items": saved_products}
        else:
            payload["photo_analysis_items"] = saved_products
        await state.update_data(**payload)
        if not saved_products:
            if data.get("photo_draft_edit_mode"):
                await callback.message.edit_text(
                    "В блюде должен остаться хотя бы один ингредиент. Анализ не сохранён."
                )
            else:
                await state.clear()
                await callback.message.answer("Черновик пуст. Отменил добавление.", reply_markup=kbju_menu)
        else:
            await callback.message.edit_text(
                "<b>✏️ Выбери продукт для редактирования:</b>",
                reply_markup=_build_weight_products_keyboard(saved_products),
            )
            await callback.message.answer("✅ Продукт удалён из черновика")
        return

    source_products = [
        _strip_source_meta(p)
        for p in saved_products
        if int(p.get("_source_meal_id") or source_meal_id) == source_meal_id
    ]

    if not source_products:
        success = MealRepository.delete_meal(source_meal_id, user_id)
        if not success:
            await callback.answer("Не удалось обновить приём пищи", show_alert=True)
            return
        if not saved_products:
            await state.clear()
            await callback.message.answer("Приём пищи теперь пуст. Запись удалена.")
        else:
            await state.update_data(saved_products=saved_products, weight_drafts=drafts)
            await _show_meal_edit_products_list(
                callback.message,
                saved_products,
                normalize_meal_type(
                    data.get("editing_meal_type") or data.get("grouped_meal_type") or data.get("meal_type"),
                    fallback=MealType.SNACK.value,
                ),
                edit_existing=True,
            )
            await callback.message.answer("✅ Продукт удалён")
    else:
        totals, api_details = _build_meal_update_payload(source_products)
        meal = MealRepository.get_meal_by_id(source_meal_id, user_id)
        raw_query = meal.raw_query if meal and hasattr(meal, "raw_query") else None
        success = MealRepository.update_meal(
            meal_id=source_meal_id,
            user_id=user_id,
            description=raw_query,
            calories=totals["calories"],
            protein=totals["protein_g"],
            fat=totals["fat_total_g"],
            carbs=totals["carbohydrates_total_g"],
            products_json=json.dumps(source_products),
            api_details=api_details,
            is_manually_corrected=bool(
                any(bool(p.get("is_manually_corrected")) for p in source_products)
            ),
        )
        if not success:
            await callback.answer("Не удалось обновить запись", show_alert=True)
            return

        await state.update_data(saved_products=saved_products, weight_drafts=drafts)
        await _show_meal_edit_products_list(
            callback.message,
            saved_products,
            normalize_meal_type(
                data.get("editing_meal_type") or data.get("grouped_meal_type") or data.get("meal_type"),
                fallback=MealType.SNACK.value,
            ),
            edit_existing=True,
        )
        await callback.message.answer("✅ Продукт удалён")


@router.callback_query(lambda c: c.data.startswith("meal_del:"))
async def delete_meal(callback: CallbackQuery):
    """Удаляет приём пищи."""
    await callback.answer()
    parts = callback.data.split(":")
    meal_id = int(parts[1])
    target_date = date.fromisoformat(parts[2]) if len(parts) > 2 else date.today()
    user_id = str(callback.from_user.id)
    
    meal = MealRepository.get_meal_by_id(meal_id, user_id)
    changed_meal_type = normalize_meal_type(getattr(meal, "meal_type", None)) if meal else None
    success = MealRepository.delete_meal(meal_id, user_id)
    if success:
        await callback.message.answer("✅ Запись удалена")
        await _render_day_meals_messages(
            callback.message,
            user_id,
            target_date,
            include_back=True,
            changed_meal_type=changed_meal_type,
        )
        await return_to_active_daily_preflight(callback.message, user_id, target_date)
    else:
        await callback.message.answer("❌ Не удалось удалить запись")


@router.callback_query(lambda c: c.data == "kbju_test_start")
async def start_kbju_test_from_button(callback: CallbackQuery, state: FSMContext):
    """Начинает тест КБЖУ из inline кнопки."""
    await callback.answer()
    from utils.keyboards import kbju_gender_menu
    from states.user_states import KbjuTestStates
    
    await state.clear()
    await state.set_state(KbjuTestStates.entering_gender)
    
    push_menu_stack(callback.message.bot, kbju_gender_menu)
    await callback.message.answer(
        "Для начала выбери пол:",
        reply_markup=kbju_gender_menu,
    )


def register_meal_handlers(dp):
    """Регистрирует обработчики КБЖУ."""
    dp.include_router(router)
