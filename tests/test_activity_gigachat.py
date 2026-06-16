import os

os.environ.setdefault("API_TOKEN", "test-token")

import asyncio
from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from handlers.activity import analyze_activity_day, analyze_activity_day_copy_2, analyze_activity_day_gigachat


def test_today_uses_gemini_provider():
    message = SimpleNamespace(
        from_user=SimpleNamespace(id=12345),
        answer=AsyncMock(),
        bot=SimpleNamespace(),
    )

    with patch("handlers.activity.generate_day_analysis", new=AsyncMock(return_value="<b>Отчёт</b>")) as generate_mock, patch(
        "handlers.activity.ActivityAnalysisRepository.create_entry"
    ) as create_entry, patch("handlers.activity.AnalyticsRepository.track_event"), patch("handlers.activity.push_menu_stack"):
        asyncio.run(analyze_activity_day(message))

    generate_mock.assert_awaited_once_with("12345", date.today(), "gemini")
    create_entry.assert_called_once()
    final_call = message.answer.await_args_list[-1]
    assert final_call.kwargs.get("parse_mode") == "HTML"


def test_today_copy_uses_gigachat_provider():
    message = SimpleNamespace(
        from_user=SimpleNamespace(id=12345),
        answer=AsyncMock(),
        bot=SimpleNamespace(),
    )

    with (
        patch("handlers.activity.generate_day_analysis", new=AsyncMock(return_value="<b>Отчёт</b>")) as generate_mock,
        patch("handlers.activity.ActivityAnalysisRepository.create_entry") as create_entry,
        patch("handlers.activity.AnalyticsRepository.track_event"),
        patch("handlers.activity.push_menu_stack"),
    ):
        asyncio.run(analyze_activity_day_gigachat(message))

    generate_mock.assert_awaited_once_with("12345", date.today(), "gigachat")
    create_entry.assert_called_once()
    final_call = message.answer.await_args_list[-1]
    assert final_call.kwargs.get("parse_mode") == "HTML"


def test_today_copy_2_uses_deepseek_provider():
    message = SimpleNamespace(
        from_user=SimpleNamespace(id=12345),
        answer=AsyncMock(),
        bot=SimpleNamespace(),
    )

    with (
        patch("handlers.activity.generate_day_analysis", new=AsyncMock(return_value="<b>Отчёт</b>")) as generate_mock,
        patch("handlers.activity.ActivityAnalysisRepository.create_entry") as create_entry,
        patch("handlers.activity.AnalyticsRepository.track_event"),
        patch("handlers.activity.push_menu_stack"),
    ):
        asyncio.run(analyze_activity_day_copy_2(message))

    generate_mock.assert_awaited_once_with("12345", date.today(), "deepseek")
    create_entry.assert_called_once()
    final_call = message.answer.await_args_list[-1]
    assert final_call.kwargs.get("parse_mode") == "HTML"
