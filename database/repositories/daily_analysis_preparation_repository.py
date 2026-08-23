"""Хранилище незавершённого preflight анализа дня."""
from __future__ import annotations

from datetime import date, datetime

from sqlalchemy.exc import SQLAlchemyError

from database.models import DailyAnalysisPreparationSession
from database.session import get_db_session


class DailyAnalysisPreparationRepository:
    """Не даёт потерять дату анализа при переходе между разделами."""

    @staticmethod
    def activate(user_id: str, target_date: date, origin: str = "menu") -> None:
        with get_db_session() as session:
            row = (
                session.query(DailyAnalysisPreparationSession)
                .filter(DailyAnalysisPreparationSession.user_id == str(user_id))
                .first()
            )
            if row is None:
                row = DailyAnalysisPreparationSession(user_id=str(user_id))
                session.add(row)
            row.target_date = target_date
            row.origin = (origin or "menu")[:32]
            row.active = True
            row.updated_at = datetime.utcnow()

    @staticmethod
    def get_active(user_id: str) -> DailyAnalysisPreparationSession | None:
        try:
            with get_db_session() as session:
                return (
                    session.query(DailyAnalysisPreparationSession)
                    .filter(
                        DailyAnalysisPreparationSession.user_id == str(user_id),
                        DailyAnalysisPreparationSession.active.is_(True),
                    )
                    .first()
                )
        except SQLAlchemyError:
            # Rolling deploy/isolated legacy tests may read before additive init_db().
            return None

    @staticmethod
    def complete(user_id: str, target_date: date | None = None) -> None:
        with get_db_session() as session:
            query = session.query(DailyAnalysisPreparationSession).filter(
                DailyAnalysisPreparationSession.user_id == str(user_id)
            )
            if target_date is not None:
                query = query.filter(DailyAnalysisPreparationSession.target_date == target_date)
            row = query.first()
            if row:
                row.active = False
                row.updated_at = datetime.utcnow()


daily_analysis_preparation_repository = DailyAnalysisPreparationRepository()
