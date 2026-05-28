import asyncio
from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from handlers import meals


class _DummyState:
    def __init__(self):
        self._data = {}
        self.set_state = AsyncMock()
        self.clear = AsyncMock()

    async def update_data(self, **kwargs):
        self._data.update(kwargs)

    async def get_data(self):
        return dict(self._data)



def _build_message():
    return SimpleNamespace(answer=AsyncMock(), bot=SimpleNamespace())


def _build_callback(callback_data: str):
    callback = SimpleNamespace()
    callback.data = callback_data
    callback.from_user = SimpleNamespace(id=12345)
    callback.message = _build_message()
    callback.answer = AsyncMock()
    return callback


def test_show_input_methods_sends_add_menu():
    message = _build_message()
    message.from_user = SimpleNamespace(id=12345)
    state = _DummyState()

    with patch("handlers.meals.push_menu_stack") as push_stack, patch(
        "handlers.meals.MealRepository.get_recent_unique_meals", return_value=[]
    ):
        asyncio.run(meals._show_input_methods(message, state))

    state.set_state.assert_awaited_once_with(meals.MealEntryStates.choosing_meal_type)
    push_stack.assert_called_once_with(message.bot, meals.kbju_add_menu)
    assert message.answer.await_count == 1


def test_add_meal_from_diary_block_sets_context_and_opens_methods():
    target_date = date.today().isoformat()
    callback = _build_callback(f"add_meal:lunch:{target_date}")
    state = _DummyState()

    with patch("handlers.meals._show_input_methods", new=AsyncMock()) as show_methods:
        asyncio.run(meals.add_meal_from_diary_block(callback, state))

    callback.answer.assert_awaited_once()
    assert state._data["meal_type"] == "lunch"
    assert state._data["entry_date"] == target_date
    show_methods.assert_awaited_once_with(callback.message, state, user_id="12345")


def test_meal_type_navigation_back_supports_hook_arrow():
    message = _build_message()
    message.text = "↩️ Назад"
    state = SimpleNamespace(clear=AsyncMock())

    with patch("handlers.common.go_back", new=AsyncMock()) as go_back:
        asyncio.run(meals.handle_meal_type_menu_navigation(message, state))

    state.clear.assert_awaited_once()
    go_back.assert_awaited_once_with(message, state)


def test_meal_type_navigation_main_menu_alias():
    message = _build_message()
    message.text = "🔄 Главное меню"
    state = SimpleNamespace(clear=AsyncMock())

    with patch("handlers.common.go_main_menu", new=AsyncMock()) as go_main_menu:
        asyncio.run(meals.handle_meal_type_menu_navigation(message, state))

    state.clear.assert_awaited_once()
    go_main_menu.assert_awaited_once_with(message, state)


def test_extract_recent_meal_amount_g_from_products_json():
    meal = SimpleNamespace(products_json='[{"name":"x","grams":40}]')
    assert meals._extract_recent_meal_amount_g(meal) == 40


def test_extract_recent_meal_amount_g_fallback_to_100():
    meal = SimpleNamespace(products_json='[{"name":"x","grams":"oops"}]')
    assert meals._extract_recent_meal_amount_g(meal) == 100


def test_expand_recent_meals_splits_multi_product_entries():
    meal = SimpleNamespace(
        id=7,
        raw_query="Кура гриль 150 г, окрошка 200 г",
        description=None,
        products_json=(
            '[{"name":"Кура гриль","grams":150,"kcal":249,"protein":35,"fat":9,"carbs":16},'
            '{"name":"Окрошка без заправки","grams":200,"kcal":120,"protein":6,"fat":3,"carbs":18}]'
        ),
        calories=369,
        protein=41,
        fat=12,
        carbs=34,
    )

    items = meals._expand_recent_meals([meal])

    assert [item.title for item in items] == ["Кура гриль", "Окрошка без заправки"]
    assert [item.product_index for item in items] == [0, 1]
    assert items[0].amount_g == 150
    assert items[1].calories == 120


def test_recent_confirm_uses_single_selected_product():
    callback = _build_callback("recent_meal_confirm:dinner:1:7:1")
    state = _DummyState()
    meal = SimpleNamespace(
        id=7,
        raw_query="Кура гриль 150 г, окрошка 200 г",
        description=None,
        products_json=(
            '[{"name":"Кура гриль","grams":150,"kcal":249,"protein":35,"fat":9,"carbs":16},'
            '{"name":"Окрошка без заправки","grams":200,"kcal":120,"protein":6,"fat":3,"carbs":18}]'
        ),
        calories=369,
        protein=41,
        fat=12,
        carbs=34,
        api_details=None,
    )
    saved = SimpleNamespace(id=99)

    with patch("handlers.meals.MealRepository.get_meal_by_id", return_value=meal), patch(
        "handlers.meals.MealRepository.save_meal", return_value=saved
    ) as save_meal, patch("handlers.meals._render_day_meals_messages", new=AsyncMock()):
        asyncio.run(meals.recent_meal_confirm(callback, state))

    save_meal.assert_called_once()
    kwargs = save_meal.call_args.kwargs
    assert kwargs["raw_query"] == "Окрошка без заправки"
    assert kwargs["calories"] == 120
    assert kwargs["protein"] == 6
    assert kwargs["products_json"] == '[{"name": "Окрошка без заправки", "grams": 200, "kcal": 120.0, "protein": 6.0, "fat": 3.0, "carbs": 18.0}]'
