import asyncio
import os
import sys
from pathlib import Path

os.environ.setdefault("API_TOKEN", "test-token")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from handlers.activity import analyze_activity_day_detailed_deepseek
from services.extended_activity_analysis_service import DETAILED_DAY_ANALYSIS_SYSTEM_PROMPT


def test_detailed_deepseek_prompt_requires_telegram_html_not_markdown() -> None:
    prompt = DETAILED_DAY_ANALYSIS_SYSTEM_PROMPT

    assert "Не используй Markdown-разметку" in prompt
    assert "Не используй Markdown ни при каких обстоятельствах" in prompt
    assert "<b>📊 Общая оценка</b>" in prompt
    assert "<b>3290 ккал</b>" in prompt
    assert "**" in prompt  # markdown tokens are explicitly forbidden in the instruction


def test_detailed_deepseek_prompt_avoids_inaccurate_nutrition_recommendations() -> None:
    prompt = DETAILED_DAY_ANALYSIS_SYSTEM_PROMPT

    assert "Не называй жиры «качественными» или «полезными»" in prompt
    assert "Не классифицируй автоматически такие продукты как источники полезных жиров" in prompt
    assert "Не связывай недостаток углеводов напрямую с похудением" in prompt
    assert "Основной фактор похудения — общий энергетический баланс" in prompt
    assert "Не советуй добирать воду перед сном" in prompt
    assert "распределять воду равномерно в течение дня" in prompt


def test_detailed_deepseek_analysis_is_sent_with_html_parse_mode() -> None:
    message = SimpleNamespace(
        from_user=SimpleNamespace(id=12345),
        answer=AsyncMock(),
        bot=SimpleNamespace(),
    )

    with (
        patch(
            "handlers.activity.extended_activity_analysis_service.generate",
            new=AsyncMock(return_value="<b>📊 Общая оценка — 9/10</b>\nТекст"),
        ) as generate_mock,
        patch("handlers.activity.EveningAnalysisNotificationRepository.mark_analysis_started"),
        patch("handlers.activity.ActivityAnalysisRepository.create_entry") as create_entry,
        patch("handlers.activity.AnalyticsRepository.track_event"),
        patch("handlers.activity.push_menu_stack"),
    ):
        asyncio.run(analyze_activity_day_detailed_deepseek(message))

    generate_mock.assert_awaited_once_with(
        "12345",
        type(generate_mock.await_args.args[1])(
            start_date=date.today(),
            end_date=date.today(),
            label="за день",
        ),
    )
    create_entry.assert_called_once()
    final_call = message.answer.await_args_list[-1]
    assert final_call.kwargs.get("parse_mode") == "HTML"


def test_detailed_deepseek_prompt_encodes_supportive_sumday77_philosophy() -> None:
    prompt = DETAILED_DAY_ANALYSIS_SYSTEM_PROMPT

    assert "Sumday77 всегда на стороне пользователя" in prompt
    assert "Всегда сначала найди реальные сильные стороны дня" in prompt
    assert "день уже потерян" in prompt
    assert "Не назначай наказание за еду" in prompt
    assert "один день не отменяет общий прогресс" in prompt
    assert "следующий хороший выбор всё ещё важен" in prompt
