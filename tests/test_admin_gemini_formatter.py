import importlib.util
import sys
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace


MODULE_PATH = Path(__file__).resolve().parents[1] / "utils" / "admin_formatters.py"
spec = importlib.util.spec_from_file_location("admin_formatters", MODULE_PATH)
module = importlib.util.module_from_spec(spec)
assert spec and spec.loader
sys.modules[spec.name] = module
spec.loader.exec_module(module)
translate_gemini_admin_stats = module.translate_gemini_admin_stats


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
