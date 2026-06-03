import importlib.util
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

MODULE_PATH = REPO_ROOT / "utils" / "admin_formatters.py"
spec = importlib.util.spec_from_file_location("admin_formatters", MODULE_PATH)
module = importlib.util.module_from_spec(spec)
assert spec and spec.loader
sys.modules[spec.name] = module
spec.loader.exec_module(module)
translate_gemini_admin_stats = module.translate_gemini_admin_stats
format_recent_events = module.format_recent_events
format_openai_ai = module.format_openai_ai
format_deepseek_ai = module.format_deepseek_ai


def _account(**kwargs):
    defaults = dict(
        account_name="GEMINI_API_KEY",
        is_active=False,
        status="active",
        api_key_masked="AIzaSy...Aggc",
        total_requests=12,
        success_requests=10,
        error_requests=2,
        temporary_errors_count=1,
        quota_errors_count=1,
        auth_errors_count=0,
        unknown_errors_count=0,
        limit_switches=3,
        temporary_failover_count=2,
        last_request_at=datetime(2026, 4, 9, 10, 18),
        last_error_at=datetime(2026, 4, 9, 10, 16),
        last_error_type="temporary",
        temporary_unavailable_until=None,
        rate_limited_until=None,
        disabled_reason=None,
        last_error_message="This model is currently experiencing high demand",
    )
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def test_translate_gemini_admin_stats_full_russian_output() -> None:
    active = _account(is_active=True)
    waiting = _account(
        account_name="GEMINI_API_KEY2",
        is_active=False,
        temporary_unavailable_until=datetime.utcnow() + timedelta(minutes=2),
    )
    stats = {
        "active_account": active,
        "user_requests_today": 5,
        "api_attempts_today": 8,
        "retries_today": 3,
        "successful_requests_today": 5,
        "failed_requests_today": 3,
        "total_requests_all_time": 32,
        "total_limit_switches": 7,
        "total_temporary_failovers": 4,
        "failovers_due_to_quota_today": 1,
        "failovers_due_to_temporary_today": 2,
        "last_switch_reason": "switch_due_to_temporary_failure",
        "accounts": [active, waiting],
        "recent_events": [
            {
                "created_at": datetime(2026, 4, 9, 10, 18),
                "account_name": "GEMINI_API_KEY",
                "event_type": "request_success",
                "error_message": None,
            },
            {
                "created_at": datetime(2026, 4, 9, 10, 16),
                "account_name": "GEMINI_API_KEY",
                "event_type": "retry_temporary_error",
                "error_message": "This model is currently experiencing high demand",
            },
        ],
    }

    result = translate_gemini_admin_stats(stats)

    assert "Переключений из-за временных ошибок" in result
    assert "Пользовательские запросы сегодня" in result
    assert "Попытки API сегодня" in result
    assert "Последнее сообщение об ошибке" in result
    assert "Временная ошибка Gemini (503: модель перегружена)" in result
    assert "🟢 Активен" in result
    assert "🟠 Ожидание / Кулдаун" in result
    assert "limit_switches" not in result
    assert "temporary_failover" not in result
    assert "Сегодня:" in result
    assert "• Попытки API: —" in result


def test_translate_gemini_admin_stats_compact_mode() -> None:
    account = _account(is_active=True, error_requests=0, total_requests=8)
    stats = {
        "active_account": account,
        "user_requests_today": 2,
        "api_attempts_today": 2,
        "retries_today": 0,
        "successful_requests_today": 2,
        "failed_requests_today": 0,
        "total_requests_all_time": 8,
        "total_limit_switches": 1,
        "total_temporary_failovers": 0,
        "failovers_due_to_quota_today": 0,
        "failovers_due_to_temporary_today": 0,
        "last_switch_reason": None,
        "accounts": [account],
        "recent_events": [],
    }

    result = translate_gemini_admin_stats(stats, compact=True)

    assert "Всего: " not in result
    assert "Запросы: <b>8</b>, Ошибки: <b>0</b>" in result
    assert "🕘 <b>Последние события</b>" in result


def test_format_recent_events_displays_naive_utc_as_moscow_time() -> None:
    events = [
        SimpleNamespace(
            created_at=datetime(2026, 4, 9, 8, 33),
            user_id="12345",
            event_name="open_main_menu",
        )
    ]

    result = format_recent_events(events)

    assert "11:33 — 12345 — 📱 Открыл главное меню" in result
    assert "08:33" not in result


def test_format_recent_events_displays_aware_utc_as_moscow_time() -> None:
    events = [
        SimpleNamespace(
            created_at=datetime(2026, 4, 9, 8, 33, tzinfo=timezone.utc),
            user_id="12345",
            event_name="open_main_menu",
        )
    ]

    result = format_recent_events(events)

    assert "11:33 — 12345 — 📱 Открыл главное меню" in result
    assert "08:33" not in result


def test_format_openai_ai_latest_events_include_moscow_date() -> None:
    metrics = {
        "requests_today": 1,
        "success_today": 1,
        "errors_today": 0,
        "input_tokens_today": 100,
        "output_tokens_today": 20,
        "total_tokens_today": 120,
        "estimated_cost_today": 0.001,
        "latest_events": [
            SimpleNamespace(
                created_at=datetime(2026, 4, 9, 8, 33, tzinfo=timezone.utc),
                feature="label_analysis",
                status="success",
                total_tokens=120,
                latency_ms=2838,
                estimated_cost_usd=0.001,
            )
        ],
    }

    result = format_openai_ai(metrics, key_configured=True)

    assert "09.04 11:33 — label_analysis — success" in result
    assert "• 11:33 — label_analysis" not in result


def test_format_deepseek_ai_latest_events_include_moscow_date() -> None:
    metrics = {
        "requests_today": 1,
        "success_today": 0,
        "errors_today": 1,
        "input_tokens_today": 0,
        "output_tokens_today": 0,
        "total_tokens_today": 0,
        "estimated_cost_today": 0,
        "latest_events": [
            SimpleNamespace(
                created_at=datetime(2026, 4, 9, 21, 5),
                feature="text_meal_analysis",
                status="error",
                error_message="timeout",
            )
        ],
    }

    result = format_deepseek_ai(metrics, key_configured=True)

    assert "10.04 00:05 — text_meal_analysis — error — timeout" in result
    assert "• 00:05 — text_meal_analysis" not in result
