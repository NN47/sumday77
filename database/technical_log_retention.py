"""TTL cleanup for technical database journals."""
from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractContextManager
from datetime import datetime, timedelta, timezone
import logging

from sqlalchemy.orm import Session

from database.models import AIUsageLog, ErrorLog, GeminiRequestLog, UserEvent
from database.session import get_db_session
from utils.log_sanitizer import safe_exception_summary

logger = logging.getLogger(__name__)

USER_EVENT_RETENTION_DAYS = 90
ERROR_LOG_RETENTION_DAYS = 30
AI_USAGE_LOG_RETENTION_DAYS = 90
GEMINI_REQUEST_LOG_RETENTION_DAYS = 30
TECHNICAL_LOG_CLEANUP_INTERVAL_SECONDS = 24 * 60 * 60

TECHNICAL_LOG_RETENTION = (
    (UserEvent, USER_EVENT_RETENTION_DAYS, False),
    (ErrorLog, ERROR_LOG_RETENTION_DAYS, False),
    (AIUsageLog, AI_USAGE_LOG_RETENTION_DAYS, True),
    (GeminiRequestLog, GEMINI_REQUEST_LOG_RETENTION_DAYS, False),
)

SessionProvider = Callable[[], AbstractContextManager[Session]]


def _normalize_utc(now: datetime | None) -> datetime:
    if now is None:
        return datetime.now(timezone.utc)
    if now.tzinfo is None:
        return now.replace(tzinfo=timezone.utc)
    return now.astimezone(timezone.utc)


def _delete_expired_records(
    model,
    cutoff: datetime,
    *,
    session_provider: SessionProvider,
) -> int:
    with session_provider() as session:
        return int(
            session.query(model)
            .filter(model.created_at < cutoff)
            .delete(synchronize_session=False)
        )


def cleanup_expired_technical_logs(
    *,
    now: datetime | None = None,
    session_provider: SessionProvider = get_db_session,
) -> dict[str, int | None]:
    """Delete expired journal rows; one table failure does not stop the others."""
    now_utc = _normalize_utc(now)
    results: dict[str, int | None] = {}

    for model, retention_days, uses_timezone in TECHNICAL_LOG_RETENTION:
        cutoff_aware = now_utc - timedelta(days=retention_days)
        cutoff = cutoff_aware if uses_timezone else cutoff_aware.replace(tzinfo=None)
        table_name = model.__tablename__
        try:
            deleted_count = _delete_expired_records(
                model,
                cutoff,
                session_provider=session_provider,
            )
            results[table_name] = deleted_count
            logger.info(
                "Technical log cleanup completed table=%s deleted_count=%s",
                table_name,
                deleted_count,
            )
        except Exception as exc:
            results[table_name] = None
            logger.error(
                "Technical log cleanup failed table=%s error_type=%s",
                table_name,
                safe_exception_summary(exc),
            )

    return results
