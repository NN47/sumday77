"""Репозиторий статистики запросов OpenRouter."""
from __future__ import annotations

from sqlalchemy import func

from database.models import OpenRouterRequestLog
from database.session import get_db_session
from time_utils import UTC_TZ, now_moscow


class OpenRouterRepository:
    """Хранение логов и метрик OpenRouter."""

    @staticmethod
    def log_success(*, model_name: str, input_text: str, response_text: str, duration_ms: int) -> None:
        with get_db_session() as session:
            session.add(
                OpenRouterRequestLog(
                    status="success",
                    model_name=model_name,
                    input_text=input_text,
                    response_text=response_text,
                    duration_ms=duration_ms,
                )
            )

    @staticmethod
    def log_error(*, model_name: str, input_text: str, error_message: str, duration_ms: int) -> None:
        with get_db_session() as session:
            session.add(
                OpenRouterRequestLog(
                    status="error",
                    model_name=model_name,
                    input_text=input_text,
                    error_message=error_message,
                    duration_ms=duration_ms,
                )
            )

    @staticmethod
    def get_metrics() -> dict:
        with get_db_session() as session:
            today_start_msk = now_moscow().replace(hour=0, minute=0, second=0, microsecond=0)
            today_start = today_start_msk.astimezone(UTC_TZ).replace(tzinfo=None)

            requests_today = (
                session.query(func.count(OpenRouterRequestLog.id))
                .filter(OpenRouterRequestLog.created_at >= today_start)
                .scalar()
                or 0
            )
            requests_total = session.query(func.count(OpenRouterRequestLog.id)).scalar() or 0

            success_today = (
                session.query(func.count(OpenRouterRequestLog.id))
                .filter(OpenRouterRequestLog.created_at >= today_start)
                .filter(OpenRouterRequestLog.status == "success")
                .scalar()
                or 0
            )
            success_total = (
                session.query(func.count(OpenRouterRequestLog.id))
                .filter(OpenRouterRequestLog.status == "success")
                .scalar()
                or 0
            )
            errors_today = (
                session.query(func.count(OpenRouterRequestLog.id))
                .filter(OpenRouterRequestLog.created_at >= today_start)
                .filter(OpenRouterRequestLog.status == "error")
                .scalar()
                or 0
            )
            errors_total = (
                session.query(func.count(OpenRouterRequestLog.id))
                .filter(OpenRouterRequestLog.status == "error")
                .scalar()
                or 0
            )

            last_request = (
                session.query(OpenRouterRequestLog)
                .order_by(OpenRouterRequestLog.created_at.desc(), OpenRouterRequestLog.id.desc())
                .first()
            )
            last_error = (
                session.query(OpenRouterRequestLog)
                .filter(OpenRouterRequestLog.status == "error")
                .order_by(OpenRouterRequestLog.created_at.desc(), OpenRouterRequestLog.id.desc())
                .first()
            )

            return {
                "model_name": "openrouter/free",
                "tariff": "free",
                "requests_today": int(requests_today),
                "requests_total": int(requests_total),
                "success_today": int(success_today),
                "success_total": int(success_total),
                "errors_today": int(errors_today),
                "errors_total": int(errors_total),
                "last_request_at": getattr(last_request, "created_at", None),
                "last_error_at": getattr(last_error, "created_at", None),
                "last_error_message": getattr(last_error, "error_message", None),
                "last_request": getattr(last_request, "input_text", None),
            }
