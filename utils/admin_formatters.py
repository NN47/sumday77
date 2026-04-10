"""Форматирование сообщений админ-панели."""
from __future__ import annotations

from datetime import datetime

EVENT_LABELS = {
    "open_main_menu": "📱 Открыл главное меню",
    "open_kbju": "🍱 Открыл КБЖУ",
    "add_meal": "➕ Добавил еду",
    "open_weight": "⚖️ Открыл раздел веса",
    "add_weight": "⚖️ Добавил вес",
    "open_activity": "🏃 Открыл активность",
    "add_steps": "🚶 Добавил шаги",
    "add_workout": "💪 Добавил тренировку",
    "open_notes": "📝 Открыл заметки",
    "request_daily_analysis": "🧠 Запросил анализ дня",
    "daily_analysis_started": "🧠 Старт анализа дня",
    "daily_analysis_sent": "✅ Анализ отправлен",
    "daily_analysis_failed": "❌ Ошибка анализа дня",
}


def fmt_dt(value) -> str:
    if not value:
        return "—"
    if isinstance(value, datetime):
        return value.strftime("%d.%m %H:%M")
    return str(value)


def human_event_name(event_name: str) -> str:
    return EVENT_LABELS.get(event_name, "🔹 Неизвестное событие")


def format_dashboard(metrics: dict) -> str:
    latest_error = metrics.get("latest_error")
    latest_error_line = (
        f"{fmt_dt(latest_error.created_at)} — {latest_error.error_type}" if latest_error else "✅ ошибок нет"
    )
    return (
        "📊 <b>Дашборд</b>\n\n"
        "👥 <b>Пользователи</b>\n"
        f"• Всего: <b>{metrics['total_users']}</b>\n"
        f"• Активные 24ч: <b>{metrics['active_24h']}</b>\n"
        f"• Активные 7д: <b>{metrics['active_7d']}</b>\n"
        f"• Активные 30д: <b>{metrics['active_30d']}</b>\n"
        f"• Новые сегодня: <b>{metrics['new_today']}</b>\n"
        f"• Новые 7д: <b>{metrics['new_7d']}</b>\n\n"
        "💎 <b>Использование</b>\n"
        f"• Пользователи с действиями сегодня: <b>{metrics['core_users_today']}</b>\n"
        f"• Пользователи с действиями 7д: <b>{metrics['core_users_7d']}</b>\n"
        f"• Пользователи с действиями 30д: <b>{metrics['core_users_30d']}</b>\n"
        f"• Конверсия в действия: <b>{metrics['conversion_to_core']:.1f}%</b>\n\n"
        "📢 <b>Активность</b>\n"
        f"• Всего действий сегодня: <b>{metrics['total_events_today']}</b>\n"
        f"• Среднее действий на активного пользователя: <b>{metrics['avg_actions_per_user']:.2f}</b>\n\n"
        "🧠 <b>Анализ дня</b>\n"
        f"• Запущено: <b>{metrics['daily_analysis_started']}</b>\n"
        f"• Отправлено: <b>{metrics['daily_analysis_sent']}</b>\n"
        f"• Ошибок: <b>{metrics['daily_analysis_failed']}</b>\n"
        f"• Успешность: <b>{metrics['daily_analysis_success_rate']:.1f}%</b>\n\n"
        "⚠️ <b>Ошибки</b>\n"
        f"• Сегодня: <b>{metrics['errors_today']}</b>\n"
        f"• Последняя: <b>{latest_error_line}</b>"
    )


def format_today(metrics: dict) -> str:
    navigation_labels = {
        "open_main_menu": "📱 Открыли главное меню",
        "open_kbju": "🍱 Открыли КБЖУ",
        "open_weight": "⚖️ Открыли вес",
        "open_activity": "🏃 Открыли активность",
        "open_notes": "📝 Открыли заметки",
    }
    helpful_labels = {
        "add_meal": "➕ Добавили еду",
        "add_weight": "⚖️ Добавили вес",
        "add_steps": "🚶 Добавили шаги",
        "add_workout": "💪 Добавили тренировку",
        "request_daily_analysis": "🧠 Запросили анализ дня",
    }
    lines = [
        "📅 <b>Сегодня</b>",
        "",
        f"👥 Активные пользователи: <b>{metrics['active_users_today']}</b>",
        "",
        "📂 <b>Переходы</b>",
    ]
    for name, count in metrics["navigation"].items():
        lines.append(f"• {navigation_labels.get(name, human_event_name(name))}: <b>{count}</b>")

    lines.extend(["", "✍️ <b>Действия</b>"])
    for name, count in metrics["helpful"].items():
        lines.append(f"• {helpful_labels.get(name, human_event_name(name))}: <b>{count}</b>")
    return "\n".join(lines)


