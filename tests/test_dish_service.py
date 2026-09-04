from contextlib import contextmanager
from datetime import date
import json
import os
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

os.environ.setdefault("API_TOKEN", "test-token")
os.environ.setdefault("GEMINI_API_KEY", "test-gemini-key")

from database.models import Base, Dish, DishIngredient, Meal
import database.repositories.dish_repository as dish_repository_module
import services.dish_service as dish_service_module
from database.repositories.meal_repository import MealSaveStatus
from handlers import meals
from services.dish_service import DishService, scale_dish_snapshot
from services.photo_food_validator import validate_photo_food_payload
from utils.meal_formatters import format_meal_edit_details, format_meal_message


@pytest.fixture
def dish_db(monkeypatch):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)

    @contextmanager
    def session_provider():
        session = session_factory()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    monkeypatch.setattr(dish_service_module, "get_db_session", session_provider)
    monkeypatch.setattr(dish_repository_module, "get_db_session", session_provider)
    monkeypatch.setattr(dish_service_module.MealRepository, "_track_saved_meal", lambda _user_id: None)
    yield session_factory
    engine.dispose()


def _items():
    return [
        {"name": "Хлеб", "grams": 120, "kcal": 300, "protein": 10, "fat": 4, "carbs": 55},
        {"name": "Буженина", "grams": 70, "kcal": 180, "protein": 20, "fat": 11, "carbs": 0},
    ]


def test_photo_save_creates_one_dish_and_one_diary_snapshot_atomically(dish_db):
    result = DishService.save_photo_dish_entry(
        save_token="A" * 22,
        user_id="42",
        dish_name="Бутерброд с бужениной",
        items=_items(),
        entry_date=date(2026, 8, 16),
        meal_type="lunch",
        provider="gemini",
    )

    assert result.status is MealSaveStatus.SAVED
    with dish_db() as session:
        assert session.query(Dish).count() == 1
        assert session.query(DishIngredient).count() == 2
        assert session.query(Meal).count() == 1
        meal = session.query(Meal).one()
        assert meal.entry_kind == "dish"
        assert meal.dish_id == result.dish.id
        assert meal.dish_name_snapshot == "Бутерброд с бужениной"
        assert meal.calories == pytest.approx(480)
        assert [item["name"] for item in json.loads(meal.products_json)] == ["Хлеб", "Буженина"]
        current = meals._format_current_meal_after_save_message("lunch", [meal], meal.date)
        assert "• <b>Бутерброд с бужениной</b> (190 г)" in current
        assert "• <b>Хлеб</b>" not in current
        assert "• <b>Буженина</b>" not in current


def test_same_photo_save_token_does_not_duplicate_dish_or_meal(dish_db):
    kwargs = dict(
        save_token="B" * 22,
        user_id="42",
        dish_name="Бутерброд",
        items=_items(),
        entry_date=date(2026, 8, 16),
        meal_type="lunch",
    )
    first = DishService.save_photo_dish_entry(**kwargs)
    second = DishService.save_photo_dish_entry(**kwargs)

    assert first.status is MealSaveStatus.SAVED
    assert second.status is MealSaveStatus.ALREADY_SAVED
    with dish_db() as session:
        assert session.query(Dish).count() == 1
        assert session.query(Meal).count() == 1


def test_repeat_add_uses_scaled_snapshot_without_creating_another_template(dish_db):
    created = DishService.save_photo_dish_entry(
        save_token="C" * 22,
        user_id="42",
        dish_name="Бутерброд",
        items=_items(),
        entry_date=date(2026, 8, 16),
        meal_type="lunch",
    )
    scaled = scale_dish_snapshot(_items(), 95)
    repeated = DishService.add_saved_dish_to_diary(
        save_token="D" * 22,
        user_id="42",
        dish_id=created.dish.id,
        entry_date=date(2026, 8, 17),
        meal_type="breakfast",
        items=scaled,
    )

    assert repeated.status is MealSaveStatus.SAVED
    with dish_db() as session:
        assert session.query(Dish).count() == 1
        assert session.query(Meal).count() == 2
        second = session.query(Meal).filter(Meal.id == repeated.meal.id).one()
        snapshot = json.loads(second.products_json)
        assert sum(item["grams"] for item in snapshot) == pytest.approx(95)
        assert second.calories == pytest.approx(240)
        current = meals._format_current_meal_after_save_message("breakfast", [second], second.date)
        assert "• <b>Бутерброд</b> (95 г)" in current
        assert "<b>240 ккал</b> <i>(Б 15.0 / Ж 7.5 / У 27.5)</i>" in current
        assert "• <b>Хлеб</b>" not in current
        assert "• <b>Буженина</b>" not in current


def test_diary_and_current_meal_use_dish_title_while_editor_keeps_ingredients():
    entry = SimpleNamespace(
        entry_kind="dish",
        dish_name_snapshot="Бутерброд с бужениной",
        description="Бутерброд с бужениной",
        products_json=json.dumps(_items(), ensure_ascii=False),
        calories=480,
        protein=30,
        fat=15,
        carbs=55,
    )

    compact = format_meal_message("lunch", [entry])
    assert "Бутерброд с бужениной" in compact
    assert "Хлеб" not in compact
    assert "Буженина" not in compact
    current = meals._format_current_meal_after_save_message("lunch", [entry], date(2026, 8, 16))
    assert "• <b>Бутерброд с бужениной</b> (190 г)" in current
    assert "Хлеб" not in current
    assert "Буженина" not in current
    editable = meals._extract_products_for_edit(entry)
    assert editable == _items()
    editor = format_meal_edit_details("lunch", editable)
    assert "• <b>Хлеб</b> (120 г)" in editor
    assert "• <b>Буженина</b> (70 г)" in editor


def test_new_dish_entries_are_not_expanded_as_my_products_but_legacy_photo_rows_are():
    dish_meal = SimpleNamespace(
        id=1,
        entry_kind="dish",
        products_json=json.dumps(_items(), ensure_ascii=False),
    )
    legacy_meal = SimpleNamespace(
        id=2,
        entry_kind="products",
        products_json=json.dumps(
            [{**_items()[0], "source": "photo_analysis"}],
            ensure_ascii=False,
        ),
    )

    dish_items = meals._expand_my_products([dish_meal], source_filter="all")
    legacy_items = meals._expand_my_products([legacy_meal], source_filter="photo_analysis")
    assert dish_items == []
    assert [item.title for item in legacy_items] == ["Хлеб"]


def test_validator_preserves_separate_dishes_until_user_choice():
    payload = {
        "dishes": [
            {"dish_name": "Суп", "ingredients": [{"name": "Суп", "grams": 300, "kcal": 150, "protein": 8, "fat": 5, "carbs": 18}]},
            {"dish_name": "Салат", "ingredients": [{"name": "Овощи", "grams": 180, "kcal": 90, "protein": 3, "fat": 4, "carbs": 10}]},
        ]
    }

    validated = validate_photo_food_payload(payload)
    assert [dish["dish_name"] for dish in validated["dishes"]] == ["Суп", "Салат"]
    assert "items" not in validated


def test_validator_rejects_zero_weight_ingredient():
    payload = {
        "dish_name": "Суп",
        "items": [{"name": "Суп", "grams": 0, "kcal": 0, "protein": 0, "fat": 0, "carbs": 0}],
    }
    assert validate_photo_food_payload(payload) is None
