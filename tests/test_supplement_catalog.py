import os
from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

os.environ.setdefault("API_TOKEN", "test-token")

from database.repositories.supplement_repository import SupplementRepository
from utils.supplement_catalog import (
    SUPPLEMENT_CATEGORIES,
    SUPPLEMENTS_BY_ID,
    get_supplement_display_name,
    resolve_supplement_identifier,
)


def test_catalog_is_broad_and_identifiers_are_unique_and_stable():
    identifiers = [
        item.identifier
        for category in SUPPLEMENT_CATEGORIES
        for item in category.items
    ]

    assert len(identifiers) >= 50
    assert len(identifiers) == len(set(identifiers))
    assert all(identifier.isascii() and identifier.replace("_", "").isalnum() for identifier in identifiers)
    assert {
        "creatine",
        "whey_protein",
        "casein",
        "plant_protein",
        "bcaa",
        "eaa",
        "omega_3",
        "vitamin_d",
        "vitamin_c",
        "vitamin_b_complex",
        "multivitamin",
        "magnesium",
        "zinc",
        "calcium",
        "electrolytes",
        "collagen",
        "fiber",
        "probiotics",
        "other_supplement",
    }.issubset(SUPPLEMENTS_BY_ID)


def test_catalog_maps_identifiers_and_recognized_legacy_names_to_one_display_name():
    assert get_supplement_display_name("magnesium") == "Магний"
    assert resolve_supplement_identifier("Магний") == "magnesium"
    assert resolve_supplement_identifier("Омега‑3") == "omega_3"
    assert get_supplement_display_name("vitamin_d") == "Витамин D"


def test_unknown_legacy_name_remains_readable_but_does_not_become_catalog_item():
    assert resolve_supplement_identifier("Старая пользовательская добавка") is None
    assert get_supplement_display_name("Старая пользовательская добавка") == "Старая пользовательская добавка"


def test_repository_displays_new_identifiers_and_unknown_legacy_names_together():
    supplements = [
        SimpleNamespace(
            id=1,
            name="magnesium",
            times_json="[]",
            days_json="[]",
            duration="постоянно",
            notifications_enabled=True,
        ),
        SimpleNamespace(
            id=2,
            name="Старая пользовательская добавка",
            times_json="[]",
            days_json="[]",
            duration="постоянно",
            notifications_enabled=False,
        ),
    ]
    supplement_query = MagicMock()
    supplement_query.filter_by.return_value.all.return_value = supplements
    entry_query = MagicMock()
    entry_query.filter.return_value.order_by.return_value.all.return_value = []
    session = MagicMock()
    session.query.side_effect = [supplement_query, entry_query]

    with patch(
        "database.repositories.supplement_repository.get_db_session",
        return_value=nullcontext(session),
    ):
        result = SupplementRepository.get_supplements("12345")

    assert result[0]["identifier"] == "magnesium"
    assert result[0]["name"] == "Магний"
    assert result[1]["identifier"] is None
    assert result[1]["name"] == "Старая пользовательская добавка"


def test_repository_stores_catalog_identifier_for_new_record():
    session = MagicMock()
    session.refresh.side_effect = lambda supplement: setattr(supplement, "id", 7)

    with patch(
        "database.repositories.supplement_repository.get_db_session",
        return_value=nullcontext(session),
    ):
        saved_id = SupplementRepository.save_supplement(
            "12345",
            {
                "identifier": "magnesium",
                "name": "Магний",
                "times": [],
                "days": [],
                "duration": "постоянно",
            },
        )

    stored = session.add.call_args.args[0]
    assert saved_id == 7
    assert stored.name == "magnesium"