def format_funnel(metrics: dict) -> str:
    return (
        "📉 <b>Воронка за сегодня</b>\n\n"
        f"• Открыли главное меню: <b>{metrics['menu']}</b>\n"
        f"• Зашли в разделы: <b>{metrics['sections']}</b>\n"
        f"• Сделали хотя бы одно действие: <b>{metrics['core']}</b>\n"
        f"• Запросили анализ дня: <b>{metrics['analysis']}</b>\n\n"
        "📈 <b>Конверсия</b>\n"
        f"• Меню → разделы: <b>{metrics['sections_from_menu']:.1f}%</b>\n"
        f"• Разделы → действия: <b>{metrics['core_from_sections']:.1f}%</b>\n"
        f"• Действия → анализ: <b>{metrics['analysis_from_core']:.1f}%</b>"
    )


def format_retention(points: list) -> str:
    lines = ["🔁 <b>Возвраты</b>", ""]
    has_data = False
    for point in points:
        if point.cohort_size > 0:
            has_data = True
            lines.append(
                f"• D{point.days}: <b>{point.percent:.1f}%</b> "
                f"({point.returned_today}/{point.cohort_size})"
            )
        else:
            lines.append(f"• D{point.days}: недостаточно данных (когорта = 0)")

    if not has_data:
        lines.append("\nПока недостаточно исторических данных для расчёта возвратов.")
    return "\n".join(lines)


def format_errors(metrics: dict) -> str:
    week_errors = metrics["week"]
    analysis_failed = metrics.get("daily_analysis_failed", 0)

    if week_errors == 0 and analysis_failed == 0:
        return "⚠️ <b>Ошибки</b>\n\n✅ Ошибок нет"

    if week_errors == 0 and analysis_failed > 0:
        return (
            "⚠️ <b>Ошибки</b>\n\n"
            "⚠️ Есть сбои анализа, но нет записей в журнале ошибок\n"
            f"• Сбоев анализа сегодня: <b>{analysis_failed}</b>"
        )

    lines = [
        "⚠️ <b>Ошибки</b>",
        "",
        f"• Сегодня: <b>{metrics['today']}</b>",
        f"• За 7 дней: <b>{week_errors}</b>",
        "",
        "Последние:",
    ]

    for source, error_type, count in metrics["grouped"]:
        lines.append(f"• {source} / {error_type} — <b>{count}</b>")

    last_error = metrics.get("last_error")
    if last_error:
        last_message = last_error.message or last_error.error_message or "—"
        last_source = last_error.source or last_error.module or "app"
        lines.extend(
            [
                "",
                "🕘 Последняя ошибка:",
                (
                    f"• {fmt_dt(last_error.created_at)} — {last_source} — "
                    f"{last_error.error_type} — {last_message[:180]}"
                ),
            ]
        )

    return "\n".join(lines)


def format_recent_events(events: list) -> str:
    if not events:
        return "🕘 <b>Последние события</b>\n\nПока нет событий."

    lines = ["🕘 <b>Последние события</b>", ""]
    for event in events:
        time_part = event.created_at.strftime("%H:%M") if event.created_at else "--:--"
        lines.append(f"{time_part} — {event.user_id} — {human_event_name(event.event_name)}")
    return "\n".join(lines)


def format_users(metrics: dict) -> str:
    users = metrics["users"]
    lines = ["👤 <b>Пользователи</b>", ""]
    if not users:
        lines.append("Пользователей пока нет.")
    else:
        for user in users:
            core_mark = "✅" if user["is_core_today"] else "—"
            lines.extend(
                [
                    f"• <b>{user['user_id']}</b>",
                    f"  рег: {fmt_dt(user['registered_at'])} | визит: {fmt_dt(user['last_seen_at'])}",
                    f"  действий сегодня: <b>{user['actions_today']}</b> | за 7д: <b>{user['actions_7d']}</b>",
                    f"  пользователь с действиями сегодня: <b>{core_mark}</b> | запросов анализа дня: <b>{user['daily_analysis_requests']}</b>",
                ]
            )

    lines.extend(["", "🏆 <b>Топ пользователей (7д)</b>"])
    top_users = metrics["top_users"]
    if not top_users:
        lines.append("Нет данных.")
    else:
        for user_id, count in top_users:
            lines.append(f"• {user_id}: <b>{count}</b>")
    return "\n".join(lines)


def clean_text(value: str | None, *, max_length: int = 140) -> str:
    if not value:
        return "—"
    cleaned = " ".join(str(value).split())
    if not cleaned:
        return "—"
    if len(cleaned) > max_length:
        return f"{cleaned[: max_length - 1].rstrip()}…"
    return cleaned


def format_datetime(value) -> str:
    if not value:
        return "—"
    if isinstance(value, datetime):
        return value.strftime("%d.%m %H:%M")
    return clean_text(str(value), max_length=32)


