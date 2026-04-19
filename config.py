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
NUTRITION_API_KEY = os.getenv("NUTRITION_API_KEY")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
OPENROUTER_MODEL = os.getenv("OPENROUTER_MODEL", "openrouter/free")
OPENROUTER_BASE_URL = os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")
OPENROUTER_HTTP_REFERER = os.getenv("OPENROUTER_HTTP_REFERER", "https://your-render-url")
OPENROUTER_APP_TITLE = os.getenv("OPENROUTER_APP_TITLE", "Sumday Bot")
GIGACHAT_API_KEY = os.getenv("GIGACHAT_API_KEY")
GIGACHAT_MODEL = os.getenv("GIGACHAT_MODEL", "GigaChat-2")
GIGACHAT_OAUTH_URL = os.getenv("GIGACHAT_OAUTH_URL", "https://ngw.devices.sberbank.ru:9443/api/v2/oauth")
GIGACHAT_API_URL = os.getenv("GIGACHAT_API_URL", "https://gigachat.devices.sberbank.ru/api/v1")

if not NUTRITION_API_KEY:
    print("⚠️ ВНИМАНИЕ: NUTRITION_API_KEY не найден. КБЖУ через CalorieNinjas работать не будет.")

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
