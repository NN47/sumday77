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
    return EVENT_LABELS.get(event_name, f"🔹 {event_name}")


def format_dashboard(metrics: dict) -> str:
    latest_error = metrics.get("latest_error")
    latest_error_line = (
        f"{fmt_dt(latest_error.created_at)} — {latest_error.error_type}" if latest_error else "✅ Ошибок нет"
    )
    return (
        "📊 <b>Дашборд</b>\n\n"
        f"👥 Всего пользователей: <b>{metrics['total_users']}</b>\n"
        f"🟢 Активные 24ч: <b>{metrics['active_24h']}</b>\n"
        f"🟢 Активные 7д: <b>{metrics['active_7d']}</b>\n"
        f"🟢 Активные 30д: <b>{metrics['active_30d']}</b>\n"
        f"🆕 Новые сегодня: <b>{metrics['new_today']}</b>\n"
        f"🆕 Новые 7д: <b>{metrics['new_7d']}</b>\n\n"
        f"💎 Core users today: <b>{metrics['core_users_today']}</b>\n"
        f"💎 Core users 7d: <b>{metrics['core_users_7d']}</b>\n"
        f"💎 Core users 30d: <b>{metrics['core_users_30d']}</b>\n"
        f"📈 Conversion to core: <b>{metrics['conversion_to_core']:.1f}%</b>\n"
        f"📣 Total events today: <b>{metrics['total_events_today']}</b>\n"
        f"⚙️ Avg actions per user: <b>{metrics['avg_actions_per_user']:.2f}</b>\n\n"
        f"🧠 Анализ дня: started <b>{metrics['daily_analysis_started']}</b> / "
        f"sent <b>{metrics['daily_analysis_sent']}</b> / "
        f"failed <b>{metrics['daily_analysis_failed']}</b>\n"
        f"✅ Success rate: <b>{metrics['daily_analysis_success_rate']:.1f}%</b>\n\n"
        f"⚠️ Ошибки сегодня: <b>{metrics['errors_today']}</b>\n"
        f"🕘 Последняя ошибка: <b>{latest_error_line}</b>"
    )


def format_today(metrics: dict) -> str:
    lines = [
        "📅 <b>Сегодня</b>",
        "",
        f"🟢 Активные пользователи сегодня: <b>{metrics['active_users_today']}</b>",
        "",
        "🧭 <b>Навигация</b>",
    ]
    for name, count in metrics["navigation"].items():
        lines.append(f"• {human_event_name(name)}: <b>{count}</b>")

    lines.extend(["", "🎯 <b>Полезные действия</b>"])
    for name, count in metrics["helpful"].items():
        lines.append(f"• {human_event_name(name)}: <b>{count}</b>")
    return "\n".join(lines)


def format_funnel(metrics: dict) -> str:
    return (
        "📉 <b>Воронка (сегодня)</b>\n\n"
        f"1) 📱 Открыли главное меню: <b>{metrics['menu']}</b>\n"
        f"2) 📂 Открыли разделы: <b>{metrics['sections']}</b>"
        f" (<b>{metrics['sections_from_menu']:.1f}%</b> от шага 1)\n"
        f"3) 🎯 Сделали полезное действие: <b>{metrics['core']}</b>"
        f" (<b>{metrics['core_from_sections']:.1f}%</b> от шага 2)\n"
        f"4) 🧠 Запросили анализ дня: <b>{metrics['analysis']}</b>"
        f" (<b>{metrics['analysis_from_core']:.1f}%</b> от шага 3)"
    )


def format_retention(points: list) -> str:
    lines = ["🔁 <b>Retention</b>", ""]
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
        lines.append("\nПока недостаточно исторических данных для расчёта retention.")
    return "\n".join(lines)


def format_errors(metrics: dict) -> str:
    if metrics["week"] == 0:
        return "⚠️ <b>Ошибки</b>\n\n✅ Ошибок нет"

    lines = [
        "⚠️ <b>Ошибки</b>",
        "",
        f"• Сегодня: <b>{metrics['today']}</b>",
        f"• За 7 дней: <b>{metrics['week']}</b>",
        "",
        "Группировка по типам:",
    ]
    for error_type, count, last_seen in metrics["grouped"]:
        lines.append(f"• {error_type}: <b>{count}</b> (последний раз: {fmt_dt(last_seen)})")
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
                    f"  core user сегодня: <b>{core_mark}</b> | запросов анализа дня: <b>{user['daily_analysis_requests']}</b>",
                ]
            )

    lines.extend(["", "🏆 <b>Top users (7д)</b>"])
    top_users = metrics["top_users"]
    if not top_users:
        lines.append("Нет данных.")
    else:
        for user_id, count in top_users:
            lines.append(f"• {user_id}: <b>{count}</b>")
    return "\n".join(lines)
