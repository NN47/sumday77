import asyncio
import os
from datetime import datetime as real_datetime
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

os.environ.setdefault("API_TOKEN", "test-token")

from handlers import supplements
from services.notification_scheduler import build_supplement_notification_keyboard


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


def test_supplement_notification_keyboard_contains_confirm_and_remind_later_buttons():
    keyboard = build_supplement_notification_keyboard(7, "21:30")

    assert keyboard.inline_keyboard[0][0].text == "✅ Подтвердить прием"
    assert keyboard.inline_keyboard[0][0].callback_data == "sup_confirm:7:21:30"
    assert keyboard.inline_keyboard[1][0].text == "⏰ Напомнить позже"
    assert keyboard.inline_keyboard[1][0].callback_data == "sup_remind:7:21:30"


def test_remind_later_callback_removes_keyboard_and_schedules_reminder():
    callback = _build_callback("sup_remind:7:21:30")
    deleted = []
    added = []

    class FakeQuery:
        def __init__(self, result=None):
            self.result = result

        def filter(self, *args, **kwargs):
            return self

        def filter_by(self, *args, **kwargs):
            return self

        def first(self):
            return self.result

    class FakeSession:
        def query(self, model):
            if model is supplements.SupplementEntry.id:
                return FakeQuery(None)
            return FakeQuery(None)

        def add(self, item):
            added.append(item)

        def delete(self, item):
            deleted.append(item)

        def commit(self):
            pass

        def rollback(self):
            pass

        def close(self):
            pass

    @contextmanager
    def fake_db_session():
        yield FakeSession()

    with patch(
        "handlers.supplements.SupplementRepository.get_supplements",
        return_value=[{"id": 7, "name": "Магний", "notifications_enabled": True}],
    ), patch("handlers.supplements.get_db_session", fake_db_session):
        asyncio.run(supplements.remind_supplement_later_from_notification(callback))

    callback.message.edit_reply_markup.assert_awaited_once_with(reply_markup=None)
    callback.answer.assert_awaited_once_with("Хорошо, напомню позже.")
    callback.message.answer.assert_awaited_once_with("Хорошо, напомню позже.")
    assert len(added) == 1
    assert added[0].user_id == "12345"
    assert added[0].supplement_id == 7
    assert added[0].scheduled_time == "21:30"
    assert not deleted


def test_supplement_amount_keyboard_has_requested_values_in_three_rows():
    keyboard = supplements.build_supplement_amount_inline_keyboard()

    rows = [[button.text for button in row] for row in keyboard.inline_keyboard]
    callbacks = [[button.callback_data for button in row] for row in keyboard.inline_keyboard]

    assert rows == [
        ["0,25", "0,5", "0,75", "1", "1,25"],
        ["1,5", "1,75", "2", "2,25", "2,5"],
        ["2,75", "3", "3,5", "4", "5"],
        ["📅 Выбрать другой день на календаре"],
        ["🕒 Изменить время приёма"],
    ]
    assert callbacks[0][0] == "sup_amount:0.25"
    assert callbacks[2][-1] == "sup_amount:5"
    assert callbacks[3][0] == "sup_amount_date:open"
    assert callbacks[4][0] == "sup_amount_time:open"


def test_confirm_notification_prompt_includes_amount_inline_keyboard():
    callback = _build_callback("sup_confirm:7:21:30")
    state = SimpleNamespace(update_data=AsyncMock(), set_state=AsyncMock())

    with patch(
        "handlers.supplements.SupplementRepository.get_supplements",
        return_value=[{"id": 7, "name": "Магний", "notifications_enabled": True}],
    ):
        asyncio.run(supplements.confirm_supplement_intake_from_notification(callback, state))

    callback.message.edit_reply_markup.assert_awaited_once_with(reply_markup=None)
    assert callback.message.answer.await_count == 2
    prompt_args, prompt_kwargs = callback.message.answer.await_args_list[0]
    assert prompt_kwargs["reply_markup"].keyboard[0][0].text == "⬅️ Назад"
    assert prompt_kwargs["parse_mode"] == "HTML"
    assert prompt_args[0] == (
        "<b>✅ Зафиксировал время приёма «Магний» в 21:30.</b>\n"
        "Выбери кнопкой или укажи количество вручную:"
    )
    amount_kwargs = callback.message.answer.await_args_list[1].kwargs
    assert amount_kwargs["reply_markup"].inline_keyboard[0][0].text == "0,25"


