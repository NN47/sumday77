"""Утилиты для безопасной отправки длинных сообщений в Telegram."""


def split_telegram_message(text: str, limit: int = 4000) -> list[str]:
    """Разбивает длинный текст на части длиной не более ``limit``.

    Предпочитает разделение по последнему символу новой строки в пределах лимита.
    Если перевод строки не найден, делает жёсткий разрез по лимиту.
    """
    if not text:
        return [""]

    parts: list[str] = []
    remaining = text

    while len(remaining) > limit:
        split_at = remaining.rfind("\n", 0, limit + 1)
        if split_at <= 0:
            split_at = limit

        chunk = remaining[:split_at]
        parts.append(chunk)

        remaining = remaining[split_at:]
        if remaining.startswith("\n"):
            remaining = remaining[1:]

    if remaining:
        parts.append(remaining)

    return parts
