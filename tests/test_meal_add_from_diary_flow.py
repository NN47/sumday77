import asyncio
from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from handlers import meals


class _DummyState:
    def __init__(self):
        self._data = {}
        self.set_state = AsyncMock()

    async def update_data(self, **kwargs):
        self._data.update(kwargs)



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
    state = _DummyState()

    with patch("handlers.meals.push_menu_stack") as push_stack:
        asyncio.run(meals._show_input_methods(message, state))

    state.set_state.assert_awaited_once_with(meals.MealEntryStates.choosing_meal_type)
    push_stack.assert_called_once_with(message.bot, meals.kbju_add_menu)
    message.answer.assert_awaited_once()


def test_add_meal_from_diary_block_sets_context_and_opens_methods():
    target_date = date.today().isoformat()
    callback = _build_callback(f"add_meal:lunch:{target_date}")
    state = _DummyState()

    with patch("handlers.meals._show_input_methods", new=AsyncMock()) as show_methods:
        asyncio.run(meals.add_meal_from_diary_block(callback, state))

    callback.answer.assert_awaited_once()
    assert state._data["meal_type"] == "lunch"
    assert state._data["entry_date"] == target_date
    show_methods.assert_awaited_once_with(callback.message, state)
