"""Репозиторий для пользовательских упражнений."""
import logging
from database.session import get_db_session
from database.models import CustomWorkoutExercise

logger = logging.getLogger(__name__)


class CustomWorkoutExerciseRepository:
    """Работа с пользовательскими упражнениями."""

    @staticmethod
    def get_user_exercises(user_id: str, category: str) -> list[str]:
        """Возвращает список пользовательских упражнений для категории."""
        with get_db_session() as session:
            rows = (
                session.query(CustomWorkoutExercise)
                .filter(CustomWorkoutExercise.user_id == user_id)
                .filter(CustomWorkoutExercise.category == category)
                .order_by(CustomWorkoutExercise.id.asc())
                .all()
            )
            return [row.name for row in rows]

    @staticmethod
    def save_exercise(user_id: str, category: str, name: str) -> bool:
        """Сохраняет пользовательское упражнение, если его ещё нет."""
        normalized_name = name.strip()
        if not normalized_name:
            return False

        with get_db_session() as session:
            existing = (
                session.query(CustomWorkoutExercise)
                .filter(CustomWorkoutExercise.user_id == user_id)
                .filter(CustomWorkoutExercise.category == category)
                .filter(CustomWorkoutExercise.name == normalized_name)
                .first()
            )
            if existing:
                return False

            custom_exercise = CustomWorkoutExercise(
                user_id=user_id,
                category=category,
                name=normalized_name,
            )
            session.add(custom_exercise)
            session.commit()
            logger.info("Saved custom workout exercise for user %s: %s", user_id, normalized_name)
            return True
