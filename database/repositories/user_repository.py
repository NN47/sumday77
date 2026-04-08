"""Репозиторий пользователей для админ-аналитики."""
from datetime import datetime, timedelta, date

from database.models import User
from database.session import get_db_session


class UserRepository:
    """Методы работы с пользователями и их активностью."""

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
