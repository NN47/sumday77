"""Репозиторий для работы с весом и замерами."""
import logging
import calendar
from datetime import date, timedelta
from typing import Optional, Set
from database.session import get_db_session
from database.models import Weight, Measurement

logger = logging.getLogger(__name__)


class WeightRepository:
    """Репозиторий для работы с весом и замерами."""
    
    @staticmethod
    def save_weight(user_id: str, value: str, entry_date: date) -> Weight:
        """Сохраняет вес."""
        with get_db_session() as session:
            weight = Weight(
                user_id=user_id,
                value=value,
                date=entry_date,
            )
            session.add(weight)
            session.commit()
            session.refresh(weight)
            logger.info(f"Saved weight {weight.id} for user {user_id}")
            return weight
    
    @staticmethod
    def get_weights(user_id: str, limit: Optional[int] = None) -> list[Weight]:
        """Получает историю веса."""
        with get_db_session() as session:
            query = (
                session.query(Weight)
                .filter(Weight.user_id == user_id)
                .order_by(Weight.date.desc())
            )
            if limit:
                query = query.limit(limit)
            return query.all()
    
    @staticmethod
    def get_weights_for_date_range(user_id: str, start_date: Optional[date], end_date: Optional[date]) -> list[Weight]:
        """Получает веса за указанный период."""
        with get_db_session() as session:
            query = (
                session.query(Weight)
                .filter(Weight.user_id == user_id)
            )
            if start_date:
                query = query.filter(Weight.date >= start_date)
            if end_date:
                query = query.filter(Weight.date <= end_date)
            return query.order_by(Weight.date.desc(), Weight.id.desc()).all()
    
    @staticmethod
    def get_weights_for_period(user_id: str, period: str) -> list[dict]:
        """Получает веса за период."""
        today = date.today()
        
        if period == "week":
            start_date = today - timedelta(days=7)
        elif period == "month":
            start_date = today - timedelta(days=30)
        elif period == "half_year":
            start_date = today - timedelta(days=180)
        else:  # all_time
            start_date = date(2000, 1, 1)
        
        with get_db_session() as session:
            weights = (
                session.query(Weight)
                .filter(Weight.user_id == user_id)
                .filter(Weight.date >= start_date)
                .order_by(Weight.date.asc())
                .all()
            )
        
        result = []
        for w in weights:
            try:
                value = float(str(w.value).replace(",", "."))
                result.append({"date": w.date, "value": value})
            except (ValueError, TypeError):
                continue
        
        return result
    
    @staticmethod
    def get_last_weight(user_id: str) -> Optional[float]:
        """Получает последний вес пользователя в кг."""
        with get_db_session() as session:
            weight = (
                session.query(Weight)
                .filter(Weight.user_id == user_id)
                .order_by(Weight.date.desc())
                .first()
            )
            if weight:
                try:
                    return float(str(weight.value).replace(",", "."))
                except (ValueError, TypeError):
                    return None
            return None
    
    @staticmethod
    def update_weight(weight_id: int, user_id: str, value: str) -> bool:
        """Обновляет вес."""
        with get_db_session() as session:
            weight = (
                session.query(Weight)
                .filter(Weight.id == weight_id)
                .filter(Weight.user_id == user_id)
                .first()
            )
            if weight:
                weight.value = value
                session.commit()
                logger.info(f"Updated weight {weight_id} for user {user_id}")
                return True
            return False
    
    @staticmethod
    def delete_weight(weight_id: int, user_id: str) -> bool:
        """Удаляет вес."""
        with get_db_session() as session:
            weight = (
                session.query(Weight)
                .filter(Weight.id == weight_id)
                .filter(Weight.user_id == user_id)
                .first()
            )
            if weight:
                session.delete(weight)
                session.commit()
                logger.info(f"Deleted weight {weight_id} for user {user_id}")
                return True
            return False
    
    @staticmethod
    def save_measurements(
        user_id: str,
        measurements: dict,
        entry_date: date,
    ) -> Measurement:
        """Сохраняет замеры."""
        with get_db_session() as session:
            measurement = Measurement(
                user_id=user_id,
                chest=measurements.get("chest"),
                waist=measurements.get("waist"),
                hips=measurements.get("hips"),
                biceps=measurements.get("biceps"),
                thigh=measurements.get("thigh"),
                date=entry_date,
            )
            session.add(measurement)
            session.commit()
            session.refresh(measurement)
            logger.info(f"Saved measurement {measurement.id} for user {user_id}")
            return measurement
    
    @staticmethod
    def get_measurements(user_id: str, limit: Optional[int] = None) -> list[Measurement]:
        """Получает историю замеров."""
        with get_db_session() as session:
            query = (
                session.query(Measurement)
                .filter(Measurement.user_id == user_id)
                .order_by(Measurement.date.desc())
            )
            if limit:
                query = query.limit(limit)
            return query.all()
    
    @staticmethod
    def delete_measurement(measurement_id: int, user_id: str) -> bool:
        """Удаляет замеры."""
        with get_db_session() as session:
            measurement = (
                session.query(Measurement)
                .filter(Measurement.id == measurement_id)
                .filter(Measurement.user_id == user_id)
                .first()
            )
            if measurement:
                session.delete(measurement)
                session.commit()
                logger.info(f"Deleted measurement {measurement_id} for user {user_id}")
                return True
            return False

    @staticmethod
    def update_measurement(measurement_id: int, user_id: str, measurements: dict) -> bool:
        """Обновляет замеры."""
        with get_db_session() as session:
            measurement = (
                session.query(Measurement)
                .filter(Measurement.id == measurement_id)
                .filter(Measurement.user_id == user_id)
                .first()
            )
            if measurement:
                measurement.chest = measurements.get("chest")
                measurement.waist = measurements.get("waist")
                measurement.hips = measurements.get("hips")
                measurement.biceps = measurements.get("biceps")
                measurement.thigh = measurements.get("thigh")
                session.commit()
                logger.info(f"Updated measurement {measurement_id} for user {user_id}")
                return True
            return False
    
    @staticmethod
    def get_weight_for_date(user_id: str, target_date: date) -> Optional[Weight]:
        """Получает вес за конкретный день."""
        with get_db_session() as session:
            return (
                session.query(Weight)
                .filter(Weight.user_id == user_id, Weight.date == target_date)
                .order_by(Weight.id.desc())
                .first()
            )
    
    @staticmethod
    def get_month_weight_days(user_id: str, year: int, month: int) -> Set[int]:
        """Получает дни месяца, в которые был записан вес."""
        first_day = date(year, month, 1)
        _, days_in_month = calendar.monthrange(year, month)
        last_day = date(year, month, days_in_month)
        
        with get_db_session() as session:
            weights = (
                session.query(Weight.date)
                .filter(
                    Weight.user_id == user_id,
                    Weight.date >= first_day,
                    Weight.date <= last_day,
                )
                .all()
            )
            return {w.date.day for w in weights}

    @staticmethod
    def get_measurement_for_date(user_id: str, target_date: date) -> Optional[Measurement]:
        """Получает замеры за конкретный день."""
        with get_db_session() as session:
            return (
                session.query(Measurement)
                .filter(Measurement.user_id == user_id, Measurement.date == target_date)
                .order_by(Measurement.id.desc())
                .first()
            )

    @staticmethod
    def get_month_measurement_days(user_id: str, year: int, month: int) -> Set[int]:
        """Получает дни месяца, в которые были замеры."""
        first_day = date(year, month, 1)
        _, days_in_month = calendar.monthrange(year, month)
        last_day = date(year, month, days_in_month)

        with get_db_session() as session:
            measurements = (
                session.query(Measurement.date)
                .filter(
                    Measurement.user_id == user_id,
                    Measurement.date >= first_day,
                    Measurement.date <= last_day,
                )
                .all()
            )
            return {m.date.day for m in measurements}
