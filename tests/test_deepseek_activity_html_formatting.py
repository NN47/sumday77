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


def test_detailed_deepseek_entrypoint_opens_preflight_without_calling_ai() -> None:
    message = SimpleNamespace(
        from_user=SimpleNamespace(id=12345),
        answer=AsyncMock(),
        bot=SimpleNamespace(),
    )

    with (
        patch("handlers.activity.ActivityAnalysisRepository.get_successful_ai_for_date", return_value=None),
        patch("handlers.activity.show_daily_analysis_preflight", new=AsyncMock()) as show_preflight,
        patch("handlers.activity.extended_activity_analysis_service.generate", new=AsyncMock()) as generate_mock,
    ):
        asyncio.run(analyze_activity_day_detailed_deepseek(message))

    show_preflight.assert_awaited_once()
    assert show_preflight.await_args.args[1] == "12345"
    generate_mock.assert_not_awaited()


def test_detailed_deepseek_prompt_encodes_supportive_sumday77_philosophy() -> None:
    prompt = DETAILED_DAY_ANALYSIS_SYSTEM_PROMPT

    assert "Sumday77 всегда на стороне пользователя" in prompt
    assert "Всегда сначала найди реальные сильные стороны дня" in prompt
    assert "день уже потерян" in prompt
    assert "Не назначай наказание за еду" in prompt
    assert "один день не отменяет общий прогресс" in prompt
    assert "следующий хороший выбор всё ещё важен" in prompt


def test_calendar_add_analysis_runs_detailed_for_selected_day() -> None:
    from handlers.activity import add_activity_analysis_from_calendar

    target_date = date.today()
    callback = SimpleNamespace(
        data=f"act_cal_add:{target_date.isoformat()}",
        from_user=SimpleNamespace(id=12345),
        message=SimpleNamespace(),
        answer=AsyncMock(),
    )
    state = SimpleNamespace(clear=AsyncMock())

    with (
        patch("handlers.activity.quota_period_key", return_value=target_date),
        patch("handlers.activity.ActivityAnalysisRepository.get_successful_ai_for_date", return_value=None),
        patch("handlers.activity.show_daily_analysis_preflight", new=AsyncMock()) as show_preflight,
    ):
        asyncio.run(add_activity_analysis_from_calendar(callback, state))

    callback.answer.assert_awaited_once()
    state.clear.assert_awaited_once()
    show_preflight.assert_awaited_once_with(
        callback.message,
        "12345",
        target_date,
        origin="calendar",
        prefer_edit=True,
    )
