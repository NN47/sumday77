"""Cleanup of process-local data that is unambiguously owned by one user."""
from __future__ import annotations

from collections.abc import MutableMapping
from typing import Any


USER_DICTIONARY_CACHES = (
    "last_meal_ids",
    "selected_food_diary_dates",
    "food_diary_message_ids",
)


def _belongs_to_user(value: Any, user_id: str) -> bool:
    if isinstance(value, tuple) and value:
        return str(value[0]) == user_id
    return str(value) == user_id


async def clear_user_fsm_states(state: Any, user_id: str | int) -> None:
    """Remove every MemoryStorage key for this user, with a generic fallback."""
    normalized_id = str(user_id)
    storage = getattr(state, "storage", None)
    memory_records = getattr(storage, "storage", None)
    if isinstance(memory_records, MutableMapping):
        matching_keys = tuple(
            key
            for key in memory_records
            if str(getattr(key, "user_id", "")) == normalized_id
        )
        for key in matching_keys:
            memory_records.pop(key, None)
        return

    await state.clear()


def clear_user_process_caches(bot: Any, user_id: str | int) -> None:
    """Remove only entries whose cache key is the deleted Telegram user id."""
    normalized_id = str(user_id)

    for attribute_name in USER_DICTIONARY_CACHES:
        cache = getattr(bot, attribute_name, None)
        if not isinstance(cache, dict):
            continue
        cache.pop(normalized_id, None)
        try:
            cache.pop(int(normalized_id), None)
        except (TypeError, ValueError):
            pass

    in_progress = getattr(bot, "meal_comment_in_progress", None)
    if isinstance(in_progress, set):
        in_progress.difference_update(
            item for item in tuple(in_progress) if _belongs_to_user(item, normalized_id)
        )

    scheduler = getattr(bot, "sumday77_notification_scheduler", None)
    if scheduler is not None:
        clear_cache = getattr(scheduler, "clear_user_cache", None)
        if callable(clear_cache):
            clear_cache(normalized_id)
