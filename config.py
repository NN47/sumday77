"""Конфигурация приложения."""
import os
from pathlib import Path
from dotenv import load_dotenv

# Загружаем переменные окружения
load_dotenv()

# База данных
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///fitness_bot.db")

# Telegram Bot
API_TOKEN = os.getenv("API_TOKEN")
if not API_TOKEN:
    raise RuntimeError("API_TOKEN не найден. Установи переменную окружения или создай .env с API_TOKEN.")
ADMIN_ID = int(os.getenv("ADMIN_ID", "6065083722"))

# Внешние API
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GEMINI_API_KEY2 = os.getenv("GEMINI_API_KEY2")  # Резервный ключ
GEMINI_API_KEY3 = os.getenv("GEMINI_API_KEY3")  # Третий резервный ключ
GEMINI_TEMP_ERROR_MAX_RETRIES = int(os.getenv("GEMINI_TEMP_ERROR_MAX_RETRIES", "2"))
GEMINI_TEMP_ERROR_BACKOFF_SECONDS = [
    int(part.strip())
    for part in os.getenv("GEMINI_TEMP_ERROR_BACKOFF_SECONDS", "1,3").split(",")
    if part.strip()
]
if not GEMINI_TEMP_ERROR_BACKOFF_SECONDS:
    GEMINI_TEMP_ERROR_BACKOFF_SECONDS = [1, 3]
GEMINI_TEMP_ERROR_JITTER_SECONDS = float(os.getenv("GEMINI_TEMP_ERROR_JITTER_SECONDS", "0.5"))
GEMINI_TEMP_KEY_COOLDOWN_SECONDS = int(os.getenv("GEMINI_TEMP_KEY_COOLDOWN_SECONDS", "180"))
GEMINI_RATE_LIMIT_COOLDOWN_SECONDS = int(os.getenv("GEMINI_RATE_LIMIT_COOLDOWN_SECONDS", "300"))
GEMINI_MAX_KEYS_PER_REQUEST = int(os.getenv("GEMINI_MAX_KEYS_PER_REQUEST", "3"))
GEMINI_MAX_TOTAL_ATTEMPTS_PER_REQUEST = int(os.getenv("GEMINI_MAX_TOTAL_ATTEMPTS_PER_REQUEST", "8"))
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")
DEEPSEEK_BASE_URL = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
DEEPSEEK_MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")

# Keep-alive сервер
KEEPALIVE_PORT = 10000

# Настройки БД
DB_POOL_PRE_PING = True
DB_POOL_RECYCLE = 1800  # 30 минут

# Названия месяцев (русский)
MONTH_NAMES = [
    "",
    "Январь",
    "Февраль",
    "Март",
    "Апрель",
    "Май",
    "Июнь",
    "Июль",
    "Август",
    "Сентябрь",
    "Октябрь",
    "Ноябрь",
    "Декабрь",
]



def _get_bool_env(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


OCR_ENABLED = _get_bool_env("OCR_ENABLED", True)
OCR_TIMEOUT_SECONDS = int(os.getenv("OCR_TIMEOUT_SECONDS", "5"))
OCR_MAX_SIDE_PX = int(os.getenv("OCR_MAX_SIDE_PX", "1600"))
OCR_MIN_TEXT_LENGTH = int(os.getenv("OCR_MIN_TEXT_LENGTH", "40"))

# Пользовательские AI-квоты и технические предохранители.
AI_QUOTA_RESERVATION_TTL_SECONDS = int(os.getenv("AI_QUOTA_RESERVATION_TTL_SECONDS", "360"))
AI_QUOTA_COOLDOWN_SECONDS = float(os.getenv("AI_QUOTA_COOLDOWN_SECONDS", "3"))
AI_TEXT_ATTEMPT_LIMIT_PER_DAY = int(os.getenv("AI_TEXT_ATTEMPT_LIMIT_PER_DAY", "20"))
AI_IMAGE_ATTEMPT_LIMIT_PER_DAY = int(os.getenv("AI_IMAGE_ATTEMPT_LIMIT_PER_DAY", "25"))
AI_DAILY_ANALYSIS_ATTEMPT_LIMIT_PER_DAY = int(
    os.getenv("AI_DAILY_ANALYSIS_ATTEMPT_LIMIT_PER_DAY", "3")
)
AI_MEAL_COMMENT_ATTEMPT_LIMIT_PER_DAY = int(
    os.getenv("AI_MEAL_COMMENT_ATTEMPT_LIMIT_PER_DAY", "8")
)
AI_GLOBAL_ATTEMPT_LIMIT_PER_DAY = int(os.getenv("AI_GLOBAL_ATTEMPT_LIMIT_PER_DAY", "10000"))
AI_MAX_IMAGE_BYTES = int(os.getenv("AI_MAX_IMAGE_BYTES", str(10 * 1024 * 1024)))
