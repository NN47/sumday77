"""Репозиторий пользователей для админ-аналитики."""
from datetime import datetime, timedelta, date

from database.models import User
from database.session import get_db_session
from utils.legal_documents import LEGAL_DOCUMENTS, has_current_acceptance


class UserRepository:
    """Методы работы с пользователями и их активностью."""

    @staticmethod
    def has_current_legal_acceptance(user_id: str) -> bool:
        with get_db_session() as session:
            user = session.query(User).filter(User.user_id == str(user_id)).first()
            return has_current_acceptance(user)

    @staticmethod
    def accept_legal_documents(user_id: str) -> None:
        """Record offer acceptance and policy acknowledgement; not PD consent."""
        with get_db_session() as session:
            user = session.query(User).filter(User.user_id == str(user_id)).first()
            if user is None:
                user = User(user_id=str(user_id))
                session.add(user)
            now = datetime.utcnow()
            terms_version = LEGAL_DOCUMENTS["terms"].version
            privacy_version = LEGAL_DOCUMENTS["privacy"].version
            if user.accepted_terms_version != terms_version or user.terms_accepted_at is None:
                user.accepted_terms_version = terms_version
                user.terms_accepted_at = now
            if user.acknowledged_privacy_version != privacy_version or user.privacy_acknowledged_at is None:
                user.acknowledged_privacy_version = privacy_version
                user.privacy_acknowledged_at = now

    @staticmethod
    def touch_user(user_id: str) -> None:
        """Создаёт пользователя при первом апдейте и обновляет last_seen_at."""
        now = datetime.utcnow()
        with get_db_session() as session:
            user = session.query(User).filter(User.user_id == user_id).first()
            if not user:
                user = User(user_id=user_id, created_at=now, last_seen_at=now)
                session.add(user)
                session.flush()
            else:
                user.last_seen_at = now

    @staticmethod
    def get_age_verification(user_id: str) -> bool | None:
        """Return the minimal persisted 18+ status, or None if not confirmed."""
        with get_db_session() as session:
            user = session.query(User).filter(User.user_id == str(user_id)).first()
            return user.age_verified if user is not None else None

    @staticmethod
    def set_age_verification(user_id: str, is_adult: bool) -> None:
        """Persist only whether the user confirmed an adult age category."""
        with get_db_session() as session:
            user = session.query(User).filter(User.user_id == str(user_id)).first()
            if user is None:
                now = datetime.utcnow()
                user = User(
                    user_id=str(user_id),
                    created_at=now,
                    last_seen_at=now,
                )
                session.add(user)
            user.age_verified = bool(is_adult)

    @staticmethod
    def is_age_verified(user_id: str) -> bool:
        return UserRepository.get_age_verification(user_id) is True

    @staticmethod
    def count_all() -> int:
        with get_db_session() as session:
            return session.query(User).count()

    @staticmethod
    def count_new_today() -> int:
        start = datetime.combine(date.today(), datetime.min.time())
        with get_db_session() as session:
            return session.query(User).filter(User.created_at >= start).count()

    @staticmethod
    def count_new_7d() -> int:
        start = datetime.utcnow() - timedelta(days=7)
        with get_db_session() as session:
            return session.query(User).filter(User.created_at >= start).count()

    @staticmethod
    def count_active_24h() -> int:
        start = datetime.utcnow() - timedelta(hours=24)
        with get_db_session() as session:
            return session.query(User).filter(User.last_seen_at >= start).count()

    @staticmethod
    def count_active_7d() -> int:
        start = datetime.utcnow() - timedelta(days=7)
        with get_db_session() as session:
            return session.query(User).filter(User.last_seen_at >= start).count()

    @staticmethod
    def count_active_30d() -> int:
        start = datetime.utcnow() - timedelta(days=30)
        with get_db_session() as session:
            return session.query(User).filter(User.last_seen_at >= start).count()

    @staticmethod
    def get_recent_active(limit: int = 10) -> list[User]:
        with get_db_session() as session:
            return (
                session.query(User)
                .order_by(User.last_seen_at.desc())
                .limit(limit)
                .all()
            )

    @staticmethod
    def get_recent_users(limit: int = 20) -> list[User]:
        with get_db_session() as session:
            return (
                session.query(User)
                .order_by(User.last_seen_at.desc(), User.created_at.desc())
                .limit(limit)
                .all()
            )

    @staticmethod
    def count_registered_on_day(days_ago: int) -> int:
        target_day = date.today() - timedelta(days=days_ago)
        start = datetime.combine(target_day, datetime.min.time())
        end = start + timedelta(days=1)
        with get_db_session() as session:
            return session.query(User).filter(User.created_at >= start, User.created_at < end).count()

    @staticmethod
    def count_registered_on_day_and_active_today(days_ago: int) -> int:
        target_day = date.today() - timedelta(days=days_ago)
        cohort_start = datetime.combine(target_day, datetime.min.time())
        cohort_end = cohort_start + timedelta(days=1)
        today_start = datetime.combine(date.today(), datetime.min.time())
        with get_db_session() as session:
            return (
                session.query(User)
                .filter(
                    User.created_at >= cohort_start,
                    User.created_at < cohort_end,
                    User.last_seen_at >= today_start,
                )
                .count()
            )
