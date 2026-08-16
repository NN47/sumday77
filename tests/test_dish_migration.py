import os

from sqlalchemy import create_engine, inspect, text

os.environ.setdefault("API_TOKEN", "test-token")
os.environ.setdefault("GEMINI_API_KEY", "test-gemini-key")

import database.session as session_module


def test_init_db_adds_dish_schema_without_rewriting_legacy_photo_meals(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'legacy.db'}")
    with engine.begin() as connection:
        connection.execute(
            text(
                "CREATE TABLE meals ("
                "id INTEGER PRIMARY KEY, user_id VARCHAR NOT NULL, description VARCHAR, "
                "raw_query VARCHAR, products_json TEXT, api_details TEXT, calories FLOAT, "
                "protein FLOAT, fat FLOAT, carbs FLOAT, date DATE)"
            )
        )
        connection.execute(
            text(
                "INSERT INTO meals "
                "(id, user_id, description, products_json, calories, protein, fat, carbs, date) "
                "VALUES (1, '42', 'legacy photo', '[{\"source\":\"photo_analysis\"}]', "
                "100, 5, 4, 10, '2026-08-15')"
            )
        )

    monkeypatch.setattr(session_module, "engine", engine)
    session_module.init_db()

    schema = inspect(engine)
    meal_columns = {column["name"] for column in schema.get_columns("meals")}
    assert {"entry_kind", "dish_id", "dish_name_snapshot", "entry_source"} <= meal_columns
    assert {"dishes", "dish_ingredients"} <= set(schema.get_table_names())
    with engine.connect() as connection:
        legacy = connection.execute(
            text("SELECT entry_kind, dish_id, products_json FROM meals WHERE id = 1")
        ).one()
    assert legacy.entry_kind == "products"
    assert legacy.dish_id is None
    assert "photo_analysis" in legacy.products_json
    engine.dispose()
