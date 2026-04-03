"""Общие обработчики (назад, главное меню и т.д.)."""
import logging
from aiogram import Router
from aiogram.types import Message, CallbackQuery
from aiogram.types.link_preview_options import LinkPreviewOptions
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from utils.keyboards import (
    MAIN_MENU_BUTTON_ALIASES,
    MAIN_MENU_BUTTON_TEXT,
    main_menu,
    push_menu_stack,
    quick_actions_inline,
)

logger = logging.getLogger(__name__)

router = Router()

async def _build_recommendations_link(message: Message) -> str:
    """Возвращает HTML-ссылку на рекомендации от бота."""
    me = await message.bot.get_me()
    return f'🔗 <a href="https://t.me/{me.username}?start=recommendations">Рекомендации от бота</a>'


def _build_recommendations_text() -> str:
    return (
        "**🤖 Рекомендации от бота**\n\n"
        "Начни с базы — она работает всегда.\n\n"
        "**Калории.**\n"
        "Я рассчитываю твою суточную норму под цель.\n"
        "Твоя задача — просто попадать в цифры. Без догадок и «на глаз».\n\n"
        "**Белок — обязателен.**\n"
        "Он помогает сохранить мышцы, ускоряет обмен веществ\n"
        "и снижает аппетит. Если контролировать что-то строго — то его.\n\n"
        "Весь фокус здесь: **калории + белок**.\n"
        "Неважно, кето это, ПП или интуитивное питание.\n"
        "Есть дефицит и достаточно белка — тело будет меняться.\n\n"
        "**Режим питания — для комфорта, не для строгости.**\n"
        "— Первый приём пищи через 1–2 часа после пробуждения.\n"
        "— Второй — в середине дня.\n"
        "— Последний — за 3–5 часов до сна.\n"
        "Так еда не мешает сну, а восстановление идёт лучше.\n\n"
        "**Завтрак с белком (~40 г).**\n"
        "Он стабилизирует уровень сахара,\n"
        "уменьшает вечерний голод\n"
        "и помогает удерживать мышечную форму.\n\n"
        "**Вода — регулярно.**\n"
        "— После пробуждения.\n"
        "— Между приёмами пищи.\n"
        "— До и после еды.\n"
        "Часто самочувствие улучшается уже на этом этапе.\n\n"
        "**8–10 тысяч шагов в день.**\n"
        "Если много сидишь — гуляй во время звонков\n"
        "или используй дорожку под стол.\n"
        "Ходьба работает тихо, но стабильно.\n\n"
        "**Алкоголь.**\n"
        "Когда ты пьёшь, тело занято переработкой алкоголя, а не жира.\n"
        "Жиросжигание в этот момент на паузе.\n"
        "Худеть и регулярно пить — можно,\n"
        "но это усложняет путь без реальной пользы.\n\n"
        "**Отслеживай главное:**\n"
        "— Вес — примерно раз в неделю.\n"
        "— Тренировки и питание — по ходу.\n"
        "— Объём талии — раз в неделю.\n\n"
        "**Короткие заметки помогают больше, чем кажется.**\n"
        "Пара строк о самочувствии и сложных моментах\n"
        "помогает видеть закономерности и не срываться.\n\n"
        "Я рядом, чтобы считать, напоминать\n"
        "и держать фокус там, где он действительно нужен 🤖\n\n"
    )


