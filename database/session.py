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

        # kbju_settings.gender
        try:
            kbju_columns = {col["name"] for col in inspector.get_columns("kbju_settings")}
            if "gender" not in kbju_columns:
                conn.execute(text("ALTER TABLE kbju_settings ADD COLUMN gender VARCHAR"))
                conn.commit()
                logger.info("Добавлен столбец kbju_settings.gender")
        except Exception as e:
            logger.warning(f"Ошибка при проверке kbju_settings.gender: {e}")

        # meals.meal_type
        try:
            meal_columns = {col["name"] for col in inspector.get_columns("meals")}
            if "meal_type" not in meal_columns:
                conn.execute(text("ALTER TABLE meals ADD COLUMN meal_type VARCHAR"))
                conn.execute(text("UPDATE meals SET meal_type = 'snack' WHERE meal_type IS NULL"))
                conn.commit()
                logger.info("Добавлен столбец meals.meal_type")
            else:
                conn.execute(text("UPDATE meals SET meal_type = 'snack' WHERE meal_type IS NULL OR meal_type = ''"))
                conn.commit()

            if "is_manually_corrected" not in meal_columns:
                conn.execute(text("ALTER TABLE meals ADD COLUMN is_manually_corrected BOOLEAN DEFAULT FALSE"))
                conn.execute(text("UPDATE meals SET is_manually_corrected = FALSE WHERE is_manually_corrected IS NULL"))
                conn.commit()
                logger.info("Добавлен столбец meals.is_manually_corrected")
            else:
                conn.execute(text("UPDATE meals SET is_manually_corrected = FALSE WHERE is_manually_corrected IS NULL"))
                conn.commit()
        except Exception as e:
            logger.warning(f"Ошибка при проверке meals.meal_type: {e}")

        # gemini_accounts расширенные статусы и метрики
        try:
            gemini_columns = {col["name"] for col in inspector.get_columns("gemini_accounts")}
        except Exception as e:
            logger.warning(f"Ошибка при чтении схемы gemini_accounts: {e}")
            gemini_columns = set()

        def _add_gemini_account_column_if_missing(column_name: str, sql_type: str) -> None:
            if column_name in gemini_columns:
                return
            try:
                conn.execute(text(f"ALTER TABLE gemini_accounts ADD COLUMN {column_name} {sql_type}"))
                conn.commit()
                logger.info(f"Добавлен столбец gemini_accounts.{column_name}")
            except Exception as e:
                logger.warning(f"Ошибка при добавлении gemini_accounts.{column_name}: {e}")

        _add_gemini_account_column_if_missing("temporary_failover_count", "INTEGER DEFAULT 0 NOT NULL")
        _add_gemini_account_column_if_missing("temporary_errors_count", "INTEGER DEFAULT 0 NOT NULL")
        _add_gemini_account_column_if_missing("quota_errors_count", "INTEGER DEFAULT 0 NOT NULL")
        _add_gemini_account_column_if_missing("auth_errors_count", "INTEGER DEFAULT 0 NOT NULL")
        _add_gemini_account_column_if_missing("unknown_errors_count", "INTEGER DEFAULT 0 NOT NULL")
        _add_gemini_account_column_if_missing("status", "VARCHAR DEFAULT 'active' NOT NULL")
        _add_gemini_account_column_if_missing("disabled_reason", "VARCHAR")
        _add_gemini_account_column_if_missing("rate_limited_until", "TIMESTAMP")
        _add_gemini_account_column_if_missing("temporary_unavailable_until", "TIMESTAMP")
        _add_gemini_account_column_if_missing("last_error_type", "VARCHAR")

        # gemini_request_logs event_type / reason
        try:
            gemini_log_columns = {col["name"] for col in inspector.get_columns("gemini_request_logs")}
            if "event_type" not in gemini_log_columns:
                conn.execute(text("ALTER TABLE gemini_request_logs ADD COLUMN event_type VARCHAR"))
                conn.commit()
                logger.info("Добавлен столбец gemini_request_logs.event_type")
            if "reason" not in gemini_log_columns:
                conn.execute(text("ALTER TABLE gemini_request_logs ADD COLUMN reason VARCHAR"))
                conn.commit()
                logger.info("Добавлен столбец gemini_request_logs.reason")
        except Exception as e:
            logger.warning(f"Ошибка при проверке gemini_request_logs event columns: {e}")


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
