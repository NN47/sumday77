"""Настройка логирования для бота."""
import logging
import sys
from pathlib import Path

from utils.log_sanitizer import PrivacySafeFormatter

# Создаём директорию для логов
LOG_DIR = Path("logs")
LOG_DIR.mkdir(exist_ok=True)


def setup_logging(log_level: str = "INFO") -> None:
    """
    Настраивает логирование для приложения.
    
    Args:
        log_level: Уровень логирования (DEBUG, INFO, WARNING, ERROR, CRITICAL)
    """
    # Формат логов
    log_format = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    date_format = "%Y-%m-%d %H:%M:%S"
    
    # Настройка уровня логирования
    level = getattr(logging, log_level.upper(), logging.INFO)
    
    formatter = PrivacySafeFormatter(log_format, datefmt=date_format)
    stdout_handler = logging.StreamHandler(sys.stdout)
    file_handler = logging.FileHandler(LOG_DIR / "bot.log", encoding="utf-8")
    stdout_handler.setFormatter(formatter)
    file_handler.setFormatter(formatter)

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

