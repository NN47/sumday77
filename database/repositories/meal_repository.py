"""Репозиторий для работы с приёмами пищи."""
import logging
from dataclasses import dataclass
from datetime import date
from enum import Enum
from typing import Optional
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from database.session import get_db_session
from database.models import Meal, KbjuSettings, MealCompletionComment
from database.repositories.analytics_repository import AnalyticsRepository
from utils.log_sanitizer import safe_exception_summary
from utils.meal_types import normalize_meal_type, MealType

logger = logging.getLogger(__name__)


class MealSaveStatus(str, Enum):
    """DB-level outcome of saving one pending meal operation."""

    SAVED = "saved"
    ALREADY_SAVED = "already_saved"
    FAILED = "failed"


@dataclass(frozen=True)
class MealSaveResult:
    """Result returned by the idempotent pending-meal save path."""

    status: MealSaveStatus
    meal: Meal | None = None
    error_type: str | None = None


class MealRepository:
    """Репозиторий для работы с приёмами пищи."""

    @staticmethod
    def _build_meal(
        *,
        user_id: str,
        raw_query: str,
        calories: float,
        protein: float,
        fat: float,
        carbs: float,
        entry_date: date,
        description: Optional[str],
        products_json: Optional[str],
        api_details: Optional[str],
        meal_type: Optional[str],
        is_manually_corrected: bool,
        save_token: str | None,
    ) -> Meal:
        return Meal(
            user_id=user_id,
            raw_query=raw_query,
            description=description or raw_query,
            calories=calories,
            protein=protein,
            fat=fat,
            carbs=carbs,
            date=entry_date,
            products_json=products_json or "[]",
            api_details=api_details,
            meal_type=normalize_meal_type(meal_type),
            is_manually_corrected=is_manually_corrected,
            save_token=save_token,
        )

    @staticmethod
    def _track_saved_meal(user_id: str) -> None:
        """Records secondary analytics without changing the committed Meal outcome."""
        try:
            AnalyticsRepository.track_event(user_id, "add_meal", section="kbju")
        except Exception as exc:
            logger.warning(
                "Meal saved but analytics failed error_type=%s",
                safe_exception_summary(exc),
            )
    
    @staticmethod
    def save_meal(
        user_id: str,
        raw_query: str,
        calories: float,
        protein: float,
        fat: float,
        carbs: float,
        entry_date: date,
        description: Optional[str] = None,
        products_json: Optional[str] = None,
        api_details: Optional[str] = None,
        meal_type: Optional[str] = MealType.SNACK.value,
        is_manually_corrected: bool = False,
    ) -> Meal:
        """Сохраняет приём пищи."""
        with get_db_session() as session:
            meal = MealRepository._build_meal(
                user_id=user_id,
                raw_query=raw_query,
                description=description,
                calories=calories,
                protein=protein,
                fat=fat,
                carbs=carbs,
                entry_date=entry_date,
                products_json=products_json,
                api_details=api_details,
                meal_type=normalize_meal_type(meal_type),
                is_manually_corrected=is_manually_corrected,
                save_token=None,
            )
            session.add(meal)
            session.commit()
            session.refresh(meal)
            logger.info("Saved meal meal_id=%s", meal.id)
        MealRepository._track_saved_meal(user_id)
        return meal

    @staticmethod
    def save_meal_idempotent(
        *,
        save_token: str,
        user_id: str,
        raw_query: str,
        calories: float,
        protein: float,
        fat: float,
        carbs: float,
        entry_date: date,
        description: Optional[str] = None,
        products_json: Optional[str] = None,
        api_details: Optional[str] = None,
        meal_type: Optional[str] = MealType.SNACK.value,
        is_manually_corrected: bool = False,
    ) -> MealSaveResult:
        """Saves one pending operation exactly once using a DB-unique opaque token."""
        if not save_token or len(save_token) > 64:
            return MealSaveResult(MealSaveStatus.FAILED, error_type="invalid_save_token")

        meal = MealRepository._build_meal(
            user_id=user_id,
            raw_query=raw_query,
            description=description,
            calories=calories,
            protein=protein,
            fat=fat,
            carbs=carbs,
            entry_date=entry_date,
            products_json=products_json,
            api_details=api_details,
            meal_type=meal_type,
            is_manually_corrected=is_manually_corrected,
            save_token=save_token,
        )

        try:
            with get_db_session() as session:
                session.add(meal)
                try:
                    session.commit()
                    session.refresh(meal)
                except IntegrityError:
                    session.rollback()
                    existing = (
                        session.query(Meal)
                        .filter(
                            Meal.save_token == save_token,
                            Meal.user_id == user_id,
                        )
                        .first()
                    )
                    if existing is not None:
                        logger.info("Duplicate meal save prevented")
                        return MealSaveResult(MealSaveStatus.ALREADY_SAVED, meal=existing)
                    logger.error("Meal save failed error_type=IntegrityError")
                    return MealSaveResult(MealSaveStatus.FAILED, error_type="IntegrityError")
        except Exception as exc:
            logger.error(
                "Meal save failed error_type=%s",
                safe_exception_summary(exc),
            )
            return MealSaveResult(
                MealSaveStatus.FAILED,
                error_type=safe_exception_summary(exc),
            )

        logger.info("Saved meal meal_id=%s", meal.id)
        MealRepository._track_saved_meal(user_id)
        return MealSaveResult(MealSaveStatus.SAVED, meal=meal)
    
    @staticmethod
    def get_meals_for_date(user_id: str, entry_date: date) -> list[Meal]:
        """Получает все приёмы пищи за дату."""
        with get_db_session() as session:
            return (
                session.query(Meal)
                .filter(Meal.user_id == user_id)
                .filter(Meal.date == entry_date)
                .order_by(Meal.id.asc())
                .all()
            )

    @staticmethod
    def get_recent_unique_meals(user_id: str, limit: int = 8) -> list[Meal]:
        """Возвращает последние уникальные приёмы по LOWER(raw_query)."""
        with get_db_session() as session:
            rows = (
                session.query(Meal)
                .filter(Meal.user_id == user_id)
                .filter(Meal.raw_query.isnot(None))
                .order_by(Meal.id.desc())
                .all()
            )
            unique: list[Meal] = []
            seen: set[str] = set()
            for meal in rows:
                key = (meal.raw_query or "").strip().lower()
                if not key or key in seen:
                    continue
                seen.add(key)
                unique.append(meal)
                if len(unique) >= limit:
                    break
            return unique
    
    @staticmethod
    def get_user_meal_history(user_id: str) -> list[Meal]:
        """Возвращает всю историю добавленных приёмов пищи пользователя от новых к старым."""
        with get_db_session() as session:
            return (
                session.query(Meal)
                .filter(Meal.user_id == user_id)
                .filter(Meal.raw_query.isnot(None))
                .order_by(Meal.id.desc())
                .all()
            )

    @staticmethod
    def get_user_meal_history_page(user_id: str, *, offset: int = 0, limit: int = 100) -> list[Meal]:
        """Возвращает страницу истории приёмов пищи пользователя от новых к старым."""
        with get_db_session() as session:
            return (
                session.query(Meal)
                .filter(Meal.user_id == user_id)
                .filter(Meal.raw_query.isnot(None))
                .order_by(Meal.id.desc())
                .offset(max(0, offset))
                .limit(max(1, limit))
                .all()
            )

    @staticmethod
    def get_daily_totals(user_id: str, entry_date: date) -> dict:
        """Получает суммарные КБЖУ за день."""
        with get_db_session() as session:
            result = (
                session.query(
                    func.sum(Meal.calories).label("calories"),
                    func.sum(Meal.protein).label("protein"),
                    func.sum(Meal.fat).label("fat"),
                    func.sum(Meal.carbs).label("carbs"),
                )
                .filter(Meal.user_id == user_id)
                .filter(Meal.date == entry_date)
                .first()
            )
            
            return {
                "calories": float(result.calories) if result.calories else 0.0,
                "protein": float(result.protein) if result.protein else 0.0,
                "protein_g": float(result.protein) if result.protein else 0.0,  # Для совместимости
                "fat": float(result.fat) if result.fat else 0.0,
                "fat_total_g": float(result.fat) if result.fat else 0.0,  # Для совместимости
                "carbs": float(result.carbs) if result.carbs else 0.0,
                "carbohydrates_total_g": float(result.carbs) if result.carbs else 0.0,  # Для совместимости
            }

    @staticmethod
    def get_meals_for_type_for_date(user_id: str, entry_date: date, meal_type: str) -> list[Meal]:
        """Получает приёмы пищи конкретного типа за дату."""
        normalized_meal_type = normalize_meal_type(meal_type, fallback=MealType.SNACK.value)
        with get_db_session() as session:
            return (
                session.query(Meal)
                .filter(Meal.user_id == user_id)
                .filter(Meal.date == entry_date)
                .filter(Meal.meal_type == normalized_meal_type)
                .order_by(Meal.id.asc())
                .all()
            )
    
    @staticmethod
    def delete_meal(meal_id: int, user_id: str) -> bool:
        """Удаляет приём пищи."""
        with get_db_session() as session:
            meal = (
                session.query(Meal)
                .filter(Meal.id == meal_id)
                .filter(Meal.user_id == user_id)
                .first()
            )
            if meal:
                session.delete(meal)
                session.commit()
                logger.info("Deleted meal meal_id=%s", meal_id)
                return True
            return False

    @staticmethod
    def delete_meals_by_type_for_date(user_id: str, entry_date: date, meal_type: str) -> int:
        """Удаляет все приёмы пищи выбранного типа за дату."""
        normalized_meal_type = normalize_meal_type(meal_type, fallback=MealType.SNACK.value)
        with get_db_session() as session:
            meals = (
                session.query(Meal)
                .filter(Meal.user_id == user_id)
                .filter(Meal.date == entry_date)
                .filter(Meal.meal_type == normalized_meal_type)
                .all()
            )
            deleted_count = len(meals)
            for meal in meals:
                session.delete(meal)
            if deleted_count:
                session.commit()
                logger.info(
                    "Deleted meals count=%s date=%s meal_type=%s",
                    deleted_count,
                    entry_date.isoformat(),
                    normalized_meal_type,
                )
            return deleted_count
    
    @staticmethod
    def get_kbju_settings(user_id: str) -> Optional[KbjuSettings]:
        """Получает настройки КБЖУ пользователя."""
        with get_db_session() as session:
            return (
                session.query(KbjuSettings)
                .filter(KbjuSettings.user_id == user_id)
                .first()
            )
    
    @staticmethod
    def get_meal_by_id(meal_id: int, user_id: str) -> Optional[Meal]:
        """Получает приём пищи по ID."""
        with get_db_session() as session:
            return (
                session.query(Meal)
                .filter(Meal.id == meal_id)
                .filter(Meal.user_id == user_id)
                .first()
            )
    
    @staticmethod
    def update_meal(
        meal_id: int,
        user_id: str,
        description: str,
        calories: float,
        protein: float,
        fat: float,
        carbs: float,
        products_json: Optional[str] = None,
        api_details: Optional[str] = None,
        is_manually_corrected: Optional[bool] = None,
    ) -> bool:
        """Обновляет приём пищи."""
        with get_db_session() as session:
            meal = (
                session.query(Meal)
                .filter(Meal.id == meal_id)
                .filter(Meal.user_id == user_id)
                .first()
            )
            if meal:
                meal.description = description
                meal.raw_query = description
                meal.calories = calories
                meal.protein = protein
                meal.fat = fat
                meal.carbs = carbs
                if is_manually_corrected is not None:
                    meal.is_manually_corrected = is_manually_corrected
                if products_json:
                    meal.products_json = products_json
                if api_details:
                    meal.api_details = api_details
                session.query(MealCompletionComment).filter(
                    MealCompletionComment.user_id == user_id,
                    MealCompletionComment.meal_id == meal_id,
                ).delete(synchronize_session=False)
                session.commit()
                logger.info("Updated meal meal_id=%s and invalidated completion comment", meal_id)
                return True
            return False
    
    @staticmethod
    def save_kbju_settings(
        user_id: str,
        calories: float,
        protein: float,
        fat: float,
        carbs: float,
        goal: Optional[str] = None,
        activity: Optional[str] = None,
        gender: Optional[str] = None,
    ) -> KbjuSettings:
        """Сохраняет настройки КБЖУ."""
        with get_db_session() as session:
            settings = (
                session.query(KbjuSettings)
                .filter(KbjuSettings.user_id == user_id)
                .first()
            )
            
            if settings:
                settings.calories = calories
                settings.protein = protein
                settings.fat = fat
                settings.carbs = carbs
                if goal:
                    settings.goal = goal
                if activity:
                    settings.activity = activity
                if gender:
                    settings.gender = gender
            else:
                settings = KbjuSettings(
                    user_id=user_id,
                    calories=calories,
                    protein=protein,
                    fat=fat,
                    carbs=carbs,
                    goal=goal,
                    activity=activity,
                    gender=gender,
                )
                session.add(settings)
            
            session.commit()
            session.refresh(settings)
            logger.info("Saved KBJU settings")
            return settings
