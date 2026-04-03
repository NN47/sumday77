"""Управление сессиями базы данных."""
from contextlib import contextmanager
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker
from config import DATABASE_URL, DB_POOL_PRE_PING, DB_POOL_RECYCLE
from database.models import Base
import logging

logger = logging.getLogger(__name__)

# Создаём engine
engine = create_engine(
    DATABASE_URL,
    pool_pre_ping=DB_POOL_PRE_PING,
    pool_recycle=DB_POOL_RECYCLE,
)

# Создаём фабрику сессий с expire_on_commit=False
# чтобы объекты оставались доступными после коммита
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


def init_db():
    """Инициализация базы данных: создание таблиц и миграции."""
    # Создаём все таблицы
    Base.metadata.create_all(engine)
    logger.info("База данных инициализирована")
    
    # Простая миграция для добавления столбцов
    with engine.connect() as conn:
        inspector = inspect(conn)
        
        # supplement_entries.amount
        try:
            columns = {col["name"] for col in inspector.get_columns("supplement_entries")}
            if "amount" not in columns:
                conn.execute(text("ALTER TABLE supplement_entries ADD COLUMN amount FLOAT"))
                conn.commit()
                logger.info("Добавлен столбец supplement_entries.amount")
        except Exception as e:
            logger.warning(f"Ошибка при проверке supplement_entries.amount: {e}")
        
        # workouts.calories
        try:
            workout_columns = {col["name"] for col in inspector.get_columns("workouts")}
            if "calories" not in workout_columns:
                conn.execute(text("ALTER TABLE workouts ADD COLUMN calories FLOAT"))
                conn.commit()
                logger.info("Добавлен столбец workouts.calories")
        except Exception as e:
            logger.warning(f"Ошибка при проверке workouts.calories: {e}")


@contextmanager
def get_db_session():
    """
    Контекстный менеджер для работы с сессией БД.
    
    Использование:
        with get_db_session() as session:
            user = session.query(User).first()
            session.commit()
    """
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()

