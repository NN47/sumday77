"""Атомарное удаление аккаунта и связанных пользовательских данных."""

import logging
from collections.abc import Callable
from contextlib import AbstractContextManager

from sqlalchemy import MetaData, Table, inspect, or_, select
from sqlalchemy.orm import Session

from database.models import (
    AIUsageLog,
    AIAttemptCounter,
    AIQuotaActiveLock,
    AIQuotaCounter,
    AIQuotaOperation,
    ActivityAnalysisEntry,
    DailySteps,
    CustomWorkoutExercise,
    Dish,
    DishIngredient,
    ErrorLog,
    EveningAnalysisNotificationState,
    DailyAnalysisPreparationSession,
    KbjuSettings,
    Meal,
    MealCompletionComment,
    Measurement,
    NoteEntry,
    QuickWaterMessage,
    SavedProduct,
    Supplement,
    SupplementEntry,
    SupplementNotificationState,
    SupportMessage,
    User,
    UserEvent,
    UserPlanAssignment,
    WaterEntry,
    Weight,
    Workout,
    TimedActivityEntry,
    WorkoutSession,
    WorkoutSessionExercise,
    WorkoutSet,
)
from database.session import get_db_session

logger = logging.getLogger(__name__)


# Все ORM-модели, в которых user_id однозначно связывает запись с пользователем.
# Дочерние записи идут раньше логически связанных родительских сущностей.
DELETE_ORDER = (
    AIQuotaActiveLock,
    AIQuotaOperation,
    AIAttemptCounter,
    AIQuotaCounter,
    UserPlanAssignment,
    DailyAnalysisPreparationSession,
    MealCompletionComment,
    SupplementNotificationState,
    SupplementEntry,
    WorkoutSet,
    WorkoutSessionExercise,
    WorkoutSession,
    TimedActivityEntry,
    DailySteps,
    Workout,
    CustomWorkoutExercise,
    Weight,
    Measurement,
    Meal,
    DishIngredient,
    Dish,
    SavedProduct,
    KbjuSettings,
    Supplement,
    WaterEntry,
    QuickWaterMessage,
    NoteEntry,
    ActivityAnalysisEntry,
    EveningAnalysisNotificationState,
    UserEvent,
    SupportMessage,
    ErrorLog,
    AIUsageLog,
    User,
)

# Модели с прямым ``user_id`` используются также общим completeness-тестом.
# ``DishIngredient`` принадлежит пользователю через Dish и удаляется отдельным
# фильтром ниже.
USER_LINKED_MODELS = tuple(model for model in DELETE_ORDER if model is not DishIngredient)

# Removed features may leave tables in an existing deployment. They are never
# created or used by current code, but account deletion must still erase rows
# that belong to the requesting user until the table is dropped operationally.
LEGACY_USER_DATA_TABLES = ("procedures",)


class AccountDeletionVerificationError(RuntimeError):
    """Удаление не прошло внутреннюю проверку полноты."""


def _owned_ids(session: Session, model: type, user_id: str) -> tuple[int, ...]:
    return tuple(
        row_id
        for (row_id,) in session.query(model.id).filter(model.user_id == user_id).all()
    )


def _linked_filter(direct_filter, linked_column, linked_ids: tuple[int, ...]):
    if not linked_ids:
        return direct_filter
    return or_(direct_filter, linked_column.in_(linked_ids))


def _reflect_legacy_user_tables(session: Session) -> tuple[Table, ...]:
    connection = session.connection()
    inspector = inspect(connection)
    tables: list[Table] = []
    for table_name in LEGACY_USER_DATA_TABLES:
        if not inspector.has_table(table_name):
            continue
        table = Table(table_name, MetaData(), autoload_with=connection)
        if "user_id" not in table.c:
            raise AccountDeletionVerificationError(
                f"В устаревшей таблице {table_name} отсутствует user_id"
            )
        tables.append(table)
    return tuple(tables)


def delete_user_account_data(session: Session, user_id: str) -> dict[str, int]:
    """Удаляет данные пользователя в текущей транзакции без её фиксации."""
    normalized_user_id = str(user_id)
    meal_ids = _owned_ids(session, Meal, normalized_user_id)
    dish_ids = _owned_ids(session, Dish, normalized_user_id)
    supplement_ids = _owned_ids(session, Supplement, normalized_user_id)
    legacy_tables = _reflect_legacy_user_tables(session)

    filters = {
        model: model.user_id == normalized_user_id
        for model in USER_LINKED_MODELS
    }
    filters[MealCompletionComment] = _linked_filter(
        MealCompletionComment.user_id == normalized_user_id,
        MealCompletionComment.meal_id,
        meal_ids,
    )
    filters[DishIngredient] = (
        DishIngredient.dish_id.in_(dish_ids)
        if dish_ids
        else DishIngredient.id.in_([])
    )
    filters[SupplementEntry] = _linked_filter(
        SupplementEntry.user_id == normalized_user_id,
        SupplementEntry.supplement_id,
        supplement_ids,
    )
    filters[SupplementNotificationState] = _linked_filter(
        SupplementNotificationState.user_id == normalized_user_id,
        SupplementNotificationState.supplement_id,
        supplement_ids,
    )

    deleted_counts: dict[str, int] = {}
    for table in legacy_tables:
        result = session.execute(
            table.delete().where(table.c.user_id == normalized_user_id)
        )
        deleted_counts[table.name] = max(int(result.rowcount or 0), 0)

    for model in DELETE_ORDER:
        deleted_counts[model.__tablename__] = (
            session.query(model)
            .filter(filters[model])
            .delete(synchronize_session=False)
        )

    session.flush()

    remaining_tables = [
        model.__tablename__
        for model in DELETE_ORDER
        if session.query(model.id).filter(filters[model]).first() is not None
    ]
    if remaining_tables:
        raise AccountDeletionVerificationError(
            "После удаления остались пользовательские записи в таблицах: "
            + ", ".join(remaining_tables)
        )

    remaining_legacy_tables = [
        table.name
        for table in legacy_tables
        if session.execute(
            select(table.c.user_id)
            .where(table.c.user_id == normalized_user_id)
            .limit(1)
        ).first()
        is not None
    ]
    if remaining_legacy_tables:
        raise AccountDeletionVerificationError(
            "После удаления остались пользовательские записи в устаревших таблицах: "
            + ", ".join(remaining_legacy_tables)
        )

    return deleted_counts


SessionProvider = Callable[[], AbstractContextManager[Session]]


def delete_user_account(
    user_id: str,
    *,
    session_provider: SessionProvider = get_db_session,
) -> bool:
    """Удаляет аккаунт одной транзакцией и сообщает об успехе операции."""
    normalized_user_id = str(user_id)
    try:
        with session_provider() as session:
            delete_user_account_data(session, normalized_user_id)
    except Exception:
        logger.exception("Account deletion failed")
        return False

    logger.info("Account deletion completed successfully")
    return True
