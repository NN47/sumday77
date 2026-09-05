"""Атомарный суточный бюджет токенов OpenAI поверх существующего usage-журнала."""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone
import logging
from typing import Any, Iterator

from sqlalchemy import case, func, or_, text

from config import (
    AI_GLOBAL_ATTEMPT_LIMIT_PER_DAY,
    OPENAI_DAILY_TOKEN_LIMIT,
    OPENAI_FOOD_PHOTO_TOKEN_RESERVE,
    OPENAI_LABEL_TOKEN_RESERVE,
    OPENAI_VISION_MODEL,
)
from database.models import AIGlobalDailyCounter, AIUsageLog
from database.session import get_db_session


logger = logging.getLogger(__name__)

_ACTIVE_RESERVATION_ID: ContextVar[int | None] = ContextVar(
    "openai_token_reservation_id",
    default=None,
)
_POSTGRES_ADVISORY_LOCK_KEY = 7_754_477


class OpenAIDailyTokenLimitExceeded(RuntimeError):
    """В текущих UTC-сутках недостаточно свободного бюджета для запроса."""


@dataclass(frozen=True)
class OpenAITokenReservation:
    log_id: int
    feature: str
    model: str
    reserved_tokens: int
    used_before: int


def get_active_openai_reservation_id() -> int | None:
    """ID резерва текущего вызова; ContextVar корректно переносится в asyncio.to_thread."""
    return _ACTIVE_RESERVATION_ID.get()


