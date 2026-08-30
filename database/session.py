"""Управление сессиями базы данных."""
from contextlib import contextmanager
from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.orm import Session, sessionmaker
from config import DATABASE_URL, DB_POOL_PRE_PING, DB_POOL_RECYCLE
from database.models import Base
import logging
from utils.log_sanitizer import safe_exception_summary
from user_operation_guard import user_operation_guard

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

_USER_WRITE_PERMITS_KEY = "sumday77_user_write_permits"
_USER_WRITE_IDS_KEY = "sumday77_user_write_ids"


@event.listens_for(Session, "before_flush")
def _guard_user_writes(session, _flush_context, _instances) -> None:
    """Keep user writes serialized with deletion until commit or rollback."""
    user_ids = {
        str(user_id)
        for instance in (*session.new, *session.dirty)
        if (user_id := getattr(instance, "user_id", None)) is not None
    }
    already_guarded = session.info.setdefault(_USER_WRITE_IDS_KEY, set())
    missing_user_ids = user_ids - already_guarded
    if not missing_user_ids:
        return

    permits = user_operation_guard.acquire_write_permits(missing_user_ids)
    session.info.setdefault(_USER_WRITE_PERMITS_KEY, []).extend(permits)
    already_guarded.update(missing_user_ids)


def _release_user_write_permits(session) -> None:
    permits = session.info.pop(_USER_WRITE_PERMITS_KEY, [])
    session.info.pop(_USER_WRITE_IDS_KEY, None)
    user_operation_guard.release_write_permits(permits)


@event.listens_for(Session, "after_commit")
def _release_user_writes_after_commit(session) -> None:
    _release_user_write_permits(session)


@event.listens_for(Session, "after_rollback")
def _release_user_writes_after_rollback(session) -> None:
    _release_user_write_permits(session)


