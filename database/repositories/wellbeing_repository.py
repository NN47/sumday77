"""Репозиторий для работы с самочувствием."""
from datetime import date
from typing import Optional

from database.models import WellbeingEntry
from database.session import get_db_session


class WellbeingRepository:
    """Репозиторий для отметок самочувствия."""

    @staticmethod
    def save_quick_entry(
        user_id: str,
        mood: str,
        influence: str,
        difficulty: Optional[str],
        entry_date: date,
    ) -> int:
        """Сохраняет быстрый опрос."""
        with get_db_session() as session:
            entry = WellbeingEntry(
                user_id=str(user_id),
                entry_type="quick",
                mood=mood,
                influence=influence,
                difficulty=difficulty,
                date=entry_date,
            )
            session.add(entry)
            session.commit()
            session.refresh(entry)
            return entry.id

    @staticmethod
    def save_comment_entry(user_id: str, comment: str, entry_date: date) -> int:
        """Сохраняет комментарий о самочувствии."""
        with get_db_session() as session:
            entry = WellbeingEntry(
                user_id=str(user_id),
                entry_type="comment",
                comment=comment,
                date=entry_date,
            )
            session.add(entry)
            session.commit()
            session.refresh(entry)
            return entry.id

    @staticmethod
    def update_quick_entry(
        entry_id: int,
        user_id: str,
        mood: str,
        influence: str,
        difficulty: Optional[str],
        entry_date: date,
    ) -> bool:
        """Обновляет быстрый опрос."""
        with get_db_session() as session:
            entry = (
                session.query(WellbeingEntry)
                .filter(WellbeingEntry.id == entry_id)
                .filter(WellbeingEntry.user_id == str(user_id))
                .first()
            )
            if not entry:
                return False
            entry.entry_type = "quick"
            entry.mood = mood
            entry.influence = influence
            entry.difficulty = difficulty
            entry.date = entry_date
            session.commit()
            return True

    @staticmethod
    def update_comment_entry(entry_id: int, user_id: str, comment: str, entry_date: date) -> bool:
        """Обновляет комментарий о самочувствии."""
        with get_db_session() as session:
            entry = (
                session.query(WellbeingEntry)
                .filter(WellbeingEntry.id == entry_id)
                .filter(WellbeingEntry.user_id == str(user_id))
                .first()
            )
            if not entry:
                return False
            entry.entry_type = "comment"
            entry.comment = comment
            entry.date = entry_date
            session.commit()
            return True

    @staticmethod
    def delete_entry(entry_id: int, user_id: str) -> bool:
        """Удаляет запись самочувствия."""
        with get_db_session() as session:
            entry = (
                session.query(WellbeingEntry)
                .filter(WellbeingEntry.id == entry_id)
                .filter(WellbeingEntry.user_id == str(user_id))
                .first()
            )
            if not entry:
                return False
            session.delete(entry)
            session.commit()
            return True

    @staticmethod
    def get_entries_for_period(user_id: str, start_date: date, end_date: date) -> list[WellbeingEntry]:
        """Получает записи самочувствия за период."""
        with get_db_session() as session:
            return (
                session.query(WellbeingEntry)
                .filter(WellbeingEntry.user_id == str(user_id))
                .filter(WellbeingEntry.date >= start_date)
                .filter(WellbeingEntry.date <= end_date)
                .order_by(WellbeingEntry.date.desc(), WellbeingEntry.created_at.desc())
                .all()
            )

    @staticmethod
    def get_entries_for_date(user_id: str, target_date: date) -> list[WellbeingEntry]:
        """Получает записи самочувствия за день."""
        with get_db_session() as session:
            return (
                session.query(WellbeingEntry)
                .filter(WellbeingEntry.user_id == str(user_id))
                .filter(WellbeingEntry.date == target_date)
                .order_by(WellbeingEntry.created_at.asc())
                .all()
            )

    @staticmethod
    def get_entry_by_id(entry_id: int, user_id: str) -> Optional[WellbeingEntry]:
        """Получает запись самочувствия по ID."""
        with get_db_session() as session:
            return (
                session.query(WellbeingEntry)
                .filter(WellbeingEntry.id == entry_id)
                .filter(WellbeingEntry.user_id == str(user_id))
                .first()
            )
