"""Репозитории для работы с базой данных."""
from .meal_repository import MealRepository
from .workout_repository import WorkoutRepository
from .weight_repository import WeightRepository
from .water_repository import WaterRepository
from .supplement_repository import SupplementRepository
from .procedure_repository import ProcedureRepository
from .wellbeing_repository import WellbeingRepository
from .activity_analysis_repository import ActivityAnalysisRepository
from .custom_workout_exercise_repository import CustomWorkoutExerciseRepository

__all__ = [
    "MealRepository",
    "WorkoutRepository",
    "WeightRepository",
    "WaterRepository",
    "SupplementRepository",
    "ProcedureRepository",
    "WellbeingRepository",
    "ActivityAnalysisRepository",
    "CustomWorkoutExerciseRepository",
]
