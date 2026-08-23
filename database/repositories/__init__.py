"""Репозитории для работы с базой данных."""
from .meal_repository import MealRepository, MealSaveResult, MealSaveStatus
from .workout_repository import WorkoutRepository
from .weight_repository import WeightRepository
from .water_repository import QuickWaterMessageRepository, WaterRepository
from .supplement_repository import SupplementRepository
from .procedure_repository import ProcedureRepository
from .note_repository import NoteRepository
from .activity_analysis_repository import ActivityAnalysisRepository
from .custom_workout_exercise_repository import CustomWorkoutExerciseRepository
from .wellbeing_repository import WellbeingRepository
from .user_repository import UserRepository
from .analytics_repository import AnalyticsRepository
from .support_repository import SupportRepository
from .error_log_repository import ErrorLogRepository
from .gemini_repository import GeminiRepository
from .ai_usage_repository import AIUsageRepository
from .evening_analysis_notification_repository import EveningAnalysisNotificationRepository
from .saved_product_repository import SavedProductRepository
from .dish_repository import DishRepository
from .daily_analysis_preparation_repository import DailyAnalysisPreparationRepository

__all__ = [
    "MealRepository",
    "MealSaveResult",
    "MealSaveStatus",
    "WorkoutRepository",
    "WeightRepository",
    "WaterRepository",
    "QuickWaterMessageRepository",
    "SupplementRepository",
    "ProcedureRepository",
    "NoteRepository",
    "ActivityAnalysisRepository",
    "CustomWorkoutExerciseRepository",
    "WellbeingRepository",
    "UserRepository",
    "AnalyticsRepository",
    "SupportRepository",
    "ErrorLogRepository",
    "GeminiRepository",
    "AIUsageRepository",
    "EveningAnalysisNotificationRepository",
    "SavedProductRepository",
    "DishRepository",
    "DailyAnalysisPreparationRepository",
]