def init_db():
    """Инициализация базы данных: создание таблиц и миграции."""
    # Создаём все таблицы
    Base.metadata.create_all(engine)
    from database.legal_migration import migrate_legal_metadata
    migrate_legal_metadata(engine)
    logger.info("База данных инициализирована")

    # Новый справочник физической активности синхронизируется отдельно от
    # пользовательских записей. Повторный запуск безопасен.
    from database.repositories.activity_repository import ActivityRepository

    @contextmanager
    def _catalog_session():
        catalog_session = Session(bind=engine)
        try:
            yield catalog_session
            catalog_session.commit()
        except Exception:
            catalog_session.rollback()
            raise
        finally:
            catalog_session.close()

    ActivityRepository.seed_catalog(session_provider=_catalog_session)
    
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
            logger.warning("Ошибка при проверке supplement_entries.amount error_type=%s", safe_exception_summary(e))
        
        # workouts activity input fields
        try:
            workout_columns = {col["name"] for col in inspector.get_columns("workouts")}

            def _add_workout_column_if_missing(column_name: str, sql_type: str) -> None:
                if column_name in workout_columns:
                    return
                conn.execute(text(f"ALTER TABLE workouts ADD COLUMN {column_name} {sql_type}"))
                conn.commit()
                logger.info(f"Добавлен столбец workouts.{column_name}")

            _add_workout_column_if_missing("calories", "FLOAT")
            _add_workout_column_if_missing("input_method", "VARCHAR")
            _add_workout_column_if_missing("duration_minutes", "FLOAT")
            _add_workout_column_if_missing("distance_km", "FLOAT")
            _add_workout_column_if_missing("jumps_count", "INTEGER")
            _add_workout_column_if_missing("working_weight", "FLOAT")
            count_column = next((col for col in inspector.get_columns("workouts") if col["name"] == "count"), None)
            if count_column and "INT" in str(count_column.get("type", "")).upper():
                try:
                    conn.execute(text("ALTER TABLE workouts ALTER COLUMN count TYPE FLOAT USING count::float"))
                    conn.commit()
                    logger.info("Изменён тип столбца workouts.count на FLOAT")
                except Exception as type_error:
                    logger.warning(
                        "Не удалось изменить тип workouts.count на FLOAT error_type=%s",
                        safe_exception_summary(type_error),
                    )
        except Exception as e:
            logger.warning("Ошибка при проверке workouts fields error_type=%s", safe_exception_summary(e))

        # users.target_weight / users.created_at / users.last_seen_at / users.age_verified
        try:
            user_columns = {col["name"] for col in inspector.get_columns("users")}
        except Exception as e:
            logger.warning("Ошибка при чтении схемы users error_type=%s", safe_exception_summary(e))
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
                logger.warning(
                    "Ошибка при добавлении users.%s error_type=%s",
                    column_name,
                    safe_exception_summary(e),
                )

        _add_users_column_if_missing("target_weight", "FLOAT")
        _add_users_column_if_missing("timezone", "VARCHAR DEFAULT 'Europe/Moscow' NOT NULL")
        _add_users_column_if_missing("notifications_enabled", "BOOLEAN DEFAULT TRUE NOT NULL")
        # Existing rows deliberately remain NULL until the user confirms 18+ once.
        _add_users_column_if_missing("age_verified", "BOOLEAN")
        # DATETIME не поддерживается в PostgreSQL, поэтому используем TIMESTAMP.
        _add_users_column_if_missing("created_at", "TIMESTAMP", fill_now=True)
        _add_users_column_if_missing("last_seen_at", "TIMESTAMP", fill_now=True)

        # AI tariffs and quota ledger. New tables are additive and do not touch
        # existing diary data; explicit checkfirst keeps old deployments safe.
        try:
            for table_name in (
                "user_plan_assignments",
                "ai_quota_counters",
                "ai_attempt_counters",
                "ai_global_daily_counters",
                "ai_quota_operations",
                "ai_quota_active_locks",
                "daily_analysis_preparation_sessions",
            ):
                Base.metadata.tables[table_name].create(bind=engine, checkfirst=True)
        except Exception as e:
            logger.error(
                "Ошибка при создании таблиц AI-квот error_type=%s",
                safe_exception_summary(e),
            )
            raise

        # Metadata of newly generated daily analyses. Existing rows deliberately
        # remain nullable and continue to open through the legacy fields.
        try:
            analysis_columns = {
                col["name"] for col in inspector.get_columns("activity_analysis_entries")
            }
        except Exception as e:
            logger.warning(
                "Ошибка при чтении activity_analysis_entries error_type=%s",
                safe_exception_summary(e),
            )
            analysis_columns = set()

        def _add_analysis_column_if_missing(column_name: str, sql_type: str) -> None:
            if column_name in analysis_columns:
                return
            try:
                conn.execute(
                    text(
                        f"ALTER TABLE activity_analysis_entries "
                        f"ADD COLUMN {column_name} {sql_type}"
                    )
                )
                conn.commit()
                logger.info("Добавлен столбец activity_analysis_entries.%s", column_name)
            except Exception as e:
                logger.warning(
                    "Ошибка при добавлении activity_analysis_entries.%s error_type=%s",
                    column_name,
                    safe_exception_summary(e),
                )

        _add_analysis_column_if_missing("analyzed_at", "TIMESTAMP")
        _add_analysis_column_if_missing("status", "VARCHAR(24) DEFAULT 'success'")
        _add_analysis_column_if_missing("plan_key", "VARCHAR")
        _add_analysis_column_if_missing("quota_request_id", "VARCHAR(160)")
        _add_analysis_column_if_missing("data_snapshot_hash", "VARCHAR(64)")
        try:
            conn.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS ix_activity_analysis_entries_quota_request_id "
                    "ON activity_analysis_entries (quota_request_id)"
                )
            )
            conn.commit()
        except Exception as e:
            logger.warning(
                "Ошибка при создании индекса анализа дня error_type=%s",
                safe_exception_summary(e),
            )

        try:
            operation_columns = {
                col["name"] for col in inspect(conn).get_columns("ai_quota_operations")
            }
            if "provider_attempt_count" not in operation_columns:
                conn.execute(
                    text(
                        "ALTER TABLE ai_quota_operations "
                        "ADD COLUMN provider_attempt_count INTEGER DEFAULT 0 NOT NULL"
                    )
                )
                conn.commit()
        except Exception as e:
            logger.warning(
                "Ошибка при добавлении ai_quota_operations.provider_attempt_count error_type=%s",
                safe_exception_summary(e),
            )



        # meal_completion_comments table (created by metadata for new DBs)
        try:
            Base.metadata.tables["meal_completion_comments"].create(bind=engine, checkfirst=True)
            comment_columns = {
                col["name"] for col in inspect(conn).get_columns("meal_completion_comments")
            }
            if "quota_request_id" not in comment_columns:
                conn.execute(
                    text(
                        "ALTER TABLE meal_completion_comments "
                        "ADD COLUMN quota_request_id VARCHAR(160)"
                    )
                )
                conn.commit()
            conn.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS ix_meal_completion_comments_quota_request_id "
                    "ON meal_completion_comments (quota_request_id)"
                )
            )
            conn.commit()
        except Exception as e:
            logger.warning(
                "Ошибка при создании meal_completion_comments error_type=%s",
                safe_exception_summary(e),
            )

        # saved_products table: nullable product reference data for existing users.
        # create_all handles new databases; checkfirst keeps this migration safe and
        # idempotent for PostgreSQL installations that already contain meal history.
        try:
            Base.metadata.tables["saved_products"].create(bind=engine, checkfirst=True)
        except Exception as e:
            logger.error(
                "Ошибка при создании saved_products error_type=%s",
                safe_exception_summary(e),
            )
            raise

        # Reusable dishes and their normalized ingredient snapshots.
        # Tables are additive: existing meal history remains untouched.
        try:
            Base.metadata.tables["dishes"].create(bind=engine, checkfirst=True)
            Base.metadata.tables["dish_ingredients"].create(bind=engine, checkfirst=True)
        except Exception as e:
            logger.error(
                "Ошибка при создании таблиц блюд error_type=%s",
                safe_exception_summary(e),
            )
            raise

        # error_logs new schema fields
        try:
            error_columns = {col["name"] for col in inspector.get_columns("error_logs")}
        except Exception as e:
            logger.warning("Ошибка при чтении схемы error_logs error_type=%s", safe_exception_summary(e))
            error_columns = set()

        def _add_error_log_column_if_missing(column_name: str, sql_type: str) -> None:
            if column_name in error_columns:
                return
            try:
                conn.execute(text(f"ALTER TABLE error_logs ADD COLUMN {column_name} {sql_type}"))
                conn.commit()
                logger.info(f"Добавлен столбец error_logs.{column_name}")
            except Exception as e:
                logger.warning(
                    "Ошибка при добавлении error_logs.%s error_type=%s",
                    column_name,
                    safe_exception_summary(e),
                )

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
            logger.warning("Ошибка при проверке kbju_settings.gender error_type=%s", safe_exception_summary(e))

        # meals meal metadata, idempotency and optional dish snapshot linkage
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

            if "save_token" not in meal_columns:
                conn.execute(text("ALTER TABLE meals ADD COLUMN save_token VARCHAR(64)"))
                conn.commit()
                logger.info("Добавлен столбец meals.save_token")

            if "entry_kind" not in meal_columns:
                conn.execute(
                    text(
                        "ALTER TABLE meals ADD COLUMN entry_kind "
                        "VARCHAR DEFAULT 'products' NOT NULL"
                    )
                )
                conn.commit()
                logger.info("Добавлен столбец meals.entry_kind")
            else:
                conn.execute(
                    text(
                        "UPDATE meals SET entry_kind = 'products' "
                        "WHERE entry_kind IS NULL OR entry_kind = ''"
                    )
                )
                conn.commit()

            if "dish_id" not in meal_columns:
                conn.execute(text("ALTER TABLE meals ADD COLUMN dish_id INTEGER"))
                conn.commit()
                logger.info("Добавлен столбец meals.dish_id")

            if "dish_name_snapshot" not in meal_columns:
                conn.execute(text("ALTER TABLE meals ADD COLUMN dish_name_snapshot VARCHAR(80)"))
                conn.commit()
                logger.info("Добавлен столбец meals.dish_name_snapshot")

            if "entry_source" not in meal_columns:
                conn.execute(text("ALTER TABLE meals ADD COLUMN entry_source VARCHAR"))
                conn.commit()
                logger.info("Добавлен столбец meals.entry_source")

            if conn.dialect.name == "postgresql":
                dish_foreign_key_exists = any(
                    "dish_id" in (foreign_key.get("constrained_columns") or [])
                    for foreign_key in inspect(conn).get_foreign_keys("meals")
                )
                if not dish_foreign_key_exists:
                    conn.execute(
                        text(
                            "ALTER TABLE meals ADD CONSTRAINT fk_meals_dish_id "
                            "FOREIGN KEY (dish_id) REFERENCES dishes(id) ON DELETE SET NULL"
                        )
                    )
                    conn.commit()
                    logger.info("Добавлен внешний ключ meals.dish_id -> dishes.id")

            conn.execute(
                text(
                    "CREATE UNIQUE INDEX IF NOT EXISTS uq_meals_save_token "
                    "ON meals (save_token)"
                )
            )
            conn.execute(text("CREATE INDEX IF NOT EXISTS ix_meals_entry_kind ON meals (entry_kind)"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS ix_meals_dish_id ON meals (dish_id)"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS ix_meals_entry_source ON meals (entry_source)"))
            conn.commit()
        except Exception as e:
            logger.error("Ошибка при проверке meals fields error_type=%s", safe_exception_summary(e))
            raise

        # ai_usage_logs универсальная таблица usage/tokens/cost
        try:
            if "ai_usage_logs" not in inspector.get_table_names():
                Base.metadata.tables["ai_usage_logs"].create(bind=conn, checkfirst=True)
                logger.info("Создана таблица ai_usage_logs")
        except Exception as e:
            logger.warning("Ошибка при проверке ai_usage_logs error_type=%s", safe_exception_summary(e))

        # gemini_accounts расширенные статусы и метрики
        try:
            gemini_columns = {col["name"] for col in inspector.get_columns("gemini_accounts")}
        except Exception as e:
            logger.warning("Ошибка при чтении схемы gemini_accounts error_type=%s", safe_exception_summary(e))
            gemini_columns = set()

        def _add_gemini_account_column_if_missing(column_name: str, sql_type: str) -> None:
            if column_name in gemini_columns:
                return
            try:
                conn.execute(text(f"ALTER TABLE gemini_accounts ADD COLUMN {column_name} {sql_type}"))
                conn.commit()
                logger.info(f"Добавлен столбец gemini_accounts.{column_name}")
            except Exception as e:
                logger.warning(
                    "Ошибка при добавлении gemini_accounts.%s error_type=%s",
                    column_name,
                    safe_exception_summary(e),
                )

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
            logger.warning(
                "Ошибка при проверке gemini_request_logs event columns error_type=%s",
                safe_exception_summary(e),
            )


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
