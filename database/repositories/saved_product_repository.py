"""Репозиторий постоянных справочных данных пользовательских продуктов."""

from __future__ import annotations

from datetime import datetime
import re

from database.models import SavedProduct
from database.session import get_db_session


def normalize_saved_product_name(name: str) -> str:
    """Возвращает стабильный ключ сопоставления в рамках текущей UX-модели."""
    return re.sub(r"\s+", " ", str(name or "").strip()).casefold()


def _positive_float(value) -> float | None:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    return numeric if numeric > 0 else None


def _positive_int(value) -> int | None:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    if numeric <= 0 or not numeric.is_integer():
        return None
    return int(numeric)


class SavedProductRepository:
    """Читает и обновляет последнюю порцию и постоянные свойства продукта."""

    @staticmethod
    def get_by_name(user_id: str, name: str) -> SavedProduct | None:
        normalized_name = normalize_saved_product_name(name)
        if not normalized_name:
            return None
        with get_db_session() as session:
            return (
                session.query(SavedProduct)
                .filter(
                    SavedProduct.user_id == str(user_id),
                    SavedProduct.normalized_name == normalized_name,
                )
                .first()
            )

    @staticmethod
    def upsert(
        *,
        user_id: str,
        name: str,
        last_weight_g=None,
        unit_weight_g=None,
        unit_name=None,
        package_weight_g=None,
        package_units=None,
    ) -> SavedProduct | None:
        """Обновляет последнюю порцию, не стирая отсутствующие справочные данные."""
        clean_name = re.sub(r"\s+", " ", str(name or "").strip())
        normalized_name = normalize_saved_product_name(clean_name)
        if not normalized_name:
            return None

        clean_unit_name = str(unit_name or "").strip() or None
        last_weight = _positive_float(last_weight_g)
        unit_weight = _positive_float(unit_weight_g)
        package_weight = _positive_float(package_weight_g)
        units = _positive_int(package_units)

        with get_db_session() as session:
            saved = (
                session.query(SavedProduct)
                .filter(
                    SavedProduct.user_id == str(user_id),
                    SavedProduct.normalized_name == normalized_name,
                )
                .first()
            )
            if saved is None:
                saved = SavedProduct(
                    user_id=str(user_id),
                    normalized_name=normalized_name,
                    name=clean_name,
                )
                session.add(saved)

            saved.name = clean_name
            if last_weight is not None:
                saved.last_weight_g = last_weight
            if unit_weight is not None:
                saved.unit_weight_g = unit_weight
            if clean_unit_name is not None:
                saved.unit_name = clean_unit_name
            if package_weight is not None:
                saved.package_weight_g = package_weight
            if units is not None:
                saved.package_units = units
            saved.updated_at = datetime.utcnow()
            session.commit()
            session.refresh(saved)
            return saved

    @staticmethod
    def upsert_from_product(user_id: str, product: dict) -> SavedProduct | None:
        """Сохраняет поддерживаемые поля из общего словаря продукта."""
        return SavedProductRepository.upsert(
            user_id=str(user_id),
            name=product.get("name"),
            last_weight_g=product.get("grams"),
            unit_weight_g=product.get("unit_weight_g"),
            unit_name=product.get("unit_name"),
            package_weight_g=product.get("package_weight_g"),
            package_units=product.get("package_units"),
        )
