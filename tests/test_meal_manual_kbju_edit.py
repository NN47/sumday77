from types import SimpleNamespace

import pytest

from handlers.meals import (
    _apply_product_manual_macros,
    _build_kbju_editor_keyboard,
    _build_kbju_field_editor_keyboard,
    _build_product_actions_keyboard,
    _build_weight_editor_keyboard,
    _render_product_actions_text,
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

    assert any("КБЖУ скорректированы вручную" in line for line in lines)


def test_kbju_editor_uses_shared_carbs_emoji():
    keyboard = _build_kbju_editor_keyboard(0)
    texts = [button.text for row in keyboard.inline_keyboard for button in row]
    assert f"{EMOJI_MAP['carbs']} Углеводы" in texts
    assert "🍞 Углеводы" not in texts
    assert "✅ Сохранить" not in texts


def test_kbju_field_editor_has_expected_steps():
    kcal_keyboard = _build_kbju_field_editor_keyboard(0, "calories")
    kcal_rows = [[button.text for button in row] for row in kcal_keyboard.inline_keyboard]
    assert kcal_rows[0] == ["-100", "-50", "+50", "+100"]
    assert kcal_rows[1] == ["-25", "-10", "+10", "+25"]
    assert kcal_rows[2] == ["-5", "-1", "+1", "+5"]

    protein_keyboard = _build_kbju_field_editor_keyboard(0, "protein")
    protein_rows = [[button.text for button in row] for row in protein_keyboard.inline_keyboard]
    assert protein_rows[0] == ["-100", "-50", "+50", "+100"]
    assert protein_rows[1] == ["-25", "-10", "+10", "+25"]
    assert protein_rows[2] == ["-5", "-1", "+1", "+5"]


def test_weight_editor_uses_smaller_first_two_step_rows():
    keyboard = _build_weight_editor_keyboard(0)
    rows = [[button.text for button in row] for row in keyboard.inline_keyboard]

    assert rows[0] == ["−100 г", "−50 г", "+50 г", "+100 г"]
    assert rows[1] == ["−25 г", "−10 г", "+10 г", "+25 г"]
    assert rows[2] == ["−5 г", "−1 г", "+1 г", "+5 г"]

    callback_rows = [[button.callback_data for button in row] for row in keyboard.inline_keyboard]
    assert callback_rows[0] == [
        "meal_wchg:0:-100",
        "meal_wchg:0:-50",
        "meal_wchg:0:50",
        "meal_wchg:0:100",
    ]
    assert callback_rows[1] == [
        "meal_wchg:0:-25",
        "meal_wchg:0:-10",
        "meal_wchg:0:10",
        "meal_wchg:0:25",
    ]


def test_product_actions_text_has_bold_labels_and_bju_letters():
    text = _render_product_actions_text({
        "name": "Творог",
        "grams": 50,
        "calories": 182,
        "protein_g": 10,
        "fat_total_g": 8,
        "carbohydrates_total_g": 5,
    })

    assert "<b>✏️ Редактирование продукта</b>" in text
    assert "<b>Продукт:</b> Творог" in text
    assert "<b>Вес:</b> 50 г" in text
    assert "💪 Б 10.0 г" in text
    assert "🥑 Ж 8.0 г" in text
    assert f"{EMOJI_MAP['carbs']} У 5.0 г" in text


def test_product_actions_keyboard_has_change_name_button():
    keyboard = _build_product_actions_keyboard(2)
    rows = [[button.text for button in row] for row in keyboard.inline_keyboard]
    callback_rows = [[button.callback_data for button in row] for row in keyboard.inline_keyboard]

    assert rows[0] == ["✏️ Изменить название"]
    assert callback_rows[0] == ["meal_pact_name:2"]
    assert ["⚖️ Изменить вес"] in rows
    assert ["🧮 Исправить КБЖУ"] in rows
