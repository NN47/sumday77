"""Атомарное удаление аккаунта и связанных пользовательских данных."""

import logging
from collections.abc import Callable
from contextlib import AbstractContextManager

from sqlalchemy import or_
from sqlalchemy.orm import Session

from database.models import (
    AIUsageLog,
    ActivityAnalysisEntry,
    CustomWorkoutExercise,
    ErrorLog,
    EveningAnalysisNotificationState,
    KbjuSettings,
    Meal,
    MealCompletionComment,
    Measurement,
    NoteEntry,
    Procedure,
    QuickWaterMessage,
    SavedProduct,
    Supplement,
    SupplementEntry,
    SupplementNotificationState,
    SupportMessage,
    User,
    UserEvent,
    WaterEntry,
    Weight,
    WellbeingEntry,
    Workout,
)
from database.session import get_db_session

logger = logging.getLogger(__name__)


# Все ORM-модели, в которых user_id однозначно связывает запись с пользователем.
# Дочерние записи идут раньше логически связанных родительских сущностей.
DELETE_ORDER = (
    MealCompletionComment,
    SupplementNotificationState,
    SupplementEntry,
    Workout,
    CustomWorkoutExercise,
    Weight,
    Measurement,
    Meal,
    SavedProduct,
    KbjuSettings,
    Supplement,
    Procedure,
    WaterEntry,
    QuickWaterMessage,
    WellbeingEntry,
    NoteEntry,
    ActivityAnalysisEntry,
    EveningAnalysisNotificationState,
    UserEvent,
    SupportMessage,
    ErrorLog,
    AIUsageLog,
    User,
)

# Один и тот же централизованный перечень используется для удаления и проверки.
USER_LINKED_MODELS = DELETE_ORDER


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


def delete_user_account_data(session: Session, user_id: str) -> dict[str, int]:
    """Удаляет данные пользователя в текущей транзакции без её фиксации."""
    normalized_user_id = str(user_id)
    meal_ids = _owned_ids(session, Meal, normalized_user_id)
    supplement_ids = _owned_ids(session, Supplement, normalized_user_id)

    filters = {
        model: model.user_id == normalized_user_id
        for model in USER_LINKED_MODELS
    }
    filters[MealCompletionComment] = _linked_filter(
        MealCompletionComment.user_id == normalized_user_id,
        MealCompletionComment.meal_id,
        meal_ids,
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
    for model in DELETE_ORDER:
        deleted_counts[model.__tablename__] = (
            session.query(model)
            .filter(filters[model])
            .delete(synchronize_session=False)
        )

    session.flush()

    remaining_tables = [
        model.__tablename__
        for model in USER_LINKED_MODELS
        if session.query(model.id).filter(filters[model]).first() is not None
    ]
    if remaining_tables:
        raise AccountDeletionVerificationError(
            "После удаления остались пользовательские записи в таблицах: "
            + ", ".join(remaining_tables)
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
