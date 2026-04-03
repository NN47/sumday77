"""Репозиторий для работы с процедурами."""
import logging
import calendar
from datetime import date
from typing import Optional, List, Set
from database.session import get_db_session
from database.models import Procedure

logger = logging.getLogger(__name__)


class ProcedureRepository:
    """Репозиторий для работы с процедурами."""
    
    @staticmethod
    def get_procedures_for_day(user_id: str, target_date: date) -> List[Procedure]:
        """Получает процедуры за день."""
        with get_db_session() as session:
            return (
                session.query(Procedure)
                .filter(Procedure.user_id == user_id, Procedure.date == target_date)
                .order_by(Procedure.id)
                .all()
            )
    
    @staticmethod
    def get_month_procedure_days(user_id: str, year: int, month: int) -> Set[int]:
        """Получает дни месяца, в которые были процедуры."""
        first_day = date(year, month, 1)
        _, days_in_month = calendar.monthrange(year, month)
        last_day = date(year, month, days_in_month)
        
        with get_db_session() as session:
            procedures = (
                session.query(Procedure.date)
                .filter(
                    Procedure.user_id == user_id,
                    Procedure.date >= first_day,
                    Procedure.date <= last_day,
                )
                .all()
            )
            return {p.date.day for p in procedures}
    
    @staticmethod
    def save_procedure(user_id: str, name: str, entry_date: date, notes: Optional[str] = None) -> Optional[int]:
        """Сохраняет процедуру."""
        with get_db_session() as session:
            procedure = Procedure(
                user_id=str(user_id),
                name=name,
                date=entry_date,
                notes=notes,
            )
            session.add(procedure)
            session.commit()
            session.refresh(procedure)
            return procedure.id
    
    @staticmethod
    def delete_procedure(user_id: str, procedure_id: int) -> bool:
        """Удаляет процедуру."""
        with get_db_session() as session:
            try:
                session.query(Procedure).filter_by(id=procedure_id, user_id=user_id).delete()
                session.commit()
                return True
            except Exception as e:
                logger.error(f"Error deleting procedure: {e}", exc_info=True)
                session.rollback()
                return False
