"""Shared inline pagination helpers."""
from __future__ import annotations

import math

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

PAGINATION_NOOP_CALLBACK = "pagination_noop"
PAGINATION_PREV_TEXT = "⬅️ Предыдущая"
PAGINATION_NEXT_TEXT = "Следующая ➡️"


def total_pages_for(total_items: int, page_size: int) -> int:
    """Return a non-zero page count for a collection."""
    return max(1, math.ceil(max(0, total_items) / page_size))


def clamp_page(current_page: int, total_pages: int) -> int:
    """Clamp zero-based page index to an existing page."""
    return min(max(0, current_page), max(1, total_pages) - 1)


def build_pagination_row(current_page: int, total_pages: int, callback_prefix: str, *, page_base: int = 0) -> list[InlineKeyboardButton]:
    """Build a compact pagination row. current_page is zero-based; callback page can be offset."""
    total_pages = max(1, total_pages)
    current_page = clamp_page(current_page, total_pages)
    row: list[InlineKeyboardButton] = []
    if current_page > 0:
        row.append(InlineKeyboardButton(text=PAGINATION_PREV_TEXT, callback_data=f"{callback_prefix}:{current_page - 1 + page_base}"))
    row.append(
        InlineKeyboardButton(
            text=f"{current_page + 1}/{total_pages}",
            callback_data=PAGINATION_NOOP_CALLBACK,
        )
    )
    if current_page < total_pages - 1:
        row.append(InlineKeyboardButton(text=PAGINATION_NEXT_TEXT, callback_data=f"{callback_prefix}:{current_page + 1 + page_base}"))
    return row


def build_pagination_keyboard(
    current_page: int,
    total_pages: int,
    callback_prefix: str,
    extra_rows: list[list[InlineKeyboardButton]] | None = None,
    *,
    pagination_first: bool = False,
    page_base: int = 0,
) -> InlineKeyboardMarkup:
    """Build inline keyboard with optional rows and a shared compact pagination row."""
    rows = list(extra_rows or [])
    pagination_row = build_pagination_row(current_page, total_pages, callback_prefix, page_base=page_base)
    if total_pages > 1:
        if pagination_first:
            rows.insert(0, pagination_row)
        else:
            rows.append(pagination_row)
    return InlineKeyboardMarkup(inline_keyboard=rows)
