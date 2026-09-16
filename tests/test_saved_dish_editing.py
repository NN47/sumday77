from contextlib import contextmanager
import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database.models import Base, Dish, DishIngredient, Meal, SavedProduct
import database.repositories.dish_repository as repository_module
import services.dish_service as service_module
from services.dish_service import DishService, calculate_dish_totals, calculate_dish_weight, dish_to_snapshot
from handlers import meals


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


def test_editor_formats_actual_ingredient_kbju_and_shared_dish_totals():
    dish = SimpleNamespace(id=7, name="Бутерброд")
    items = [
        _ingredient("Хлеб", 110, 275, 8.8, 3.3, 52.8),
        _ingredient("Сыр", 20, 70, 5, 5.4, 0.2),
    ]

    text = meals._format_saved_dish_editor(dish, items)

    assert "1️⃣ <b>Хлеб (110 г)</b>" in text
    assert "275 ккал (Б 8.8 / Ж 3.3 / У 52.8)" in text
    assert "2️⃣ <b>Сыр (20 г)</b>" in text
    assert "70 ккал (Б 5.0 / Ж 5.4 / У 0.2)" in text
    assert "<b>Итого по блюду:</b>" in text
    assert "📦 <b>Общий вес:</b> 130 г" in text
    assert "🔥 <b>Калории:</b> 345 ккал" in text
    assert "🥩 <b>Белки:</b> 13.8 г" in text


def test_editor_uses_explicit_done_action():
    keyboard = meals._build_saved_dish_editor_keyboard(
        7, [_ingredient("Хлеб"), _ingredient("Сыр")]
    )

    last_button = keyboard.inline_keyboard[-1][0]
    assert last_button.text == "✅ Готово"
    assert last_button.callback_data == "my_dish_edit_done:7"
    assert all(button.text != "⬅️ Назад" for row in keyboard.inline_keyboard for button in row)


def test_done_clears_edit_context_hides_reply_keyboard_and_opens_fresh_card():
    dish = SimpleNamespace(id=7, name="Новый бутерброд")
    items = [_ingredient("Хлеб", 120, 300, 9.6, 3.6, 57.6)]
    message = SimpleNamespace(edit_text=AsyncMock())
    callback = SimpleNamespace(
        data="my_dish_edit_done:7",
        from_user=SimpleNamespace(id=42),
        message=message,
        answer=AsyncMock(),
    )

    class State:
        def __init__(self):
            self.data = {
                "meal_type": "lunch",
                "my_dishes_page": 2,
                "dish_edit_mode": True,
                "dish_edit_id": 7,
                "dish_edit_ingredient_id": 0,
                "dish_add_destination": True,
                "custom_product": {"name": "stale"},
            }
            self.clear = AsyncMock(side_effect=self.data.clear)

        async def get_data(self):
            return dict(self.data)

        async def update_data(self, **values):
            self.data.update(values)

    state = State()
    with patch.object(meals.DishRepository, "get_by_id", return_value=dish), patch.object(
        meals, "dish_to_snapshot", return_value=items
    ), patch.object(meals, "_hide_meal_reply_keyboard", new=AsyncMock()) as hide_keyboard:
        asyncio.run(meals.my_dish_edit_done(callback, state))

    state.clear.assert_awaited_once()
    hide_keyboard.assert_awaited_once_with(message)
    assert "dish_edit_mode" not in state.data
    assert "dish_edit_ingredient_id" not in state.data
    assert "dish_add_destination" not in state.data
    assert "custom_product" not in state.data
    assert state.data["my_dish_items"] == items
    rendered_text = message.edit_text.await_args.args[0]
    assert "Новый бутерброд" in rendered_text
    assert "300 ккал" in rendered_text
