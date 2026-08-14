from __future__ import annotations

import asyncio
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import os
from types import SimpleNamespace
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

os.environ.setdefault("API_TOKEN", "test-token")

from database.models import (
    AIUsageLog,
    Base,
    ErrorLog,
    GeminiAccount,
    GeminiRequestLog,
    Meal,
    User,
    UserEvent,
)
from database.technical_log_retention import (
    AI_USAGE_LOG_RETENTION_DAYS,
    ERROR_LOG_RETENTION_DAYS,
    GEMINI_REQUEST_LOG_RETENTION_DAYS,
    TECHNICAL_LOG_CLEANUP_INTERVAL_SECONDS,
    USER_EVENT_RETENTION_DAYS,
    cleanup_expired_technical_logs,
)
from services.notification_scheduler import NotificationScheduler


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


def _technical_row(model, created_at, *, account_id=None):
    if model is UserEvent:
        return UserEvent(user_id="1001", event_name="test_event", created_at=created_at)
    if model is ErrorLog:
        return ErrorLog(error_type="TestError", created_at=created_at)
    if model is AIUsageLog:
        return AIUsageLog(
            provider="openai",
            feature="test",
            model="test-model",
            status="success",
            created_at=created_at,
        )
    if model is GeminiRequestLog:
        return GeminiRequestLog(
            account_id=account_id,
            status="request_success",
            created_at=created_at,
        )
    raise AssertionError(f"Unsupported model: {model}")


def test_ttl_cleanup_deletes_only_expired_technical_rows() -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    fixed_now = datetime(2026, 8, 14, 12, 0, tzinfo=timezone.utc)
    specifications = (
        (UserEvent, USER_EVENT_RETENTION_DAYS, False),
        (ErrorLog, ERROR_LOG_RETENTION_DAYS, False),
        (AIUsageLog, AI_USAGE_LOG_RETENTION_DAYS, True),
        (GeminiRequestLog, GEMINI_REQUEST_LOG_RETENTION_DAYS, False),
    )
    row_ids: dict[type, dict[str, int]] = {}

    with Session() as session:
        user = User(user_id="1001")
        business_meal = Meal(user_id="1001", description="Business data", meal_type="snack")
        gemini_account = GeminiAccount(
            account_name="primary",
            api_key_masked="sha256:test",
            priority_order=1,
        )
        session.add_all([user, business_meal, gemini_account])
        session.flush()

        for model, retention_days, uses_timezone in specifications:
            cutoff_aware = fixed_now - timedelta(days=retention_days)
            cutoff = cutoff_aware if uses_timezone else cutoff_aware.replace(tzinfo=None)
            old = _technical_row(
                model,
                cutoff - timedelta(seconds=1),
                account_id=gemini_account.id,
            )
            boundary = _technical_row(model, cutoff, account_id=gemini_account.id)
            fresh_time = fixed_now - timedelta(days=1)
            if not uses_timezone:
                fresh_time = fresh_time.replace(tzinfo=None)
            fresh = _technical_row(model, fresh_time, account_id=gemini_account.id)
            session.add_all([old, boundary, fresh])
            session.flush()
            row_ids[model] = {
                "old": old.id,
                "boundary": boundary.id,
                "fresh": fresh.id,
            }
        session.commit()

    results = cleanup_expired_technical_logs(
        now=fixed_now,
        session_provider=_session_provider(Session),
    )

    assert results == {
        "user_events": 1,
        "error_logs": 1,
        "ai_usage_logs": 1,
        "gemini_request_logs": 1,
    }
    with Session() as session:
        for model, _days, _uses_timezone in specifications:
            remaining_ids = {row.id for row in session.query(model).all()}
            assert row_ids[model]["old"] not in remaining_ids
            assert row_ids[model]["boundary"] in remaining_ids
            assert row_ids[model]["fresh"] in remaining_ids

        assert session.query(User).filter_by(user_id="1001").count() == 1
        assert session.query(Meal).filter_by(user_id="1001").count() == 1
        assert session.query(GeminiAccount).filter_by(account_name="primary").count() == 1

    engine.dispose()


def test_one_table_cleanup_failure_does_not_stop_other_tables() -> None:
    calls = []

    def fake_delete(model, cutoff, *, session_provider):
        calls.append(model)
        if model is ErrorLog:
            raise RuntimeError("database unavailable")
        return 2

    with patch(
        "database.technical_log_retention._delete_expired_records",
        side_effect=fake_delete,
    ):
        results = cleanup_expired_technical_logs(
            now=datetime(2026, 8, 14, tzinfo=timezone.utc),
        )

    assert calls == [UserEvent, ErrorLog, AIUsageLog, GeminiRequestLog]
    assert results == {
        "user_events": 2,
        "error_logs": None,
        "ai_usage_logs": 2,
        "gemini_request_logs": 2,
    }


def test_scheduler_contains_unexpected_cleanup_error() -> None:
    scheduler = NotificationScheduler(SimpleNamespace())
    with patch(
        "services.notification_scheduler.cleanup_expired_technical_logs",
        side_effect=RuntimeError("unexpected cleanup failure"),
    ):
        result = asyncio.run(scheduler.run_technical_log_cleanup())

    assert result is False
    assert TECHNICAL_LOG_CLEANUP_INTERVAL_SECONDS == 24 * 60 * 60
