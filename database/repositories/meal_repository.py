"""Репозиторий для работы с приёмами пищи."""
import logging
from datetime import date
from typing import Optional
from sqlalchemy import func
from database.session import get_db_session
from database.models import Meal, KbjuSettings

logger = logging.getLogger(__name__)


class MealRepository:
    """Репозиторий для работы с приёмами пищи."""
    
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
    ) -> Meal:
        """Сохраняет приём пищи."""
        with get_db_session() as session:
            meal = Meal(
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
            )
            session.add(meal)
            session.commit()
            session.refresh(meal)
            logger.info(f"Saved meal {meal.id} for user {user_id}")
            return meal
    
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
                logger.info(f"Deleted meal {meal_id} for user {user_id}")
                return True
            return False
    
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
                if products_json:
                    meal.products_json = products_json
                if api_details:
                    meal.api_details = api_details
                session.commit()
                logger.info(f"Updated meal {meal_id} for user {user_id}")
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
            else:
                settings = KbjuSettings(
                    user_id=user_id,
                    calories=calories,
                    protein=protein,
                    fat=fat,
                    carbs=carbs,
                    goal=goal,
                    activity=activity,
                )
                session.add(settings)
            
            session.commit()
            session.refresh(settings)
            logger.info(f"Saved KBJU settings for user {user_id}")
            return settings
