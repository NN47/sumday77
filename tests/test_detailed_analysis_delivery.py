import asyncio
import os
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import SendMessage

os.environ.setdefault("API_TOKEN", "test-token")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from handlers.activity import run_detailed_activity_analysis
from utils.telegram_text import split_telegram_message


def _run_analysis(analysis: str, answer: AsyncMock | None = None):
    message = SimpleNamespace(
        answer=answer or AsyncMock(),
        bot=SimpleNamespace(),
    )
    with (
        patch(
            "handlers.activity.extended_activity_analysis_service.generate",
            new=AsyncMock(return_value=analysis),
        ),
        patch("handlers.activity.EveningAnalysisNotificationRepository.mark_analysis_started"),
        patch("handlers.activity.ActivityAnalysisRepository.create_entry"),
        patch("handlers.activity.AnalyticsRepository.track_event") as track_event,
        patch("handlers.activity.push_menu_stack"),
        patch("handlers.activity.log_app_error") as log_error,
    ):
        result = asyncio.run(run_detailed_activity_analysis(message, "12345"))
    return result, message, track_event, log_error


def test_short_detailed_analysis_is_sent_as_one_html_message() -> None:
    result, message, track_event, _ = _run_analysis("<b>Итог</b>\nКороткий анализ")

    assert result is True
    result_calls = message.answer.await_args_list[1:]
    assert len(result_calls) == 1
    assert result_calls[0].kwargs["parse_mode"] == "HTML"
    track_event.assert_any_call("12345", "daily_analysis_sent", section="activity")


def test_long_detailed_analysis_is_split_below_safe_limit() -> None:
    analysis = ("Абзац " + "слово " * 500 + "\n") * 3
    result, message, _, _ = _run_analysis(analysis)

    assert result is True
    chunks = [call.args[0] for call in message.answer.await_args_list[1:]]
    assert len(chunks) > 1
    assert all(len(chunk) <= 3900 for chunk in chunks)


def test_very_long_detailed_analysis_is_split_into_many_safe_parts() -> None:
    analysis = "\n\n".join(f"Раздел {idx}: " + "данные " * 700 for idx in range(8))
    result, message, _, _ = _run_analysis(analysis)

    assert result is True
    chunks = [call.args[0] for call in message.answer.await_args_list[1:]]
    assert len(chunks) >= 8
    assert max(map(len, chunks)) <= 3900


def test_delivery_error_on_later_part_is_logged_and_user_is_not_left_waiting() -> None:
    calls = []

    async def answer(text, **kwargs):
        calls.append((text, kwargs))
        if text.startswith("FAIL"):
            raise RuntimeError("telegram unavailable")

    analysis = "первая часть " * 300 + "\nFAIL вторая часть"
    result, _, track_event, log_error = _run_analysis(analysis, AsyncMock(side_effect=answer))

    assert result is False
    assert calls[-1][0].startswith("⚠️ Анализ был подготовлен")
    track_event.assert_any_call("12345", "daily_analysis_failed", section="activity")
    delivery_log = log_error.call_args_list[0].kwargs
    assert delivery_log["context"] == "detailed_analysis_delivery"
    assert delivery_log["extra"]["stage"] == "send_result"
    assert delivery_log["extra"]["parts_count"] == 2
    assert delivery_log["extra"]["part_index"] == 2


def test_invalid_html_is_retried_without_parse_mode(caplog) -> None:
    calls = []

    async def answer(text, **kwargs):
        calls.append((text, kwargs))
        if kwargs.get("parse_mode") == "HTML":
            raise TelegramBadRequest(
                method=SendMessage(chat_id=12345, text=text),
                message="Bad Request: can't parse entities",
            )

    result, _, track_event, log_error = _run_analysis(
        "<b>Незакрытый заголовок & детали",
        AsyncMock(side_effect=answer),
    )

    assert result is True
    assert calls[-1][0] == "Незакрытый заголовок & детали"
    assert calls[-1][1]["parse_mode"] is None
    track_event.assert_any_call("12345", "daily_analysis_sent", section="activity")
    log_error.assert_not_called()
    assert "code=telegram_parse_entities" in caplog.text


def test_splitter_prefers_whitespace_to_cutting_a_word() -> None:
    parts = split_telegram_message("один два три четыре", limit=10)

    assert parts == ["один два", "три четыре"]
