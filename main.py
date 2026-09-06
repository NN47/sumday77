"""
Точка входа для запуска бота.
"""
import asyncio
import nest_asyncio
import logging
import threading

from aiogram import Bot, Dispatcher
from aiogram.enums import ParseMode
from aiogram.client.bot import DefaultBotProperties
from aiogram.fsm.storage.memory import MemoryStorage
from psycopg2 import OperationalError as PsycopgOperationalError
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, OperationalError

from config import API_TOKEN, KEEPALIVE_PORT
from keepalive_server import HealthCheckHandler, ReusableTCPServer
from middlewares import OnboardingMiddleware, UserActivityMiddleware
from middlewares.legal import LegalAcceptanceMiddleware
from utils.logging_config import setup_logging
from utils.log_sanitizer import safe_exception_summary

# Настраиваем логирование
setup_logging()

logger = logging.getLogger(__name__)


def start_keepalive_server():
    """Запускает keep-alive HTTP сервер в отдельном потоке."""
    PORT = KEEPALIVE_PORT

    with ReusableTCPServer(("", PORT), HealthCheckHandler) as httpd:
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
POLLING_LOCK_RETRY_SECONDS = 7
POLLING_LOCK_DB_ERRORS = (OperationalError, DBAPIError, PsycopgOperationalError)


def is_connection_open(connection) -> bool:
    """Проверяет, можно ли использовать уже существующее соединение без переподключения."""
    if connection is None:
        return False
    if connection.closed:
        return False
    if getattr(connection, "invalidated", False):
        return False

    try:
        dbapi_connection = connection.connection.driver_connection
    except POLLING_LOCK_DB_ERRORS as error:
        logger.warning(
            "Не удалось проверить состояние соединения для polling lock: %s",
            safe_exception_summary(error),
        )
        return False

    dbapi_closed = getattr(dbapi_connection, "closed", None)
    return dbapi_closed in (None, False, 0)


def release_polling_lock_safely(connection) -> None:
    """Освобождает PostgreSQL advisory lock, не мешая корректному завершению процесса."""
    if not is_connection_open(connection):
        logger.warning(
            "Advisory lock для polling не был освобождён вручную: "
            "соединение с БД уже закрыто или недоступно. "
            "PostgreSQL освободит lock автоматически при закрытии сессии."
        )
        logger.info("Polling lock освобождён при закрытии соединения с БД")
        return

    try:
        connection.execute(
            text("SELECT pg_advisory_unlock(:lock_key)"),
            {"lock_key": TELEGRAM_POLLING_LOCK_KEY},
        )
        logger.info("Polling lock освобождён")
    except POLLING_LOCK_DB_ERRORS as error:
        logger.warning(
            "Не удалось вручную освободить advisory lock для polling: %s. "
            "Вероятно, соединение с БД уже потеряно; PostgreSQL освободит lock "
            "автоматически при закрытии сессии.",
            safe_exception_summary(error),
        )
        logger.info("Polling lock освобождён при закрытии соединения с БД")


def close_connection_safely(connection) -> None:
    """Закрывает соединение с БД без ошибки при повторном или неудачном закрытии."""
    if connection is None or connection.closed:
        return
    try:
        connection.close()
    except Exception as error:
        logger.warning(
            "Ошибка при закрытии соединения polling lock error_type=%s",
            safe_exception_summary(error),
        )


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

    connection = None
    try:
        connection = engine.connect()
        acquired = connection.execute(
            text("SELECT pg_try_advisory_lock(:lock_key)"),
            {"lock_key": TELEGRAM_POLLING_LOCK_KEY},
        ).scalar()
        if not acquired:
            close_connection_safely(connection)
            return None
        return connection
    except POLLING_LOCK_DB_ERRORS as error:
        logger.warning(
            "Не удалось получить polling lock из-за ошибки БД: %s",
            safe_exception_summary(error),
        )
        close_connection_safely(connection)
        return None


async def wait_for_polling_lock():
    """Ждёт advisory lock, повторяя попытки во время rolling deploy."""
    attempt = 0
    while True:
        attempt += 1
        if attempt == 1:
            logger.info("Попытка получить polling lock")
        else:
            logger.info("Повторная попытка получить polling lock")

        connection = acquire_polling_lock()
        if connection is not None:
            logger.info("Polling lock получен")
            if attempt > 1:
                logger.info("Переход standby → active")
            return connection

        logger.warning(
            "Polling lock занят, standby. Повтор через %s секунд.",
            POLLING_LOCK_RETRY_SECONDS,
        )
        await asyncio.sleep(POLLING_LOCK_RETRY_SECONDS)


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
    dp.message.outer_middleware(LegalAcceptanceMiddleware())
    dp.callback_query.outer_middleware(LegalAcceptanceMiddleware())
    dp.message.outer_middleware(onboarding_middleware)
    dp.callback_query.outer_middleware(onboarding_middleware)
    
    # Регистрируем обработчики
    logger.info("Регистрация обработчиков...")
    from handlers.legal import register_legal_handlers
    register_legal_handlers(dp)
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
    
    logger.info("🚀 Бот запущен и готов к работе!")
    polling_lock_conn = None
    notification_scheduler = None
    scheduler_task = None
    try:
        polling_lock_conn = await wait_for_polling_lock()

        # Запускаем планировщик уведомлений только в активном инстансе
        logger.info("Запуск планировщика уведомлений...")
        notification_scheduler = NotificationScheduler(bot, dp)
        bot.sumday77_notification_scheduler = notification_scheduler
        scheduler_task = asyncio.create_task(notification_scheduler.start())
        logger.info("Polling и планировщик запущены")

        await bot.delete_webhook(drop_pending_updates=False)
        # Запускаем polling
        await dp.start_polling(bot)
    finally:
        try:
            # Останавливаем планировщик при завершении
            if notification_scheduler is not None:
                notification_scheduler.stop()
            if scheduler_task is not None:
                scheduler_task.cancel()
                try:
                    await scheduler_task
                except asyncio.CancelledError:
                    pass
        finally:
            try:
                if polling_lock_conn is not None:
                    release_polling_lock_safely(polling_lock_conn)
            finally:
                close_connection_safely(polling_lock_conn)
                await bot.session.close()


if __name__ == "__main__":
    nest_asyncio.apply()
    asyncio.run(main())