class OpenAITokenBudgetService:
    """Резервирует бюджет транзакционно и заменяет резерв фактическим usage."""

    @staticmethod
    def reserve_tokens_for_feature(feature: str) -> int:
        normalized = (feature or "").lower()
        if "label" in normalized:
            return OPENAI_LABEL_TOKEN_RESERVE
        if "food_photo" in normalized or "meal_photo" in normalized:
            return OPENAI_FOOD_PHOTO_TOKEN_RESERVE
        raise ValueError(f"Unsupported OpenAI budget feature: {feature}")

    @staticmethod
    def _utc_day_bounds(now: datetime | None = None) -> tuple[datetime, datetime]:
        moment = now or datetime.now(timezone.utc)
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=timezone.utc)
        else:
            moment = moment.astimezone(timezone.utc)
        start = datetime.combine(moment.date(), time.min, tzinfo=timezone.utc)
        return start, start + timedelta(days=1)

    @staticmethod
    def _lock_daily_budget(session) -> None:
        """Сериализует read+reserve между процессами на PostgreSQL и в SQLite-тестах."""
        dialect = session.get_bind().dialect.name
        if dialect == "postgresql":
            session.execute(
                text("SELECT pg_advisory_xact_lock(:lock_key)"),
                {"lock_key": _POSTGRES_ADVISORY_LOCK_KEY},
            )
            return
        if dialect == "sqlite":
            session.execute(text("BEGIN IMMEDIATE"))
            return

        # На других SQL-БД используем уже существующую строку глобального
        # суточного счётчика как lock anchor. Основные окружения проекта —
        # PostgreSQL и SQLite, для которых ветки выше не имеют gap-race.
        today = datetime.now(timezone.utc).date()
        counter = (
            session.query(AIGlobalDailyCounter)
            .filter(AIGlobalDailyCounter.period_key == today)
            .with_for_update()
            .one_or_none()
        )
        if counter is None:
            session.add(
                AIGlobalDailyCounter(
                    period_key=today,
                    attempt_limit=AI_GLOBAL_ATTEMPT_LIMIT_PER_DAY,
                )
            )
            session.flush()

    @staticmethod
    def _token_sum_query(session, *, start: datetime, end: datetime, model: str):
        actual_tokens = case(
            (
                or_(
                    AIUsageLog.input_tokens.isnot(None),
                    AIUsageLog.output_tokens.isnot(None),
                ),
                func.coalesce(AIUsageLog.input_tokens, 0)
                + func.coalesce(AIUsageLog.output_tokens, 0),
            ),
            else_=func.coalesce(AIUsageLog.total_tokens, 0),
        )
        accounted_tokens = case(
            (
                AIUsageLog.status == "reserved",
                func.coalesce(AIUsageLog.total_tokens, 0),
            ),
            (AIUsageLog.status == "released", 0),
            else_=actual_tokens,
        )
        return session.query(func.coalesce(func.sum(accounted_tokens), 0)).filter(
            AIUsageLog.provider == "openai",
            AIUsageLog.model == model,
            AIUsageLog.created_at >= start,
            AIUsageLog.created_at < end,
        )

    def used_today(
        self,
        *,
        now: datetime | None = None,
        model: str = OPENAI_VISION_MODEL,
    ) -> int:
        """Фактические токены плюс активные резервы текущих UTC-суток."""
        start, end = self._utc_day_bounds(now)
        with get_db_session() as session:
            value = self._token_sum_query(
                session,
                start=start,
                end=end,
                model=model,
            ).scalar()
        return int(value or 0)

    def reserve(
        self,
        *,
        user_id: str | int | None,
        feature: str,
        reserve_tokens: int | None = None,
        now: datetime | None = None,
        model: str = OPENAI_VISION_MODEL,
    ) -> OpenAITokenReservation:
        amount = int(
            reserve_tokens
            if reserve_tokens is not None
            else self.reserve_tokens_for_feature(feature)
        )
        if amount <= 0:
            raise ValueError("OpenAI token reserve must be positive")

        start, end = self._utc_day_bounds(now)
        created_at = now or datetime.now(timezone.utc)
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=timezone.utc)
        else:
            created_at = created_at.astimezone(timezone.utc)

        with get_db_session() as session:
            self._lock_daily_budget(session)
            used = int(
                self._token_sum_query(
                    session,
                    start=start,
                    end=end,
                    model=model,
                ).scalar()
                or 0
            )
            if used + amount > OPENAI_DAILY_TOKEN_LIMIT:
                raise OpenAIDailyTokenLimitExceeded(
                    f"OpenAI daily token budget exhausted: used={used}, reserve={amount}"
                )

            row = AIUsageLog(
                created_at=created_at,
                user_id=str(user_id) if user_id is not None else None,
                provider="openai",
                feature=feature,
                model=model,
                status="reserved",
                total_tokens=amount,
                raw_metadata={"budget_reservation": True, "reserved_tokens": amount},
            )
            session.add(row)
            session.flush()
            reservation = OpenAITokenReservation(
                log_id=row.id,
                feature=feature,
                model=model,
                reserved_tokens=amount,
                used_before=used,
            )

        logger.info(
            "OpenAI token budget reserved feature=%s used_before=%s reserve=%s limit=%s",
            feature,
            used,
            amount,
            OPENAI_DAILY_TOKEN_LIMIT,
        )
        return reservation

    def release(self, reservation_id: int) -> bool:
        """Удаляет ещё не заменённый usage резерв (ошибка до получения usage)."""
        with get_db_session() as session:
            deleted = (
                session.query(AIUsageLog)
                .filter(
                    AIUsageLog.id == reservation_id,
                    AIUsageLog.status == "reserved",
                )
                .delete(synchronize_session=False)
            )
        return bool(deleted)

    def finalize_usage(self, reservation_id: int, **values: Any) -> bool:
        """Атомарно заменяет активный резерв фактической записью usage."""
        with get_db_session() as session:
            row = (
                session.query(AIUsageLog)
                .filter(
                    AIUsageLog.id == reservation_id,
                    AIUsageLog.provider == "openai",
                    AIUsageLog.model == OPENAI_VISION_MODEL,
                    AIUsageLog.status == "reserved",
                )
                .one_or_none()
            )
            if row is None:
                return False
            for field, value in values.items():
                setattr(row, field, value)
            return True

    @contextmanager
    def reservation(
        self,
        *,
        user_id: str | int | None,
        feature: str,
        reserve_tokens: int | None = None,
        now: datetime | None = None,
    ) -> Iterator[OpenAITokenReservation]:
        reservation = self.reserve(
            user_id=user_id,
            feature=feature,
            reserve_tokens=reserve_tokens,
            now=now,
        )
        token = _ACTIVE_RESERVATION_ID.set(reservation.log_id)
        try:
            yield reservation
        finally:
            _ACTIVE_RESERVATION_ID.reset(token)
            self.release(reservation.log_id)


openai_token_budget_service = OpenAITokenBudgetService()


def finalize_active_openai_usage(**values: Any) -> bool:
    reservation_id = get_active_openai_reservation_id()
    if reservation_id is None:
        return False
    return openai_token_budget_service.finalize_usage(reservation_id, **values)
