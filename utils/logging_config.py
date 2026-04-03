"""Настройка логирования для бота."""
import logging
import sys
from pathlib import Path

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
    
    # Базовый конфиг
    logging.basicConfig(
        level=level,
        format=log_format,
        datefmt=date_format,
        handlers=[
            # Вывод в консоль
            logging.StreamHandler(sys.stdout),
            # Вывод в файл
            logging.FileHandler(LOG_DIR / "bot.log", encoding="utf-8"),
        ],
    )
    
    # Настройка уровней для внешних библиотек
    logging.getLogger("aiogram").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    
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

