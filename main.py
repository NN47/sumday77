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

from config import API_TOKEN, KEEPALIVE_PORT
from middlewares import OnboardingMiddleware
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
)
from services.notification_scheduler import NotificationScheduler


async def main():
    """Основная функция запуска бота."""
    # Инициализация БД
    logger.info("Инициализация базы данных...")
    init_db()
    
    # Создаём бота и диспетчер с FSM storage
    bot = Bot(token=API_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    storage = MemoryStorage()
    dp = Dispatcher(storage=storage)
    onboarding_middleware = OnboardingMiddleware()
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
    from handlers.calendar import register_calendar_handlers
    register_calendar_handlers(dp)
    from handlers.procedures import register_procedure_handlers
    register_procedure_handlers(dp)
    
    # Запускаем планировщик уведомлений
    logger.info("Запуск планировщика уведомлений...")
    notification_scheduler = NotificationScheduler(bot)
    scheduler_task = asyncio.create_task(notification_scheduler.start())
    
    logger.info("🚀 Бот запущен и готов к работе!")
    
    try:
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


if __name__ == "__main__":
    nest_asyncio.apply()
    asyncio.run(main())
