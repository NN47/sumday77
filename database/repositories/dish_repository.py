"""Repository for reusable user-owned dishes."""

from __future__ import annotations

import re

from sqlalchemy.orm import selectinload

from database.models import Dish
from database.session import get_db_session


def normalize_dish_name(name: str) -> str:
    return re.sub(r"\s+", " ", str(name or "").strip()).casefold()


class DishRepository:
    @staticmethod
    def get_by_id(user_id: str, dish_id: int, *, include_archived: bool = False) -> Dish | None:
        with get_db_session() as session:
            query = (
                session.query(Dish)
                .options(selectinload(Dish.ingredients))
                .filter(Dish.id == int(dish_id), Dish.user_id == str(user_id))
            )
            if not include_archived:
                query = query.filter(Dish.archived_at.is_(None))
            return query.first()

    @staticmethod
    def list_active(user_id: str, *, offset: int = 0, limit: int | None = 50) -> list[Dish]:
        with get_db_session() as session:
            query = (
                session.query(Dish)
                .options(selectinload(Dish.ingredients))
                .filter(Dish.user_id == str(user_id), Dish.archived_at.is_(None))
                .order_by(Dish.updated_at.desc(), Dish.id.desc())
                .offset(max(0, int(offset)))
            )
            if limit is not None:
                query = query.limit(max(1, int(limit)))
            return query.all()

    @staticmethod
    def count_active(user_id: str) -> int:
        with get_db_session() as session:
            return int(
                session.query(Dish)
                .filter(Dish.user_id == str(user_id), Dish.archived_at.is_(None))
                .count()
            )

    @staticmethod
    def archive(user_id: str, dish_id: int) -> bool:
        from datetime import datetime

        with get_db_session() as session:
            dish = (
                session.query(Dish)
                .filter(Dish.id == int(dish_id), Dish.user_id == str(user_id))
                .first()
            )
            if dish is None or dish.archived_at is not None:
                return False
            dish.archived_at = datetime.utcnow()
            dish.updated_at = datetime.utcnow()
            session.commit()
            return True
