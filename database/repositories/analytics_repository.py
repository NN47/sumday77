"""Репозиторий событий аналитики пользователей."""
from datetime import datetime, timedelta, date

from sqlalchemy import func

from database.models import UserEvent
from database.session import get_db_session


class AnalyticsRepository:
    """Аналитические события пользователей."""

    @staticmethod
    def track_event(user_id: str, event_name: str, section: str | None = None) -> None:
        with get_db_session() as session:
            session.add(UserEvent(user_id=user_id, event_name=event_name, section=section))

    @staticmethod
    def count_events_today(event_name: str) -> int:
        start = datetime.combine(date.today(), datetime.min.time())
        with get_db_session() as session:
            return (
                session.query(UserEvent)
                .filter(UserEvent.event_name == event_name, UserEvent.created_at >= start)
                .count()
            )

    @staticmethod
    def count_events_period(event_name: str, days: int) -> int:
        start = datetime.utcnow() - timedelta(days=days)
        with get_db_session() as session:
            return (
                session.query(UserEvent)
                .filter(UserEvent.event_name == event_name, UserEvent.created_at >= start)
                .count()
            )

    @staticmethod
    def count_events_today_bulk(event_names: list[str]) -> dict[str, int]:
        start = datetime.combine(date.today(), datetime.min.time())
        with get_db_session() as session:
            rows = (
                session.query(UserEvent.event_name, func.count(UserEvent.id))
                .filter(UserEvent.created_at >= start, UserEvent.event_name.in_(event_names))
                .group_by(UserEvent.event_name)
                .all()
            )
        data = {name: 0 for name in event_names}
        for name, count in rows:
            data[name] = int(count)
        return data

    @staticmethod
    def get_recent_events(limit: int = 20) -> list[UserEvent]:
        with get_db_session() as session:
            return session.query(UserEvent).order_by(UserEvent.created_at.desc()).limit(limit).all()

    @staticmethod
    def get_top_users(days: int = 7, limit: int = 10) -> list[tuple[str, int]]:
        start = datetime.utcnow() - timedelta(days=days)
        with get_db_session() as session:
            rows = (
                session.query(UserEvent.user_id, func.count(UserEvent.id).label("events_count"))
                .filter(UserEvent.created_at >= start)
                .group_by(UserEvent.user_id)
                .order_by(func.count(UserEvent.id).desc())
                .limit(limit)
                .all()
            )
        return [(str(user_id), int(count)) for user_id, count in rows]
