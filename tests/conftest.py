"""Общая изоляция нового постоянного квотного журнала в unit-тестах."""
from __future__ import annotations

from contextlib import contextmanager
import os

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

os.environ.setdefault("API_TOKEN", "test-token")
os.environ.setdefault("GEMINI_API_KEY", "test-gemini-key")

from database.models import Base
import services.ai_quota_service as quota_module


@pytest.fixture(autouse=True)
def isolated_default_ai_quota_store(monkeypatch):
    """Старые handler-тесты не должны делить дневные лимиты через рабочий sqlite-файл."""
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)

    @contextmanager
    def get_test_session():
        session = session_factory()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    monkeypatch.setattr(quota_module, "get_db_session", get_test_session)
    yield
    engine.dispose()
