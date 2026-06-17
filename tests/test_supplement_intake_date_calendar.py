import asyncio
import os
from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

os.environ.setdefault("API_TOKEN", "test-token")

from handlers import supplements
from utils.supplement_keyboards import supplement_history_time_menu, supplements_main_menu


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

    assert message.answer.await_count == 2
    back_args, back_kwargs = message.answer.await_args_list[0]
    assert "Назад" in back_args[0]
    assert back_kwargs["reply_markup"].keyboard[0][0].text == "⬅️ Назад"
    _, kwargs = message.answer.await_args_list[1]
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


def test_amount_date_button_opens_intake_calendar_with_current_supplement_marks():
    callback = _build_callback("sup_amount_date:open")
    state = _DummyState({"entry_date": "2026-05-28", "supplement_id": 7})

    with patch(
        "utils.calendar_utils.SupplementRepository.get_history_days", return_value={27}
    ) as get_history_days:
        asyncio.run(supplements.open_supplement_amount_date_calendar(callback, state))

    get_history_days.assert_called_with("12345", 2026, 5, supplement_id=7)
    assert state._data["selecting_amount_date"] is True
    assert callback.message.answer.await_count == 2
    assert callback.message.answer.await_args_list[0].kwargs["reply_markup"].keyboard[0][0].text == "⬅️ Назад"
    keyboard = callback.message.answer.await_args_list[1].kwargs["reply_markup"]
    marked_buttons = [button.text for row in keyboard.inline_keyboard for button in row]
    assert "27💊" in marked_buttons


def test_opening_supplement_calendar_pushes_back_keyboard_to_menu_stack():
    message = _build_message("📅 Календарь добавок")
    state = AsyncMock()
    previous_menu = supplements_main_menu(has_items=True)
    message.bot.menu_stack = [previous_menu]

    with patch("handlers.supplements.show_supplement_calendar", new=AsyncMock()) as show_calendar:
        asyncio.run(supplements.show_supplement_calendar_menu(message, state))

    state.clear.assert_awaited_once()
    assert message.bot.menu_stack[-2] is previous_menu
    assert message.bot.menu_stack[-1] is supplements.calendar_back_menu
    message.answer.assert_awaited_once()
    show_calendar.assert_awaited_once_with(message, "12345")
