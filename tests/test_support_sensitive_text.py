import asyncio
import os
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

os.environ.setdefault("API_TOKEN", "test-token")

from handlers import settings
from services.deepseek_service import deepseek_service
from utils.sensitive_text import SensitiveDataType, check_sensitive_support_text


class _DummyState:
    def __init__(self):
        self._state = None
        self._data = {}
        self.set_state = AsyncMock(side_effect=self._set_state)
        self.clear = AsyncMock(side_effect=self._clear)

    async def _set_state(self, value):
        self._state = value.state if hasattr(value, "state") else value

    async def _clear(self):
        self._state = None
        self._data.clear()


def _build_message(text: str):
    bot = SimpleNamespace(send_message=AsyncMock())
    return SimpleNamespace(
        text=text,
        caption=None,
        bot=bot,
        from_user=SimpleNamespace(
            id=12345,
            username="user_name",
            first_name="Иван",
            last_name="Иванов",
            language_code="ru",
        ),
        answer=AsyncMock(),
    )


def test_support_warning_is_shown_before_message_input():
    message = _build_message("💬 Поддержка")
    state = _DummyState()

    asyncio.run(settings.support(message, state))

    assert state._state == settings.SupportStates.waiting_for_message.state
    prompt = message.answer.await_args.args[0]
    assert settings.SUPPORT_SENSITIVE_INPUT_WARNING in prompt
    assert "пароли" in prompt
    assert "диагнозы" in prompt
    buttons = [
        button.text
        for row in message.answer.await_args.kwargs["reply_markup"].keyboard
        for button in row
    ]
    assert buttons == ["⬅️ Назад", settings.MAIN_MENU_BUTTON_TEXT]


@pytest.mark.parametrize(
    ("source_text", "expected_reason"),
    [
        ("Мой пароль: qwerty123", SensitiveDataType.CREDENTIAL),
        ("Код доступа 483920", SensitiveDataType.CREDENTIAL),
        ("Паспорт: 45 01 123456", SensitiveDataType.DOCUMENT),
        ("Номер карты 4111 1111 1111 1111", SensitiveDataType.BANKING),
        ("У меня диабет, принимаю препарат", SensitiveDataType.MEDICAL),
        ("Мои результаты анализов показали анемию", SensitiveDataType.MEDICAL),
    ],
)
def test_sensitive_support_text_is_not_saved_forwarded_logged_or_sent_to_ai(
    source_text,
    expected_reason,
    caplog,
):
    message = _build_message(source_text)
    state = _DummyState()
    state._state = settings.SupportStates.waiting_for_message.state

    with patch.object(settings.SupportRepository, "create_message") as create_message, patch.object(
        settings.AnalyticsRepository, "track_event"
    ) as track_event, patch.object(deepseek_service, "analyze_food_text") as analyze_food:
        caplog.set_level("INFO", logger="handlers.settings")
        asyncio.run(settings.handle_support_message(message, state))

    assert check_sensitive_support_text(source_text).reason is expected_reason
    message.bot.send_message.assert_not_awaited()
    create_message.assert_not_called()
    track_event.assert_not_called()
    analyze_food.assert_not_called()
    state.clear.assert_not_awaited()
    assert state._state == settings.SupportStates.waiting_for_message.state
    assert state._data == {}
    message.answer.assert_awaited_once_with(settings.SUPPORT_SENSITIVE_INPUT_REJECTED_TEXT)
    assert source_text not in caplog.text
    assert "Sensitive support input rejected" in caplog.text
    assert f"reason={expected_reason.value}" in caplog.text
    assert f"message_chars={len(source_text)}" in caplog.text


@pytest.mark.parametrize(
    "source_text",
    [
        "В разделе «Анализ дня» ошибка",
        "Бот написал, что он врач",
        "После обновления не открывается раздел процедур",
        "Слово пароль отображается дважды",
        "Ответьте на test@example.com или +7 999 123-45-67",
    ],
)
def test_regular_technical_support_messages_are_allowed(source_text):
    assert check_sensitive_support_text(source_text).is_sensitive is False


def test_allowed_support_text_is_html_escaped_before_forwarding():
    source_text = "Кнопка <b>Назад</b> не работает"
    message = _build_message(source_text)
    state = _DummyState()
    state._state = settings.SupportStates.waiting_for_message.state

    with patch.object(settings.SupportRepository, "create_message") as create_message, patch.object(
        settings.AnalyticsRepository, "track_event"
    ):
        asyncio.run(settings.handle_support_message(message, state))

    forwarded = message.bot.send_message.await_args.kwargs["text"]
    assert source_text not in forwarded
    assert "&lt;b&gt;Назад&lt;/b&gt;" in forwarded
    create_message.assert_called_once_with(
        user_id="12345",
        message_text=source_text,
    )
    assert "user_name" not in forwarded
    assert "Иван" not in forwarded
    assert "Язык:" not in forwarded
    assert "12345" in forwarded
    state.clear.assert_awaited_once()


def test_support_back_and_main_menu_do_not_send_messages():
    for button_text in ("⬅️ Назад", settings.MAIN_MENU_BUTTON_TEXT):
        message = _build_message(button_text)
        state = _DummyState()
        state._state = settings.SupportStates.waiting_for_message.state
        with patch.object(settings.SupportRepository, "create_message") as create_message, patch(
            "handlers.common.go_main_menu", new=AsyncMock()
        ) as go_main_menu:
            asyncio.run(settings.handle_support_message(message, state))

        message.bot.send_message.assert_not_awaited()
        create_message.assert_not_called()
        state.clear.assert_awaited_once()
        if button_text == settings.MAIN_MENU_BUTTON_TEXT:
            go_main_menu.assert_awaited_once_with(message, state)
        else:
            go_main_menu.assert_not_awaited()