def test_supplement_amount_inline_button_saves_selected_amount():
    callback = _build_callback("sup_amount:1.25")
    state = SimpleNamespace(
        get_data=AsyncMock(return_value={
            "supplement_id": 7,
            "supplement_name": "Магний",
            "timestamp": "2026-06-06T21:30:00",
            "entry_date": "2026-06-06",
            "from_calendar": False,
        }),
        clear=AsyncMock(),
    )

    with patch(
        "handlers.supplements.SupplementRepository.save_entry",
        return_value=42,
    ) as save_entry, patch("handlers.supplements.push_menu_stack"):
        asyncio.run(supplements.handle_history_amount_button(callback, state))

    save_entry.assert_called_once()
    assert save_entry.call_args.args[0] == "12345"
    assert save_entry.call_args.args[1] == 7
    assert save_entry.call_args.args[3] == 1.25
    callback.message.edit_reply_markup.assert_awaited_once_with(reply_markup=None)
    callback.message.answer.assert_awaited_once()
    assert callback.message.answer.await_args.args[0] == (
        "✅ Записал приём Магний (1.25) на 06.06.2026 21:30."
    )
    state.clear.assert_awaited_once()


def test_supplement_amount_inline_button_formats_integer_without_decimal_part():
    callback = _build_callback("sup_amount:2")
    state = SimpleNamespace(
        get_data=AsyncMock(return_value={
            "supplement_id": 7,
            "supplement_name": "Магний",
            "timestamp": "2026-06-18T21:30:00",
            "entry_date": "2026-06-18",
            "from_calendar": False,
        }),
        clear=AsyncMock(),
    )

    with patch(
        "handlers.supplements.SupplementRepository.save_entry",
        return_value=42,
    ), patch("handlers.supplements.push_menu_stack"):
        asyncio.run(supplements.handle_history_amount_button(callback, state))

    callback.message.answer.assert_awaited_once()
    assert callback.message.answer.await_args.args[0] == (
        "✅ Записал приём Магний (2) на 18.06.2026 21:30."
    )


def test_manual_supplement_selection_prompts_amount_without_time_step():
    message = SimpleNamespace(
        text="Магний",
        from_user=SimpleNamespace(id=12345),
        bot=SimpleNamespace(),
        answer=AsyncMock(),
    )
    state_data = {"from_calendar": False}
    state = SimpleNamespace(
        get_data=AsyncMock(return_value=state_data),
        update_data=AsyncMock(),
        set_state=AsyncMock(),
    )
    fixed_now = real_datetime(2026, 6, 13, 22, 45, tzinfo=supplements.MSK_TZ)

    with patch(
        "handlers.supplements.SupplementRepository.get_supplements",
        return_value=[{"id": 7, "name": "Магний", "notifications_enabled": True}],
    ), patch("handlers.supplements.datetime") as datetime_mock:
        datetime_mock.now.return_value = fixed_now
        datetime_mock.combine.side_effect = real_datetime.combine
        asyncio.run(supplements.log_supplement_intake(message, state))

    state.set_state.assert_awaited_once_with(supplements.SupplementStates.entering_history_amount)
    state.update_data.assert_awaited_once()
    update_kwargs = state.update_data.await_args.kwargs
    assert update_kwargs["supplement_id"] == 7
    assert update_kwargs["supplement_name"] == "Магний"
    assert update_kwargs["entry_date"] == "2026-06-13"
    assert update_kwargs["timestamp"] == "2026-06-13T22:45:00"
    assert message.answer.await_count == 2
    prompt_args, prompt_kwargs = message.answer.await_args_list[0]
    assert prompt_kwargs["reply_markup"].keyboard[0][0].text == "⬅️ Назад"
    assert prompt_kwargs["parse_mode"] == "HTML"
    assert prompt_args[0] == (
        "<b>✅ Зафиксировал время приёма «Магний» в 22:45.</b>\n"
        "Выбери кнопкой или укажи количество вручную:"
    )
    amount_kwargs = message.answer.await_args_list[1].kwargs
    assert amount_kwargs["reply_markup"].inline_keyboard[0][0].text == "0,25"
