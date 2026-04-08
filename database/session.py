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

        # users.target_weight / users.created_at / users.last_seen_at
        try:
            user_columns = {col["name"] for col in inspector.get_columns("users")}
        except Exception as e:
            logger.warning(f"Ошибка при чтении схемы users: {e}")
            user_columns = set()

        def _add_users_column_if_missing(column_name: str, sql_type: str, fill_now: bool = False) -> None:
            if column_name in user_columns:
                return
            try:
                conn.execute(text(f"ALTER TABLE users ADD COLUMN {column_name} {sql_type}"))
                if fill_now:
                    conn.execute(
                        text(
                            f"UPDATE users SET {column_name} = CURRENT_TIMESTAMP "
                            f"WHERE {column_name} IS NULL"
                        )
                    )
                conn.commit()
                logger.info(f"Добавлен столбец users.{column_name}")
            except Exception as e:
                logger.warning(f"Ошибка при добавлении users.{column_name}: {e}")

        _add_users_column_if_missing("target_weight", "FLOAT")
        # DATETIME не поддерживается в PostgreSQL, поэтому используем TIMESTAMP.
        _add_users_column_if_missing("created_at", "TIMESTAMP", fill_now=True)
        _add_users_column_if_missing("last_seen_at", "TIMESTAMP", fill_now=True)


        # error_logs new schema fields
        try:
            error_columns = {col["name"] for col in inspector.get_columns("error_logs")}
        except Exception as e:
            logger.warning(f"Ошибка при чтении схемы error_logs: {e}")
            error_columns = set()

        def _add_error_log_column_if_missing(column_name: str, sql_type: str) -> None:
            if column_name in error_columns:
                return
            try:
                conn.execute(text(f"ALTER TABLE error_logs ADD COLUMN {column_name} {sql_type}"))
                conn.commit()
                logger.info(f"Добавлен столбец error_logs.{column_name}")
            except Exception as e:
                logger.warning(f"Ошибка при добавлении error_logs.{column_name}: {e}")

        _add_error_log_column_if_missing("source", "VARCHAR")
        _add_error_log_column_if_missing("message", "TEXT")
        _add_error_log_column_if_missing("context", "VARCHAR")
        _add_error_log_column_if_missing("severity", "VARCHAR")


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
