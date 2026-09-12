"""Domain service for dish templates and immutable diary snapshots."""

from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass
from datetime import date, datetime

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload

from database.models import Dish, DishIngredient, Meal
from database.repositories.meal_repository import MealRepository, MealSaveResult, MealSaveStatus
from database.session import get_db_session
from utils.log_sanitizer import safe_exception_summary
from utils.meal_types import normalize_meal_type


MAX_DISH_NAME_LENGTH = 80
MAX_INGREDIENT_NAME_LENGTH = 160


def _number(value) -> float:
    if isinstance(value, bool):
        return 0.0
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        return 0.0
    return result if math.isfinite(result) and result >= 0 else 0.0


def normalize_dish_display_name(name: object, ingredients: list[dict] | None = None) -> str:
    clean = re.sub(r"\s+", " ", str(name or "").strip())
    if clean and any(character.isalpha() for character in clean):
        return clean[:MAX_DISH_NAME_LENGTH].rstrip(" ,.;:-")

    ingredient_names = [
        re.sub(r"\s+", " ", str(item.get("name") or "").strip())
        for item in ingredients or []
    ]
    ingredient_names = [value for value in ingredient_names if value]
    if ingredient_names:
        generated = ", ".join(ingredient_names[:3])
        return generated[:MAX_DISH_NAME_LENGTH].rstrip(" ,.;:-") or "Блюдо из фото"
    return "Блюдо из фото"


def normalize_ingredient_snapshot(item: dict) -> dict:
    name = re.sub(r"\s+", " ", str(item.get("name") or "Ингредиент").strip())
    name = name[:MAX_INGREDIENT_NAME_LENGTH] or "Ингредиент"
    weight = max(1.0, _number(item.get("grams") or item.get("weight_g") or item.get("amount_g")))
    calories = _number(item.get("kcal") or item.get("calories"))
    protein = _number(item.get("protein") or item.get("protein_g"))
    fat = _number(item.get("fat") or item.get("fat_total_g"))
    carbs = _number(item.get("carbs") or item.get("carbohydrates_total_g"))
    factor = 100.0 / weight
    return {
        "name": name,
        "grams": weight,
        "kcal": calories,
        "calories": calories,
        "protein": protein,
        "protein_g": protein,
        "fat": fat,
        "fat_total_g": fat,
        "carbs": carbs,
        "carbohydrates_total_g": carbs,
        "calories_per_100g": calories * factor,
        "protein_per_100g": protein * factor,
        "fat_per_100g": fat * factor,
        "carbs_per_100g": carbs * factor,
        "is_manually_corrected": bool(item.get("is_manually_corrected")),
        "source": "dish_ingredient",
    }


def calculate_dish_totals(items: list[dict]) -> dict[str, float]:
    return {
        "calories": sum(_number(item.get("kcal") or item.get("calories")) for item in items),
        "protein": sum(_number(item.get("protein") or item.get("protein_g")) for item in items),
        "fat": sum(_number(item.get("fat") or item.get("fat_total_g")) for item in items),
        "carbs": sum(_number(item.get("carbs") or item.get("carbohydrates_total_g")) for item in items),
    }


def calculate_dish_weight(items: list[dict]) -> float:
    return sum(_number(item.get("grams") or item.get("weight_g")) for item in items)


def scale_dish_snapshot(items: list[dict], new_total_weight: float) -> list[dict]:
    """Scale a copied diary draft without mutating the reusable template."""
    current_weight = calculate_dish_weight(items)
    if current_weight <= 0 or new_total_weight <= 0:
        return [dict(item) for item in items]
    factor = float(new_total_weight) / current_weight
    scaled: list[dict] = []
    for source in items:
        item = dict(source)
        weight = max(1.0, _number(item.get("grams")) * factor)
        for key in (
            "kcal",
            "calories",
            "protein",
            "protein_g",
            "fat",
            "fat_total_g",
            "carbs",
            "carbohydrates_total_g",
        ):
            if key in item:
                item[key] = _number(item.get(key)) * factor
        item["grams"] = weight
        scaled.append(item)
    return scaled


