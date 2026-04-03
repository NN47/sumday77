"""Репозиторий для работы с водой."""
import logging
import calendar
from datetime import date, datetime
from typing import Optional
from sqlalchemy import func
from database.session import get_db_session
from database.models import WaterEntry

logger = logging.getLogger(__name__)


class WaterRepository:
    """Репозиторий для работы с водой."""
    
    @staticmethod
    def save_water_entry(
        user_id: str,
        amount: float,
        entry_date: date,
        timestamp: Optional[datetime] = None,
    ) -> WaterEntry:
        """Сохраняет запись воды."""
        with get_db_session() as session:
            entry = WaterEntry(
                user_id=user_id,
                amount=amount,
                date=entry_date,
                timestamp=timestamp or datetime.utcnow(),
            )
            session.add(entry)
            session.commit()
            session.refresh(entry)
            logger.info(f"Saved water entry {entry.id} for user {user_id}")
            return entry
    
    @staticmethod
    def get_daily_total(user_id: str, entry_date: date) -> float:
        """Получает общее количество воды за день."""
        with get_db_session() as session:
            result = (
                session.query(func.sum(WaterEntry.amount))
                .filter(WaterEntry.user_id == user_id)
                .filter(WaterEntry.date == entry_date)
                .scalar()
            )
            return float(result) if result else 0.0
    
    @staticmethod
    def get_entries_for_day(user_id: str, target_date: date) -> list[WaterEntry]:
        """Получает записи воды за день."""
        with get_db_session() as session:
            return (
                session.query(WaterEntry)
                .filter(WaterEntry.user_id == user_id)
                .filter(WaterEntry.date == target_date)
                .order_by(WaterEntry.timestamp.asc())
                .all()
            )
    
    @staticmethod
    def get_recent_entries(user_id: str, limit: int = 7) -> list[WaterEntry]:
        """Получает последние записи воды."""
        with get_db_session() as session:
            return (
                session.query(WaterEntry)
                .filter(WaterEntry.user_id == user_id)
                .order_by(WaterEntry.date.desc())
                .limit(limit)
                .all()
            )

    @staticmethod
    def get_month_water_days(user_id: str, year: int, month: int) -> set[int]:
        """Получает дни месяца, в которые была вода."""
        first_day = date(year, month, 1)
        _, days_in_month = calendar.monthrange(year, month)
        last_day = date(year, month, days_in_month)

        with get_db_session() as session:
            entries = (
                session.query(WaterEntry.date)
                .filter(
                    WaterEntry.user_id == user_id,
                    WaterEntry.date >= first_day,
                    WaterEntry.date <= last_day,
                )
                .all()
            )
            return {entry.date.day for entry in entries}
    
    @staticmethod
    def delete_entry(entry_id: int, user_id: str) -> bool:
        """Удаляет запись воды."""
        with get_db_session() as session:
            entry = (
                session.query(WaterEntry)
                .filter(WaterEntry.id == entry_id)
                .filter(WaterEntry.user_id == user_id)
                .first()
            )
            if entry:
                session.delete(entry)
                session.commit()
                logger.info(f"Deleted water entry {entry_id} for user {user_id}")
                return True
            return False
