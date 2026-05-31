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