def translate_error_type(error_type: str | None) -> str:
    mapping = {
        "temporary": "Временная ошибка",
        "quota": "Превышен лимит",
        "auth": "Ошибка авторизации",
        "unknown": "Неизвестная ошибка",
    }
    if not error_type:
        return "—"
    return mapping.get(error_type, "Неизвестная ошибка")


def translate_switch_reason(reason: str | None) -> str:
    mapping = {
        "switch_due_to_quota": "Переключение из-за лимита",
        "switch_due_to_temporary_failure": "Переключение из-за временной ошибки",
        "switch_due_to_auth_error": "Переключение из-за ошибки авторизации",
    }
    return mapping.get(reason or "", "—")


def translate_error_message(message: str | None, *, debug: bool = False, max_length: int = 140) -> str:
    normalized = clean_text(message, max_length=300)
    if normalized == "—":
        return "—"

    lowered = normalized.lower()
    if "503" in lowered or "service unavailable" in lowered or "high demand" in lowered or "overloaded" in lowered:
        translated = "Временная ошибка Gemini (503 / model overloaded)"
    elif "client has been closed" in lowered:
        translated = "Клиент Gemini был закрыт"
    elif any(token in lowered for token in ("quota", "rate limit", "resource exhausted", "429")):
        translated = "Ошибка лимита Gemini API (quota exceeded)"
    elif any(token in lowered for token in ("auth", "unauthenticated", "unauthorized", "401", "403")):
        translated = "Ошибка авторизации Gemini API"
    else:
        translated = normalized

    translated = clean_text(translated, max_length=max_length)
    if debug and translated != normalized:
        return clean_text(f"{translated} | raw: {normalized}", max_length=max_length)
    return translated


def _val(value, *, default: str = "—") -> str:
    if value is None:
        return default
    return str(value)


def _account_health(account) -> str:
    if getattr(account, "disabled_reason", None) or getattr(account, "status", None) in {
        "cooldown",
        "rate_limited",
        "auth_failed",
        "disabled",
    }:
        return "Недоступен"
    errors = int(getattr(account, "error_requests", 0) or 0)
    total = int(getattr(account, "total_requests", 0) or 0)
    if total == 0:
        return "Стабильный"
    if errors / total >= 0.35 or errors >= 10:
        return "Нестабильный"
    return "Стабильный"


def translate_status(account, *, active_account_name: str | None) -> str:
    now = datetime.utcnow()
    raw_status = getattr(account, "status", None)
    disabled_reason = getattr(account, "disabled_reason", None)
    cooldown_until = getattr(account, "temporary_unavailable_until", None)

    if disabled_reason or raw_status in {"auth_failed", "disabled"}:
        return "🔴 Unavailable"
    if getattr(account, "account_name", None) == active_account_name or getattr(account, "is_active", False):
        return "🟢 Active"
    if raw_status == "rate_limited":
        return "🟣 Quota limited"
    if cooldown_until and isinstance(cooldown_until, datetime) and cooldown_until > now:
        return "🟠 Cooldown"
    if raw_status in {"active", "cooldown", "rate_limited", "auth_failed", "disabled", None, ""}:
        return "🟡 Standby"
    return "⚪ Неизвестно"


def build_account_block(account, *, active_account_name: str | None, compact: bool = False, debug: bool = False) -> str:
    title = f"• <b>{_val(getattr(account, 'account_name', None))}</b> {translate_status(account, active_account_name=active_account_name)}"
    if compact:
        return (
            f"{title}\n"
            f"  • Запросы: <b>{_val(getattr(account, 'total_requests', 0), default='0')}</b>, "
            f"Ошибки: <b>{_val(getattr(account, 'error_requests', 0), default='0')}</b>, "
            f"Состояние: <b>{_account_health(account)}</b>"
        )

    lines = [
        title,
        "",
        f"  Ключ: {_val(getattr(account, 'api_key_masked', None))}",
        "",
        "  Запросы:",
        f"  • Сегодня: {_val(getattr(account, 'today_requests', None))}",
        f"  • Всего: {_val(getattr(account, 'total_requests', 0), default='0')}",
        f"  • Успешных: {_val(getattr(account, 'success_requests', 0), default='0')}",
        f"  • Ошибок: {_val(getattr(account, 'error_requests', 0), default='0')}",
        "",
        "  Ошибки:",
        f"  • Временные: {_val(getattr(account, 'temporary_errors_count', 0), default='0')}",
        f"  • Лимиты: {_val(getattr(account, 'quota_errors_count', 0), default='0')}",
        f"  • Авторизация: {_val(getattr(account, 'auth_errors_count', 0), default='0')}",
        f"  • Неизвестные: {_val(getattr(account, 'unknown_errors_count', 0), default='0')}",
        "",
        "  Переключения:",
        f"  • По лимиту: {_val(getattr(account, 'limit_switches', 0), default='0')}",
        f"  • По временным ошибкам: {_val(getattr(account, 'temporary_failover_count', 0), default='0')}",
        "",
        "  Статус:",
        f"  • Состояние: {_account_health(account)}",
        f"  • Последний запрос: {format_datetime(getattr(account, 'last_request_at', None))}",
        f"  • Последняя ошибка: {format_datetime(getattr(account, 'last_error_at', None))}",
        f"  • Тип ошибки: {translate_error_type(getattr(account, 'last_error_type', None))}",
        f"  • Ожидание до: {format_datetime(getattr(account, 'temporary_unavailable_until', None))}",
        f"  • Ограничение API до: {format_datetime(getattr(account, 'rate_limited_until', None))}",
        f"  • Причина отключения: {clean_text(getattr(account, 'disabled_reason', None), max_length=90)}",
        f"  • Последнее сообщение об ошибке: {translate_error_message(getattr(account, 'last_error_message', None), debug=debug, max_length=90)}",
    ]
    return "\n".join(lines)


