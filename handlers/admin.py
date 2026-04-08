"""Закрытый admin-раздел внутри Telegram-бота."""
from __future__ import annotations

from datetime import datetime

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton

from config import ADMIN_ID
from database.repositories import (
    UserRepository,
    AnalyticsRepository,
    SupportRepository,
    ErrorLogRepository,
)

router = Router()


def _is_admin_telegram_id(user_id: int) -> bool:
    return int(user_id) == int(ADMIN_ID)


def _admin_menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📊 Общая статистика", callback_data="admin:stats")],
            [InlineKeyboardButton(text="📅 Сегодня", callback_data="admin:today")],
            [InlineKeyboardButton(text="🧠 Анализ дня", callback_data="admin:daily")],
            [InlineKeyboardButton(text="⚠️ Ошибки", callback_data="admin:errors")],
            [InlineKeyboardButton(text="👤 Пользователи", callback_data="admin:users")],
            [InlineKeyboardButton(text="💬 Поддержка", callback_data="admin:support")],
            [InlineKeyboardButton(text="🔍 Последние события", callback_data="admin:events")],
            [InlineKeyboardButton(text="🔄 Обновить", callback_data="admin:refresh")],
        ]
    )


def _back_kb(extra: list[InlineKeyboardButton] | None = None) -> InlineKeyboardMarkup:
    row = [InlineKeyboardButton(text="⬅️ Назад", callback_data="admin:home")]
    if extra:
        row.extend(extra)
    return InlineKeyboardMarkup(inline_keyboard=[row])


def _fmt_dt(value) -> str:
    if not value:
        return "—"
    if isinstance(value, datetime):
        return value.strftime("%d.%m %H:%M")
    return str(value)


async def _edit_or_answer(message: Message, text: str, reply_markup: InlineKeyboardMarkup) -> None:
    try:
        await message.edit_text(text, reply_markup=reply_markup)
    except Exception:
        await message.answer(text, reply_markup=reply_markup)


@router.message(Command("admin"))
async def admin_entry(message: Message):
    if not message.from_user or not _is_admin_telegram_id(message.from_user.id):
        return
    await message.answer("🔐 Админ раздел", reply_markup=_admin_menu_kb())


