"""Настройка логирования для бота."""
import logging
from logging.handlers import RotatingFileHandler
import sys
from pathlib import Path

from utils.log_sanitizer import PrivacySafeFormatter

# Создаём директорию для логов
LOG_DIR = Path("logs")
LOG_DIR.mkdir(exist_ok=True)

BOT_LOG_MAX_BYTES = 5 * 1024 * 1024
BOT_LOG_BACKUP_COUNT = 3
LOG_FORMAT = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
LOG_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


def create_bot_log_handler(
    log_path: str | Path | None = None,
    *,
    max_bytes: int = BOT_LOG_MAX_BYTES,
    backup_count: int = BOT_LOG_BACKUP_COUNT,
) -> RotatingFileHandler:
    """Create a bounded, privacy-safe handler for the application log."""
    handler = RotatingFileHandler(
        log_path or (LOG_DIR / "bot.log"),
        maxBytes=max_bytes,
        backupCount=backup_count,
        encoding="utf-8",
    )
    handler.setFormatter(PrivacySafeFormatter(LOG_FORMAT, datefmt=LOG_DATE_FORMAT))
    return handler


def setup_logging(log_level: str = "INFO") -> None:
    """
    Настраивает логирование для приложения.
    
    Args:
        log_level: Уровень логирования (DEBUG, INFO, WARNING, ERROR, CRITICAL)
    """
    # Формат логов
    # Настройка уровня логирования
    level = getattr(logging, log_level.upper(), logging.INFO)
    
    formatter = PrivacySafeFormatter(LOG_FORMAT, datefmt=LOG_DATE_FORMAT)
    stdout_handler = logging.StreamHandler(sys.stdout)
    file_handler = create_bot_log_handler()
    stdout_handler.setFormatter(formatter)

    # Базовый конфиг
    logging.basicConfig(
        level=level,
        handlers=[stdout_handler, file_handler],
        force=True,
    )
    
    # Настройка уровней для внешних библиотек
    for external_logger in (
        "aiogram",
        "httpx",
        "httpcore",
        "openai",
        "google",
        "google.genai",
        "google.generativeai",
        "google_genai",
    ):
        logging.getLogger(external_logger).setLevel(logging.WARNING)
    
    logger = logging.getLogger(__name__)
    logger.info(f"Логирование настроено. Уровень: {log_level}")


def get_logger(name: str) -> logging.Logger:
    """
    Получает логгер с указанным именем.
    
    Args:
        name: Имя логгера (обычно __name__)
        
    Returns:
        Logger instance
    """
    return logging.getLogger(name)

