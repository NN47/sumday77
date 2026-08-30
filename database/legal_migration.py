"""Idempotent legal metadata migration; no diary data is rewritten."""
from sqlalchemy import inspect, text


def migrate_legal_metadata(engine) -> None:
    # Fail startup if this migration fails: the new ORM requires these columns.
    with engine.begin() as connection:
        schema = inspect(connection)
        columns = {column["name"] for column in schema.get_columns("users")}
        for name, sql_type in (
            ("accepted_terms_version", "VARCHAR(40)"),
            ("acknowledged_privacy_version", "VARCHAR(40)"),
            ("terms_accepted_at", "TIMESTAMP"),
            ("privacy_acknowledged_at", "TIMESTAMP"),
        ):
            if name not in columns:
                connection.execute(text(f"ALTER TABLE users ADD COLUMN {name} {sql_type}"))

        # Legacy nullable columns are scrubbed, then left unmapped for compatibility
        # with SQLite versions without DROP COLUMN. New databases omit them entirely.
        support_columns = {column["name"] for column in schema.get_columns("support_messages")}
        for name in ("username", "full_name"):
            if name in support_columns:
                connection.execute(text(f"UPDATE support_messages SET {name} = NULL WHERE {name} IS NOT NULL"))
