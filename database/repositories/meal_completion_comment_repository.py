"""Репозиторий коротких AI-комментариев к завершённым приёмам пищи."""
from __future__ import annotations

from datetime import date
from typing import Optional

from database.models import MealCompletionComment
from database.session import get_db_session


class MealCompletionCommentRepository:
    """Работа с сохранёнными комментариями DeepSeek по приёмам пищи."""

    @staticmethod
    def get_by_meal(user_id: str, meal_id: int) -> Optional[MealCompletionComment]:
        with get_db_session() as session:
            return (
                session.query(MealCompletionComment)
                .filter(MealCompletionComment.user_id == user_id)
                .filter(MealCompletionComment.meal_id == meal_id)
                .first()
            )

    @staticmethod
    def get_success_for_date(user_id: str, target_date: date) -> list[MealCompletionComment]:
        with get_db_session() as session:
            return (
                session.query(MealCompletionComment)
                .filter(MealCompletionComment.user_id == user_id)
                .filter(MealCompletionComment.date == target_date)
                .filter(MealCompletionComment.status == "success")
                .order_by(MealCompletionComment.id.asc())
                .all()
            )

    @staticmethod
    def save(
        user_id: str,
        meal_id: int,
        target_date: date,
        meal_type: str,
        *,
        comment_text: str | None,
        model: str | None,
        status: str,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
        total_tokens: int | None = None,
        estimated_cost_usd: float | None = None,
        error_message: str | None = None,
    ) -> MealCompletionComment:
        with get_db_session() as session:
            entry = (
                session.query(MealCompletionComment)
                .filter(MealCompletionComment.user_id == user_id)
                .filter(MealCompletionComment.meal_id == meal_id)
                .first()
            )
            if entry is None:
                entry = MealCompletionComment(user_id=user_id, meal_id=meal_id, date=target_date, meal_type=meal_type)
                session.add(entry)
            entry.comment_text = comment_text
            entry.model = model
            entry.status = status
            entry.input_tokens = input_tokens
            entry.output_tokens = output_tokens
            entry.total_tokens = total_tokens
            entry.estimated_cost_usd = estimated_cost_usd
            entry.error_message = error_message
            session.commit()
            session.refresh(entry)
            return entry
