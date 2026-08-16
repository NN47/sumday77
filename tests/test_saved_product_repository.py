from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import sessionmaker
from sqlalchemy.schema import CreateTable

from database.models import Base, SavedProduct
from database.repositories.saved_product_repository import SavedProductRepository
import database.repositories.saved_product_repository as repository_module


def _build_session_provider():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)

    @contextmanager
    def provider():
        session = session_factory()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    return engine, provider


def test_last_weight_update_preserves_unit_and_package_metadata(monkeypatch) -> None:
    engine, provider = _build_session_provider()
    monkeypatch.setattr(repository_module, "get_db_session", provider)
    try:
        SavedProductRepository.upsert(
            user_id="42",
            name="Хлебцы",
            last_weight_g=20,
            unit_weight_g=10,
            unit_name="хлебец",
            package_weight_g=100,
            package_units=10,
        )

        SavedProductRepository.upsert(
            user_id="42",
            name="  ХЛЕБЦЫ  ",
            last_weight_g=30,
        )
        saved = SavedProductRepository.get_by_name("42", "хлебцы")

        assert saved is not None
        assert saved.last_weight_g == 30
        assert saved.unit_weight_g == 10
        assert saved.unit_name == "хлебец"
        assert saved.package_weight_g == 100
        assert saved.package_units == 10
    finally:
        engine.dispose()


def test_old_product_can_exist_with_all_reference_fields_null(monkeypatch) -> None:
    engine, provider = _build_session_provider()
    monkeypatch.setattr(repository_module, "get_db_session", provider)
    try:
        saved = SavedProductRepository.upsert(
            user_id="42",
            name="Старый продукт",
            last_weight_g=75,
        )

        assert saved is not None
        assert saved.last_weight_g == 75
        assert saved.unit_weight_g is None
        assert saved.unit_name is None
        assert saved.package_weight_g is None
        assert saved.package_units is None
    finally:
        engine.dispose()


def test_saved_products_ddl_is_postgresql_compatible_and_reference_fields_nullable() -> None:
    ddl = str(CreateTable(SavedProduct.__table__).compile(dialect=postgresql.dialect()))

    assert "CREATE TABLE saved_products" in ddl
    for column_name in (
        "last_weight_g",
        "unit_weight_g",
        "unit_name",
        "package_weight_g",
        "package_units",
    ):
        assert SavedProduct.__table__.c[column_name].nullable is True
