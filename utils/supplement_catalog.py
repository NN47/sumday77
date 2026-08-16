"""Central catalog of food and sports supplements.

The database can contain both catalog identifiers (new records) and historical
display names (legacy records).  All user-facing catalog names are defined here.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass


@dataclass(frozen=True)
class SupplementItem:
    identifier: str
    display_name: str
    legacy_aliases: tuple[str, ...] = ()


@dataclass(frozen=True)
class SupplementCategory:
    identifier: str
    display_name: str
    items: tuple[SupplementItem, ...]


SUPPLEMENT_CATEGORIES: tuple[SupplementCategory, ...] = (
    SupplementCategory(
        "protein",
        "Белковые добавки",
        (
            SupplementItem("protein", "Протеин"),
            SupplementItem("whey_protein", "Сывороточный протеин"),
            SupplementItem("casein", "Казеин"),
            SupplementItem("plant_protein", "Растительный протеин"),
            SupplementItem("egg_protein", "Яичный протеин"),
            SupplementItem("protein_blend", "Протеиновая смесь"),
        ),
    ),
    SupplementCategory(
        "amino_acids",
        "Аминокислоты",
        (
            SupplementItem("amino_acids", "Аминокислоты"),
            SupplementItem("bcaa", "BCAA"),
            SupplementItem("eaa", "EAA"),
            SupplementItem("glutamine", "Глютамин"),
            SupplementItem("arginine", "Аргинин"),
            SupplementItem("citrulline", "Цитруллин"),
            SupplementItem("beta_alanine", "Бета-аланин"),
            SupplementItem("amino_acid_complex", "Спортивный аминокислотный комплекс"),
        ),
    ),
    SupplementCategory(
        "sport",
        "Спортивные комплексы",
        (
            SupplementItem("creatine", "Креатин"),
            SupplementItem("pre_workout", "Предтренировочный комплекс"),
            SupplementItem("carbohydrate_drink", "Углеводный напиток"),
            SupplementItem("gainer", "Гейнер"),
            SupplementItem("recovery_complex", "Восстановительный спортивный комплекс"),
            SupplementItem("l_carnitine", "L-карнитин", ("Карнитин",)),
            SupplementItem("caffeine_supplement", "Кофеиновая спортивная добавка"),
            SupplementItem("sports_energy_supplement", "Спортивная энергетическая добавка"),
        ),
    ),
    SupplementCategory(
        "fatty_acids",
        "Жирные кислоты",
        (
            SupplementItem("omega_3", "Омега-3", ("Омега‑3", "Omega-3")),
            SupplementItem("fish_oil", "Рыбий жир"),
            SupplementItem("omega_3_6_9", "Комплекс омега-3-6-9"),
            SupplementItem("mct_oil", "МСТ-масло", ("MCT-масло", "MCT oil")),
        ),
    ),
    SupplementCategory(
        "vitamins",
        "Витамины",
        (
            SupplementItem("vitamin_d", "Витамин D"),
            SupplementItem("vitamin_d3", "Витамин D3"),
            SupplementItem("vitamin_d3_k2", "Комплекс витаминов D3 + K2"),
            SupplementItem("vitamin_c", "Витамин C"),
            SupplementItem("vitamin_a", "Витамин A"),
            SupplementItem("vitamin_e", "Витамин E"),
            SupplementItem("vitamin_k", "Витамин K"),
            SupplementItem("vitamin_b_complex", "Витамины группы B"),
            SupplementItem("vitamin_b1", "Витамин B1 (тиамин)"),
            SupplementItem("vitamin_b2", "Витамин B2 (рибофлавин)"),
            SupplementItem("vitamin_b3", "Витамин B3 (ниацин)"),
            SupplementItem("vitamin_b5", "Витамин B5 (пантотеновая кислота)"),
            SupplementItem("vitamin_b6", "Витамин B6"),
            SupplementItem("vitamin_b7", "Витамин B7 (биотин)"),
            SupplementItem("vitamin_b9", "Витамин B9 (фолиевая кислота)"),
            SupplementItem("vitamin_b12", "Витамин B12"),
            SupplementItem("vitamin_complex", "Комплекс витаминов"),
            SupplementItem("multivitamin", "Мультивитамины"),
        ),
    ),
    SupplementCategory(
        "minerals",
        "Минералы",
        (
            SupplementItem("magnesium", "Магний"),
            SupplementItem("zinc", "Цинк"),
            SupplementItem("calcium", "Кальций"),
            SupplementItem("iron", "Железо"),
            SupplementItem("selenium", "Селен"),
            SupplementItem("potassium", "Калий"),
            SupplementItem("iodine", "Йод"),
            SupplementItem("copper", "Медь"),
            SupplementItem("chromium", "Хром"),
            SupplementItem("manganese", "Марганец"),
            SupplementItem("mineral_complex", "Минеральный комплекс"),
        ),
    ),
    SupplementCategory(
        "hydration",
        "Электролиты и гидратация",
        (
            SupplementItem("electrolytes", "Электролиты"),
            SupplementItem("mineral_electrolyte_complex", "Минерально-электролитный комплекс"),
            SupplementItem("hydration_mix", "Спортивная смесь для гидратации"),
        ),
    ),
    SupplementCategory(
        "connective_tissue",
        "Коллаген и соединительная ткань",
        (
            SupplementItem("collagen", "Коллаген"),
            SupplementItem("gelatin", "Желатин"),
            SupplementItem("collagen_complex", "Комплекс коллагена"),
            SupplementItem("glucosamine_chondroitin", "Глюкозамин и хондроитин"),
        ),
    ),
    SupplementCategory(
        "digestion",
        "Пищеварительные добавки",
        (
            SupplementItem("fiber", "Клетчатка"),
            SupplementItem("prebiotics", "Пребиотики"),
            SupplementItem("probiotics", "Пробиотики"),
            SupplementItem("digestive_enzymes", "Пищевой ферментный комплекс"),
        ),
    ),
    SupplementCategory(
        "other",
        "Другие добавки",
        (
            SupplementItem("coenzyme_q10", "Коэнзим Q10"),
            SupplementItem("botanical_complex", "Растительный пищевой комплекс"),
            SupplementItem("greens_superfood", "Greens / superfood-комплекс"),
            SupplementItem("antioxidant_complex", "Антиоксидантный пищевой комплекс"),
            SupplementItem("adaptogen_complex", "Растительный адаптогенный комплекс"),
            SupplementItem("other_supplement", "Другая пищевая/спортивная добавка"),
        ),
    ),
)


SUPPLEMENT_CATEGORIES_BY_ID = {
    category.identifier: category for category in SUPPLEMENT_CATEGORIES
}
SUPPLEMENTS_BY_ID = {
    item.identifier: item
    for category in SUPPLEMENT_CATEGORIES
    for item in category.items
}


def _normalize_catalog_name(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", str(value or ""))
    normalized = normalized.replace("ё", "е").replace("Ё", "Е")
    normalized = normalized.replace("‑", "-").replace("–", "-").replace("—", "-")
    return re.sub(r"\s+", " ", normalized).strip().casefold()


_SUPPLEMENT_ID_BY_LEGACY_NAME = {
    _normalize_catalog_name(alias): item.identifier
    for item in SUPPLEMENTS_BY_ID.values()
    for alias in (item.display_name, *item.legacy_aliases)
}


def get_supplement_item(identifier: str | None) -> SupplementItem | None:
    """Return a catalog item by its stable identifier."""
    return SUPPLEMENTS_BY_ID.get(str(identifier or ""))


def get_supplement_category(identifier: str | None) -> SupplementCategory | None:
    """Return a catalog category by its stable identifier."""
    return SUPPLEMENT_CATEGORIES_BY_ID.get(str(identifier or ""))


def resolve_supplement_identifier(stored_value: str | None) -> str | None:
    """Resolve an identifier from a new value or a recognized legacy name."""
    value = str(stored_value or "").strip()
    if value in SUPPLEMENTS_BY_ID:
        return value
    return _SUPPLEMENT_ID_BY_LEGACY_NAME.get(_normalize_catalog_name(value))


def get_supplement_display_name(stored_value: str | None) -> str:
    """Display catalog names centrally while preserving unknown legacy records."""
    value = str(stored_value or "").strip()
    identifier = resolve_supplement_identifier(value)
    if identifier:
        return SUPPLEMENTS_BY_ID[identifier].display_name
    return value or "Добавка"

