"""Репозиторий для работы с добавками."""
import json
import logging
from datetime import date, datetime
from typing import Optional, List, Dict
from database.session import get_db_session
from database.models import Supplement, SupplementEntry, SupplementNotificationState
from utils.log_sanitizer import safe_exception_summary
from utils.supplement_catalog import (
    get_supplement_display_name,
    resolve_supplement_identifier,
)

logger = logging.getLogger(__name__)


class SupplementRepository:
    """Репозиторий для работы с добавками."""
    
    @staticmethod
    def get_supplements(user_id: str) -> List[Dict]:
        """Получает все добавки пользователя с их историей."""
        with get_db_session() as session:
            supplements = session.query(Supplement).filter_by(user_id=user_id).all()
            ids = [sup.id for sup in supplements]
            entries_map: Dict[int, List[Dict]] = {sup_id: [] for sup_id in ids}
            
            if ids:
                all_entries = (
                    session.query(SupplementEntry)
                    .filter(
                        SupplementEntry.user_id == user_id,
                        SupplementEntry.supplement_id.in_(ids),
                    )
                    .order_by(SupplementEntry.timestamp.asc())
                    .all()
                )
                for entry in all_entries:
                    entries_map.setdefault(entry.supplement_id, []).append({
                        "id": entry.id,
                        "timestamp": entry.timestamp,
                        "amount": entry.amount,
                    })
            
            result: List[Dict] = []
            for sup in supplements:
                identifier = resolve_supplement_identifier(sup.name)
                notifications_enabled = True
                try:
                    if hasattr(sup, 'notifications_enabled'):
                        notifications_enabled = sup.notifications_enabled
                except (AttributeError, KeyError):
                    notifications_enabled = True
                
                result.append({
                    "id": sup.id,
                    "identifier": identifier,
                    "name": get_supplement_display_name(sup.name),
                    "times": json.loads(sup.times_json or "[]"),
                    "days": json.loads(sup.days_json or "[]"),
                    "duration": sup.duration or "постоянно",
                    "history": entries_map.get(sup.id, []).copy(),
                    "ready": True,
                    "notifications_enabled": notifications_enabled,
                })
            
            return result
    
    @staticmethod
    def save_supplement(user_id: str, payload: Dict, supplement_id: Optional[int] = None) -> Optional[int]:
        """Save catalog identifiers while preserving unknown historical names."""
        with get_db_session() as session:
            if supplement_id:
                sup = session.query(Supplement).filter_by(id=supplement_id, user_id=user_id).first()
                if not sup:
                    return None
            else:
                sup = Supplement(user_id=user_id)

            requested_identifier = payload.get("identifier")
            identifier = resolve_supplement_identifier(requested_identifier)
            if identifier is None:
                identifier = resolve_supplement_identifier(payload.get("name"))

            if identifier is None and not supplement_id:
                logger.warning("Rejected supplement creation without catalog identifier")
                return None
            if identifier is not None:
                sup.name = identifier
            # For an unrecognized legacy value, editing schedule, duration, or
            # notifications deliberately leaves the stored name untouched.

            sup.times_json = json.dumps(payload.get("times", []), ensure_ascii=False)
            sup.days_json = json.dumps(payload.get("days", []), ensure_ascii=False)
            sup.duration = payload.get("duration", sup.duration or "постоянно")
            if hasattr(sup, 'notifications_enabled'):
                sup.notifications_enabled = payload.get("notifications_enabled", True)
            
            session.add(sup)
            session.commit()
            session.refresh(sup)
            return sup.id
    
    @staticmethod
    def delete_supplement(user_id: str, supplement_id: int) -> bool:
        """Удаляет добавку и все её записи."""
        with get_db_session() as session:
            try:
                session.query(SupplementEntry).filter_by(
                    user_id=user_id, supplement_id=supplement_id
                ).delete()
                session.query(Supplement).filter_by(id=supplement_id, user_id=user_id).delete()
                session.commit()
                return True
            except Exception as e:
                logger.error("Error deleting supplement error_type=%s", safe_exception_summary(e), exc_info=True)
                session.rollback()
                return False
    
    @staticmethod
    def save_entry(user_id: str, supplement_id: int, timestamp: datetime, amount: Optional[float] = None) -> Optional[int]:
        """Сохраняет запись приёма добавки."""
        with get_db_session() as session:
            entry = SupplementEntry(
                user_id=user_id,
                supplement_id=supplement_id,
                timestamp=timestamp,
                amount=amount,
            )
            session.add(entry)
            session.query(SupplementNotificationState).filter_by(
                user_id=user_id, supplement_id=supplement_id
            ).delete()
            session.commit()
            session.refresh(entry)
            return entry.id
    
    @staticmethod
    def delete_entry(user_id: str, entry_id: int) -> bool:
        """Удаляет запись приёма добавки."""
        with get_db_session() as session:
            try:
                session.query(SupplementEntry).filter_by(id=entry_id, user_id=user_id).delete()
                session.commit()
                return True
            except Exception as e:
                logger.error(
                    "Error deleting supplement entry error_type=%s",
                    safe_exception_summary(e),
                    exc_info=True,
                )
                session.rollback()
                return False
    
    @staticmethod
    def get_entries_for_day(user_id: str, target_date: date) -> List[Dict]:
        """Получает записи приёма добавок за день."""
        supplements = SupplementRepository.get_supplements(user_id)
        result = []
        
        for sup_idx, sup in enumerate(supplements):
            for entry_idx, entry in enumerate(sup.get("history", [])):
                entry_date = entry["timestamp"].date() if isinstance(entry["timestamp"], datetime) else entry["timestamp"]
                if entry_date == target_date:
                    time_text = entry["timestamp"].strftime("%H:%M") if isinstance(entry["timestamp"], datetime) else ""
                    amount_text = f" ({entry['amount']})" if entry.get("amount") else ""
                    result.append({
                        "supplement_name": sup.get("name", "Добавка"),
                        "supplement_index": sup_idx,
                        "entry_index": entry_idx,
                        "entry_id": entry["id"],
                        "time_text": time_text,
                        "amount": entry.get("amount"),
                        "amount_text": amount_text,
                    })
        
        return result
    
    @staticmethod
    def get_history_days(user_id: str, year: int, month: int, supplement_id: Optional[int] = None) -> set:
        """Получает дни месяца, в которые были записи приёма добавок."""
        supplements = SupplementRepository.get_supplements(user_id)
        days = set()
        
        for sup in supplements:
            if supplement_id is not None and sup.get("id") != supplement_id:
                continue
            for entry in sup.get("history", []):
                timestamp = entry["timestamp"]
                if isinstance(timestamp, datetime):
                    if timestamp.year == year and timestamp.month == month:
                        days.add(timestamp.day)
        
        return days
