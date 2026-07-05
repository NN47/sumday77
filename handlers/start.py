"""Обработчики команды /start и главного меню."""
import logging
from aiogram import Router
from aiogram.types import Message
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from database.session import get_db_session
from database.models import User
from database.repositories import AnalyticsRepository
from handlers.kbju_test import has_completed_kbju_test, restart_required_kbju_test

logger = logging.getLogger(__name__)

router = Router()


@router.message(Command("start"))
async def start(message: Message, state: FSMContext):
    """Обработчик команды /start."""
    user_id = str(message.from_user.id)

    payload = ""
    if message.text and " " in message.text:
        payload = message.text.split(" ", 1)[1].strip().lower()
    if payload == "recommendations":
        from handlers.common import _build_recommendations_text
        await message.answer(_build_recommendations_text(), parse_mode="Markdown")
        return
    logger.info(f"User {user_id} started the bot")
    AnalyticsRepository.track_event(user_id, "start", section="entry")
    # Создаём или обновляем пользователя в БД
    with get_db_session() as session:
        user = session.query(User).filter(User.user_id == user_id).first()
        if not user:
            user = User(user_id=user_id)
            session.add(user)
            session.commit()
            logger.info(f"New user {user_id} registered")

    if not has_completed_kbju_test(user_id):
        await restart_required_kbju_test(message, state)
        return
    
    from handlers.common import send_main_menu_screen

    await send_main_menu_screen(message, user_id)


def register_start_handlers(dp):
    """Регистрирует обработчики команды /start."""
    dp.include_router(router)
