"""Репозиторий событий аналитики пользователей."""
from datetime import datetime, timedelta, date

from sqlalchemy import func, case, distinct

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

    @staticmethod
    def count_all_events_today() -> int:
        start = datetime.combine(date.today(), datetime.min.time())
        with get_db_session() as session:
            return session.query(UserEvent).filter(UserEvent.created_at >= start).count()

    @staticmethod
    def count_unique_users_today() -> int:
        start = datetime.combine(date.today(), datetime.min.time())
        with get_db_session() as session:
            return (
                session.query(func.count(distinct(UserEvent.user_id)))
                .filter(UserEvent.created_at >= start)
                .scalar()
                or 0
            )

    @staticmethod
    def count_core_users(days: int = 1) -> int:
        core_events = ["add_meal", "add_weight", "add_steps", "add_workout", "request_daily_analysis"]
        start = datetime.combine(date.today(), datetime.min.time()) if days == 1 else (datetime.utcnow() - timedelta(days=days))
        with get_db_session() as session:
            return (
                session.query(func.count(distinct(UserEvent.user_id)))
                .filter(UserEvent.created_at >= start, UserEvent.event_name.in_(core_events))
                .scalar()
                or 0
            )

    @staticmethod
    def get_funnel_today() -> dict[str, int]:
        start = datetime.combine(date.today(), datetime.min.time())
        section_events = ["open_kbju", "open_weight", "open_activity", "open_notes"]
        core_events = ["add_meal", "add_weight", "add_steps", "add_workout", "request_daily_analysis"]
        with get_db_session() as session:
            row = (
                session.query(
                    func.count(distinct(case((UserEvent.event_name == "open_main_menu", UserEvent.user_id)))).label("menu"),
                    func.count(distinct(case((UserEvent.event_name.in_(section_events), UserEvent.user_id)))).label("sections"),
                    func.count(distinct(case((UserEvent.event_name.in_(core_events), UserEvent.user_id)))).label("core"),
                    func.count(distinct(case((UserEvent.event_name == "request_daily_analysis", UserEvent.user_id)))).label("analysis"),
                )
                .filter(UserEvent.created_at >= start)
                .one()
            )
        return {
            "menu": int(row.menu or 0),
            "sections": int(row.sections or 0),
            "core": int(row.core or 0),
            "analysis": int(row.analysis or 0),
        }

    @staticmethod
    def get_users_with_event_today(event_name: str) -> set[str]:
        start = datetime.combine(date.today(), datetime.min.time())
        with get_db_session() as session:
            rows = (
                session.query(distinct(UserEvent.user_id))
                .filter(UserEvent.created_at >= start, UserEvent.event_name == event_name)
                .all()
            )
        return {str(row[0]) for row in rows}

    @staticmethod
    def get_users_active_today() -> set[str]:
        start = datetime.combine(date.today(), datetime.min.time())
        with get_db_session() as session:
            rows = session.query(distinct(UserEvent.user_id)).filter(UserEvent.created_at >= start).all()
        return {str(row[0]) for row in rows}

    @staticmethod
    def count_events_for_user(user_id: str, days: int = 1) -> int:
        start = datetime.combine(date.today(), datetime.min.time()) if days == 1 else (datetime.utcnow() - timedelta(days=days))
        with get_db_session() as session:
            return (
                session.query(UserEvent)
                .filter(UserEvent.user_id == str(user_id), UserEvent.created_at >= start)
                .count()
            )

    @staticmethod
    def count_event_for_user(user_id: str, event_name: str, days: int = 3650) -> int:
        start = datetime.combine(date.today(), datetime.min.time()) if days == 1 else (datetime.utcnow() - timedelta(days=days))
        with get_db_session() as session:
            return (
                session.query(UserEvent)
                .filter(
                    UserEvent.user_id == str(user_id),
                    UserEvent.event_name == event_name,
                    UserEvent.created_at >= start,
                )
                .count()
            )

    @staticmethod
    def count_daily_analysis_metrics_today() -> dict[str, int]:
        start = datetime.combine(date.today(), datetime.min.time())
        with get_db_session() as session:
            rows = (
                session.query(UserEvent.event_name, func.count(UserEvent.id))
                .filter(
                    UserEvent.created_at >= start,
                    UserEvent.event_name.in_(["daily_analysis_started", "daily_analysis_sent", "daily_analysis_failed"]),
                )
                .group_by(UserEvent.event_name)
                .all()
            )
        data = {"daily_analysis_started": 0, "daily_analysis_sent": 0, "daily_analysis_failed": 0}
        for event_name, count in rows:
            data[event_name] = int(count)
        return data
