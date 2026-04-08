"""Репозиторий ошибок приложения."""
from __future__ import annotations

from datetime import datetime, timedelta, date

from sqlalchemy import func

from database.models import ErrorLog
from database.session import get_db_session


class ErrorLogRepository:
    """Хранение ошибок в базе данных."""

    @staticmethod
    def log_error(
        source: str | None = None,
        error_type: str = "Exception",
        message: str | None = None,
        user_id: str | None = None,
        context: str | None = None,
        severity: str | None = "error",
        traceback_text: str | None = None,
        extra: dict | None = None,
        # backward compatible kwargs:
        error_message: str | None = None,
        module: str | None = None,
        function_name: str | None = None,
    ) -> None:
        resolved_source = source or module or "app"
        resolved_context = context or function_name
        resolved_message = message or error_message or ""

        with get_db_session() as session:
            session.add(
                ErrorLog(
                    source=resolved_source,
                    error_type=error_type,
                    message=resolved_message,
                    user_id=user_id,
                    context=resolved_context,
                    severity=severity,
                    traceback_text=traceback_text,
                    # old fields for compatibility
                    error_message=resolved_message,
                    module=module or resolved_source,
                    function_name=function_name or resolved_context,
                )
            )

    @staticmethod
    def count_today() -> int:
        start = datetime.combine(date.today(), datetime.min.time())
        with get_db_session() as session:
            return session.query(ErrorLog).filter(ErrorLog.created_at >= start).count()

    @staticmethod
    def count_7d() -> int:
        start = datetime.utcnow() - timedelta(days=7)
        with get_db_session() as session:
            return session.query(ErrorLog).filter(ErrorLog.created_at >= start).count()

    @staticmethod
    def get_recent(limit: int = 10) -> list[ErrorLog]:
        with get_db_session() as session:
            return session.query(ErrorLog).order_by(ErrorLog.created_at.desc()).limit(limit).all()

    @staticmethod
    def get_grouped_7d() -> list[tuple[str, str, int]]:
        start = datetime.utcnow() - timedelta(days=7)
        with get_db_session() as session:
            rows = (
                session.query(
                    func.coalesce(ErrorLog.source, ErrorLog.module, "app").label("src"),
                    ErrorLog.error_type,
                    func.count(ErrorLog.id).label("cnt"),
                )
                .filter(ErrorLog.created_at >= start)
                .group_by("src", ErrorLog.error_type)
                .order_by(func.count(ErrorLog.id).desc())
                .all()
            )
        return [(str(source), str(error_type), int(cnt)) for source, error_type, cnt in rows]
