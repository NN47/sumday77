"""Add recipe metadata without rewriting existing dishes or diary snapshots."""
from sqlalchemy import inspect, text


def migrate_recipe_metadata(engine) -> None:
    columns = {column["name"] for column in inspect(engine).get_columns("dishes")}
    with engine.begin() as connection:
        for name, sql_type in (
            ("cooking_method", "VARCHAR(24)"),
            ("cooked_weight_g", "FLOAT"),
            ("preparation", "TEXT"),
        ):
            if name not in columns:
                connection.execute(text(f"ALTER TABLE dishes ADD COLUMN {name} {sql_type}"))
