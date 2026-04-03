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

# Внешние API
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GEMINI_API_KEY2 = os.getenv("GEMINI_API_KEY2")  # Резервный ключ
GEMINI_API_KEY3 = os.getenv("GEMINI_API_KEY3")  # Третий резервный ключ
NUTRITION_API_KEY = os.getenv("NUTRITION_API_KEY")

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

