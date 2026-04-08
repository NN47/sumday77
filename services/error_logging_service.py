"""Сервис единообразного логирования ошибок приложения."""
from __future__ import annotations

import logging
import traceback
from typing import Any

from repositories.error_log_repository import ErrorLogRepository

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
    message = str(error)
    payload = {
        "source": source,
        "context": context,
        "user_id": user_id,
        "error_type": error_type,
        "severity": severity,
    }
    if extra:
        payload.update(extra)

    if severity.lower() == "error":
        logger.exception("Application error", extra=payload)
    else:
        logger.error("Application issue", extra=payload)

    ErrorLogRepository.log_error(
        source=source,
        error_type=error_type,
        message=message,
        user_id=user_id,
        context=context,
        severity=severity,
        traceback_text=traceback.format_exc(),
        extra=extra,
    )
