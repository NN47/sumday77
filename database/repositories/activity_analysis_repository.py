"""Репозиторий для сохранённых ИИ-анализов деятельности."""
from datetime import date
from typing import Optional

from database.models import ActivityAnalysisEntry
from database.session import get_db_session


class ActivityAnalysisRepository:
    """Репозиторий сохранённых ИИ-анализов деятельности."""

    @staticmethod
    def create_entry(user_id: str, analysis_text: str, entry_date: date, source: str = "manual") -> int:
        """Создаёт запись анализа."""
        with get_db_session() as session:
            entry = ActivityAnalysisEntry(
                user_id=str(user_id),
                analysis_text=analysis_text,
                date=entry_date,
                source=source,
            )
            session.add(entry)
            session.commit()
            session.refresh(entry)
            return entry.id

    @staticmethod
    def get_entries_for_date(user_id: str, target_date: date) -> list[ActivityAnalysisEntry]:
        """Возвращает анализы за конкретный день."""
        with get_db_session() as session:
            return (
                session.query(ActivityAnalysisEntry)
                .filter(ActivityAnalysisEntry.user_id == str(user_id))
                .filter(ActivityAnalysisEntry.date == target_date)
                .order_by(ActivityAnalysisEntry.created_at.asc())
                .all()
            )

    @staticmethod
    def get_entry_by_id(entry_id: int, user_id: str) -> Optional[ActivityAnalysisEntry]:
        """Возвращает анализ по ID пользователя."""
        with get_db_session() as session:
            return (
                session.query(ActivityAnalysisEntry)
                .filter(ActivityAnalysisEntry.id == entry_id)
                .filter(ActivityAnalysisEntry.user_id == str(user_id))
                .first()
            )

    @staticmethod
    def delete_entry(entry_id: int, user_id: str) -> bool:
        """Удаляет анализ по ID пользователя."""
        with get_db_session() as session:
            entry = (
                session.query(ActivityAnalysisEntry)
                .filter(ActivityAnalysisEntry.id == entry_id)
                .filter(ActivityAnalysisEntry.user_id == str(user_id))
                .first()
            )
            if not entry:
                return False
            session.delete(entry)
            session.commit()
            return True

    @staticmethod
    def get_month_days(user_id: str, year: int, month: int) -> set[int]:
        """Возвращает дни месяца, где есть анализы."""
        start_date = date(year, month, 1)
        if month == 12:
            end_date = date(year + 1, 1, 1)
        else:
            end_date = date(year, month + 1, 1)

        with get_db_session() as session:
            rows = (
                session.query(ActivityAnalysisEntry.date)
                .filter(ActivityAnalysisEntry.user_id == str(user_id))
                .filter(ActivityAnalysisEntry.date >= start_date)
                .filter(ActivityAnalysisEntry.date < end_date)
                .all()
            )
            return {row[0].day for row in rows}
