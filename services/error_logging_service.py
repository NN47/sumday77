"""Сервис единообразного логирования ошибок приложения."""
from __future__ import annotations

import logging
from typing import Any

from repositories.error_log_repository import ErrorLogRepository
from utils.log_sanitizer import (
    safe_exception_summary,
    safe_traceback,
    sanitize_log_label,
    sanitize_metadata,
)

logger = logging.getLogger(__name__)


def log_app_error(
    source: str,
    error: Exception,
    user_id: str | None = None,
    context: str | None = None,
    severity: str = "error",
    extra: dict[str, Any] | None = None,
) -> None:
    """Логирует ошибку в logger и сохраняет запись в БД."""
    error_type = type(error).__name__
    safe_source = sanitize_log_label(source, fallback="app") or "app"
    safe_context = sanitize_log_label(context)
    payload = {
        "source": safe_source,
        "context": safe_context,
        "error_type": error_type,
        "severity": severity,
    }
    payload.update(sanitize_metadata(extra) or {})

    logger.log(
        logging.ERROR if severity.lower() == "error" else logging.WARNING,
        "Application issue source=%s context=%s error_type=%s",
        safe_source,
        safe_context or "none",
        error_type,
        exc_info=(type(error), error, error.__traceback__) if error.__traceback__ else None,
        extra=payload,
    )

    ErrorLogRepository.log_error(
        source=safe_source,
        error_type=error_type,
        message=safe_exception_summary(error),
        user_id=user_id,
        context=safe_context,
        severity=severity,
        traceback_text=safe_traceback(error),
        extra=sanitize_metadata(extra),
    )
