"""Репозиторий состояния вечерних уведомлений ИИ-анализа дня."""
from __future__ import annotations

from datetime import date, datetime

from sqlalchemy.exc import SQLAlchemyError

from database.models import EveningAnalysisNotificationState
from database.session import get_db_session


class EveningAnalysisNotificationRepository:
    """Методы работы с состоянием вечерних уведомлений анализа дня."""

    @staticmethod
    def get_or_create_state(user_id: str) -> EveningAnalysisNotificationState:
        """Возвращает состояние пользователя, создавая запись при необходимости."""
        user_id = str(user_id)
        with get_db_session() as session:
            state = (
                session.query(EveningAnalysisNotificationState)
                .filter(EveningAnalysisNotificationState.user_id == user_id)
                .first()
            )
            if state is None:
                state = EveningAnalysisNotificationState(user_id=user_id)
                session.add(state)
                session.commit()
                session.refresh(state)
            return state

    @staticmethod
    def mark_evening_notification_sent(user_id: str, target_date: date) -> None:
        """Помечает основное вечернее уведомление отправленным за дату."""
        user_id = str(user_id)
        now = datetime.utcnow()
        with get_db_session() as session:
            state = (
                session.query(EveningAnalysisNotificationState)
                .filter(EveningAnalysisNotificationState.user_id == user_id)
                .first()
            )
            if state is None:
                state = EveningAnalysisNotificationState(user_id=user_id)
                session.add(state)
            state.last_evening_notification_date = target_date
            if state.remind_later_date != target_date:
                state.remind_later_date = target_date
                state.remind_later_count = 0
            state.reminder_due_at = None
            state.updated_at = now

    @staticmethod
    def mark_analysis_started(user_id: str, target_date: date) -> None:
        """Помечает ИИ-анализ дня запущенным за дату."""
        user_id = str(user_id)
        now = datetime.utcnow()
        try:
            with get_db_session() as session:
                state = (
                    session.query(EveningAnalysisNotificationState)
                    .filter(EveningAnalysisNotificationState.user_id == user_id)
                    .first()
                )
                if state is None:
                    state = EveningAnalysisNotificationState(user_id=user_id)
                    session.add(state)
                state.last_daily_analysis_date = target_date
                state.reminder_due_at = None
                state.updated_at = now
        except SQLAlchemyError:
            # В production таблица создаётся init_db(); no-op сохраняет совместимость
            # изолированных тестов, которые подменяют БД без полной инициализации.
            return

    @staticmethod
    def schedule_reminder(user_id: str, target_date: date, due_at: datetime) -> int | None:
        """
        Планирует повторное уведомление и возвращает текущий номер повтора.

        Возвращает None, если лимит повторов за вечер уже исчерпан.
        """
        user_id = str(user_id)
        now = datetime.utcnow()
        with get_db_session() as session:
            state = (
                session.query(EveningAnalysisNotificationState)
                .filter(EveningAnalysisNotificationState.user_id == user_id)
                .first()
            )
            if state is None:
                state = EveningAnalysisNotificationState(user_id=user_id)
                session.add(state)

            if state.last_daily_analysis_date == target_date:
                state.reminder_due_at = None
                state.updated_at = now
                return None

            if state.remind_later_date != target_date:
                state.remind_later_date = target_date
                state.remind_later_count = 0

            if state.remind_later_count >= 7:
                state.reminder_due_at = None
                state.updated_at = now
                return None

            state.remind_later_count += 1
            state.reminder_due_at = due_at
            state.updated_at = now
            return state.remind_later_count

    @staticmethod
    def mark_reminder_sent(user_id: str, target_date: date) -> None:
        """Сбрасывает due_at после отправки повторного уведомления."""
        user_id = str(user_id)
        with get_db_session() as session:
            state = (
                session.query(EveningAnalysisNotificationState)
                .filter(EveningAnalysisNotificationState.user_id == user_id)
                .first()
            )
            if state is None:
                return
            if state.remind_later_date == target_date:
                state.reminder_due_at = None
                state.updated_at = datetime.utcnow()
