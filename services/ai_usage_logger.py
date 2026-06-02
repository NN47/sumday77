"""Безопасное логирование usage/tokens/cost для AI-провайдеров."""
from __future__ import annotations

import logging
from typing import Any

from database.models import AIUsageLog
from database.session import get_db_session

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
        with get_db_session() as session:
            session.add(
                AIUsageLog(
                    user_id=str(user_id) if user_id is not None else None,
                    provider=(provider or "").lower(),
                    feature=feature,
                    model=model,
                    status=status,
                    latency_ms=_to_int_or_none(latency_ms),
                    input_tokens=_to_int_or_none(input_tokens),
                    output_tokens=_to_int_or_none(output_tokens),
                    total_tokens=_to_int_or_none(total_tokens),
                    estimated_cost_usd=_to_float_or_none(estimated_cost_usd),
                    error_message=error_message,
                    raw_metadata=raw_metadata,
                )
            )
    except Exception as exc:  # pragma: no cover - защитное логирование
        logger.warning("Failed to log AI usage provider=%s feature=%s status=%s: %s", provider, feature, status, exc)
