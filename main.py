"""
Точка входа для запуска бота.
"""
import asyncio
import nest_asyncio
import logging
import threading
import http.server
import socketserver

from aiogram import Bot, Dispatcher
from aiogram.enums import ParseMode
from aiogram.client.bot import DefaultBotProperties
from aiogram.fsm.storage.memory import MemoryStorage
from sqlalchemy import text

from config import API_TOKEN, KEEPALIVE_PORT
from middlewares import OnboardingMiddleware, UserActivityMiddleware
from utils.logging_config import setup_logging

# Настраиваем логирование
setup_logging()

logger = logging.getLogger(__name__)


class ReusableTCPServer(socketserver.TCPServer):
    """TCP сервер с возможностью переиспользования адреса."""
    allow_reuse_address = True


def start_keepalive_server():
    """Запускает keep-alive HTTP сервер в отдельном потоке."""
    PORT = KEEPALIVE_PORT
    handler = http.server.SimpleHTTPRequestHandler
    
    class QuietHandler(handler):
        """Handler без вывода логов."""
        def log_message(self, format, *args):
            pass
    
    with ReusableTCPServer(("", PORT), QuietHandler) as httpd:
        logger.info(f"✅ Keep-alive сервер запущен на порту {PORT}")
        httpd.serve_forever()


# Запускаем keep-alive сервер СРАЗУ, до импорта handlers
logger.info("Запуск keep-alive сервера...")
threading.Thread(target=start_keepalive_server, daemon=True).start()

# Теперь импортируем handlers
logger.info("Импорт обработчиков...")
from database.session import init_db
from database.session import engine
from handlers import (
    register_common_handlers,
    register_start_handlers,
    register_workout_handlers,
    register_meal_handlers,
    register_weight_handlers,
    register_supplement_handlers,
    register_water_handlers,
    register_settings_handlers,
    register_activity_handlers,
    register_kbju_test_handlers,
    register_wellbeing_handlers,
    register_admin_handlers,
)
from services.notification_scheduler import NotificationScheduler

TELEGRAM_POLLING_LOCK_KEY = 8471265468


def acquire_polling_lock():
    """Пытается получить межпроцессный lock для polling (PostgreSQL advisory lock)."""
    backend_name = engine.url.get_backend_name()
    if backend_name != "postgresql":
        logger.warning(
            "База данных %s не поддерживает advisory lock. "
            "Защита от параллельного polling неактивна.",
            backend_name,
        )
        return None

    connection = engine.connect()
    acquired = connection.execute(
        text("SELECT pg_try_advisory_lock(:lock_key)"),
        {"lock_key": TELEGRAM_POLLING_LOCK_KEY},
    ).scalar()
    if not acquired:
        connection.close()
        return None
    return connection


async def main():
    """Основная функция запуска бота."""
    # Инициализация БД
    logger.info("Инициализация базы данных...")
    init_db()
    
    # Создаём бота и диспетчер с FSM storage
    bot = Bot(token=API_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    storage = MemoryStorage()
    dp = Dispatcher(storage=storage)
    user_activity_middleware = UserActivityMiddleware()
    onboarding_middleware = OnboardingMiddleware()
    dp.message.outer_middleware(user_activity_middleware)
    dp.callback_query.outer_middleware(user_activity_middleware)
    dp.message.outer_middleware(onboarding_middleware)
    dp.callback_query.outer_middleware(onboarding_middleware)
    
    # Регистрируем обработчики
    logger.info("Регистрация обработчиков...")
    register_common_handlers(dp)
    register_start_handlers(dp)
    register_workout_handlers(dp)
    register_meal_handlers(dp)
    register_weight_handlers(dp)
    register_supplement_handlers(dp)
    register_water_handlers(dp)
    register_settings_handlers(dp)
    register_activity_handlers(dp)
    register_kbju_test_handlers(dp)
    register_wellbeing_handlers(dp)
    register_admin_handlers(dp)
    from handlers.calendar import register_calendar_handlers
    register_calendar_handlers(dp)
    
    # Запускаем планировщик уведомлений
    logger.info("Запуск планировщика уведомлений...")
    notification_scheduler = NotificationScheduler(bot)
    scheduler_task = asyncio.create_task(notification_scheduler.start())
    
    logger.info("🚀 Бот запущен и готов к работе!")
    polling_lock_conn = acquire_polling_lock()
    if polling_lock_conn is None:
        logger.error(
            "Не удалось получить lock для polling. "
            "Скорее всего, другой инстанс бота уже запущен."
        )
        return

    logger.info("Lock для polling получен. Запускаем long polling.")

    try:
        await bot.delete_webhook(drop_pending_updates=False)
        # Запускаем polling
        await dp.start_polling(bot)
    finally:
        # Останавливаем планировщик при завершении
        notification_scheduler.stop()
        scheduler_task.cancel()
        try:
            await scheduler_task
        except asyncio.CancelledError:
            pass
        try:
            polling_lock_conn.execute(
                text("SELECT pg_advisory_unlock(:lock_key)"),
                {"lock_key": TELEGRAM_POLLING_LOCK_KEY},
            )
        finally:
            polling_lock_conn.close()


if __name__ == "__main__":
    nest_asyncio.apply()
    asyncio.run(main())
