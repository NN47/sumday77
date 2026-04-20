from types import SimpleNamespace

import pytest

from handlers.meals import (
    _apply_product_manual_macros,
    _build_kbju_editor_keyboard,
    _build_kbju_field_editor_keyboard,
    _parse_kbju_bulk_input,
)
from utils.emoji_map import EMOJI_MAP
from utils.meal_formatters import _extract_product_lines


def test_parse_kbju_bulk_input_accepts_four_values():
    assert _parse_kbju_bulk_input("120 14 5 3") == (120.0, 14.0, 5.0, 3.0)
    assert _parse_kbju_bulk_input("120,5 14 5 3") == (120.5, 14.0, 5.0, 3.0)


def test_parse_kbju_bulk_input_rejects_invalid_payload():
    assert _parse_kbju_bulk_input("120 14 5") is None
    assert _parse_kbju_bulk_input("120 14 5 -1") is None


def test_apply_product_manual_macros_updates_per_100g_and_flag():
    product = {
        "name": "Творог",
        "grams": 200,
        "calories": 250,
        "protein_g": 30,
        "fat_total_g": 12,
        "carbohydrates_total_g": 8,
    }

    ok = _apply_product_manual_macros(
        product,
        calories=220,
        protein=34,
        fat=10,
        carbs=6,
    )

    assert ok is True
    assert product["grams"] == 200
    assert product["calories"] == 220
    assert product["protein_g"] == 34
    assert product["fat_total_g"] == 10
    assert product["carbohydrates_total_g"] == 6
    assert product["calories_per_100g"] == pytest.approx(110)
    assert product["is_manually_corrected"] is True


def test_extract_product_lines_contains_manual_correction_label():
    meal = SimpleNamespace(
        products_json='[{"name":"Курица","grams":150,"calories":200,"protein_g":30,"fat_total_g":5,"carbohydrates_total_g":1,"is_manually_corrected":true}]'
    )

    lines = _extract_product_lines(meal)

    assert "✏️ КБЖУ скорректированы вручную" in lines


def test_kbju_editor_uses_shared_carbs_emoji():
    keyboard = _build_kbju_editor_keyboard(0)
    texts = [button.text for row in keyboard.inline_keyboard for button in row]
    assert f"{EMOJI_MAP['carbs']} Углеводы" in texts
    assert "🍞 Углеводы" not in texts


def test_kbju_field_editor_has_expected_steps():
    kcal_keyboard = _build_kbju_field_editor_keyboard(0, "calories")
    kcal_rows = [[button.text for button in row] for row in kcal_keyboard.inline_keyboard]
    assert kcal_rows[0] == ["-100", "-50", "-10"]
    assert kcal_rows[1] == ["+10", "+50", "+100"]

    protein_keyboard = _build_kbju_field_editor_keyboard(0, "protein")
    protein_rows = [[button.text for button in row] for row in protein_keyboard.inline_keyboard]
    assert protein_rows[0] == ["-10", "-5", "-1"]
    assert protein_rows[1] == ["+1", "+5", "+10"]