@router.callback_query(lambda c: c.data and c.data.startswith("admin:"))
async def admin_callbacks(callback: CallbackQuery):
    if not callback.from_user or not _is_admin_telegram_id(callback.from_user.id):
        await callback.answer()
        return

    await callback.answer()
    action = callback.data.split(":", 1)[1]

    if action in {"home", "refresh"}:
        await _edit_or_answer(callback.message, "🔐 Админ раздел", _admin_menu_kb())
        return

    if action == "stats":
        text = (
            "📊 Общая статистика\n\n"
            f"• Всего пользователей: <b>{UserRepository.count_all()}</b>\n"
            f"• Активные 24ч: <b>{UserRepository.count_active_24h()}</b>\n"
            f"• Активные 7д: <b>{UserRepository.count_active_7d()}</b>\n"
            f"• Активные 30д: <b>{UserRepository.count_active_30d()}</b>\n\n"
            f"• Новые сегодня: <b>{UserRepository.count_new_today()}</b>\n"
            f"• Новые 7д: <b>{UserRepository.count_new_7d()}</b>"
        )
        await _edit_or_answer(callback.message, text, _back_kb())
        return

    if action == "today":
        events = AnalyticsRepository.count_events_today_bulk([
            "open_kbju", "add_meal", "open_weight", "add_weight", "open_activity", "add_steps", "add_workout",
            "open_notes", "request_daily_analysis",
        ])
        text = (
            "📅 Сегодня\n\n"
            f"• Активные сегодня: <b>{UserRepository.count_active_24h()}</b>\n\n"
            f"open_kbju: <b>{events['open_kbju']}</b>\n"
            f"add_meal: <b>{events['add_meal']}</b>\n"
            f"open_weight: <b>{events['open_weight']}</b>\n"
            f"add_weight: <b>{events['add_weight']}</b>\n"
            f"open_activity: <b>{events['open_activity']}</b>\n"
            f"add_steps: <b>{events['add_steps']}</b>\n"
            f"add_workout: <b>{events['add_workout']}</b>\n"
            f"open_notes: <b>{events['open_notes']}</b>\n"
            f"request_daily_analysis: <b>{events['request_daily_analysis']}</b>"
        )
        await _edit_or_answer(callback.message, text, _back_kb())
        return

    if action == "daily":
        started = AnalyticsRepository.count_events_period("daily_analysis_started", 7)
        sent = AnalyticsRepository.count_events_period("daily_analysis_sent", 7)
        failed = AnalyticsRepository.count_events_period("daily_analysis_failed", 7)
        total = sent + failed
        rate = (sent * 100 / total) if total else 0
        recent_errors = ErrorLogRepository.get_recent_daily_analysis_errors(limit=5)
        lines = [
            "🧠 Анализ дня (7д)\n",
            f"started: <b>{started}</b>",
            f"sent: <b>{sent}</b>",
            f"failed: <b>{failed}</b>",
            f"success rate: <b>{rate:.1f}%</b>",
            "\nПоследние ошибки:",
        ]
        if recent_errors:
            for item in recent_errors:
                lines.append(f"• {_fmt_dt(item.created_at)} | {item.user_id or '-'} | {item.error_type}")
        else:
            lines.append("• Нет записей")
        await _edit_or_answer(callback.message, "\n".join(lines), _back_kb())
        return

    if action == "errors":
        recent = ErrorLogRepository.get_recent(limit=10)
        lines = [
            "⚠️ Ошибки\n",
            f"• today: <b>{ErrorLogRepository.count_today()}</b>",
            f"• 7d: <b>{ErrorLogRepository.count_7d()}</b>",
            "\nПоследние 10:",
        ]
        for item in recent:
            lines.append(
                f"• {_fmt_dt(item.created_at)} | uid={item.user_id or '-'} | {item.error_type} | {item.module or '-'}"
            )
        await _edit_or_answer(callback.message, "\n".join(lines), _back_kb())
        return

    if action == "users":
        recent = UserRepository.get_recent_active(limit=10)
        top = AnalyticsRepository.get_top_users(days=7, limit=10)
        lines = [
            "👤 Пользователи\n",
            f"• new today: <b>{UserRepository.count_new_today()}</b>",
            f"• new 7d: <b>{UserRepository.count_new_7d()}</b>",
            "\nПоследние активные:",
        ]
        for item in recent:
            lines.append(f"• {item.user_id} | {_fmt_dt(item.last_seen_at)}")
        lines.append("\nTop 10 по событиям (7д):")
        for user_id, cnt in top:
            lines.append(f"• {user_id}: <b>{cnt}</b>")
        await _edit_or_answer(callback.message, "\n".join(lines), _back_kb())
        return

    if action == "support":
        recent = SupportRepository.get_recent(limit=10)
        lines = [
            "💬 Поддержка\n",
            f"• today: <b>{SupportRepository.count_today()}</b>",
            f"• 7d: <b>{SupportRepository.count_7d()}</b>",
            "\nПоследние 10:",
        ]
        for item in recent:
            text_short = (item.message_text or "").replace("\n", " ")[:70]
            lines.append(f"• [{item.id}] {_fmt_dt(item.created_at)} | {item.user_id} | @{item.username or '-'} | {text_short}")
        extra = [InlineKeyboardButton(text="Mark as read", callback_data="admin:support_mark")]
        await _edit_or_answer(callback.message, "\n".join(lines), _back_kb(extra=extra))
        return

    if action == "support_mark":
        updated = 0
        for msg in SupportRepository.get_recent(limit=10):
            if not msg.is_read and SupportRepository.mark_read(msg.id):
                updated += 1
        await _edit_or_answer(callback.message, f"✅ Отмечено прочитанными: {updated}", _back_kb())
        return

    if action == "events":
        recent = AnalyticsRepository.get_recent_events(limit=20)
        lines = ["🔍 Последние события\n"]
        for item in recent:
            lines.append(f"• {_fmt_dt(item.created_at)} | {item.user_id} | {item.event_name}")
        await _edit_or_answer(callback.message, "\n".join(lines), _back_kb())


def register_admin_handlers(dp):
    """Регистрирует admin router."""
    dp.include_router(router)
