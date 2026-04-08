"""Закрытый admin-раздел внутри Telegram-бота."""
from __future__ import annotations

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton

from config import ADMIN_ID
from services.admin_stats_service import AdminStatsService
from utils.admin_formatters import (
    format_dashboard,
    format_today,
    format_funnel,
    format_retention,
    format_errors,
    format_recent_events,
    format_users
)

router = Router()


def _is_admin_telegram_id(user_id: int) -> bool:
    return int(user_id) == int(ADMIN_ID)


def _admin_menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📊 Дашборд", callback_data="admin:dashboard")],
            [InlineKeyboardButton(text="📅 Сегодня", callback_data="admin:today")],
            [InlineKeyboardButton(text="📉 Воронка", callback_data="admin:funnel")],
            [InlineKeyboardButton(text="🔁 Возвраты", callback_data="admin:retention")],
            [InlineKeyboardButton(text="🧠 Анализ дня", callback_data="admin:daily")],
            [InlineKeyboardButton(text="👤 Пользователи", callback_data="admin:users")],
            [InlineKeyboardButton(text="⚠️ Ошибки", callback_data="admin:errors")],
            [InlineKeyboardButton(text="🕘 Последние события", callback_data="admin:events")],
            [InlineKeyboardButton(text="🔄 Обновить", callback_data="admin:refresh")],
        ]
    )


def _back_kb(extra: list[InlineKeyboardButton] | None = None) -> InlineKeyboardMarkup:
    row = [InlineKeyboardButton(text="⬅️ Назад", callback_data="admin:home")]
    if extra:
        row.extend(extra)
    return InlineKeyboardMarkup(inline_keyboard=[row])


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

    if action == "dashboard":
        text = format_dashboard(AdminStatsService.get_dashboard_metrics())
        await _edit_or_answer(callback.message, text, _back_kb())
        return

    if action == "today":
        text = format_today(AdminStatsService.get_today_metrics())
        await _edit_or_answer(callback.message, text, _back_kb())
        return

    if action == "funnel":
        text = format_funnel(AdminStatsService.get_funnel_metrics())
        await _edit_or_answer(callback.message, text, _back_kb())
        return

    if action == "retention":
        text = format_retention(AdminStatsService.get_retention_metrics())
        await _edit_or_answer(callback.message, text, _back_kb())
        return

    if action == "daily":
        dashboard = AdminStatsService.get_dashboard_metrics()
        lines = [
            "🧠 <b>Анализ дня (сегодня)</b>",
            "",
            f"• Запущено: <b>{dashboard['daily_analysis_started']}</b>",
            f"• Отправлено: <b>{dashboard['daily_analysis_sent']}</b>",
            f"• Ошибок: <b>{dashboard['daily_analysis_failed']}</b>",
            f"• Успешность: <b>{dashboard['daily_analysis_success_rate']:.1f}%</b>",
        ]
        await _edit_or_answer(callback.message, "\n".join(lines), _back_kb())
        return

    if action == "errors":
        text = format_errors(AdminStatsService.get_errors_metrics())
        await _edit_or_answer(callback.message, text, _back_kb())
        return

    if action == "users":
        text = format_users(AdminStatsService.get_users_metrics(limit=15))
        await _edit_or_answer(callback.message, text, _back_kb())
        return

    if action == "events":
        text = format_recent_events(AdminStatsService.get_latest_events(limit=20))
        await _edit_or_answer(callback.message, text, _back_kb())


def register_admin_handlers(dp):
    """Регистрирует admin router."""
    dp.include_router(router)
