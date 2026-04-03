"""Репозиторий для работы с тренировками."""
import logging
from datetime import date
from typing import Optional
from database.session import get_db_session
from database.models import Workout

logger = logging.getLogger(__name__)


class WorkoutRepository:
    """Репозиторий для работы с тренировками."""
    
    @staticmethod
    def save_workout(
        user_id: str,
        exercise: str,
        count: int,
        entry_date: date,
        variant: Optional[str] = None,
        calories: float = 0.0,
    ) -> Workout:
        """Сохраняет тренировку."""
        with get_db_session() as session:
            workout = Workout(
                user_id=user_id,
                exercise=exercise,
                variant=variant,
                count=count,
                date=entry_date,
                calories=calories,
            )
            session.add(workout)
            session.commit()
            session.refresh(workout)
            logger.info(f"Saved workout {workout.id} for user {user_id}")
            return workout
    
    @staticmethod
    def get_workouts_for_day(user_id: str, target_date: date) -> list[Workout]:
        """Получает тренировки за день."""
        with get_db_session() as session:
            return (
                session.query(Workout)
                .filter(Workout.user_id == user_id)
                .filter(Workout.date == target_date)
                .order_by(Workout.id.asc())
                .all()
            )
    
    @staticmethod
    def delete_workout(workout_id: int, user_id: str) -> bool:
        """Удаляет тренировку."""
        with get_db_session() as session:
            workout = (
                session.query(Workout)
                .filter(Workout.id == workout_id)
                .filter(Workout.user_id == user_id)
                .first()
            )
            if workout:
                session.delete(workout)
                session.commit()
                logger.info(f"Deleted workout {workout_id} for user {user_id}")
                return True
            return False
    
    @staticmethod
    def get_workouts_for_period(user_id: str, start_date: date, end_date: date) -> list[Workout]:
        """Получает тренировки за период."""
        with get_db_session() as session:
            return (
                session.query(Workout)
                .filter(Workout.user_id == user_id)
                .filter(Workout.date >= start_date)
                .filter(Workout.date <= end_date)
                .order_by(Workout.date.asc(), Workout.id.asc())
                .all()
            )
    
    @staticmethod
    def get_workout_by_id(workout_id: int, user_id: str) -> Optional[Workout]:
        """Получает тренировку по ID."""
        with get_db_session() as session:
            return (
                session.query(Workout)
                .filter(Workout.id == workout_id)
                .filter(Workout.user_id == user_id)
                .first()
            )
    
    @staticmethod
    def update_workout(workout_id: int, user_id: str, count: int, calories: float) -> bool:
        """Обновляет количество и калории тренировки."""
        with get_db_session() as session:
            workout = (
                session.query(Workout)
                .filter(Workout.id == workout_id)
                .filter(Workout.user_id == user_id)
                .first()
            )
            if workout:
                workout.count = count
                workout.calories = calories
                session.commit()
                logger.info(f"Updated workout {workout_id} for user {user_id}: count={count}, calories={calories}")
                return True
            return False
