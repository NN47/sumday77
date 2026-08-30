"""Обработчики команды /start и главного меню."""
import logging
from datetime import date
from aiogram import Router
from aiogram.types import Message
from aiogram.types.link_preview_options import LinkPreviewOptions
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from utils.keyboards import main_menu, push_menu_stack, quick_actions_inline
from utils.progress_formatters import (
    format_progress_block,
    format_water_progress_block,
    get_today_summary_text,
)
from database.session import get_db_session
from database.models import User
from database.repositories import AnalyticsRepository
from handlers.kbju_test import (
    has_completed_kbju_test,
    restart_required_kbju_test,
    start_age_confirmation,
)
from utils.log_sanitizer import safe_exception_summary

logger = logging.getLogger(__name__)

router = Router()


async def _build_recommendations_link(message: Message) -> str:
    """Возвращает HTML-ссылку на рекомендации от бота."""
    me = await message.bot.get_me()
    return f'🔗 <a href="https://t.me/{me.username}?start=recommendations">🔥 Философия Sumday77</a>'


@router.message(Command("start"))
async def start(message: Message, state: FSMContext):
    """Обработчик команды /start."""
    user_id = str(message.from_user.id)
    logger.info("Bot start command received")
    AnalyticsRepository.track_event(user_id, "start", section="entry")
    is_new_user = False
    
    # Создаём или обновляем пользователя в БД
    with get_db_session() as session:
        user = session.query(User).filter(User.user_id == user_id).first()
        if not user:
            user = User(user_id=user_id)
            session.add(user)
            session.commit()
            logger.info("New user registered")
            is_new_user = True

        age_verified = user.age_verified

    await continue_start(message, state, user_id=user_id, age_verified=age_verified, is_new_user=is_new_user)


async def continue_start(message: Message, state: FSMContext, *, user_id: str,
                         age_verified: bool | None, is_new_user: bool = False,
                         start_payload: str | None = None) -> None:
    """Continue the existing age/test flow using the actual Telegram actor ID."""
    if age_verified is False:
        await start_age_confirmation(message, state)
        return

    if age_verified is not True:
        if has_completed_kbju_test(user_id):
            await start_age_confirmation(message, state)
        else:
            await restart_required_kbju_test(message, state)
        return

    await show_verified_start(message, state, is_new_user=is_new_user,
                              user_id=user_id, start_payload=start_payload)


async def show_verified_start(
    message: Message,
    state: FSMContext,
    *,
    is_new_user: bool = False,
    user_id: str | None = None,
    start_payload: str | None = None,
) -> None:
    """Render /start content only after the persisted 18+ check succeeds."""
    user_id = str(user_id if user_id is not None else message.from_user.id)

    payload = ""
    if message.text and " " in message.text:
        payload = message.text.split(" ", 1)[1].strip().lower()
    if start_payload is not None:
        payload = start_payload.strip().lower()
    if payload == "recommendations":
        from handlers.common import _build_recommendations_text
        await message.answer(_build_recommendations_text(), parse_mode="Markdown")
        return

    if not has_completed_kbju_test(user_id):
        await restart_required_kbju_test(message, state)
        return
    
    # Формируем приветствие с прогрессом
    progress_text = format_progress_block(user_id)
    water_progress_text = format_water_progress_block(user_id)
    today_line = f"📅 <b>{date.today().strftime('%d.%m.%Y')}</b>"
    recommendations_link = await _build_recommendations_link(message)
    
    if is_new_user:
        # Мини-онбординг для новых пользователей
        welcome_intro = (
            "👋 Привет! Я твой фитнес-бот-помощник.\n\n"
            "Что я умею:\n"
            "• следить за КБЖУ и приёмами пищи\n"
            "• учитывать тренировки и расход калорий\n"
            "• помогать контролировать воду и вес\n"
            "• анализировать твою активность с помощью ИИ\n\n"
            "С чего начать прямо сейчас:\n"
            "1️⃣ В разделе «🍱 Дневник питания» задай цель или просто добавь первый приём пищи\n"
            "2️⃣ В «💧 Контроль воды» начни отмечать выпитую воду\n"
            "3️⃣ В «⚖️ Вес» укажи текущий вес для более точных рекомендаций\n"
        )
        welcome_text = (
            f"{today_line}\n\n"
            f"{welcome_intro}\n"
            f"{recommendations_link}\n\n"
            f"{progress_text}\n\n{water_progress_text}"
        )
    else:
        # Для существующих пользователей показываем краткий дайджест
        try:
            summary_text = get_today_summary_text(user_id)
        except Exception:
            summary_text = ""
        if summary_text:
            welcome_text = (
                f"{today_line}\n\n"
                f"{summary_text}\n\n"
                f"{recommendations_link}\n\n"
                f"{progress_text}\n\n{water_progress_text}"
            )
        else:
            welcome_text = (
                f"{today_line}\n\n"
                f"{recommendations_link}\n\n"
                f"{progress_text}\n\n{water_progress_text}"
            )
    
    push_menu_stack(message.bot, main_menu)
    # Сначала отправляем основной текст с inline-кнопками быстрых действий
    try:
        await message.answer(
            welcome_text,
            reply_markup=quick_actions_inline,
            parse_mode="HTML",
            link_preview_options=LinkPreviewOptions(is_disabled=True),
        )
    except Exception as exc:
        logger.error("Failed to send start summary error_type=%s", safe_exception_summary(exc), exc_info=True)
    # Отдельным сообщением показываем главное меню (reply-клавиатура) без уведомления
    await message.answer("⬇️ Кнопки управления", reply_markup=main_menu, disable_notification=True)


def register_start_handlers(dp):
    """Регистрирует обработчики команды /start."""
    dp.include_router(router)
