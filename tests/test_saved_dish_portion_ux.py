import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from handlers import meals
from services.dish_service import calculate_dish_totals, calculate_dish_weight


class State:
    def __init__(self, **data):
        self.data = data
        self.current = None

    async def get_data(self):
        return dict(self.data)

    async def update_data(self, **values):
        self.data.update(values)

    async def set_state(self, value):
        self.current = value


def _items():
    return [
        {"name": "Мясо", "grams": 900, "kcal": 1200, "protein": 180, "fat": 48, "carbs": 0},
        {"name": "Лук", "grams": 135, "kcal": 127, "protein": 30, "fat": 5.4, "carbs": 1.2},
    ]


def _callback(data):
    return SimpleNamespace(
        data=data, from_user=SimpleNamespace(id=42), answer=AsyncMock(),
        message=SimpleNamespace(edit_text=AsyncMock(), answer=AsyncMock(), bot=SimpleNamespace()),
    )


def _state():
    items = _items()
    return State(my_dish_id=7, my_dish_items=items, my_dish_original_items=items,
                 my_dish_save_token="S" * 22, meal_type="breakfast", my_dishes_page=1)


def test_fraction_selection_always_scales_from_original(monkeypatch):
    dish = SimpleNamespace(id=7, name="Шашлык")
    monkeypatch.setattr(meals.DishRepository, "get_by_id", lambda *_: dish)
    state = _state()
    callback = _callback("my_dish_portion:7:third")

    asyncio.run(meals.my_dish_portion_select(callback, state))
    third = state.data["my_dish_portion_items"]
    assert calculate_dish_weight(third) == pytest.approx(345)
    totals = calculate_dish_totals(third)
    assert totals["calories"] == pytest.approx(1327 / 3)
    assert totals["protein"] == pytest.approx(70)

    callback.data = "my_dish_portion:7:half"
    asyncio.run(meals.my_dish_portion_select(callback, state))
    half = state.data["my_dish_portion_items"]
    assert calculate_dish_weight(half) == pytest.approx(517.5)
    assert calculate_dish_totals(half)["calories"] == pytest.approx(663.5)
    assert calculate_dish_weight(state.data["my_dish_original_items"]) == pytest.approx(1035)


def test_whole_dish_matches_original(monkeypatch):
    monkeypatch.setattr(meals.DishRepository, "get_by_id", lambda *_: SimpleNamespace(id=7, name="Шашлык"))
    state = _state()
    asyncio.run(meals.my_dish_portion_select(_callback("my_dish_portion:7:whole"), state))
    assert calculate_dish_weight(state.data["my_dish_portion_items"]) == calculate_dish_weight(_items())
    assert calculate_dish_totals(state.data["my_dish_portion_items"]) == calculate_dish_totals(_items())


def test_manual_portion_scales_and_accepts_more_than_whole(monkeypatch):
    monkeypatch.setattr(meals.DishRepository, "get_by_id", lambda *_: SimpleNamespace(id=7, name="Шашлык"))
    state = _state()
    message = SimpleNamespace(text="1500", from_user=SimpleNamespace(id=42), answer=AsyncMock())
    asyncio.run(meals.my_dish_portion_manual_apply(message, state))
    assert calculate_dish_weight(state.data["my_dish_portion_items"]) == pytest.approx(1500)
    assert calculate_dish_totals(state.data["my_dish_portion_items"])["calories"] == pytest.approx(1327 * 1500 / 1035)


@pytest.mark.parametrize("value", ["0", "-10", "текст", "NaN", "inf", "-inf"])
def test_invalid_manual_portion_is_rejected(value, monkeypatch):
    monkeypatch.setattr(meals.DishRepository, "get_by_id", lambda *_: SimpleNamespace(id=7, name="Шашлык"))
    state = _state()
    message = SimpleNamespace(text=value, from_user=SimpleNamespace(id=42), answer=AsyncMock())
    asyncio.run(meals.my_dish_portion_manual_apply(message, state))
    assert state.data.get("my_dish_portion_items") is None
    assert "Введи вес числом" in message.answer.await_args.args[0] or "не меньше 1 г" in message.answer.await_args.args[0]


def test_back_restores_card_with_original_snapshot(monkeypatch):
    dish = SimpleNamespace(id=7, name="Шашлык", source="manual")
    monkeypatch.setattr(meals.DishRepository, "get_by_id", lambda *_: dish)
    state = _state()
    state.data["my_dish_portion_items"] = [{**_items()[0], "grams": 300}]
    callback = _callback("my_dish_portion_back:7")
    asyncio.run(meals.my_dish_portion_back(callback, state))
    assert state.data["my_dish_portion_items"] is None
    assert "Общий вес:</b> 1035 г" in callback.message.edit_text.await_args.args[0]
    buttons = [button.text for row in callback.message.edit_text.await_args.kwargs["reply_markup"].inline_keyboard for button in row]
    assert "⚖️ Изменить общий вес" not in buttons
