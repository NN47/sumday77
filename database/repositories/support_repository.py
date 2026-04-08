"""Репозиторий обращений в поддержку."""
from datetime import datetime, timedelta, date

from database.models import SupportMessage
from database.session import get_db_session


class SupportRepository:
    """Операции с сообщениями поддержки."""

    @staticmethod
    def create_message(user_id: str, message_text: str, username: str | None = None, full_name: str | None = None) -> None:
        with get_db_session() as session:
            session.add(
                SupportMessage(
                    user_id=user_id,
                    username=username,
                    full_name=full_name,
                    message_text=message_text,
                )
            )

    @staticmethod
    def count_today() -> int:
        start = datetime.combine(date.today(), datetime.min.time())
        with get_db_session() as session:
            return session.query(SupportMessage).filter(SupportMessage.created_at >= start).count()

    @staticmethod
    def count_7d() -> int:
        start = datetime.utcnow() - timedelta(days=7)
        with get_db_session() as session:
            return session.query(SupportMessage).filter(SupportMessage.created_at >= start).count()

    @staticmethod
    def get_recent(limit: int = 10) -> list[SupportMessage]:
        with get_db_session() as session:
            return session.query(SupportMessage).order_by(SupportMessage.created_at.desc()).limit(limit).all()

    @staticmethod
    def mark_read(message_id: int) -> bool:
        with get_db_session() as session:
            item = session.query(SupportMessage).filter(SupportMessage.id == message_id).first()
            if not item:
                return False
            item.is_read = True
            return True
