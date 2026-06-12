import asyncio
import os
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
    created_tasks = []

    def fake_create_task(coro):
        created_tasks.append(coro)
        coro.close()
        return SimpleNamespace()

    with patch(
        "handlers.supplements.SupplementRepository.get_supplements",
        return_value=[{"id": 7, "name": "Магний", "notifications_enabled": True}],
    ), patch("handlers.supplements.asyncio.create_task", side_effect=fake_create_task):
        asyncio.run(supplements.remind_supplement_later_from_notification(callback))

    callback.message.edit_reply_markup.assert_awaited_once_with(reply_markup=None)
    callback.answer.assert_awaited_once_with("Напомню через 30 минут")
    callback.message.answer.assert_awaited_once_with(
        "⏰ Хорошо, напомню принять «Магний» через 30 минут."
    )
    assert len(created_tasks) == 1


def test_supplement_amount_keyboard_has_requested_values_in_three_rows():
    keyboard = supplements.build_supplement_amount_inline_keyboard()

    rows = [[button.text for button in row] for row in keyboard.inline_keyboard]
    callbacks = [[button.callback_data for button in row] for row in keyboard.inline_keyboard]

    assert rows == [
        ["0,25", "0,5", "0,75", "1", "1,25"],
        ["1,5", "1,75", "2", "2,25", "2,5"],
        ["2,75", "3", "3,5", "4", "5"],
    ]
    assert callbacks[0][0] == "sup_amount:0.25"
    assert callbacks[2][-1] == "sup_amount:5"


def test_confirm_notification_prompt_includes_amount_inline_keyboard():
    callback = _build_callback("sup_confirm:7:21:30")
    state = SimpleNamespace(update_data=AsyncMock(), set_state=AsyncMock())

    with patch(
        "handlers.supplements.SupplementRepository.get_supplements",
        return_value=[{"id": 7, "name": "Магний", "notifications_enabled": True}],
    ):
        asyncio.run(supplements.confirm_supplement_intake_from_notification(callback, state))

    callback.message.edit_reply_markup.assert_awaited_once_with(reply_markup=None)
    answer_kwargs = callback.message.answer.await_args.kwargs
    assert answer_kwargs["reply_markup"].inline_keyboard[0][0].text == "0,25"
    assert answer_kwargs["parse_mode"] == "HTML"
    assert callback.message.answer.await_args.args[0] == (
        "<b>✅ Зафиксировал время приёма «Магний» в 21:30.</b>\n"
        "Выбери кнопкой или укажи количество вручную:"
    )


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
    state.clear.assert_awaited_once()
