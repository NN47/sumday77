"""Минимальный контекст из БД и программные агрегаты для одного вопроса."""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
import re

from sqlalchemy.orm import selectinload

from database.models import (
    DailySteps, Dish, KbjuSettings, Meal, TimedActivityEntry, Workout, WorkoutSession,
)
from database.session import get_db_session


def _round(value) -> float:
    return round(float(value or 0), 1)


def _period(question: str, history: list[dict[str, str]], today: date) -> tuple[date, date, str]:
    text = question.casefold()
    prior = " ".join(item["content"] for item in history[-4:] if item["role"] == "user").casefold()
    if "прошл" in text and "недел" in (text + prior):
        start = today - timedelta(days=today.weekday() + 7)
        return start, start + timedelta(days=6), "прошлая неделя"
    if "две недел" in text or "14 д" in text:
        return today - timedelta(days=13), today, "последние 14 дней"
    if "недел" in text or (re.search(r"по сравнени|а за прошл", text) and "недел" in prior):
        return today - timedelta(days=6), today, "последние 7 дней"
    if "вчера" in text:
        return today - timedelta(days=1), today - timedelta(days=1), "вчера"
    return today, today, "сегодня"


def _dish_payload(dish: Dish) -> dict:
    totals = {"calories": Decimal(0), "protein": Decimal(0), "fat": Decimal(0), "carbs": Decimal(0)}
    ingredients = []
    for item in dish.ingredients:
        weight = Decimal(item.weight_g or 0)
        ingredients.append(item.name_snapshot)
        for key in totals:
            totals[key] += Decimal(getattr(item, f"{key}_per_100g") or 0) * weight / 100
    return {"name": dish.name, "ingredients": ingredients, **{key: _round(value) for key, value in totals.items()}}


def build_user_context(user_id: str, question: str, history: list[dict[str, str]], *, today: date | None = None) -> dict:
    """Выбирает только относящийся к intent срез; данные самочувствия намеренно не импортируются."""
    today = today or date.today()
    start, end, label = _period(question, history, today)
    q = question.casefold()
    wants_recipes = bool(re.search(r"рецепт|сохран[её]нн.*блюд|творог", q))
    wants_activity = bool(re.search(r"активност|трениров|шаг", q))
    context: dict = {"period": label, "date_from": start.isoformat(), "date_to": end.isoformat()}

    with get_db_session() as session:
        settings = session.query(KbjuSettings).filter(KbjuSettings.user_id == str(user_id)).first()
        if not wants_recipes and not wants_activity:
            meals = session.query(Meal).filter(
                Meal.user_id == str(user_id), Meal.date >= start, Meal.date <= end
            ).order_by(Meal.date, Meal.id).all()
            daily = {}
            for meal in meals:
                row = daily.setdefault(meal.date.isoformat(), {"calories": 0, "protein": 0, "fat": 0, "carbs": 0, "meals": []})
                for key in ("calories", "protein", "fat", "carbs"):
                    row[key] += float(getattr(meal, key) or 0)
                row["meals"].append(meal.description or meal.raw_query or "Приём пищи")
            for row in daily.values():
                for key in ("calories", "protein", "fat", "carbs"):
                    row[key] = _round(row[key])
            context["nutrition_by_day"] = daily
            if daily:
                count = (end - start).days + 1
                context["averages"] = {key: _round(sum(row[key] for row in daily.values()) / count) for key in ("calories", "protein", "fat", "carbs")}
            if settings:
                context["goals"] = {key: _round(getattr(settings, key)) for key in ("calories", "protein", "fat", "carbs")}
                if start == end == today:
                    totals = next(iter(daily.values()), {})
                    context["remaining_today"] = {key: _round(max(0, getattr(settings, key) - float(totals.get(key, 0)))) for key in ("calories", "protein", "fat", "carbs")}

        if wants_recipes:
            query = session.query(Dish).options(selectinload(Dish.ingredients)).filter(
                Dish.user_id == str(user_id), Dish.archived_at.is_(None)
            )
            # Search is narrowed in SQL when a likely ingredient/name term is supplied.
            terms = [word for word in re.findall(r"[а-яёa-z]{4,}", q) if word not in {"найди", "рецепт", "самый", "белковый", "сохранённых", "блюд", "подойдет", "сейчас"}]
            dishes = query.order_by(Dish.updated_at.desc()).limit(30).all()
            payload = [_dish_payload(dish) for dish in dishes]
            if terms:
                relevant = [dish for dish in payload if any(term in (dish["name"] + " " + " ".join(dish["ingredients"])).casefold() for term in terms)]
                payload = relevant or payload[:10]
            context["recipes"] = payload[:10]

        if wants_activity:
            steps = session.query(DailySteps).filter(DailySteps.user_id == str(user_id), DailySteps.entry_date >= start, DailySteps.entry_date <= end).all()
            timed = session.query(TimedActivityEntry).filter(TimedActivityEntry.user_id == str(user_id), TimedActivityEntry.entry_date >= start, TimedActivityEntry.entry_date <= end).all()
            legacy = session.query(Workout).filter(Workout.user_id == str(user_id), Workout.date >= start, Workout.date <= end).all()
            sessions = session.query(WorkoutSession).filter(WorkoutSession.user_id == str(user_id), WorkoutSession.entry_date >= start, WorkoutSession.entry_date <= end, WorkoutSession.status == "completed").all()
            context["activity"] = {
                "steps": [{"date": row.entry_date.isoformat(), "steps": row.steps} for row in steps],
                "activities": [{"date": row.entry_date.isoformat(), "name": row.activity_name_snapshot, "minutes": _round(row.duration_minutes), "calories": _round(row.credited_calories)} for row in timed],
                "legacy_workouts": [{"date": row.date.isoformat(), "name": row.exercise, "calories": _round(row.calories)} for row in legacy],
                "workout_sessions": [{"date": row.entry_date.isoformat(), "duration_minutes": _round((row.duration_seconds or 0) / 60), "calories": _round(row.credited_calories)} for row in sessions],
            }

    context["has_numeric_data"] = any(key in context for key in ("nutrition_by_day", "recipes", "activity")) and bool(
        context.get("nutrition_by_day") or context.get("recipes") or any(context.get("activity", {}).values())
    )
    return context
