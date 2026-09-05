from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
import threading

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from config import (
    OPENAI_DAILY_TOKEN_LIMIT,
    OPENAI_FOOD_PHOTO_TOKEN_RESERVE,
    OPENAI_LABEL_TOKEN_RESERVE,
    OPENAI_VISION_MODEL,
)
from database.models import AIUsageLog, Base
from services.ai_usage_logger import log_ai_usage
from services.openai_token_budget_service import (
    OpenAIDailyTokenLimitExceeded,
    OpenAITokenBudgetService,
)
import services.openai_token_budget_service as budget_module


@pytest.fixture
def budget_store(monkeypatch):
    with TemporaryDirectory() as temp_dir:
        engine = create_engine(
            f"sqlite:///{Path(temp_dir) / 'openai-budget.db'}",
            connect_args={"check_same_thread": False, "timeout": 15},
        )
        Base.metadata.create_all(engine)
        session_factory = sessionmaker(bind=engine, expire_on_commit=False)

        @contextmanager
        def session_provider():
            session = session_factory()
            try:
                yield session
                session.commit()
            except Exception:
                session.rollback()
                raise
            finally:
                session.close()

        monkeypatch.setattr(budget_module, "get_db_session", session_provider)
        yield OpenAITokenBudgetService(), session_factory
        engine.dispose()


def _add_usage(session_factory, *, at, model=OPENAI_VISION_MODEL, tokens=1):
    with session_factory() as session:
        session.add(
            AIUsageLog(
                created_at=at,
                user_id="seed",
                provider="openai",
                feature="food_photo_analysis",
                model=model,
                status="success",
                input_tokens=tokens - 1,
                output_tokens=1,
                total_tokens=tokens,
            )
        )
        session.commit()


def test_feature_reserves_are_centralized():
    service = OpenAITokenBudgetService()

    assert service.reserve_tokens_for_feature("label_analysis") == OPENAI_LABEL_TOKEN_RESERVE
    assert service.reserve_tokens_for_feature("food_photo_analysis") == OPENAI_FOOD_PHOTO_TOKEN_RESERVE


def test_only_current_model_and_current_utc_day_reduce_budget(budget_store):
    service, Session = budget_store
    now = datetime(2026, 9, 5, 12, tzinfo=timezone.utc)
    _add_usage(Session, at=now, tokens=1_000)
    _add_usage(Session, at=now, model="gpt-4o-mini", tokens=90_000)
    _add_usage(Session, at=now - timedelta(days=1), tokens=80_000)

    assert service.used_today(now=now) == 1_000


def test_reservation_at_limit_is_allowed_and_next_one_is_rejected(budget_store):
    service, Session = budget_store
    now = datetime(2026, 9, 5, 23, 59, tzinfo=timezone.utc)
    _add_usage(
        Session,
        at=now,
        tokens=OPENAI_DAILY_TOKEN_LIMIT - OPENAI_LABEL_TOKEN_RESERVE,
    )

    reservation = service.reserve(
        user_id="42",
        feature="label_analysis",
        now=now,
    )
    assert service.used_today(now=now) == OPENAI_DAILY_TOKEN_LIMIT

    with pytest.raises(OpenAIDailyTokenLimitExceeded):
        service.reserve(user_id="43", feature="label_analysis", now=now)

    service.release(reservation.log_id)


def test_new_utc_day_makes_openai_available_again(budget_store):
    service, Session = budget_store
    before_midnight = datetime(2026, 9, 5, 23, 59, 59, tzinfo=timezone.utc)
    after_midnight = datetime(2026, 9, 6, 0, 0, 0, tzinfo=timezone.utc)
    _add_usage(Session, at=before_midnight, tokens=OPENAI_DAILY_TOKEN_LIMIT)

    with pytest.raises(OpenAIDailyTokenLimitExceeded):
        service.reserve(user_id="42", feature="label_analysis", now=before_midnight)

    reservation = service.reserve(
        user_id="42",
        feature="label_analysis",
        now=after_midnight,
    )
    assert reservation.used_before == 0


def test_usage_log_replaces_reservation_with_actual_tokens(budget_store):
    service, Session = budget_store
    now = datetime.now(timezone.utc)

    with service.reservation(user_id="42", feature="food_photo_analysis", now=now):
        log_ai_usage(
            provider="openai",
            feature="food_photo_analysis",
            model=OPENAI_VISION_MODEL,
            status="success",
            user_id="42",
            input_tokens=1_100,
            output_tokens=234,
            total_tokens=1_334,
        )

    assert service.used_today(now=now) == 1_334
    with Session() as session:
        rows = session.query(AIUsageLog).all()
        assert len(rows) == 1
        assert rows[0].status == "success"
        assert rows[0].total_tokens == 1_334


def test_openai_error_without_usage_releases_reservation(budget_store):
    service, Session = budget_store
    now = datetime.now(timezone.utc)

    with pytest.raises(RuntimeError):
        with service.reservation(user_id="42", feature="label_analysis", now=now):
            raise RuntimeError("provider failed before returning usage")

    assert service.used_today(now=now) == 0
    with Session() as session:
        assert session.query(AIUsageLog).count() == 0


def test_parallel_reservations_near_limit_are_serialized(budget_store):
    service, Session = budget_store
    now = datetime(2026, 9, 5, 12, tzinfo=timezone.utc)
    _add_usage(
        Session,
        at=now,
        tokens=OPENAI_DAILY_TOKEN_LIMIT - OPENAI_FOOD_PHOTO_TOKEN_RESERVE,
    )
    barrier = threading.Barrier(2)

    def reserve(index):
        barrier.wait()
        try:
            return service.reserve(
                user_id=str(index),
                feature="food_photo_analysis",
                now=now,
            )
        except Exception as error:
            return error

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(reserve, range(2)))

    assert sum(not isinstance(result, Exception) for result in results) == 1
    assert sum(isinstance(result, OpenAIDailyTokenLimitExceeded) for result in results) == 1
    assert service.used_today(now=now) == OPENAI_DAILY_TOKEN_LIMIT
