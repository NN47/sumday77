"""Хранилище ограниченной по времени истории «Спросить Sumday»."""
from datetime import datetime, timedelta

from database.models import AIChatMessage
from database.session import get_db_session


class AIChatRepository:
    TTL_DAYS = 30
    MAX_MESSAGES = 12

    @classmethod
    def add(cls, user_id: str, role: str, content: str) -> None:
        if role not in {"user", "assistant"}:
            raise ValueError("invalid chat role")
        with get_db_session() as session:
            cutoff = datetime.utcnow() - timedelta(days=cls.TTL_DAYS)
            session.query(AIChatMessage).filter(
                AIChatMessage.user_id == str(user_id), AIChatMessage.created_at < cutoff
            ).delete(synchronize_session=False)
            session.add(AIChatMessage(user_id=str(user_id), role=role, content=content[:4000]))
            session.flush()
            keep_ids = [row[0] for row in session.query(AIChatMessage.id).filter(
                AIChatMessage.user_id == str(user_id)
            ).order_by(AIChatMessage.created_at.desc(), AIChatMessage.id.desc()).limit(cls.MAX_MESSAGES).all()]
            if keep_ids:
                session.query(AIChatMessage).filter(
                    AIChatMessage.user_id == str(user_id), AIChatMessage.id.notin_(keep_ids)
                ).delete(synchronize_session=False)

    @classmethod
    def recent(cls, user_id: str) -> list[dict[str, str]]:
        with get_db_session() as session:
            rows = session.query(AIChatMessage).filter(
                AIChatMessage.user_id == str(user_id),
                AIChatMessage.created_at >= datetime.utcnow() - timedelta(days=cls.TTL_DAYS),
            ).order_by(AIChatMessage.created_at.asc(), AIChatMessage.id.asc()).all()
            return [{"role": row.role, "content": row.content} for row in rows[-cls.MAX_MESSAGES:]]

    @staticmethod
    def clear(user_id: str) -> int:
        with get_db_session() as session:
            return session.query(AIChatMessage).filter(
                AIChatMessage.user_id == str(user_id)
            ).delete(synchronize_session=False)
