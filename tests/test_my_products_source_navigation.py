import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from aiogram import Bot
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import Message

from handlers import meals
from states.user_states import MealEntryStates


@pytest.mark.parametrize("button_text", [
    "📷 Из анализа еды по фото",
    "🗂 Старые продукты из фотоанализа",
])
@pytest.mark.parametrize("has_products", [True, False])
def test_photo_source_button_routes_to_filtered_history(button_text, has_products):
    async def scenario():
        bot = Bot("12345:test-token")
        storage = MemoryStorage()
        state = FSMContext(storage, StorageKey(bot.id, 1, 1))
        await state.set_state(MealEntryStates.choosing_meal_type)
        await state.update_data(meal_type="lunch", in_my_products_section=True)
        message = Message.model_validate({
            "message_id": 1, "date": 0, "chat": {"id": 1, "type": "private"},
            "from": {"id": 1, "is_bot": False, "first_name": "Test"},
            "text": button_text,
        }, context={"bot": bot})
        history = [SimpleNamespace(
            id=10, entry_kind="products",
            products_json=json.dumps([
                {"name": "Яблоко", "source": "gemini", "grams": 100, "kcal": 52},
                {"name": "Творог", "source": "manual", "grams": 150, "kcal": 180},
            ]),
        )] if has_products else []
        with patch.object(Message, "answer", new_callable=AsyncMock) as answer, patch.object(
            meals.MealRepository, "get_user_meal_history_page", return_value=history,
        ):
            await meals.router.propagate_event(
                "message", message, state=state, raw_state=await state.get_state(),
            )
        assert answer.await_count == 1
        text = answer.await_args.args[0]
        if has_products:
            assert "Мои продукты из анализа еды по фото" in text
            assert "Яблоко" in text
            assert "Творог" not in text
            buttons = answer.await_args.kwargs["reply_markup"].inline_keyboard
            assert all(b.text != "⬅️ Назад" for row in buttons for b in row)
        else:
            assert "пока нет продуктов" in text
        assert (await state.get_data())["my_products_source_filter"] == "photo_analysis"
        if has_products:
            callback = SimpleNamespace(
                from_user=message.from_user, answer=AsyncMock(),
                message=SimpleNamespace(edit_text=AsyncMock(), answer=AsyncMock()),
            )
            with patch.object(meals.MealRepository, "get_user_meal_history_page", return_value=history):
                await meals.my_products_back_to_main(callback, state)
            all_products_text = callback.message.edit_text.await_args.args[0]
            assert "Яблоко" in all_products_text and "Творог" in all_products_text
            assert (await state.get_data())["my_products_source_filter"] is None
            assert (await state.get_data())["meal_type"] == "lunch"
        back_message = message.model_copy(update={"text": "⬅️ Назад"})
        with patch.object(meals, "_show_input_methods", new_callable=AsyncMock) as show_methods:
            await meals.router.propagate_event(
                "message", back_message, state=state, raw_state=await state.get_state(),
            )
        show_methods.assert_awaited_once_with(back_message, state, user_id="1")
        assert not (await state.get_data())["in_my_products_section"]
        await storage.close()
        await bot.session.close()

    asyncio.run(scenario())


def test_every_visible_source_button_is_registered():
    keyboard = meals._build_my_products_source_filter_reply_keyboard()
    labels = [b.text for row in keyboard.keyboard for b in row if b.text != "⬅️ Назад"]
    assert len(labels) == len(meals.MY_PRODUCTS_SOURCE_FILTERS)
    assert all(label in meals.MY_PRODUCTS_SOURCE_BUTTON_TO_FILTER for label in labels)