def _composition_fingerprint(items: list[dict]) -> str:
    canonical = [
        {
            "name": str(item["name"]).casefold(),
            "grams": round(float(item["grams"]), 4),
            "kcal": round(float(item["kcal"]), 4),
            "protein": round(float(item["protein"]), 4),
            "fat": round(float(item["fat"]), 4),
            "carbs": round(float(item["carbs"]), 4),
        }
        for item in items
    ]
    raw = json.dumps(canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _api_details(items: list[dict]) -> str:
    return "\n".join(
        f"• {item['name']} ({float(item['grams']):.0f} г) — {float(item['kcal']):.0f} ккал "
        f"(Б {float(item['protein']):.1f} / Ж {float(item['fat']):.1f} / У {float(item['carbs']):.1f})"
        for item in items
    )


def dish_to_snapshot(dish: Dish) -> list[dict]:
    result: list[dict] = []
    for ingredient in sorted(dish.ingredients, key=lambda value: value.position):
        weight = float(ingredient.weight_g)
        calories = float(ingredient.calories_per_100g) * weight / 100.0
        protein = float(ingredient.protein_per_100g) * weight / 100.0
        fat = float(ingredient.fat_per_100g) * weight / 100.0
        carbs = float(ingredient.carbs_per_100g) * weight / 100.0
        result.append(
            normalize_ingredient_snapshot(
                {
                    "name": ingredient.name_snapshot,
                    "grams": weight,
                    "kcal": calories,
                    "protein": protein,
                    "fat": fat,
                    "carbs": carbs,
                    "is_manually_corrected": ingredient.is_manually_corrected,
                }
            )
        )
    return result


@dataclass(frozen=True)
class DishEntrySaveResult:
    status: MealSaveStatus
    meal: Meal | None = None
    dish: Dish | None = None
    error_type: str | None = None


class DishService:
    @staticmethod
    def update_template(
        *, user_id: str, dish_id: int, name: str | None = None,
        items: list[dict] | None = None,
    ) -> Dish | None:
        """Atomically update a dish template without touching diary snapshots.

        Ingredient rows are owned by a dish, so replacing them cannot mutate a
        saved product, another dish, or an already-recorded meal.
        """
        normalized_items = None
        if items is not None:
            normalized_items = [
                normalize_ingredient_snapshot(item) for item in items
                if isinstance(item, dict)
            ]
            if not normalized_items:
                return None
        with get_db_session() as session:
            dish = (
                session.query(Dish)
                .options(selectinload(Dish.ingredients))
                .filter(
                    Dish.id == int(dish_id), Dish.user_id == str(user_id),
                    Dish.archived_at.is_(None),
                )
                .first()
            )
            if dish is None:
                return None
            if name is not None:
                clean_name = normalize_dish_display_name(name, normalized_items)
                dish.name = clean_name
                dish.normalized_name = clean_name.casefold()
            if normalized_items is not None:
                dish.ingredients.clear()
                session.flush()
                for position, item in enumerate(normalized_items):
                    dish.ingredients.append(DishIngredient(
                        position=position,
                        name_snapshot=item["name"],
                        weight_g=item["grams"],
                        calories_per_100g=item["calories_per_100g"],
                        protein_per_100g=item["protein_per_100g"],
                        fat_per_100g=item["fat_per_100g"],
                        carbs_per_100g=item["carbs_per_100g"],
                        is_manually_corrected=item["is_manually_corrected"],
                    ))
                dish.composition_fingerprint = _composition_fingerprint(normalized_items)
            dish.updated_at = datetime.utcnow()
            session.commit()
        # Return a freshly loaded, usable object rather than a detached object
        # whose relationship was modified during the transaction.
        from database.repositories.dish_repository import DishRepository
        return DishRepository.get_by_id(str(user_id), int(dish_id))

    @staticmethod
    def rename(*, user_id: str, dish_id: int, name: str) -> Dish | None:
        clean_name = re.sub(r"\s+", " ", str(name or "").strip())
        if not clean_name or len(clean_name) > MAX_DISH_NAME_LENGTH:
            return None
        return DishService.update_template(user_id=user_id, dish_id=dish_id, name=clean_name)

    @staticmethod
    def replace_ingredients(*, user_id: str, dish_id: int, items: list[dict]) -> Dish | None:
        return DishService.update_template(user_id=user_id, dish_id=dish_id, items=items)

    @staticmethod
    def add_ingredient(*, user_id: str, dish_id: int, item: dict) -> Dish | None:
        from database.repositories.dish_repository import DishRepository
        dish = DishRepository.get_by_id(str(user_id), int(dish_id))
        if dish is None:
            return None
        return DishService.replace_ingredients(
            user_id=user_id, dish_id=dish_id,
            items=[*dish_to_snapshot(dish), normalize_ingredient_snapshot(item)],
        )

    @staticmethod
    def remove_ingredient(*, user_id: str, dish_id: int, position: int) -> Dish | None:
        from database.repositories.dish_repository import DishRepository
        dish = DishRepository.get_by_id(str(user_id), int(dish_id))
        if dish is None:
            return None
        items = dish_to_snapshot(dish)
        if len(items) <= 1 or position < 0 or position >= len(items):
            return None
        items.pop(position)
        return DishService.replace_ingredients(user_id=user_id, dish_id=dish_id, items=items)

    @staticmethod
    def save_photo_dish_entry(
        *,
        save_token: str,
        user_id: str,
        dish_name: str,
        items: list[dict],
        entry_date: date,
        meal_type: str,
        provider: str | None = None,
    ) -> DishEntrySaveResult:
        if not save_token or len(save_token) > 64:
            return DishEntrySaveResult(MealSaveStatus.FAILED, error_type="invalid_save_token")
        normalized_items = [normalize_ingredient_snapshot(item) for item in items if isinstance(item, dict)]
        if not normalized_items:
            return DishEntrySaveResult(MealSaveStatus.FAILED, error_type="empty_dish")
        name = normalize_dish_display_name(dish_name, normalized_items)
        totals = calculate_dish_totals(normalized_items)
        user_id = str(user_id)

        try:
            with get_db_session() as session:
                existing = (
                    session.query(Meal)
                    .filter(Meal.save_token == save_token, Meal.user_id == user_id)
                    .first()
                )
                if existing is not None:
                    dish = (
                        session.query(Dish)
                        .options(selectinload(Dish.ingredients))
                        .filter(Dish.id == existing.dish_id, Dish.user_id == user_id)
                        .first()
                        if existing.dish_id
                        else None
                    )
                    return DishEntrySaveResult(MealSaveStatus.ALREADY_SAVED, existing, dish)

                dish = Dish(
                    user_id=user_id,
                    name=name,
                    normalized_name=name.casefold(),
                    source="photo_analysis",
                    source_provider=(str(provider).strip() or None) if provider else None,
                    composition_fingerprint=_composition_fingerprint(normalized_items),
                    save_token=save_token,
                )
                session.add(dish)
                session.flush()
                for position, item in enumerate(normalized_items):
                    dish.ingredients.append(
                        DishIngredient(
                            position=position,
                            name_snapshot=item["name"],
                            weight_g=item["grams"],
                            calories_per_100g=item["calories_per_100g"],
                            protein_per_100g=item["protein_per_100g"],
                            fat_per_100g=item["fat_per_100g"],
                            carbs_per_100g=item["carbs_per_100g"],
                            is_manually_corrected=item["is_manually_corrected"],
                        )
                    )

                meal = Meal(
                    user_id=user_id,
                    raw_query=name,
                    description=name,
                    products_json=json.dumps(normalized_items, ensure_ascii=False),
                    api_details=_api_details(normalized_items),
                    calories=totals["calories"],
                    protein=totals["protein"],
                    fat=totals["fat"],
                    carbs=totals["carbs"],
                    is_manually_corrected=any(item["is_manually_corrected"] for item in normalized_items),
                    meal_type=normalize_meal_type(meal_type),
                    date=entry_date,
                    save_token=save_token,
                    entry_kind="dish",
                    dish_id=dish.id,
                    dish_name_snapshot=name,
                    entry_source="photo_analysis",
                )
                session.add(meal)
                session.commit()
                session.refresh(meal)
                session.refresh(dish)
        except IntegrityError:
            with get_db_session() as session:
                existing = (
                    session.query(Meal)
                    .filter(Meal.save_token == save_token, Meal.user_id == user_id)
                    .first()
                )
                if existing is not None:
                    dish = (
                        session.query(Dish)
                        .options(selectinload(Dish.ingredients))
                        .filter(Dish.id == existing.dish_id, Dish.user_id == user_id)
                        .first()
                        if existing.dish_id
                        else None
                    )
                    return DishEntrySaveResult(MealSaveStatus.ALREADY_SAVED, existing, dish)
            return DishEntrySaveResult(MealSaveStatus.FAILED, error_type="IntegrityError")
        except Exception as exc:
            return DishEntrySaveResult(
                MealSaveStatus.FAILED,
                error_type=safe_exception_summary(exc),
            )

        MealRepository._track_saved_meal(user_id)
        return DishEntrySaveResult(MealSaveStatus.SAVED, meal, dish)

    @staticmethod
    def add_saved_dish_to_diary(
        *,
        save_token: str,
        user_id: str,
        dish_id: int,
        entry_date: date,
        meal_type: str,
        items: list[dict] | None = None,
    ) -> MealSaveResult:
        if not save_token or len(save_token) > 64:
            return MealSaveResult(MealSaveStatus.FAILED, error_type="invalid_save_token")
        user_id = str(user_id)
        try:
            with get_db_session() as session:
                existing = (
                    session.query(Meal)
                    .filter(Meal.save_token == save_token, Meal.user_id == user_id)
                    .first()
                )
                if existing is not None:
                    return MealSaveResult(MealSaveStatus.ALREADY_SAVED, existing)
                dish = (
                    session.query(Dish)
                    .options(selectinload(Dish.ingredients))
                    .filter(
                        Dish.id == int(dish_id),
                        Dish.user_id == user_id,
                        Dish.archived_at.is_(None),
                    )
                    .first()
                )
                if dish is None:
                    return MealSaveResult(MealSaveStatus.FAILED, error_type="dish_not_found")
                snapshot = [normalize_ingredient_snapshot(item) for item in items] if items else dish_to_snapshot(dish)
                if not snapshot:
                    return MealSaveResult(MealSaveStatus.FAILED, error_type="empty_dish")
                totals = calculate_dish_totals(snapshot)
                meal = Meal(
                    user_id=user_id,
                    raw_query=dish.name,
                    description=dish.name,
                    products_json=json.dumps(snapshot, ensure_ascii=False),
                    api_details=_api_details(snapshot),
                    calories=totals["calories"],
                    protein=totals["protein"],
                    fat=totals["fat"],
                    carbs=totals["carbs"],
                    is_manually_corrected=any(bool(item.get("is_manually_corrected")) for item in snapshot),
                    meal_type=normalize_meal_type(meal_type),
                    date=entry_date,
                    save_token=save_token,
                    entry_kind="dish",
                    dish_id=dish.id,
                    dish_name_snapshot=dish.name,
                    entry_source="saved_dish",
                )
                session.add(meal)
                session.commit()
                session.refresh(meal)
        except IntegrityError:
            with get_db_session() as session:
                existing = (
                    session.query(Meal)
                    .filter(Meal.save_token == save_token, Meal.user_id == user_id)
                    .first()
                )
                if existing is not None:
                    return MealSaveResult(MealSaveStatus.ALREADY_SAVED, existing)
            return MealSaveResult(MealSaveStatus.FAILED, error_type="IntegrityError")
        except Exception as exc:
            return MealSaveResult(MealSaveStatus.FAILED, error_type=safe_exception_summary(exc))

        MealRepository._track_saved_meal(user_id)
        return MealSaveResult(MealSaveStatus.SAVED, meal)
