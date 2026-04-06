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
    return f'🔗 <a href="https://t.me/{me.username}?start=recommendations">🔥 Философия Sumday77</a>'


def _build_recommendations_text() -> str:
    return (
        "🔥 *Философия Sumday77*\n\n"
        "🔥 Sumday77\n\n"
        "Не ещё один счётчик калорий.\n"
        "Система, которая помогает тебе менять тело осознанно.\n\n"
        "Я здесь чтобы убрать случайность из прогресса.\n\n"
        "Чтобы ты видел не просто цифры —\n"
        "а понимал что происходит.\n\n"
        "⚡ Твоё тело всегда работает по системе.\n"
        "Я просто показываю её.\n\n"
        "🍱 Калории — это не ограничения.\n"
        "Это управление.\n\n"
        "Когда есть понимание:\n"
        "сколько нужно —\n"
        "становится проще решать\n"
        "что делать дальше.\n\n"
        "Без крайностей.\n"
        "Без перегибов.\n"
        "Без «начну с понедельника».\n\n"
        "💪 Форма — это не только вес\n\n"
        "Можно просто худеть.\n"
        "А можно менять композицию тела.\n\n"
        "Поэтому я держу в фокусе белок.\n"
        "Он помогает сохранять то,\n"
        "что делает форму сильной.\n\n"
        "💧 Вода — это база, о которой забывают\n\n"
        "Иногда разница между:\n"
        "усталостью,\n"
        "голодом,\n"
        "отсутствием прогресса\n\n"
        "— это просто нехватка воды.\n\n"
        "Когда тело получает достаточно воды,\n"
        "всё начинает работать как задумано.\n\n"
        "🚶 Прогресс редко выглядит героически\n\n"
        "Чаще он выглядит как:\n"
        "обычные дни,\n"
        "обычные решения,\n"
        "обычная последовательность.\n\n"
        "Именно это работает.\n\n"
        "📊 Sumday77 превращает действия в систему\n\n"
        "Ты просто живёшь жизнь:\n"
        "ешь,\n"
        "двигаешься,\n"
        "тренируешься,\n"
        "отдыхаешь.\n\n"
        "Я:\n"
        "— считаю\n"
        "— анализирую\n"
        "— показываю динамику\n"
        "— помогаю держать направление\n\n"
        "Без перегруза.\n"
        "Без давления.\n\n"
        "🧠 Со временем происходит главное:\n\n"
        "Ты перестаёшь действовать наугад.\n\n"
        "Появляется понимание:\n"
        "что работает лично для тебя.\n\n"
        "А это уже уровень,\n"
        "где результат становится предсказуемым.\n\n"
        "🔥 Философия Sumday77:\n\n"
        "Сильное тело — это не случайность.\n"
        "Это сумма дней.\n\n"
        "Sum day.\n"
        "Sum day77.\n\n"
        "День за днём.\n"
        "Решение за решением.\n"
        "Результат становится неизбежным ⚡"
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


@router.message(lambda m: m.text in {"🔥 Философия Sumday77", "🤖 Рекомендации"})
async def show_recommendations(message: Message):
    """Показывает рекомендации из главного меню."""
    await message.answer(_build_recommendations_text(), parse_mode="Markdown")


def register_common_handlers(dp):
    """Регистрирует общие обработчики."""
    dp.include_router(router)
