import asyncio
import os
from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

os.environ.setdefault("API_TOKEN", "test-token")

from handlers import supplements
from utils.supplement_keyboards import supplement_history_time_menu


class _DummyState:
    def __init__(self, data=None):
        self._data = dict(data or {})
        self.set_state = AsyncMock()

    async def update_data(self, **kwargs):
        self._data.update(kwargs)

    async def get_data(self):
        return dict(self._data)



def _build_message(text="📅 Выбрать дату"):
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
            edit_reply_markup=AsyncMock(),
        ),
        answer=AsyncMock(),
    )



def test_history_time_menu_contains_date_selection_button():
    keyboard_texts = [
        button.text
        for row in supplement_history_time_menu().keyboard
        for button in row
    ]

    assert "📅 Выбрать дату" in keyboard_texts



def test_history_time_date_button_opens_intake_calendar():
    message = _build_message()
    state = _DummyState({"entry_date": "2026-05-28"})

    with patch(
        "utils.calendar_utils.SupplementRepository.get_history_days", return_value=set()
    ):
        asyncio.run(supplements.handle_history_time(message, state))

    message.answer.assert_awaited_once()
    _, kwargs = message.answer.await_args
    keyboard = kwargs["reply_markup"]
    first_day_callback = keyboard.inline_keyboard[2][4].callback_data
    assert first_day_callback == "supintakecal_day:2026-05-01"



def test_selecting_intake_calendar_day_updates_entry_date_and_prompts_time():
    target_date = date(2026, 5, 27)
    callback = _build_callback(f"supintakecal_day:{target_date.isoformat()}")
    state = _DummyState({"entry_date": "2026-05-28"})

    with patch(
        "handlers.supplements.push_menu_stack"
    ):
        asyncio.run(supplements.select_supplement_intake_date(callback, state))

    callback.answer.assert_awaited_once()
    assert state._data["entry_date"] == target_date.isoformat()
    state.set_state.assert_awaited_once_with(supplements.SupplementStates.entering_history_time)
    callback.message.answer.assert_awaited_once()
    text = callback.message.answer.await_args.args[0]
    assert "✅ Дата изменена." in text
    assert "📅 Дата: 27.05.2026" in text
