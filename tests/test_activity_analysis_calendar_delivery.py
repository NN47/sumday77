import asyncio
import os
import sys
from datetime import date, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

os.environ.setdefault("API_TOKEN", "test-token")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from handlers.activity import show_activity_analysis_day


def _entry(analysis_text: str):
    return SimpleNamespace(
        id=77,
        source="detailed_deepseek",
        analysis_text=analysis_text,
        analyzed_at=datetime(2026, 9, 17, 18, 47),
        created_at=None,
    )


def test_long_calendar_analysis_is_split_and_actions_are_on_last_part() -> None:
    message = SimpleNamespace(edit_text=AsyncMock(), answer=AsyncMock())
    entry = _entry(("Длинный анализ дня " + "подробности " * 350 + "\n") * 3)

    with patch(
        "handlers.activity.ActivityAnalysisRepository.get_entries_for_date",
        return_value=[entry],
    ):
        asyncio.run(show_activity_analysis_day(message, "12345", date(2026, 9, 17)))

    delivered = [message.edit_text.await_args.args[0]] + [
        call.args[0] for call in message.answer.await_args_list
    ]
    assert len(delivered) > 1
    assert all(len(part) <= 4000 for part in delivered)
    assert message.edit_text.await_args.kwargs["reply_markup"] is None
    assert message.answer.await_args_list[-1].kwargs["reply_markup"] is not None
    assert all(
        call.kwargs["reply_markup"] is None
        for call in message.answer.await_args_list[:-1]
    )


def test_short_calendar_analysis_keeps_actions_on_edited_message() -> None:
    message = SimpleNamespace(edit_text=AsyncMock(), answer=AsyncMock())

    with patch(
        "handlers.activity.ActivityAnalysisRepository.get_entries_for_date",
        return_value=[_entry("Короткий анализ")],
    ):
        asyncio.run(show_activity_analysis_day(message, "12345", date(2026, 9, 17)))

    message.edit_text.assert_awaited_once()
    assert message.edit_text.await_args.kwargs["reply_markup"] is not None
    message.answer.assert_not_awaited()
