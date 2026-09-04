"""Единые тарифы, дневные AI-квоты и технические предохранители."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from enum import StrEnum
import hashlib
from typing import Iterable
from zoneinfo import ZoneInfo

from sqlalchemy import and_, func
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from config import (
    AI_DAILY_ANALYSIS_ATTEMPT_LIMIT_PER_DAY,
    AI_GLOBAL_ATTEMPT_LIMIT_PER_DAY,
    AI_IMAGE_ATTEMPT_LIMIT_PER_DAY,
    AI_MAX_IMAGE_BYTES,
    AI_MEAL_COMMENT_ATTEMPT_LIMIT_PER_DAY,
    AI_QUOTA_COOLDOWN_SECONDS,
    AI_QUOTA_RESERVATION_TTL_SECONDS,
    AI_TEXT_ATTEMPT_LIMIT_PER_DAY,
)
from database.models import (
    AIAttemptCounter,
    AIGlobalDailyCounter,
    AIQuotaActiveLock,
    AIQuotaCounter,
    AIQuotaOperation,
    UserPlanAssignment,
)
from database.session import get_db_session


MSK_TZ = ZoneInfo("Europe/Moscow")
FREE_PLAN_KEY = "free"


class AIFeature(StrEnum):
    MEAL_TEXT = "meal_text_ai"
    MEAL_PHOTO = "meal_photo_ai"
    NUTRITION_LABEL = "nutrition_label_ai"
    DAILY_ANALYSIS = "daily_analysis"
    MEAL_COMPLETION_COMMENT = "meal_completion_comment"


@dataclass(frozen=True)
class FeatureEntitlement:
    limit: int
    attempt_group: str
    attempt_limit: int
    period_kind: str = "quota_day"


@dataclass(frozen=True)
class PlanDefinition:
    key: str
    features: dict[AIFeature, FeatureEntitlement]


FREE_PLAN = PlanDefinition(
    key=FREE_PLAN_KEY,
    features={
        AIFeature.MEAL_TEXT: FeatureEntitlement(15, "meal_text", AI_TEXT_ATTEMPT_LIMIT_PER_DAY),
        AIFeature.MEAL_PHOTO: FeatureEntitlement(5, "meal_image", AI_IMAGE_ATTEMPT_LIMIT_PER_DAY),
        AIFeature.NUTRITION_LABEL: FeatureEntitlement(10, "meal_image", AI_IMAGE_ATTEMPT_LIMIT_PER_DAY),
        AIFeature.DAILY_ANALYSIS: FeatureEntitlement(
            1,
            "daily_analysis",
            AI_DAILY_ANALYSIS_ATTEMPT_LIMIT_PER_DAY,
        ),
        AIFeature.MEAL_COMPLETION_COMMENT: FeatureEntitlement(
            17,
            "meal_completion_comment",
            AI_MEAL_COMMENT_ATTEMPT_LIMIT_PER_DAY,
        ),
    },
)

# Новые тарифы добавляются сюда без изменения обработчиков.
PLAN_CATALOG: dict[str, PlanDefinition] = {FREE_PLAN.key: FREE_PLAN}


@dataclass(frozen=True)
class QuotaStatus:
    feature: AIFeature
    plan_key: str
    period_key: date
    limit: int
    used: int
    reserved: int

    @property
    def remaining(self) -> int:
        return max(0, self.limit - self.used - self.reserved)


@dataclass(frozen=True)
class AttemptStatus:
    group_key: str
    period_key: date
    limit: int
    used: int

    @property
    def remaining(self) -> int:
        return max(0, self.limit - self.used)


@dataclass(frozen=True)
class QuotaReservation:
    request_id: str
    user_id: str
    feature: AIFeature
    plan_key: str
    period_key: date
    limit: int
    used_before: int

    @property
    def remaining_after_success(self) -> int:
        return max(0, self.limit - self.used_before - 1)


class AIQuotaError(RuntimeError):
    """Базовая контролируемая ошибка квоты."""


class AIQuotaExceeded(AIQuotaError):
    def __init__(self, status: QuotaStatus):
        super().__init__("feature quota exhausted")
        self.status = status


class AIAttemptLimitExceeded(AIQuotaError):
    pass


class AIGlobalLimitExceeded(AIQuotaError):
    pass


class AIOperationInProgress(AIQuotaError):
    pass


class AIOperationCooldown(AIQuotaError):
    pass


class AIQuotaAlreadyConsumed(AIQuotaError):
    def __init__(self, request_id: str, result_ref: str | None = None):
        super().__init__("quota operation already consumed")
        self.request_id = request_id
        self.result_ref = result_ref


def quota_period_key(now: datetime | None = None) -> date:
    """Дата лимитного дня 02:00–01:59 МСК."""
    moment = now or datetime.now(MSK_TZ)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=MSK_TZ)
    else:
        moment = moment.astimezone(MSK_TZ)
    return (moment - timedelta(hours=2)).date()


def next_quota_reset(now: datetime | None = None) -> datetime:
    """Следующие 02:00 МСК."""
    moment = now or datetime.now(MSK_TZ)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=MSK_TZ)
    else:
        moment = moment.astimezone(MSK_TZ)
    reset = moment.replace(hour=2, minute=0, second=0, microsecond=0)
    if moment >= reset:
        reset += timedelta(days=1)
    return reset


def feature_period_key(entitlement: FeatureEntitlement, now: datetime | None = None) -> date:
    """Ключ периода возможности; новые тарифы могут выбирать другой период."""
    moment = now or datetime.now(MSK_TZ)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=MSK_TZ)
    else:
        moment = moment.astimezone(MSK_TZ)
    if entitlement.period_kind == "quota_day":
        return quota_period_key(moment)
    if entitlement.period_kind == "calendar_day":
        return moment.date()
    if entitlement.period_kind == "calendar_month":
        return moment.date().replace(day=1)
    if entitlement.period_kind == "lifetime":
        return date(1970, 1, 1)
    raise ValueError(f"Unsupported quota period: {entitlement.period_kind}")


def build_quota_request_id(namespace: str, *parts: object) -> str:
    """Компактный стабильный request_id без пользовательского содержимого."""
    normalized_namespace = "".join(ch for ch in str(namespace) if ch.isalnum() or ch in "_-")[:40]
    payload = "\x1f".join(str(part) for part in parts)
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    return f"{normalized_namespace or 'ai'}:{digest}"


def validate_ai_image(image_data: bytes) -> None:
    """Проверяет размер и сигнатуру JPEG/PNG/WebP до отправки провайдеру."""
    if not image_data:
        raise ValueError("empty_image")
    if len(image_data) > AI_MAX_IMAGE_BYTES:
        raise ValueError("image_too_large")
    is_jpeg = image_data.startswith(b"\xff\xd8\xff")
    is_png = image_data.startswith(b"\x89PNG\r\n\x1a\n")
    is_webp = len(image_data) >= 12 and image_data[:4] == b"RIFF" and image_data[8:12] == b"WEBP"
    if not (is_jpeg or is_png or is_webp):
        raise ValueError("unsupported_image_format")


class AIQuotaService:
    """Единая точка резервирования, списания и чтения AI-квот."""

    @staticmethod
    def _utcnow_naive() -> datetime:
        return datetime.now(timezone.utc).replace(tzinfo=None)

    @staticmethod
    def _get_or_create(session, model, lookup: dict, defaults: dict):
        row = session.query(model).filter_by(**lookup).first()
        if row is not None:
            return row
        try:
            with session.begin_nested():
                row = model(**lookup, **defaults)
                session.add(row)
                session.flush()
            return row
        except IntegrityError:
            return session.query(model).filter_by(**lookup).one()

    def get_plan_key(self, user_id: str | int, *, now: datetime | None = None) -> str:
        user_id = str(user_id)
        current = now or self._utcnow_naive()
        if current.tzinfo is not None:
            current = current.astimezone(timezone.utc).replace(tzinfo=None)
        with get_db_session() as session:
            assignment = (
                session.query(UserPlanAssignment)
                .filter(UserPlanAssignment.user_id == user_id)
                .filter(UserPlanAssignment.status == "active")
                .filter(
                    and_(
                        (UserPlanAssignment.starts_at.is_(None) | (UserPlanAssignment.starts_at <= current)),
                        (UserPlanAssignment.ends_at.is_(None) | (UserPlanAssignment.ends_at > current)),
                    )
                )
                .order_by(UserPlanAssignment.starts_at.desc(), UserPlanAssignment.created_at.desc())
                .first()
            )
            if assignment and assignment.plan_key in PLAN_CATALOG:
                return assignment.plan_key
        return FREE_PLAN_KEY

    def entitlement(self, plan_key: str, feature: AIFeature | str) -> FeatureEntitlement:
        normalized = AIFeature(feature)
        plan = PLAN_CATALOG.get(plan_key, FREE_PLAN)
        return plan.features[normalized]

    def _expire_stale(self, session, user_id: str, now: datetime) -> None:
        stale = (
            session.query(AIQuotaOperation)
            .filter(AIQuotaOperation.user_id == user_id)
            .filter(AIQuotaOperation.status == "reserved")
            .filter(AIQuotaOperation.expires_at <= now)
            .all()
        )
        for operation in stale:
            updated = (
                session.query(AIQuotaOperation)
                .filter(AIQuotaOperation.id == operation.id)
                .filter(AIQuotaOperation.status == "reserved")
                .update(
                    {
                        AIQuotaOperation.status: "expired",
                        AIQuotaOperation.outcome: "reservation_timeout",
                        AIQuotaOperation.completed_at: now,
                        AIQuotaOperation.updated_at: now,
                    },
                    synchronize_session=False,
                )
            )
            if updated:
                session.query(AIQuotaCounter).filter(
                    AIQuotaCounter.user_id == operation.user_id,
                    AIQuotaCounter.feature_key == operation.feature_key,
                    AIQuotaCounter.period_key == operation.period_key,
                    AIQuotaCounter.reserved_count > 0,
                ).update(
                    {
                        AIQuotaCounter.reserved_count: AIQuotaCounter.reserved_count - 1,
                        AIQuotaCounter.updated_at: now,
                    },
                    synchronize_session=False,
                )
                session.query(AIQuotaActiveLock).filter(
                    AIQuotaActiveLock.user_id == operation.user_id,
                    AIQuotaActiveLock.request_id == operation.request_id,
                ).delete(synchronize_session=False)
        session.query(AIQuotaActiveLock).filter(
            AIQuotaActiveLock.user_id == user_id,
            AIQuotaActiveLock.expires_at <= now,
        ).delete(synchronize_session=False)

    def _acquire_active_lock(self, session, user_id: str, request_id: str, expires_at: datetime) -> None:
        existing = (
            session.query(AIQuotaActiveLock)
            .filter(AIQuotaActiveLock.user_id == user_id)
            .first()
        )
        if existing is not None:
            if existing.request_id == request_id:
                raise AIOperationInProgress("operation is already running")
            raise AIOperationInProgress("another AI operation is running")
        try:
            with session.begin_nested():
                session.add(
                    AIQuotaActiveLock(
                        user_id=user_id,
                        request_id=request_id,
                        expires_at=expires_at,
                    )
                )
                session.flush()
        except IntegrityError as exc:
            raise AIOperationInProgress("another AI operation is running") from exc

    def _check_cooldown(self, session, user_id: str, now: datetime) -> None:
        if AI_QUOTA_COOLDOWN_SECONDS <= 0:
            return
        cutoff = now - timedelta(seconds=AI_QUOTA_COOLDOWN_SECONDS)
        recent = (
            session.query(AIQuotaOperation.id)
            .filter(AIQuotaOperation.user_id == user_id)
            .filter(AIQuotaOperation.provider_started.is_(True))
            .filter(AIQuotaOperation.completed_at.is_not(None))
            .filter(AIQuotaOperation.completed_at >= cutoff)
            .first()
        )
        if recent:
            raise AIOperationCooldown("AI requests are too frequent")

    def reserve(
        self,
        user_id: str | int,
        feature: AIFeature | str,
        request_id: str,
        *,
        now: datetime | None = None,
    ) -> QuotaReservation:
        """Атомарно резервирует пользовательскую квоту и одну техническую попытку."""
        user_id = str(user_id)
        feature = AIFeature(feature)
        if not request_id or len(request_id) > 160:
            raise ValueError("request_id must be 1..160 characters")
        current = now or self._utcnow_naive()
        if current.tzinfo is not None:
            current = current.astimezone(timezone.utc).replace(tzinfo=None)
        plan_key = self.get_plan_key(user_id, now=current)
        entitlement = self.entitlement(plan_key, feature)
        period = feature_period_key(entitlement, now or datetime.now(MSK_TZ))
        attempt_period = quota_period_key(now or datetime.now(MSK_TZ))
        expires_at = current + timedelta(seconds=max(30, AI_QUOTA_RESERVATION_TTL_SECONDS))
        denial: AIQuotaError | None = None
        reservation: QuotaReservation | None = None

        with get_db_session() as session:
            self._expire_stale(session, user_id, current)
            existing_operation = (
                session.query(AIQuotaOperation)
                .filter(AIQuotaOperation.request_id == request_id)
                .first()
            )
            if existing_operation and existing_operation.user_id != user_id:
                denial = AIOperationInProgress("request_id belongs to another user")
            elif existing_operation and existing_operation.status == "consumed":
                denial = AIQuotaAlreadyConsumed(request_id, existing_operation.result_ref)
            elif existing_operation and existing_operation.status == "reserved":
                denial = AIOperationInProgress("operation is already running")

            if denial is None:
                self._check_cooldown(session, user_id, current)
                self._acquire_active_lock(session, user_id, request_id, expires_at)

                quota = self._get_or_create(
                    session,
                    AIQuotaCounter,
                    {
                        "user_id": user_id,
                        "feature_key": feature.value,
                        "period_key": period,
                    },
                    {
                        "plan_key": plan_key,
                        "limit_value": entitlement.limit,
                    },
                )
                attempts = self._get_or_create(
                    session,
                    AIAttemptCounter,
                    {
                        "user_id": user_id,
                        "group_key": entitlement.attempt_group,
                        "period_key": attempt_period,
                    },
                    {"attempt_limit": entitlement.attempt_limit},
                )
                global_counter = self._get_or_create(
                    session,
                    AIGlobalDailyCounter,
                    {"period_key": attempt_period},
                    {"attempt_limit": AI_GLOBAL_ATTEMPT_LIMIT_PER_DAY},
                )

                quota.limit_value = entitlement.limit
                quota.plan_key = plan_key
                attempts.attempt_limit = entitlement.attempt_limit
                global_counter.attempt_limit = AI_GLOBAL_ATTEMPT_LIMIT_PER_DAY
                session.flush()

                quota_updated = (
                    session.query(AIQuotaCounter)
                    .filter(AIQuotaCounter.id == quota.id)
                    .filter(AIQuotaCounter.used_count + AIQuotaCounter.reserved_count < entitlement.limit)
                    .update(
                        {
                            AIQuotaCounter.reserved_count: AIQuotaCounter.reserved_count + 1,
                            AIQuotaCounter.updated_at: current,
                        },
                        synchronize_session=False,
                    )
                )
                if not quota_updated:
                    session.query(AIQuotaCounter).filter(AIQuotaCounter.id == quota.id).update(
                        {
                            AIQuotaCounter.blocked_count: AIQuotaCounter.blocked_count + 1,
                            AIQuotaCounter.updated_at: current,
                        },
                        synchronize_session=False,
                    )
                    session.query(AIQuotaActiveLock).filter(
                        AIQuotaActiveLock.user_id == user_id,
                        AIQuotaActiveLock.request_id == request_id,
                    ).delete(synchronize_session=False)
                    denial = AIQuotaExceeded(
                        QuotaStatus(
                            feature=feature,
                            plan_key=plan_key,
                            period_key=period,
                            limit=entitlement.limit,
                            used=int(quota.used_count or 0),
                            reserved=int(quota.reserved_count or 0),
                        )
                    )

                if denial is None:
                    attempt_updated = (
                        session.query(AIAttemptCounter)
                        .filter(AIAttemptCounter.id == attempts.id)
                        .filter(AIAttemptCounter.attempt_count < entitlement.attempt_limit)
                        .update(
                            {
                                AIAttemptCounter.attempt_count: AIAttemptCounter.attempt_count + 1,
                                AIAttemptCounter.updated_at: current,
                            },
                            synchronize_session=False,
                        )
                    )
                    if not attempt_updated:
                        session.query(AIAttemptCounter).filter(AIAttemptCounter.id == attempts.id).update(
                            {
                                AIAttemptCounter.blocked_count: AIAttemptCounter.blocked_count + 1,
                                AIAttemptCounter.updated_at: current,
                            },
                            synchronize_session=False,
                        )
                        session.query(AIQuotaCounter).filter(AIQuotaCounter.id == quota.id).update(
                            {AIQuotaCounter.reserved_count: AIQuotaCounter.reserved_count - 1},
                            synchronize_session=False,
                        )
                        session.query(AIQuotaActiveLock).filter(
                            AIQuotaActiveLock.user_id == user_id,
                            AIQuotaActiveLock.request_id == request_id,
                        ).delete(synchronize_session=False)
                        denial = AIAttemptLimitExceeded("daily attempt limit exhausted")

                if denial is None:
                    global_updated = (
                        session.query(AIGlobalDailyCounter)
                        .filter(AIGlobalDailyCounter.id == global_counter.id)
                        .filter(AIGlobalDailyCounter.attempt_count < AI_GLOBAL_ATTEMPT_LIMIT_PER_DAY)
                        .update(
                            {
                                AIGlobalDailyCounter.attempt_count: AIGlobalDailyCounter.attempt_count + 1,
                                AIGlobalDailyCounter.updated_at: current,
                            },
                            synchronize_session=False,
                        )
                    )
                    if not global_updated:
                        session.query(AIGlobalDailyCounter).filter(
                            AIGlobalDailyCounter.id == global_counter.id
                        ).update(
                            {
                                AIGlobalDailyCounter.blocked_count: AIGlobalDailyCounter.blocked_count + 1,
                                AIGlobalDailyCounter.updated_at: current,
                            },
                            synchronize_session=False,
                        )
                        session.query(AIAttemptCounter).filter(AIAttemptCounter.id == attempts.id).update(
                            {AIAttemptCounter.attempt_count: AIAttemptCounter.attempt_count - 1},
                            synchronize_session=False,
                        )
                        session.query(AIQuotaCounter).filter(AIQuotaCounter.id == quota.id).update(
                            {AIQuotaCounter.reserved_count: AIQuotaCounter.reserved_count - 1},
                            synchronize_session=False,
                        )
                        session.query(AIQuotaActiveLock).filter(
                            AIQuotaActiveLock.user_id == user_id,
                            AIQuotaActiveLock.request_id == request_id,
                        ).delete(synchronize_session=False)
                        denial = AIGlobalLimitExceeded("global AI limit exhausted")

                if denial is None:
                    if existing_operation is None:
                        operation = AIQuotaOperation(
                            request_id=request_id,
                            user_id=user_id,
                            plan_key=plan_key,
                            feature_key=feature.value,
                            period_key=period,
                            status="reserved",
                            provider_started=True,
                            provider_attempt_count=1,
                            expires_at=expires_at,
                        )
                        session.add(operation)
                    else:
                        existing_operation.plan_key = plan_key
                        existing_operation.feature_key = feature.value
                        existing_operation.period_key = period
                        existing_operation.status = "reserved"
                        existing_operation.outcome = None
                        existing_operation.provider_started = True
                        existing_operation.provider_attempt_count = 1
                        existing_operation.result_ref = None
                        existing_operation.completed_at = None
                        existing_operation.updated_at = current
                        existing_operation.expires_at = expires_at
                    session.flush()
                    reservation = QuotaReservation(
                        request_id=request_id,
                        user_id=user_id,
                        feature=feature,
                        plan_key=plan_key,
                        period_key=period,
                        limit=entitlement.limit,
                        used_before=int(quota.used_count or 0),
                    )

        if denial is not None:
            raise denial
        if reservation is None:  # pragma: no cover - защитный инвариант
            raise AIQuotaError("reservation was not created")
        return reservation

    def consume(self, request_id: str, *, outcome: str = "success", result_ref: str | None = None) -> bool:
        """Подтверждает ровно одно списание; повторный вызов идемпотентен."""
        now = self._utcnow_naive()
        with get_db_session() as session:
            operation = (
                session.query(AIQuotaOperation)
                .filter(AIQuotaOperation.request_id == request_id)
                .first()
            )
            if operation is None:
                return False
            if operation.status == "consumed":
                return True
            if operation.status != "reserved":
                return False
            updated = (
                session.query(AIQuotaOperation)
                .filter(AIQuotaOperation.id == operation.id)
                .filter(AIQuotaOperation.status == "reserved")
                .update(
                    {
                        AIQuotaOperation.status: "consumed",
                        AIQuotaOperation.outcome: outcome[:64],
                        AIQuotaOperation.result_ref: (result_ref or "")[:160] or None,
                        AIQuotaOperation.completed_at: now,
                        AIQuotaOperation.updated_at: now,
                    },
                    synchronize_session=False,
                )
            )
            if not updated:
                return False
            session.query(AIQuotaCounter).filter(
                AIQuotaCounter.user_id == operation.user_id,
                AIQuotaCounter.feature_key == operation.feature_key,
                AIQuotaCounter.period_key == operation.period_key,
                AIQuotaCounter.reserved_count > 0,
            ).update(
                {
                    AIQuotaCounter.reserved_count: AIQuotaCounter.reserved_count - 1,
                    AIQuotaCounter.used_count: AIQuotaCounter.used_count + 1,
                    AIQuotaCounter.updated_at: now,
                },
                synchronize_session=False,
            )
            session.query(AIQuotaActiveLock).filter(
                AIQuotaActiveLock.user_id == operation.user_id,
                AIQuotaActiveLock.request_id == request_id,
            ).delete(synchronize_session=False)
            return True

    def release(self, request_id: str, *, outcome: str = "technical_error") -> bool:
        """Освобождает видимую квоту; техническая попытка остаётся учтённой."""
        now = self._utcnow_naive()
        with get_db_session() as session:
            operation = (
                session.query(AIQuotaOperation)
                .filter(AIQuotaOperation.request_id == request_id)
                .first()
            )
            if operation is None:
                return False
            if operation.status == "released":
                return True
            if operation.status != "reserved":
                return False
            updated = (
                session.query(AIQuotaOperation)
                .filter(AIQuotaOperation.id == operation.id)
                .filter(AIQuotaOperation.status == "reserved")
                .update(
                    {
                        AIQuotaOperation.status: "released",
                        AIQuotaOperation.outcome: outcome[:64],
                        AIQuotaOperation.completed_at: now,
                        AIQuotaOperation.updated_at: now,
                    },
                    synchronize_session=False,
                )
            )
            if not updated:
                return False
            session.query(AIQuotaCounter).filter(
                AIQuotaCounter.user_id == operation.user_id,
                AIQuotaCounter.feature_key == operation.feature_key,
                AIQuotaCounter.period_key == operation.period_key,
                AIQuotaCounter.reserved_count > 0,
            ).update(
                {
                    AIQuotaCounter.reserved_count: AIQuotaCounter.reserved_count - 1,
                    AIQuotaCounter.updated_at: now,
                },
                synchronize_session=False,
            )
            session.query(AIQuotaActiveLock).filter(
                AIQuotaActiveLock.user_id == operation.user_id,
                AIQuotaActiveLock.request_id == request_id,
            ).delete(synchronize_session=False)
            return True

    def get_status(
        self,
        user_id: str | int,
        feature: AIFeature | str,
        *,
        now: datetime | None = None,
    ) -> QuotaStatus:
        user_id = str(user_id)
        feature = AIFeature(feature)
        plan_key = self.get_plan_key(user_id, now=now)
        entitlement = self.entitlement(plan_key, feature)
        period = feature_period_key(entitlement, now)
        current = now or self._utcnow_naive()
        if current.tzinfo is not None:
            current = current.astimezone(timezone.utc).replace(tzinfo=None)
        with get_db_session() as session:
            self._expire_stale(session, user_id, current)
            row = (
                session.query(AIQuotaCounter)
                .filter(AIQuotaCounter.user_id == user_id)
                .filter(AIQuotaCounter.feature_key == feature.value)
                .filter(AIQuotaCounter.period_key == period)
                .first()
            )
            return QuotaStatus(
                feature=feature,
                plan_key=plan_key,
                period_key=period,
                limit=entitlement.limit,
                used=int(row.used_count or 0) if row else 0,
                reserved=int(row.reserved_count or 0) if row else 0,
            )

    def get_statuses(
        self,
        user_id: str | int,
        features: Iterable[AIFeature] | None = None,
        *,
        now: datetime | None = None,
    ) -> dict[AIFeature, QuotaStatus]:
        selected = tuple(features or AIFeature)
        return {feature: self.get_status(user_id, feature, now=now) for feature in selected}

    def get_attempt_status(
        self,
        user_id: str | int,
        feature: AIFeature | str,
        *,
        now: datetime | None = None,
    ) -> AttemptStatus:
        """Возвращает общий технический запас запросов для группы функции."""
        user_id = str(user_id)
        feature = AIFeature(feature)
        plan_key = self.get_plan_key(user_id, now=now)
        entitlement = self.entitlement(plan_key, feature)
        period = quota_period_key(now or datetime.now(MSK_TZ))
        with get_db_session() as session:
            row = (
                session.query(AIAttemptCounter)
                .filter(AIAttemptCounter.user_id == user_id)
                .filter(AIAttemptCounter.group_key == entitlement.attempt_group)
                .filter(AIAttemptCounter.period_key == period)
                .first()
            )
            return AttemptStatus(
                group_key=entitlement.attempt_group,
                period_key=period,
                limit=entitlement.attempt_limit,
                used=int(row.attempt_count or 0) if row else 0,
            )

    def get_operation(self, request_id: str) -> AIQuotaOperation | None:
        with get_db_session() as session:
            return (
                session.query(AIQuotaOperation)
                .filter(AIQuotaOperation.request_id == request_id)
                .first()
            )

    def register_additional_provider_attempt(self, request_id: str) -> bool:
        """Учитывает fallback как отдельный дорогой вызов без второй пользовательской квоты."""
        now = self._utcnow_naive()
        period = quota_period_key()
        denial: AIQuotaError | None = None
        with get_db_session() as session:
            operation = (
                session.query(AIQuotaOperation)
                .filter(AIQuotaOperation.request_id == request_id)
                .filter(AIQuotaOperation.status == "reserved")
                .first()
            )
            if operation is None:
                return False
            entitlement = self.entitlement(operation.plan_key, AIFeature(operation.feature_key))
            attempts = self._get_or_create(
                session,
                AIAttemptCounter,
                {
                    "user_id": operation.user_id,
                    "group_key": entitlement.attempt_group,
                    "period_key": period,
                },
                {"attempt_limit": entitlement.attempt_limit},
            )
            global_counter = self._get_or_create(
                session,
                AIGlobalDailyCounter,
                {"period_key": period},
                {"attempt_limit": AI_GLOBAL_ATTEMPT_LIMIT_PER_DAY},
            )
            session.flush()
            attempt_updated = (
                session.query(AIAttemptCounter)
                .filter(AIAttemptCounter.id == attempts.id)
                .filter(AIAttemptCounter.attempt_count < entitlement.attempt_limit)
                .update(
                    {
                        AIAttemptCounter.attempt_count: AIAttemptCounter.attempt_count + 1,
                        AIAttemptCounter.updated_at: now,
                    },
                    synchronize_session=False,
                )
            )
            if not attempt_updated:
                session.query(AIAttemptCounter).filter(AIAttemptCounter.id == attempts.id).update(
                    {AIAttemptCounter.blocked_count: AIAttemptCounter.blocked_count + 1},
                    synchronize_session=False,
                )
                denial = AIAttemptLimitExceeded("daily provider attempt limit exhausted")
            if denial is None:
                global_updated = (
                    session.query(AIGlobalDailyCounter)
                    .filter(AIGlobalDailyCounter.id == global_counter.id)
                    .filter(AIGlobalDailyCounter.attempt_count < AI_GLOBAL_ATTEMPT_LIMIT_PER_DAY)
                    .update(
                        {
                            AIGlobalDailyCounter.attempt_count: AIGlobalDailyCounter.attempt_count + 1,
                            AIGlobalDailyCounter.updated_at: now,
                        },
                        synchronize_session=False,
                    )
                )
                if not global_updated:
                    session.query(AIAttemptCounter).filter(AIAttemptCounter.id == attempts.id).update(
                        {AIAttemptCounter.attempt_count: AIAttemptCounter.attempt_count - 1},
                        synchronize_session=False,
                    )
                    session.query(AIGlobalDailyCounter).filter(
                        AIGlobalDailyCounter.id == global_counter.id
                    ).update(
                        {AIGlobalDailyCounter.blocked_count: AIGlobalDailyCounter.blocked_count + 1},
                        synchronize_session=False,
                    )
                    denial = AIGlobalLimitExceeded("global AI limit exhausted")
            if denial is None:
                operation.provider_attempt_count = int(operation.provider_attempt_count or 1) + 1
                operation.updated_at = now
        if denial is not None:
            raise denial
        return True

    def record_existing_success(
        self,
        user_id: str | int,
        feature: AIFeature | str,
        request_id: str,
        *,
        result_ref: str | None = None,
        now: datetime | None = None,
    ) -> bool:
        """Принимает старый сохранённый результат в квотный журнал без AI-попытки."""
        user_id = str(user_id)
        feature = AIFeature(feature)
        current = now or self._utcnow_naive()
        if current.tzinfo is not None:
            current = current.astimezone(timezone.utc).replace(tzinfo=None)
        plan_key = self.get_plan_key(user_id, now=current)
        entitlement = self.entitlement(plan_key, feature)
        period = feature_period_key(entitlement, now)
        with get_db_session() as session:
            existing = session.query(AIQuotaOperation).filter_by(request_id=request_id).first()
            if existing:
                return existing.status == "consumed"
            try:
                with session.begin_nested():
                    session.add(
                        AIQuotaOperation(
                            request_id=request_id,
                            user_id=user_id,
                            plan_key=plan_key,
                            feature_key=feature.value,
                            period_key=period,
                            status="consumed",
                            outcome="migrated_existing_result",
                            provider_started=False,
                            provider_attempt_count=0,
                            result_ref=(result_ref or "")[:160] or None,
                            expires_at=current,
                            completed_at=current,
                        )
                    )
                    session.flush()
            except IntegrityError:
                existing = session.query(AIQuotaOperation).filter_by(request_id=request_id).one()
                return existing.status == "consumed"
            quota = self._get_or_create(
                session,
                AIQuotaCounter,
                {"user_id": user_id, "feature_key": feature.value, "period_key": period},
                {"plan_key": plan_key, "limit_value": entitlement.limit},
            )
            quota.limit_value = entitlement.limit
            quota.plan_key = plan_key
            session.flush()
            session.query(AIQuotaCounter).filter(
                AIQuotaCounter.id == quota.id,
                AIQuotaCounter.used_count < entitlement.limit,
            ).update(
                {
                    AIQuotaCounter.used_count: AIQuotaCounter.used_count + 1,
                    AIQuotaCounter.updated_at: current,
                },
                synchronize_session=False,
            )
            return True

    def get_admin_metrics(self, *, period_key: date | None = None, user_limit: int = 15) -> dict:
        period = period_key or quota_period_key()
        with get_db_session() as session:
            counters = (
                session.query(AIQuotaCounter)
                .filter(AIQuotaCounter.period_key == period)
                .all()
            )
            operations = session.query(AIQuotaOperation).filter(AIQuotaOperation.period_key == period)
            attempts = (
                session.query(AIAttemptCounter)
                .filter(AIAttemptCounter.period_key == period)
                .all()
            )
            global_counter = (
                session.query(AIGlobalDailyCounter)
                .filter(AIGlobalDailyCounter.period_key == period)
                .first()
            )
            by_feature: dict[str, dict[str, int]] = {}
            for counter in counters:
                item = by_feature.setdefault(
                    counter.feature_key,
                    {"used": 0, "reserved": 0, "blocked": 0, "limit_hits": 0},
                )
                item["used"] += int(counter.used_count or 0)
                item["reserved"] += int(counter.reserved_count or 0)
                item["blocked"] += int(counter.blocked_count or 0)
                if int(counter.used_count or 0) >= int(counter.limit_value or 0):
                    item["limit_hits"] += 1

            user_rows = (
                session.query(
                    AIQuotaCounter.user_id,
                    func.sum(AIQuotaCounter.used_count).label("used"),
                    func.sum(AIQuotaCounter.blocked_count).label("blocked"),
                )
                .filter(AIQuotaCounter.period_key == period)
                .group_by(AIQuotaCounter.user_id)
                .order_by(func.sum(AIQuotaCounter.used_count).desc())
                .limit(user_limit)
                .all()
            )
            return {
                "period_key": period,
                "by_feature": by_feature,
                "attempts": {
                    row.group_key: {
                        "attempted": int(row.attempt_count or 0),
                        "blocked": int(row.blocked_count or 0),
                    }
                    for row in attempts
                },
                "global": {
                    "attempted": int(global_counter.attempt_count or 0) if global_counter else 0,
                    "limit": int(global_counter.attempt_limit or AI_GLOBAL_ATTEMPT_LIMIT_PER_DAY)
                    if global_counter
                    else AI_GLOBAL_ATTEMPT_LIMIT_PER_DAY,
                    "blocked": int(global_counter.blocked_count or 0) if global_counter else 0,
                },
                "operations": {
                    "success": operations.filter(AIQuotaOperation.status == "consumed").count(),
                    "released": operations.filter(AIQuotaOperation.status == "released").count(),
                    "expired": operations.filter(AIQuotaOperation.status == "expired").count(),
                    "pending": operations.filter(AIQuotaOperation.status == "reserved").count(),
                    "fallbacks": operations.filter(AIQuotaOperation.provider_attempt_count > 1).count(),
                },
                "top_users": [
                    {"user_id": str(row.user_id), "used": int(row.used or 0), "blocked": int(row.blocked or 0)}
                    for row in user_rows
                ],
            }


ai_quota_service = AIQuotaService()


def format_free_ai_status_block(user_id: str | int, *, compact: bool = False) -> str:
    """Показывает только пользовательские AI-квоты, без внутренних предохранителей."""
    try:
        statuses = ai_quota_service.get_statuses(
            user_id,
            (
                AIFeature.MEAL_TEXT,
                AIFeature.MEAL_PHOTO,
                AIFeature.NUTRITION_LABEL,
                AIFeature.MEAL_COMPLETION_COMMENT,
                AIFeature.DAILY_ANALYSIS,
            ),
        )
    except SQLAlchemyError:
        statuses = {
            feature: QuotaStatus(
                feature=feature,
                plan_key=FREE_PLAN_KEY,
                period_key=quota_period_key(),
                limit=FREE_PLAN.features[feature].limit,
                used=0,
                reserved=0,
            )
            for feature in (
                AIFeature.MEAL_TEXT,
                AIFeature.MEAL_PHOTO,
                AIFeature.NUTRITION_LABEL,
                AIFeature.MEAL_COMPLETION_COMMENT,
                AIFeature.DAILY_ANALYSIS,
            )
        }
    text = statuses[AIFeature.MEAL_TEXT]
    photo = statuses[AIFeature.MEAL_PHOTO]
    label = statuses[AIFeature.NUTRITION_LABEL]
    comment = statuses[AIFeature.MEAL_COMPLETION_COMMENT]
    daily = statuses[AIFeature.DAILY_ANALYSIS]
    from database.repositories.activity_analysis_repository import ActivityAnalysisRepository

    try:
        existing_daily = ActivityAnalysisRepository.get_successful_ai_for_date(
            str(user_id), daily.period_key
        )
    except SQLAlchemyError:
        existing_daily = None
    daily_value = "готов" if daily.used or existing_daily else "доступен" if daily.remaining else "лимит исчерпан"
    if compact:
        lines = [
            "AI-лимиты до 02:00 МСК:",
            f"📝 Текст: {text.remaining}/{text.limit}",
            f"📷 Фото еды: {photo.remaining}/{photo.limit}",
            f"📋 Этикетки: {label.remaining}/{label.limit}",
            f"💬 Советы после еды: {comment.remaining}/{comment.limit}",
        ]
        lines.append(f"🧠 Анализ дня: {daily_value}")
        return "\n".join(lines)
    return (
        "Бесплатные AI-возможности до 02:00 МСК:\n\n"
        f"📝 Текст: осталось {text.remaining} из {text.limit}\n"
        f"📷 Фото еды: осталось {photo.remaining} из {photo.limit}\n"
        f"📋 Этикетки: осталось {label.remaining} из {label.limit}\n"
        f"💬 Советы после еды: осталось {comment.remaining} из {comment.limit}\n"
        f"🧠 Анализ дня: {daily_value}"
    )
