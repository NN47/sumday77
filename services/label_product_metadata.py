"""Нормализация постоянных свойств продукта, найденных на этикетке."""

from __future__ import annotations

import re


def _to_float(value) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        match = re.search(r"\d+(?:[.,]\d+)?", value)
        if match:
            try:
                return float(match.group(0).replace(",", "."))
            except ValueError:
                return None
    return None


def _pick_numeric(payload: dict, keys: tuple[str, ...]) -> float | None:
    for key in keys:
        if key in payload:
            value = _to_float(payload.get(key))
            if value is not None:
                return value
    return None


def normalize_label_product_metadata(
    payload: dict,
    *,
    package_weight_g: float | None,
) -> dict:
    """Возвращает только достоверные nullable-данные единицы и упаковки.

    Вес единицы вычисляется лишь тогда, когда на этикетке явно распознаны и
    масса всей упаковки, и целое положительное количество единиц.
    """
    unit_weight_g = _pick_numeric(
        payload,
        ("unit_weight_g", "unit_weight", "piece_weight_g", "weight_per_unit_g"),
    )
    package_units_raw = _pick_numeric(
        payload,
        ("package_units", "units_in_package", "pieces", "piece_count"),
    )
    package_units = (
        int(package_units_raw)
        if package_units_raw is not None
        and package_units_raw > 0
        and package_units_raw.is_integer()
        else None
    )

    if unit_weight_g is not None and unit_weight_g <= 0:
        unit_weight_g = None
    if (
        unit_weight_g is None
        and package_weight_g is not None
        and package_weight_g > 0
        and package_units is not None
    ):
        unit_weight_g = package_weight_g / package_units

    raw_unit_name = (
        payload.get("unit_name")
        or payload.get("piece_name")
    )
    unit_name = str(raw_unit_name).strip() if raw_unit_name is not None else None
    if not unit_name or unit_name.casefold() in {"null", "none", "неизвестно"}:
        unit_name = None

    return {
        "unit_weight_g": unit_weight_g,
        "unit_name": unit_name,
        "package_units": package_units,
    }
