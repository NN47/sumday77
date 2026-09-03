"""Совместимость тренировок после перехода от таймера к ручному дневнику."""
from sqlalchemy import inspect, text


def migrate_workout_sessions(engine) -> None:
    """Сохраняет старые записи и выравнивает обе версии схемы без пересоздания."""
    with engine.begin() as connection:
        columns = {column["name"] for column in inspect(connection).get_columns("workout_sessions")}
        # Эти поля существовали в первой версии как NOT NULL без SQL-default,
        # затем были удалены только из ORM. Модель снова заполняет их при INSERT.
        # Базам, созданным уже без таймера, добавляем поля с безопасными defaults.
        for name, definition in (
            ("session_kind", "VARCHAR(24) NOT NULL DEFAULT 'workout'"),
            ("paused_seconds", "INTEGER NOT NULL DEFAULT 0"),
        ):
            if name not in columns:
                connection.execute(text(f"ALTER TABLE workout_sessions ADD COLUMN {name} {definition}"))

        # Незавершённые сессии старого таймера доступны для продолжения.
        # Подходы, параметры и итоги завершённых тренировок не меняются.
        connection.execute(text(
            "UPDATE workout_sessions SET status = 'draft' "
            "WHERE status IN ('active', 'paused', 'awaiting_intensity')"
        ))
