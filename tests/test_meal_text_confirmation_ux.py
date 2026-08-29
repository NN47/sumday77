import asyncio
import os
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch


os.environ.setdefault("API_TOKEN", "test-token")

from handlers import meals


class DummyState:
    def __init__(self, data=None):
        self.data = dict(data or {})
        self.current_state = None
        self.set_state = AsyncMock(side_effect=self._set_state)
        self.update_data = AsyncMock(side_effect=self._update_data)
        self.clear = AsyncMock(side_effect=self._clear)

    async def get_data(self):
        return dict(self.data)

    async def _set_state(self, value):
        self.current_state = value

    async def _update_data(self, **kwargs):
        self.data.update(kwargs)

    async def _clear(self):
        self.data.clear()
        self.current_state = None


def build_callback(data=None):
    message = SimpleNamespace(
        bot=SimpleNamespace(menu_stack=[]),
        edit_text=AsyncMock(),
        edit_reply_markup=AsyncMock(),
        answer=AsyncMock(),
    )
    return SimpleNamespace(
        from_user=SimpleNamespace(id=12345),
        message=message,
        answer=AsyncMock(),
        data=data,
    )


def sample_draft():
    return {
        "raw_query": "творог 150 г",
        "analysis_title": "AI-анализ приёма пищи",
        "meal_type": "breakfast",
        "entry_date": "2026-08-11",
        "save_token": "T" * meals.MEAL_SAVE_TOKEN_LENGTH,
        "items": [
            {
                "name": "Творог",
                "grams": 150,
                "calories": 180,
                "protein_g": 24,
                "fat_total_g": 7,
                "carbohydrates_total_g": 5,
            }
        ],
    }


def test_text_meal_preview_uses_one_inline_keyboard_with_save_last():
    save_token = "T" * meals.MEAL_SAVE_TOKEN_LENGTH
    inline = meals._build_ai_meal_preview_inline_menu(save_token)

    assert [[button.text for button in row] for row in inline.inline_keyboard] == [
        ["❌ Отмена", "✏️ Редактировать"],
        ["✅ Сохранить"],
    ]
    assert [[button.callback_data for button in row] for row in inline.inline_keyboard] == [
        ["cancel_ai_meal_draft", "edit_ai_meal_draft"],
        [f"save_ai_meal_draft:{save_token}"],
    ]


def test_first_cancel_click_keeps_draft_and_requests_confirmation():
    draft = sample_draft()
    state = DummyState({"ai_pending_meal": draft})
    callback = build_callback("cancel_ai_meal_draft")

    asyncio.run(meals.request_cancel_ai_meal_draft(callback, state))

    assert state.data["ai_pending_meal"] == draft
    state.clear.assert_not_awaited()
    state.set_state.assert_awaited_once_with(meals.MealEntryStates.confirming_ai_meal_cancel)
    text = callback.message.edit_text.await_args.args[0]
    assert "Все распознанные данные будут удалены" in text
    keyboard = callback.message.edit_text.await_args.kwargs["reply_markup"]
    assert [[button.text for button in row] for row in keyboard.inline_keyboard] == [
        ["✅ Да, отменить", "↩️ Нет, вернуться"]
    ]


def test_declining_cancel_restores_unchanged_draft_preview():
    draft = sample_draft()
    state = DummyState({"ai_pending_meal": draft})
    callback = build_callback("back_to_ai_meal_draft")

    asyncio.run(meals.back_to_ai_meal_draft(callback, state))

    assert state.data["ai_pending_meal"]["raw_query"] == draft["raw_query"]
    assert state.data["ai_pending_meal"]["items"] == draft["items"]
    state.clear.assert_not_awaited()
    state.set_state.assert_awaited_once_with(meals.MealEntryStates.confirming_ai_meal)
    keyboard = callback.message.edit_text.await_args.kwargs["reply_markup"]
    assert [button.text for button in keyboard.inline_keyboard[0]] == ["❌ Отмена", "✏️ Редактировать"]
    assert [button.text for button in keyboard.inline_keyboard[-1]] == ["✅ Сохранить"]


def test_confirmed_cancel_clears_draft_and_returns_to_add_methods():
    state = DummyState(
        {
            "ai_pending_meal": sample_draft(),
            "meal_type": "breakfast",
            "entry_date": "2026-08-11",
            "meal_entry_open": True,
        }
    )
    callback = build_callback("confirm_cancel_ai_meal_draft")

    with patch("handlers.meals.MealRepository.get_meals_for_date", return_value=[]):
        asyncio.run(meals.confirm_cancel_ai_meal_draft(callback, state))

    state.clear.assert_awaited_once()
    assert "ai_pending_meal" not in state.data
    assert state.data["meal_type"] == "breakfast"
    assert state.data["entry_date"] == "2026-08-11"
    assert state.data["meal_entry_open"] is True
    assert state.current_state is meals.MealEntryStates.choosing_meal_type
    callback.message.edit_text.assert_awaited_once_with("❌ Добавление приёма пищи отменено.")
    assert callback.message.answer.await_args.kwargs["reply_markup"] is meals.kbju_add_menu

    asyncio.run(meals.kbju_add_via_ai(callback.message, state))

    assert state.current_state is meals.MealEntryStates.waiting_for_ai_food_input
    assert "ai_pending_meal" not in state.data


def test_reply_save_uses_existing_draft_save_logic():
    message = SimpleNamespace(
        text="✅ Сохранить",
        from_user=SimpleNamespace(id=12345),
        bot=SimpleNamespace(),
        answer=AsyncMock(),
    )
    state = DummyState({"ai_pending_meal": sample_draft()})

    with patch("handlers.meals._save_ai_meal_draft", new_callable=AsyncMock) as save_draft:
        asyncio.run(meals.handle_ai_confirm(message, state))

    save_draft.assert_awaited_once_with(message, state, user_id="12345")


def test_inline_save_uses_bound_draft_and_removes_old_buttons():
    draft = sample_draft()
    state = DummyState({"ai_pending_meal": draft})
    callback = build_callback(f"save_ai_meal_draft:{draft['save_token']}")
    saved = meals.MealSaveResult(meals.MealSaveStatus.SAVED, SimpleNamespace(id=7))

    with patch(
        "handlers.meals._save_ai_meal_draft",
        new_callable=AsyncMock,
        return_value=saved,
    ) as save_draft:
        asyncio.run(meals.save_ai_meal_draft_from_inline(callback, state))

    save_draft.assert_awaited_once_with(callback.message, state, user_id="12345")
    callback.message.edit_reply_markup.assert_awaited_once_with(reply_markup=None)
