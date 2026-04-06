"""Репозиторий для заметок дня."""
import calendar
from datetime import date, datetime
from typing import Optional

from database.models import NoteEntry
from database.session import get_db_session


class NoteRepository:
    """Репозиторий для CRUD операций по заметкам дня."""

    @staticmethod
    def upsert_note(
        user_id: str,
        entry_date: date,
        day_rating: int,
        factors: list[str],
        text: Optional[str],
    ) -> NoteEntry:
        """Создаёт или обновляет заметку за конкретный день."""
        with get_db_session() as session:
            note = (
                session.query(NoteEntry)
                .filter(NoteEntry.user_id == str(user_id))
                .filter(NoteEntry.date == entry_date)
                .first()
            )

            normalized_text = (text or "").strip()[:500] or None
            factors_json = NoteEntry.serialize_factors(factors)

            if note:
                note.day_rating = int(day_rating)
                note.factors_json = factors_json
                note.text = normalized_text
                note.updated_at = datetime.utcnow()
                session.commit()
                session.refresh(note)
                return note

            note = NoteEntry(
                user_id=str(user_id),
                date=entry_date,
                day_rating=int(day_rating),
                factors_json=factors_json,
                text=normalized_text,
            )
            session.add(note)
            session.commit()
            session.refresh(note)
            return note

    @staticmethod
    def get_note_for_date(user_id: str, target_date: date) -> Optional[NoteEntry]:
        """Возвращает заметку пользователя за конкретный день."""
        with get_db_session() as session:
            return (
                session.query(NoteEntry)
                .filter(NoteEntry.user_id == str(user_id))
                .filter(NoteEntry.date == target_date)
                .first()
            )

    @staticmethod
    def delete_note_for_date(user_id: str, target_date: date) -> bool:
        """Удаляет заметку пользователя за день."""
        with get_db_session() as session:
            note = (
                session.query(NoteEntry)
                .filter(NoteEntry.user_id == str(user_id))
                .filter(NoteEntry.date == target_date)
                .first()
            )
            if not note:
                return False
            session.delete(note)
            session.commit()
            return True

    @staticmethod
    def get_month_note_days(user_id: str, year: int, month: int) -> set[int]:
        """Возвращает множество дней месяца, где есть заметка."""
        _, days_in_month = calendar.monthrange(year, month)
        start_date = date(year, month, 1)
        end_date = date(year, month, days_in_month)

        with get_db_session() as session:
            notes = (
                session.query(NoteEntry)
                .filter(NoteEntry.user_id == str(user_id))
                .filter(NoteEntry.date >= start_date)
                .filter(NoteEntry.date <= end_date)
                .all()
            )
            return {n.date.day for n in notes}
