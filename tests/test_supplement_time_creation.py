import asyncio
import os
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

os.environ.setdefault("API_TOKEN", "test-token")

from handlers import supplements
from utils.supplement_keyboards import supplement_test_time_inline_menu


class _DummyState:
    def __init__(self, data=None, state=None):
        self._data = dict(data or {})
        self._state = state
        self.set_state = AsyncMock(side_effect=self._set_state)
        self.clear = AsyncMock()

    async def _set_state(self, value):
        self._state = value.state if hasattr(value, "state") else value

    async def update_data(self, **kwargs):
        self._data.update(kwargs)

    async def get_data(self):
        return dict(self._data)

    async def get_state(self):
        return self._state


def _build_message(text):
    return SimpleNamespace(
        text=text,
        from_user=SimpleNamespace(id=12345),
        bot=SimpleNamespace(),
        answer=AsyncMock(),
    )


def _build_callback(callback_data: str):
    return SimpleNamespace(
        data=callback_data,
        from_user=SimpleNamespace(id=12345),
        message=SimpleNamespace(
            bot=SimpleNamespace(),
            answer=AsyncMock(),
            edit_text=AsyncMock(),
            edit_reply_markup=AsyncMock(),
        ),
        answer=AsyncMock(),
    )


def test_create_time_inline_menu_contains_hours_from_6_to_23_and_actions():
    keyboard = supplement_test_time_inline_menu([])
    buttons = [button for row in keyboard.inline_keyboard for button in row]

    assert buttons[0].text == "06:00"
    assert buttons[0].callback_data == "sup_create_time:add:06:00"
    assert any(button.text == "23:00" for button in buttons)
    assert not any(button.text == "05:00" for button in buttons)
    assert any(button.text == "⏭️ Пропустить" for button in buttons)


def test_create_time_inline_menu_marks_selected_time_and_shows_save():
    keyboard = supplement_test_time_inline_menu(["09:00"])
    buttons = [button for row in keyboard.inline_keyboard for button in row]

    assert any(button.text == "✅ 09:00" for button in buttons)
    assert any(button.text == "💾 Сохранить время" for button in buttons)


def test_parse_supplement_time_input_accepts_digits():
    assert supplements.parse_supplement_time_input("9") == "09:00"
    assert supplements.parse_supplement_time_input("09") == "09:00"
    assert supplements.parse_supplement_time_input("930") == "09:30"
    assert supplements.parse_supplement_time_input("09:30") == "09:30"
    assert supplements.parse_supplement_time_input("2360") is None


def test_manual_digit_time_is_added_during_creation():
    message = _build_message("930")
    state = _DummyState({"supplement_id": None, "name": "Магний", "times": []})

    asyncio.run(supplements.handle_time_value(message, state))

    assert state._data["times"] == ["09:30"]
    message.answer.assert_awaited_once()
    _, kwargs = message.answer.await_args
    keyboard = kwargs["reply_markup"]
    assert any(
        button.text == "💾 Сохранить время"
        for row in keyboard.inline_keyboard
        for button in row
    )


def test_inline_time_callback_adds_selected_time():
    callback = _build_callback("sup_create_time:add:06:00")
    state = _DummyState(
        {"supplement_id": None, "name": "Магний", "times": []},
        supplements.SupplementStates.entering_time.state,
    )

    asyncio.run(supplements.handle_create_supplement_time_callback(callback, state))

    assert state._data["times"] == ["06:00"]
    callback.answer.assert_awaited_once_with("Добавлено 06:00")
    callback.message.edit_text.assert_awaited_once()
    _, kwargs = callback.message.edit_text.await_args
    keyboard = kwargs["reply_markup"]
    assert any(
        button.text == "✅ 06:00"
        for row in keyboard.inline_keyboard
        for button in row
    )


def test_inline_time_save_moves_to_days_step():
    callback = _build_callback("sup_create_time:save")
    state = _DummyState(
        {"supplement_id": None, "name": "Магний", "times": ["06:00"]},
        supplements.SupplementStates.entering_time.state,
    )

    with patch("handlers.supplements.push_menu_stack"):
        asyncio.run(supplements.handle_create_supplement_time_callback(callback, state))

    state.set_state.assert_awaited_with(supplements.SupplementStates.selecting_days)
    callback.message.edit_reply_markup.assert_awaited_once_with(reply_markup=None)
    callback.message.answer.assert_awaited_once()


def test_inline_time_callback_resumes_from_unselected_days_step():
    callback = _build_callback("sup_create_time:add:07:00")
    state = _DummyState(
        {"supplement_id": None, "name": "Магний", "times": [], "days": []},
        supplements.SupplementStates.selecting_days.state,
    )

    asyncio.run(supplements.handle_create_supplement_time_callback(callback, state))

    assert state._state == supplements.SupplementStates.entering_time.state
    assert state._data["times"] == ["07:00"]
    callback.answer.assert_awaited_once_with("Добавлено 07:00")
    callback.message.edit_text.assert_awaited_once()


def test_inline_time_callback_still_rejects_after_days_selected():
    callback = _build_callback("sup_create_time:add:07:00")
    state = _DummyState(
        {"supplement_id": None, "name": "Магний", "times": ["06:00"], "days": ["Пн"]},
        supplements.SupplementStates.selecting_days.state,
    )

    asyncio.run(supplements.handle_create_supplement_time_callback(callback, state))

    assert state._state == supplements.SupplementStates.selecting_days.state
    assert state._data["times"] == ["06:00"]
    callback.answer.assert_awaited_once_with("Этот шаг уже завершён", show_alert=True)
    callback.message.edit_text.assert_not_awaited()

