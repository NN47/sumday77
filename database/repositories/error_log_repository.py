"""Репозиторий ошибок приложения."""
from __future__ import annotations

from datetime import datetime, timedelta, date

from sqlalchemy import func

from database.models import ErrorLog
from database.session import get_db_session
from utils.log_sanitizer import (
    safe_error_code,
    sanitize_identifier,
    sanitize_log_label,
    sanitize_traceback_text,
    sanitize_user_id,
)


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
        resolved_source = sanitize_log_label(source or module, fallback="app") or "app"
        resolved_context = sanitize_log_label(context or function_name)
        resolved_message = safe_error_code(message or error_message)
        resolved_error_type = sanitize_identifier(error_type, fallback="Exception") or "Exception"
        resolved_severity = sanitize_log_label(severity, fallback="error") or "error"
        resolved_module = sanitize_log_label(module or resolved_source, fallback="app") or "app"
        resolved_function = sanitize_log_label(function_name or resolved_context)

        with get_db_session() as session:
            session.add(
                ErrorLog(
                    source=resolved_source,
                    error_type=resolved_error_type,
                    message=resolved_message,
                    user_id=sanitize_user_id(user_id),
                    context=resolved_context,
                    severity=resolved_severity,
                    traceback_text=sanitize_traceback_text(traceback_text),
                    # old fields for compatibility
                    error_message=resolved_message,
                    module=resolved_module,
                    function_name=resolved_function,
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
