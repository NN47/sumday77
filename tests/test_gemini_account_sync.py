from contextlib import contextmanager
from datetime import datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database.models import Base, GeminiAccount
from database.repositories import gemini_repository as repository_module
from database.repositories.gemini_repository import GeminiRepository


def _session_provider(session_factory):
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

    return provider


def _database(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr(repository_module, "get_db_session", _session_provider(session_factory))
    return engine, session_factory


def test_sync_retries_configured_key_after_persisted_auth_failure(monkeypatch) -> None:
    engine, session_factory = _database(monkeypatch)
    api_key = "configured-key"
    with session_factory() as session:
        session.add(
            GeminiAccount(
                account_name="GEMINI_API_KEY",
                api_key_masked=GeminiRepository.mask_api_key(api_key),
                priority_order=1,
                is_active=False,
                status=GeminiRepository.STATUS_AUTH_FAILED,
                disabled_reason="authentication_error",
            )
        )
        session.commit()

    GeminiRepository.sync_accounts(
        [{"account_name": "GEMINI_API_KEY", "api_key": api_key, "priority_order": 1}]
    )

    with session_factory() as session:
        account = session.query(GeminiAccount).one()
        assert account.status == GeminiRepository.STATUS_ACTIVE
        assert account.is_active is True
        assert account.disabled_reason is None
    engine.dispose()


def test_sync_disables_removed_key_and_restores_changed_key(monkeypatch) -> None:
    engine, session_factory = _database(monkeypatch)
    with session_factory() as session:
        session.add_all(
            [
                GeminiAccount(
                    account_name="GEMINI_API_KEY",
                    api_key_masked=GeminiRepository.mask_api_key("old-key"),
                    priority_order=1,
                    is_active=False,
                    status=GeminiRepository.STATUS_RATE_LIMITED,
                    rate_limited_until=datetime.utcnow() + timedelta(hours=1),
                ),
                GeminiAccount(
                    account_name="GEMINI_API_KEY2",
                    api_key_masked=GeminiRepository.mask_api_key("removed-key"),
                    priority_order=2,
                    is_active=True,
                    status=GeminiRepository.STATUS_ACTIVE,
                ),
            ]
        )
        session.commit()

    GeminiRepository.sync_accounts(
        [{"account_name": "GEMINI_API_KEY", "api_key": "new-key", "priority_order": 1}]
    )

    with session_factory() as session:
        primary = session.query(GeminiAccount).filter_by(account_name="GEMINI_API_KEY").one()
        removed = session.query(GeminiAccount).filter_by(account_name="GEMINI_API_KEY2").one()
        assert primary.status == GeminiRepository.STATUS_ACTIVE
        assert primary.is_active is True
        assert primary.rate_limited_until is None
        assert removed.status == GeminiRepository.STATUS_DISABLED
        assert removed.is_active is False
        assert removed.disabled_reason == GeminiRepository.CONFIGURATION_MISSING_REASON
    engine.dispose()
