import json

from sqlalchemy import Column, Integer, MetaData, String, Table, Text, create_engine, inspect, select

from database.models import Base, NoteEntry
from database.retired_free_text_migration import migrate_retired_free_text_data


def test_migration_removes_legacy_free_text_and_keeps_allowed_note_factors():
    engine = create_engine("sqlite:///:memory:")
    metadata = MetaData()
    notes = Table(
        "notes",
        metadata,
        Column("id", Integer, primary_key=True),
        Column("factors_json", Text, nullable=False),
        Column("text", String(500), nullable=True),
    )
    wellbeing_entries = Table(
        "wellbeing_entries",
        metadata,
        Column("id", Integer, primary_key=True),
        Column("user_id", String, nullable=False),
        Column("comment", Text, nullable=True),
    )
    procedures = Table(
        "procedures",
        metadata,
        Column("id", Integer, primary_key=True),
        Column("user_id", String, nullable=False),
        Column("name", Text, nullable=False),
        Column("notes", Text, nullable=True),
    )
    metadata.create_all(engine)

    with engine.begin() as connection:
        connection.execute(
            notes.insert(),
            [
                {
                    "id": 1,
                    "factors_json": json.dumps(
                        ["tired", "custom private factor", "workout", "tired"]
                    ),
                    "text": "старый свободный текст",
                },
                {
                    "id": 2,
                    "factors_json": "not-json private text",
                    "text": "ещё один старый текст",
                },
            ],
        )
        connection.execute(
            wellbeing_entries.insert().values(
                id=1,
                user_id="123",
                comment="старый комментарий",
            )
        )
        connection.execute(
            procedures.insert().values(
                id=1,
                user_id="123",
                name="старое название процедуры",
                notes="старый свободный комментарий",
            )
        )

    migrate_retired_free_text_data(engine)
    migrate_retired_free_text_data(engine)

    schema = inspect(engine)
    assert "wellbeing_entries" not in schema.get_table_names()
    assert "procedures" not in schema.get_table_names()
    assert "text" not in {column["name"] for column in schema.get_columns("notes")}
    with engine.connect() as connection:
        rows = connection.execute(
            select(notes.c.id, notes.c.factors_json).order_by(notes.c.id)
        ).all()
    assert rows == [
        (1, json.dumps(["tired", "workout"], ensure_ascii=False)),
        (2, "[]"),
    ]
    engine.dispose()


def test_current_schema_does_not_recreate_retired_free_text_storage():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)

    migrate_retired_free_text_data(engine)

    schema = inspect(engine)
    assert "wellbeing_entries" not in schema.get_table_names()
    assert "procedures" not in schema.get_table_names()
    assert "text" not in {column["name"] for column in schema.get_columns("notes")}
    assert "text" not in NoteEntry.__table__.columns
    engine.dispose()
