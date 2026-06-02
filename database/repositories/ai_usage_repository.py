"""Репозиторий отчетов AI usage для админ-панели."""
from __future__ import annotations

from sqlalchemy import func

from database.models import AIUsageLog
from database.session import get_db_session
from time_utils import UTC_TZ, now_moscow


class AIUsageRepository:
    """Чтение агрегатов и последних событий AI usage."""

    @staticmethod
    def get_provider_metrics(provider: str, *, limit: int = 10) -> dict:
        with get_db_session() as session:
            today_start_msk = now_moscow().replace(hour=0, minute=0, second=0, microsecond=0)
            today_start = today_start_msk.astimezone(UTC_TZ)

            base_today = session.query(AIUsageLog).filter(
                AIUsageLog.provider == provider,
                AIUsageLog.created_at >= today_start,
            )

            aggregates = (
                session.query(
                    func.count(AIUsageLog.id),
                    func.coalesce(func.sum(AIUsageLog.input_tokens), 0),
                    func.coalesce(func.sum(AIUsageLog.output_tokens), 0),
                    func.coalesce(func.sum(AIUsageLog.total_tokens), 0),
                    func.coalesce(func.sum(AIUsageLog.estimated_cost_usd), 0.0),
                )
                .filter(AIUsageLog.provider == provider, AIUsageLog.created_at >= today_start)
                .one()
            )

            success_today = base_today.filter(AIUsageLog.status == "success").count()
            errors_today = base_today.filter(AIUsageLog.status == "error").count()
            latest = (
                session.query(AIUsageLog)
                .filter(AIUsageLog.provider == provider)
                .order_by(AIUsageLog.created_at.desc(), AIUsageLog.id.desc())
                .limit(limit)
                .all()
            )

            return {
                "requests_today": int(aggregates[0] or 0),
                "success_today": int(success_today or 0),
                "errors_today": int(errors_today or 0),
                "input_tokens_today": int(aggregates[1] or 0),
                "output_tokens_today": int(aggregates[2] or 0),
                "total_tokens_today": int(aggregates[3] or 0),
                "estimated_cost_today": float(aggregates[4] or 0),
                "latest_events": latest,
            }
