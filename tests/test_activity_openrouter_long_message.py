import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from handlers.activity import analyze_activity_day_openrouter


def test_openrouter_day_analysis_splits_long_messages():
    long_analysis = "<b>Отчёт</b>\n" + ("Данные\n" * 1800)
    message = SimpleNamespace(
        from_user=SimpleNamespace(id=12345),
        answer=AsyncMock(),
        bot=SimpleNamespace(),
    )

    with (
        patch("handlers.activity.generate_activity_analysis", new=AsyncMock(return_value=long_analysis)),
        patch("handlers.activity.ActivityAnalysisRepository.create_entry") as create_entry,
        patch("handlers.activity.AnalyticsRepository.track_event"),
        patch("handlers.activity.push_menu_stack"),
    ):
        asyncio.run(analyze_activity_day_openrouter(message))

    assert message.answer.await_count >= 3

    calls = message.answer.await_args_list
    assert "Подожди немного" in calls[0].args[0]

    chunk_calls = calls[1:]
    for call in chunk_calls:
        assert len(call.args[0]) <= 3900
        assert call.kwargs.get("parse_mode") == "HTML"

    assert chunk_calls[-1].kwargs.get("reply_markup") is not None
    for call in chunk_calls[:-1]:
        assert call.kwargs.get("reply_markup") is None

    create_entry.assert_called_once()
