import os

from sqlalchemy import create_engine, inspect, text

os.environ.setdefault("API_TOKEN", "test-token")
os.environ.setdefault("GEMINI_API_KEY", "test-gemini-key")

import database.session as session_module


def test_init_db_adds_quota_schema_without_rewriting_legacy_data(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'legacy-quota.db'}")
    with engine.begin() as connection:
        connection.execute(
            text(
                "CREATE TABLE users ("
                "id INTEGER PRIMARY KEY, user_id VARCHAR NOT NULL UNIQUE)"
            )
        )
        connection.execute(text("INSERT INTO users (id, user_id) VALUES (1, 'legacy-user')"))
        connection.execute(
            text(
                "CREATE TABLE activity_analysis_entries ("
                "id INTEGER PRIMARY KEY, user_id VARCHAR NOT NULL, analysis_text TEXT NOT NULL, "
                "date DATE, source VARCHAR, created_at TIMESTAMP)"
            )
        )
        connection.execute(
            text(
                "INSERT INTO activity_analysis_entries "
                "(id, user_id, analysis_text, date, source) "
                "VALUES (1, 'legacy-user', 'Старый анализ', '2026-08-22', 'manual')"
            )
        )
        connection.execute(
            text(
                "CREATE TABLE meal_completion_comments ("
                "id INTEGER PRIMARY KEY, user_id VARCHAR NOT NULL, meal_id INTEGER NOT NULL UNIQUE, "
                "date DATE NOT NULL, meal_type VARCHAR NOT NULL, comment_text TEXT, "
                "status VARCHAR NOT NULL, created_at TIMESTAMP)"
            )
        )
        connection.execute(
            text(
                "INSERT INTO meal_completion_comments "
                "(id, user_id, meal_id, date, meal_type, comment_text, status) "
                "VALUES (1, 'legacy-user', 7, '2026-08-22', 'dinner', 'Старый комментарий', 'success')"
            )
        )

    monkeypatch.setattr(session_module, "engine", engine)
    session_module.init_db()
    session_module.init_db()  # Миграция должна быть идемпотентной.

    schema = inspect(engine)
    tables = set(schema.get_table_names())
    assert {
        "user_plan_assignments",
        "ai_quota_counters",
        "ai_attempt_counters",
        "ai_global_daily_counters",
        "ai_quota_operations",
        "ai_quota_active_locks",
        "daily_analysis_preparation_sessions",
    } <= tables

    analysis_columns = {column["name"] for column in schema.get_columns("activity_analysis_entries")}
    assert {
        "status",
        "analyzed_at",
        "plan_key",
        "quota_request_id",
        "data_snapshot_hash",
    } <= analysis_columns
    comment_columns = {column["name"] for column in schema.get_columns("meal_completion_comments")}
    assert "quota_request_id" in comment_columns
    operation_columns = {column["name"] for column in schema.get_columns("ai_quota_operations")}
    assert "provider_attempt_count" in operation_columns

    with engine.connect() as connection:
        old_user = connection.execute(
            text("SELECT user_id FROM users WHERE id = 1")
        ).scalar_one()
        old_analysis = connection.execute(
            text("SELECT analysis_text FROM activity_analysis_entries WHERE id = 1")
        ).scalar_one()
        old_comment = connection.execute(
            text("SELECT comment_text FROM meal_completion_comments WHERE id = 1")
        ).scalar_one()

    assert old_user == "legacy-user"
    assert old_analysis == "Старый анализ"
    assert old_comment == "Старый комментарий"
    engine.dispose()