@router.message(lambda m: m.text in MAIN_MENU_BUTTON_ALIASES)
async def go_main_menu(message: Message, state: FSMContext):
    """Обработчик кнопки 'Главное меню'."""
    from datetime import date
    from utils.progress_formatters import (
        format_progress_block,
        format_water_progress_block,
        format_today_workouts_block,
    )
    
    user_id = str(message.from_user.id)
    logger.info(f"User {user_id} navigated to main menu")
    
    # Очищаем FSM состояние
    await state.clear()
    
    # Формируем сообщение с прогрессом
    progress_text = format_progress_block(user_id)
    water_progress_text = format_water_progress_block(user_id)
    workouts_text = format_today_workouts_block(user_id, include_date=False)
    recommendations_link = await _build_recommendations_link(message)

    today_line = f"📅 <b>{date.today().strftime('%d.%m.%Y')}</b>"
    welcome_text = (
        f"{today_line}\n\n{recommendations_link}\n\n"
        f"{workouts_text}\n\n{progress_text}\n\n{water_progress_text}"
    )
    
    push_menu_stack(message.bot, main_menu)
    # Отправляем текст с кратким дневным статусом и inline-кнопками быстрых действий
    try:
        await message.answer(
            welcome_text,
            reply_markup=quick_actions_inline,
            parse_mode="HTML",
            link_preview_options=LinkPreviewOptions(is_disabled=True),
        )
    except Exception:
        logger.exception("Failed to send main menu summary for user %s", user_id)
    # Затем — отдельное сообщение с основной клавиатурой без уведомления
    await message.answer("⬇️ Главное меню", reply_markup=main_menu, disable_notification=True)


@router.message(StateFilter(None), lambda m: m.text == "⬅️ Назад")
async def go_back(message: Message, state: FSMContext):
    """Обработчик кнопки 'Назад' - возвращает на шаг назад."""
    logger.info(f"User {message.from_user.id} pressed back button")
    
    stack = getattr(message.bot, "menu_stack", [])
    
    if len(stack) > 1:
        # Убираем текущее меню из стека
        stack.pop()
        prev_menu = stack[-1]  # Берем предыдущее меню
        message.bot.menu_stack = stack
        push_menu_stack(message.bot, prev_menu)
        await message.answer("⬅️ Назад", reply_markup=prev_menu)
    else:
        # Если стек пуст или только главное меню - возвращаемся в главное
        await state.clear()
        push_menu_stack(message.bot, main_menu)
        await message.answer(MAIN_MENU_BUTTON_TEXT, reply_markup=main_menu)


@router.callback_query(lambda c: c.data == "cal_close")
async def close_calendar(callback: CallbackQuery):
    """Закрывает календарь."""
    await callback.answer()
    await callback.message.delete()


@router.callback_query(lambda c: c.data == "noop")
async def ignore_callback(callback: CallbackQuery):
    """Игнорирует callback без действия."""
    await callback.answer()


@router.callback_query(lambda c: c.data == "quick_supplements")
async def quick_supplements(callback: CallbackQuery, state: FSMContext):
    """Быстрый переход к отметке добавки."""
    await callback.answer()
    # Импортируем обработчик отметки добавки
    from handlers.supplements import start_log_supplement_flow
    await start_log_supplement_flow(callback.message, state, str(callback.from_user.id))


@router.callback_query(lambda c: c.data == "quick_workout_add")
async def quick_workout_add(callback: CallbackQuery, state: FSMContext):
    """Быстрый переход к добавлению тренировки."""
    await callback.answer()
    from handlers.workouts import add_training_entry
    await add_training_entry(callback.message, state)


@router.callback_query(lambda c: c.data == "quick_weight")
async def quick_weight(callback: CallbackQuery, state: FSMContext):
    """Быстрое открытие ввода веса."""
    await callback.answer()
    # Импортируем обработчик веса
    from handlers.weight import add_weight_start
    await add_weight_start(callback.message, state)


@router.callback_query(lambda c: c.data == "quick_wellbeing")
async def quick_wellbeing(callback: CallbackQuery, state: FSMContext):
    """Быстрый переход к самочувствию."""
    await callback.answer()
    from handlers.wellbeing import start_wellbeing
    await start_wellbeing(callback.message, state)


@router.callback_query(lambda c: c.data == "quick_recommendations")
async def quick_recommendations(callback: CallbackQuery):
    """Быстрый показ рекомендаций."""
    await callback.answer()
    await callback.message.answer(_build_recommendations_text(), parse_mode="Markdown")


@router.message(lambda m: m.text == "🤖 Рекомендации")
async def show_recommendations(message: Message):
    """Показывает рекомендации из главного меню."""
    await message.answer(_build_recommendations_text(), parse_mode="Markdown")


def register_common_handlers(dp):
    """Регистрирует общие обработчики."""
    dp.include_router(router)
