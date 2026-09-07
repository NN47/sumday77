import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest
from aiogram.types import Chat, Contact, Location, Message, User

from middlewares.sensitive_input import (
    SENSITIVE_INPUT_REJECTED_TEXT,
    SensitiveInputMiddleware,
)


def _message(*, text=None, caption=None, contact=None, location=None):
    return Message(
        message_id=1,
        date=datetime.now(timezone.utc),
        chat=Chat(id=12345, type="private"),
        from_user=User(id=12345, is_bot=False, first_name="Test"),
        text=text,
        caption=caption,
        contact=contact,
        location=location,
    )


def _run(message):
    async def scenario():
        handler = AsyncMock(return_value="handled")
        answer = AsyncMock()
        with patch.object(Message, "answer", new=answer):
            result = await SensitiveInputMiddleware()(handler, message, {"state": object()})
        return result, handler, answer

    return asyncio.run(scenario())


@pytest.mark.parametrize(
    "message",
    [
        _message(text="Мой телефон +7 999 123-45-67"),
        _message(caption="Напишите мне на person@example.com"),
        _message(text="ФИО: Иванов Петр Иванович"),
        _message(text="ИВАНОВ ИВАН ИВАНОВИЧ"),
        _message(text="45 01 123456"),
        _message(text="123-456-789 00"),
        _message(text="ул. Пушкина, д. 12, кв. 4"),
    ],
)
def test_personal_data_in_text_or_caption_is_stopped_before_handler(message, caplog):
    source_text = message.text or message.caption
    caplog.set_level("INFO", logger="middlewares.sensitive_input")

    result, handler, answer = _run(message)

    assert result is None
    handler.assert_not_awaited()
    answer.assert_awaited_once_with(SENSITIVE_INPUT_REJECTED_TEXT)
    assert "Sensitive input rejected" in caplog.text
    assert source_text not in caplog.text


@pytest.mark.parametrize(
    "text",
    [
        "🔄 Главное меню",
        "250",
        "12.09.2026",
        "гречка 150 г",
        "жим Арнольда",
        "найти беговую дорожку",
        "Телефон: кнопка не работает",
        "ФИО: поле не сохраняется",
        "Адрес проживания: поле не сохраняется",
    ],
)
def test_regular_messages_reach_handler(text):
    result, handler, answer = _run(_message(text=text))

    assert result == "handled"
    handler.assert_awaited_once()
    answer.assert_not_awaited()


@pytest.mark.parametrize(
    "message",
    [
        _message(contact=Contact(phone_number="+79991234567", first_name="Иван")),
        _message(location=Location(latitude=55.75, longitude=37.62)),
    ],
)
def test_structured_contact_and_location_are_rejected(message):
    result, handler, answer = _run(message)

    assert result is None
    handler.assert_not_awaited()
    answer.assert_awaited_once_with(SENSITIVE_INPUT_REJECTED_TEXT)
