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
        input_method: str | None = None,
        duration_minutes: float | None = None,
        distance_km: float | None = None,
        jumps_count: int | None = None,
        working_weight: float | None = None,
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
                input_method=input_method,
                duration_minutes=duration_minutes,
                distance_km=distance_km,
                jumps_count=jumps_count,
                working_weight=working_weight,
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
    def update_workout(
        workout_id: int,
        user_id: str,
        count: int | float,
        calories: float,
        *,
        input_method: str | None = None,
        duration_minutes: float | None = None,
        distance_km: float | None = None,
        jumps_count: int | None = None,
        working_weight: float | None = None,
        update_weight: bool = False,
    ) -> bool:
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
                if input_method is not None:
                    workout.input_method = input_method
                    workout.duration_minutes = duration_minutes
                    workout.distance_km = distance_km
                    workout.jumps_count = jumps_count
                if update_weight:
                    workout.working_weight = working_weight
                session.commit()
                logger.info(f"Updated workout {workout_id} for user {user_id}: count={count}, calories={calories}")
                return True
            return False

    @staticmethod
    def update_workout_reps(workout_id: int, user_id: str, count: int, calories: float) -> bool:
        """Обновляет повторы и калории одной записи тренировки."""
        return WorkoutRepository.update_workout(workout_id, user_id, count, calories)

    @staticmethod
    def update_workout_weight(workout_id: int, user_id: str, working_weight: float, calories: float | None = None) -> bool:
        """Обновляет рабочий вес одной записи тренировки."""
        with get_db_session() as session:
            workout = (
                session.query(Workout)
                .filter(Workout.id == workout_id)
                .filter(Workout.user_id == user_id)
                .first()
            )
            if not workout:
                return False
            workout.working_weight = working_weight
            if calories is not None:
                workout.calories = calories
            session.commit()
            logger.info(f"Updated workout {workout_id} weight for user {user_id}: {working_weight}")
            return True