def build_events_block(events: list[dict], *, debug: bool = False) -> str:
    mapping = {
        "user_request_started": "Пользовательский запрос стартовал",
        "api_attempt": "Попытка обращения к API",
        "retry_temporary_error": "Повтор запроса после ошибки",
        "request_success": "Успешный запрос",
        "request_failed": "Неуспешный API-запрос",
        "key_put_on_cooldown": "Ключ временно на паузе",
        "switch_due_to_quota": "Переключение из-за лимита",
        "switch_due_to_temporary_failure": "Переключение из-за временной ошибки",
        "switch_due_to_auth_error": "Переключение из-за ошибки авторизации",
        "request_finished_success": "Пользовательский запрос завершён успешно",
        "request_finished_failed": "Пользовательский запрос окончательно не выполнен",
    }
    lines = ["🕘 <b>Последние события</b>", ""]
    if not events:
        lines.append("• —")
        return "\n".join(lines)

    for event in events:
        event_type = event.get("event_type") or event.get("status")
        label = mapping.get(event_type, "Событие")
        details = translate_error_message(event.get("error_message"), debug=debug, max_length=45)
        suffix = (
            f" ({details})"
            if details != "—"
            and event_type in {"retry_temporary_error", "key_put_on_cooldown", "request_failed", "request_finished_failed"}
            else ""
        )
        lines.append(
            f"• {format_datetime(event.get('created_at'))} — {event.get('account_name') or '—'} — {label}{suffix}"
        )
    return "\n".join(lines)


def translate_gemini_admin_stats(stats: dict, compact: bool = False, debug: bool = False) -> str:
    active = stats.get("active_account")
    active_name = getattr(active, "account_name", None) or "—"
    lines = [
        "🤖 <b>Gemini / AI</b>",
        "",
        f"• Активный аккаунт: <b>{active_name}</b>",
        f"• User requests сегодня: <b>{stats.get('user_requests_today', '—')}</b>",
        f"• API attempts сегодня: <b>{stats.get('api_attempts_today', '—')}</b>",
        f"• Retries сегодня: <b>{stats.get('retries_today', '—')}</b>",
        f"• Успешных API запросов сегодня: <b>{stats.get('successful_requests_today', '—')}</b>",
        f"• Неуспешных API запросов сегодня: <b>{stats.get('failed_requests_today', '—')}</b>",
        f"• Запросов всего: <b>{stats.get('total_requests_all_time', '—')}</b>",
        f"• Переключений по лимиту: <b>{stats.get('total_limit_switches', '—')}</b>",
        f"• Переключений из-за временных ошибок: <b>{stats.get('total_temporary_failovers', '—')}</b>",
        f"• Failover по лимиту сегодня: <b>{stats.get('failovers_due_to_quota_today', '—')}</b>",
        f"• Failover по временным ошибкам сегодня: <b>{stats.get('failovers_due_to_temporary_today', '—')}</b>",
        f"• Последняя причина переключения: <b>{translate_switch_reason(stats.get('last_switch_reason'))}</b>",
        "",
        "📚 <b>Аккаунты</b>",
    ]

    accounts = stats.get("accounts", [])
    if not accounts:
        lines.append("• —")
    else:
        for account in accounts:
            lines.append(build_account_block(account, active_account_name=active_name, compact=compact, debug=debug))
            lines.append("")

    lines.append(build_events_block(stats.get("recent_events", []), debug=debug))
    return "\n".join(lines).strip()


def format_gemini(metrics: dict) -> str:
    return translate_gemini_admin_stats(metrics)
