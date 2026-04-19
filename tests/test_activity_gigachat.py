import asyncio
from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from handlers.activity import analyze_activity_day_gigachat


def test_gigachat_day_analysis_uses_gigachat_backend():
    message = SimpleNamespace(
        from_user=SimpleNamespace(id=12345),
        answer=AsyncMock(),
        bot=SimpleNamespace(),
    )

    with (
        patch("handlers.activity.generate_activity_analysis", new=AsyncMock(return_value="<b>Отчёт</b>")) as generate_mock,
        patch("handlers.activity.ActivityAnalysisRepository.create_entry") as create_entry,
        patch("handlers.activity.AnalyticsRepository.track_event"),
        patch("handlers.activity.push_menu_stack"),
    ):
        asyncio.run(analyze_activity_day_gigachat(message))

    generate_mock.assert_awaited_once_with("12345", date.today(), date.today(), "за день", backend="gigachat")
    create_entry.assert_called_once()
    final_call = message.answer.await_args_list[-1]
    assert final_call.kwargs.get("parse_mode") == "HTML"
