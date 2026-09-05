"""Безопасное логирование usage/tokens/cost для AI-провайдеров."""
from __future__ import annotations

import logging
from typing import Any

from database.models import AIUsageLog
from database.session import get_db_session
from services.openai_token_budget_service import finalize_active_openai_usage
from utils.log_sanitizer import (
    safe_error_code,
    safe_exception_summary,
    sanitize_identifier,
    sanitize_log_label,
    sanitize_metadata,
    sanitize_user_id,
)

logger = logging.getLogger(__name__)

# ВАЖНО: цены нужно обновлять при изменении тарифов провайдеров.
# Значения указаны в USD за 1_000_000 токенов.
# OpenAI gpt-4.1-mini: https://platform.openai.com/docs/pricing/ — input $0.40, output $1.60.
# DeepSeek deepseek-chat: https://api-docs.deepseek.com/quick_start/pricing-details-usd —
# input cache miss $0.27, output $1.10. Usage API не разделяет cache hit/miss здесь,
# поэтому считаем консервативно по cache miss.
AI_TOKEN_PRICES_USD_PER_1M: dict[tuple[str, str], dict[str, float]] = {
    ("openai", "gpt-4.1-mini"): {"input": 0.40, "output": 1.60},
    ("deepseek", "deepseek-chat"): {"input": 0.27, "output": 1.10},
}


def _to_int_or_none(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _to_float_or_none(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def calculate_ai_cost(
    provider: str,
    model: str,
    input_tokens: int | None,
    output_tokens: int | None,
) -> float | None:
    """Возвращает примерную стоимость запроса в USD или None, если цена неизвестна."""
    prices = AI_TOKEN_PRICES_USD_PER_1M.get(((provider or "").lower(), model or ""))
    if not prices:
        return None

    input_count = _to_int_or_none(input_tokens) or 0
    output_count = _to_int_or_none(output_tokens) or 0
    return (input_count * prices["input"] + output_count * prices["output"]) / 1_000_000


def log_ai_usage(
    provider: str,
    feature: str,
    model: str,
    status: str,
    user_id: str | int | None = None,
    latency_ms: int | None = None,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
    total_tokens: int | None = None,
    estimated_cost_usd: float | None = None,
    error_message: str | None = None,
    raw_metadata: dict | None = None,
) -> None:
    """Безопасно пишет AI usage в БД; сбой логирования не ломает основной сценарий."""
    try:
        values = {
            "user_id": sanitize_user_id(user_id),
            "provider": sanitize_log_label(provider, fallback="unknown") or "unknown",
            "feature": sanitize_log_label(feature, fallback="unknown") or "unknown",
            "model": sanitize_identifier(model, fallback="unknown") or "unknown",
            "status": sanitize_log_label(status, fallback="unknown") or "unknown",
            "latency_ms": _to_int_or_none(latency_ms),
            "input_tokens": _to_int_or_none(input_tokens),
            "output_tokens": _to_int_or_none(output_tokens),
            "total_tokens": _to_int_or_none(total_tokens),
            "estimated_cost_usd": _to_float_or_none(estimated_cost_usd),
            "error_message": safe_error_code(error_message),
            "raw_metadata": sanitize_metadata(raw_metadata),
        }
        if values["provider"] == "openai" and finalize_active_openai_usage(**values):
            return
        with get_db_session() as session:
            session.add(AIUsageLog(**values))
    except Exception as exc:  # pragma: no cover - защитное логирование
        logger.warning(
            "Failed to log AI usage provider=%s feature=%s status=%s error_type=%s",
            sanitize_log_label(provider, fallback="unknown"),
            sanitize_log_label(feature, fallback="unknown"),
            sanitize_log_label(status, fallback="unknown"),
            safe_exception_summary(exc),
        )
