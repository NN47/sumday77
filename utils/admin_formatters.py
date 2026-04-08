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
