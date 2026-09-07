"""Удаление неиспользуемых свободных текстов выведенных из эксплуатации функций."""
from __future__ import annotations

import json
import logging

from sqlalchemy import inspect, text
from sqlalchemy.exc import SQLAlchemyError

from utils.log_sanitizer import safe_exception_summary
from utils.note_factors import sanitize_note_factors

logger = logging.getLogger(__name__)


def _sanitize_factors_payload(payload: object) -> str:
    try:
        decoded = json.loads(str(payload or "[]"))
    except (TypeError, ValueError):
        decoded = []
    if not isinstance(decoded, list):
        decoded = []
    return json.dumps(sanitize_note_factors(decoded), ensure_ascii=False)


def migrate_retired_free_text_data(engine) -> None:
    """Удаляет старые свободные тексты, сохраняя структурированные заметки."""
    with engine.begin() as connection:
        schema = inspect(connection)
        table_names = set(schema.get_table_names())

        if "notes" in table_names:
            columns = {column["name"] for column in schema.get_columns("notes")}
            if "factors_json" in columns:
                rows = connection.execute(
                    text("SELECT id, factors_json FROM notes")
                ).mappings().all()
                sanitized_count = 0
                for row in rows:
                    sanitized_payload = _sanitize_factors_payload(row["factors_json"])
                    if sanitized_payload == (row["factors_json"] or "[]"):
                        continue
                    connection.execute(
                        text(
                            "UPDATE notes SET factors_json = :factors_json "
                            "WHERE id = :note_id"
                        ),
                        {
                            "factors_json": sanitized_payload,
                            "note_id": row["id"],
                        },
                    )
                    sanitized_count += 1
                if sanitized_count:
                    logger.info(
                        "Legacy note factors sanitized rows=%s",
                        sanitized_count,
                    )

            if "text" in columns:
                result = connection.execute(
                    text("UPDATE notes SET text = NULL WHERE text IS NOT NULL")
                )
                logger.info(
                    "Legacy note free text cleared rows=%s",
                    max(int(result.rowcount or 0), 0),
                )

        if "wellbeing_entries" in table_names:
            connection.execute(text("DROP TABLE IF EXISTS wellbeing_entries"))
            logger.info("Retired wellbeing_entries table dropped")

        if "procedures" in table_names:
            connection.execute(text("DROP TABLE IF EXISTS procedures"))
            logger.info("Retired procedures table dropped")

    # The content is already cleared above. Dropping the now-unmapped column is
    # best-effort for compatibility with older SQLite deployments.
    with engine.begin() as connection:
        schema = inspect(connection)
        if "notes" not in set(schema.get_table_names()):
            return
        columns = {column["name"] for column in schema.get_columns("notes")}
        if "text" not in columns:
            return
        try:
            connection.execute(text("ALTER TABLE notes DROP COLUMN text"))
            logger.info("Legacy notes.text column dropped")
        except SQLAlchemyError as exc:
            logger.warning(
                "Legacy notes.text column kept empty error_type=%s",
                safe_exception_summary(exc),
            )
