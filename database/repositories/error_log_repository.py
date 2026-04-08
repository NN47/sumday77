"""Репозиторий ошибок приложения."""
from datetime import datetime, timedelta, date

from sqlalchemy import func

from database.models import ErrorLog
from database.session import get_db_session


class ErrorLogRepository:
    """Хранение ошибок в базе данных."""

    @staticmethod
    def log_error(
        error_type: str,
        error_message: str,
        user_id: str | None = None,
        module: str | None = None,
        function_name: str | None = None,
        traceback_text: str | None = None,
    ) -> None:
        with get_db_session() as session:
            session.add(
                ErrorLog(
                    user_id=user_id,
                    error_type=error_type,
                    error_message=error_message,
                    module=module,
                    function_name=function_name,
                    traceback_text=traceback_text,
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
    def get_recent_daily_analysis_errors(limit: int = 5) -> list[ErrorLog]:
        with get_db_session() as session:
            return (
                session.query(ErrorLog)
                .filter(
                    ErrorLog.function_name.in_(["analyze_activity_day", "add_activity_analysis_from_calendar"])
                )
                .order_by(ErrorLog.created_at.desc())
                .limit(limit)
                .all()
            )

    @staticmethod
    def get_grouped_7d() -> list[tuple[str, int, datetime | None]]:
        start = datetime.utcnow() - timedelta(days=7)
        with get_db_session() as session:
            rows = (
                session.query(
                    ErrorLog.error_type,
                    func.count(ErrorLog.id).label("cnt"),
                    func.max(ErrorLog.created_at).label("last_seen"),
                )
                .filter(ErrorLog.created_at >= start)
                .group_by(ErrorLog.error_type)
                .order_by(func.count(ErrorLog.id).desc())
                .all()
            )
        return [(str(error_type), int(cnt), last_seen) for error_type, cnt, last_seen in rows]
