"""Сервис продуктовой статистики для админ-панели."""
from __future__ import annotations

from dataclasses import dataclass

from database.repositories import UserRepository, AnalyticsRepository, ErrorLogRepository


NAVIGATION_EVENTS = ["open_main_menu", "open_kbju", "open_weight", "open_activity", "open_notes"]
CORE_EVENTS = ["add_meal", "add_weight", "add_steps", "add_workout", "request_daily_analysis"]


@dataclass
class RetentionPoint:
    days: int
    cohort_size: int
    returned_today: int

    @property
    def percent(self) -> float:
        if not self.cohort_size:
            return 0.0
        return (self.returned_today * 100) / self.cohort_size


class AdminStatsService:
    """Собирает метрики для админки без бизнес-логики UI."""

    @staticmethod
    def get_dashboard_metrics() -> dict:
        active_today = AnalyticsRepository.count_unique_users_today()
        core_today = AnalyticsRepository.count_core_users(days=1)
        total_events_today = AnalyticsRepository.count_all_events_today()
        avg_actions_per_user = (total_events_today / active_today) if active_today else 0

        daily = AnalyticsRepository.count_daily_analysis_metrics_today()
        sent = daily["daily_analysis_sent"]
        failed = daily["daily_analysis_failed"]
        total_daily_done = sent + failed
        success_rate = (sent * 100 / total_daily_done) if total_daily_done else 0

        recent_error = ErrorLogRepository.get_recent(limit=1)

        return {
            "total_users": UserRepository.count_all(),
            "active_24h": UserRepository.count_active_24h(),
            "active_7d": UserRepository.count_active_7d(),
            "active_30d": UserRepository.count_active_30d(),
            "new_today": UserRepository.count_new_today(),
            "new_7d": UserRepository.count_new_7d(),
            "core_users_today": core_today,
            "core_users_7d": AnalyticsRepository.count_core_users(days=7),
            "core_users_30d": AnalyticsRepository.count_core_users(days=30),
            "conversion_to_core": (core_today * 100 / active_today) if active_today else 0,
            "total_events_today": total_events_today,
            "avg_actions_per_user": avg_actions_per_user,
            "daily_analysis_started": daily["daily_analysis_started"],
            "daily_analysis_sent": sent,
            "daily_analysis_failed": failed,
            "daily_analysis_success_rate": success_rate,
            "errors_today": ErrorLogRepository.count_today(),
            "latest_error": recent_error[0] if recent_error else None,
        }

    @staticmethod
    def get_today_metrics() -> dict:
        event_names = NAVIGATION_EVENTS + CORE_EVENTS
        counts = AnalyticsRepository.count_events_today_bulk(event_names)
        return {
            "active_users_today": AnalyticsRepository.count_unique_users_today(),
            "navigation": {name: counts.get(name, 0) for name in NAVIGATION_EVENTS},
            "helpful": {name: counts.get(name, 0) for name in CORE_EVENTS},
        }

    @staticmethod
    def get_funnel_metrics() -> dict:
        funnel = AnalyticsRepository.get_funnel_today()

        def _step(current: int, previous: int) -> float:
            if previous <= 0:
                return 0.0
            return (current * 100) / previous

        return {
            "menu": funnel["menu"],
            "sections": funnel["sections"],
            "core": funnel["core"],
            "analysis": funnel["analysis"],
            "sections_from_menu": _step(funnel["sections"], funnel["menu"]),
            "core_from_sections": _step(funnel["core"], funnel["sections"]),
            "analysis_from_core": _step(funnel["analysis"], funnel["core"]),
        }

    @staticmethod
    def get_retention_metrics() -> list[RetentionPoint]:
        points: list[RetentionPoint] = []
        for days in (1, 7, 30):
            points.append(
                RetentionPoint(
                    days=days,
                    cohort_size=UserRepository.count_registered_on_day(days),
                    returned_today=UserRepository.count_registered_on_day_and_active_today(days),
                )
            )
        return points

    @staticmethod
    def get_errors_metrics() -> dict:
        recent = ErrorLogRepository.get_recent(limit=1)
        daily = AnalyticsRepository.count_daily_analysis_metrics_today()
        return {
            "today": ErrorLogRepository.count_today(),
            "week": ErrorLogRepository.count_7d(),
            "grouped": ErrorLogRepository.get_grouped_7d(),
            "last_error": recent[0] if recent else None,
            "daily_analysis_failed": daily["daily_analysis_failed"],
        }

    @staticmethod
    def get_latest_events(limit: int = 20):
        return AnalyticsRepository.get_recent_events(limit=limit)

    @staticmethod
    def get_users_metrics(limit: int = 20) -> dict:
        users = UserRepository.get_recent_users(limit=limit)
        core_by_action_today = set()
        for event_name in CORE_EVENTS:
            core_by_action_today.update(AnalyticsRepository.get_users_with_event_today(event_name))

        rows = []
        for user in users:
            uid = str(user.user_id)
            rows.append(
                {
                    "user_id": uid,
                    "registered_at": user.created_at,
                    "last_seen_at": user.last_seen_at,
                    "actions_today": AnalyticsRepository.count_events_for_user(uid, days=1),
                    "actions_7d": AnalyticsRepository.count_events_for_user(uid, days=7),
                    "is_core_today": uid in core_by_action_today,
                    "daily_analysis_requests": AnalyticsRepository.count_event_for_user(
                        uid, "request_daily_analysis", days=3650
                    ),
                }
            )

        return {
            "users": rows,
            "top_users": AnalyticsRepository.get_top_users(days=7, limit=10),
        }
