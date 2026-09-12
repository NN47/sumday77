from contextlib import contextmanager
import json

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database.models import Base, Dish, DishIngredient, Meal, SavedProduct
import database.repositories.dish_repository as repository_module
import services.dish_service as service_module
from services.dish_service import DishService, calculate_dish_totals, calculate_dish_weight, dish_to_snapshot


def _provider():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)

    @contextmanager
    def sessions():
        session = factory()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()
    return engine, factory, sessions


def _ingredient(name="Хлеб", grams=100, kcal=250, protein=8, fat=3, carbs=48):
    return {"name": name, "grams": grams, "kcal": kcal, "protein": protein, "fat": fat, "carbs": carbs}


def _seed(factory):
    with factory() as session:
        dish = Dish(user_id="42", name="Бутерброд", normalized_name="бутерброд", source="test")
        dish.ingredients = [
            DishIngredient(position=0, name_snapshot="Хлеб", weight_g=100, calories_per_100g=250, protein_per_100g=8, fat_per_100g=3, carbs_per_100g=48),
            DishIngredient(position=1, name_snapshot="Сыр", weight_g=20, calories_per_100g=350, protein_per_100g=25, fat_per_100g=27, carbs_per_100g=1),
        ]
        other = Dish(user_id="42", name="Другое", normalized_name="другое", source="test")
        other.ingredients = [DishIngredient(position=0, name_snapshot="Хлеб", weight_g=50, calories_per_100g=250, protein_per_100g=8, fat_per_100g=3, carbs_per_100g=48)]
        saved = SavedProduct(user_id="42", normalized_name="хлеб", name="Хлеб", last_weight_g=100)
        history = Meal(user_id="42", raw_query="Бутерброд", description="Бутерброд", products_json=json.dumps([_ingredient()]), calories=250, protein=8, fat=3, carbs=48, meal_type="breakfast", dish_name_snapshot="Старое название")
        session.add_all([dish, other, saved, history]); session.commit()
        return dish.id, other.id, history.id


def test_rename_and_composition_updates_are_isolated(monkeypatch):
    engine, factory, provider = _provider()
    monkeypatch.setattr(service_module, "get_db_session", provider)
    monkeypatch.setattr(repository_module, "get_db_session", provider)
    dish_id, other_id, history_id = _seed(factory)

    renamed = DishService.rename(user_id="42", dish_id=dish_id, name="  Новый   бутерброд ")
    assert renamed.name == "Новый бутерброд"
    updated = DishService.replace_ingredients(user_id="42", dish_id=dish_id, items=[_ingredient(grams=150, kcal=375)])
    assert calculate_dish_weight(dish_to_snapshot(updated)) == 150
    assert calculate_dish_totals(dish_to_snapshot(updated))["calories"] == 375

    with factory() as session:
        assert session.get(Dish, other_id).ingredients[0].weight_g == 50
        assert session.query(SavedProduct).one().last_weight_g == 100
        history = session.get(Meal, history_id)
        assert history.dish_name_snapshot == "Старое название"
        assert json.loads(history.products_json)[0]["grams"] == 100
    engine.dispose()


def test_add_remove_and_last_ingredient_guard(monkeypatch):
    engine, factory, provider = _provider()
    monkeypatch.setattr(service_module, "get_db_session", provider)
    monkeypatch.setattr(repository_module, "get_db_session", provider)
    dish_id, _, _ = _seed(factory)

    dish = DishService.add_ingredient(user_id="42", dish_id=dish_id, item=_ingredient("Огурец", 80, 12, 1, 0, 2))
    assert [item["name"] for item in dish_to_snapshot(dish)] == ["Хлеб", "Сыр", "Огурец"]
    dish = DishService.remove_ingredient(user_id="42", dish_id=dish_id, position=1)
    assert [item["name"] for item in dish_to_snapshot(dish)] == ["Хлеб", "Огурец"]
    dish = DishService.remove_ingredient(user_id="42", dish_id=dish_id, position=1)
    assert DishService.remove_ingredient(user_id="42", dish_id=dish_id, position=0) is None
    assert len(dish_to_snapshot(dish)) == 1
    engine.dispose()


def test_invalid_or_foreign_updates_are_rejected(monkeypatch):
    engine, factory, provider = _provider()
    monkeypatch.setattr(service_module, "get_db_session", provider)
    monkeypatch.setattr(repository_module, "get_db_session", provider)
    dish_id, _, _ = _seed(factory)
    assert DishService.rename(user_id="42", dish_id=dish_id, name="") is None
    assert DishService.replace_ingredients(user_id="42", dish_id=dish_id, items=[]) is None
    assert DishService.add_ingredient(user_id="another", dish_id=dish_id, item=_ingredient()) is None
    engine.dispose()
