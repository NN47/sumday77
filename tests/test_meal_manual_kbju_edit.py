from types import SimpleNamespace

import pytest

from handlers.meals import (
    _apply_product_manual_macros,
    _build_custom_product_value_keyboard,
    _build_kbju_editor_keyboard,
    _build_kbju_field_editor_keyboard,
    _build_product_actions_keyboard,
    _build_weight_editor_keyboard,
    _format_product_macro_summary,
    _render_product_actions_text,
    _render_weight_editor_text,
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
    assert protein_rows[0] == ["-50", "-25", "+25", "+50"]
    assert protein_rows[1] == ["-10", "-5", "+5", "+10"]
    assert protein_rows[2] == ["-1", "-0,5", "+0,5", "+1"]


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


def test_custom_product_calories_editor_has_one_kcal_step():
    keyboard = _build_custom_product_value_keyboard("calories", unit="ккал")
    rows = [[button.text for button in row] for row in keyboard.inline_keyboard]

    assert rows[:2] == [
        ["-100", "-50", "-20", "+20", "+50", "+100"],
        ["-10", "-5", "-1", "+1", "+5", "+10"],
    ]

    callback_rows = [[button.callback_data for button in row] for row in keyboard.inline_keyboard]
    assert callback_rows[0] == [
        "custom_vchg:calories:-100",
        "custom_vchg:calories:-50",
        "custom_vchg:calories:-20",
        "custom_vchg:calories:20",
        "custom_vchg:calories:50",
        "custom_vchg:calories:100",
    ]


def test_custom_product_macro_editors_use_fractional_gram_step():
    keyboard = _build_custom_product_value_keyboard("protein", unit="г")
    rows = [[button.text for button in row] for row in keyboard.inline_keyboard]

    assert rows[:2] == [
        ["-10", "-5", "-1", "+1", "+5", "+10"],
        ["-0,5", "-0,2", "-0,1", "+0,1", "+0,2", "+0,5"],
    ]

    callback_rows = [[button.callback_data for button in row] for row in keyboard.inline_keyboard]
    assert callback_rows[1] == [
        "custom_vchg:protein:-0.5",
        "custom_vchg:protein:-0.2",
        "custom_vchg:protein:-0.1",
        "custom_vchg:protein:0.1",
        "custom_vchg:protein:0.2",
        "custom_vchg:protein:0.5",
    ]


def test_format_product_macro_summary_matches_edit_card_example():
    assert _format_product_macro_summary(112, 1.8, 0, 26.9) == (
        "🔥 <b>Калории:</b> 112 ккал\n"
        "💪 <b>Белки:</b> 1.8 г\n"
        "🥑 <b>Жиры:</b> 0.0 г\n"
        f"{EMOJI_MAP['carbs']} <b>Углеводы:</b> 26.9 г"
    )


def test_product_actions_text_shows_bold_product_label_and_plain_name():
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
    assert "<b>Продукт:</b> Творог\n\n⚖️ <b>Вес:</b> 50 г" in text
    assert "<b>Творог</b>" not in text
    assert "⚖️ <b>Вес:</b> 50 г" in text
    assert "🔥 <b>Калории:</b> 182 ккал" in text
    assert "💪 <b>Белки:</b> 10.0 г" in text
    assert "🥑 <b>Жиры:</b> 8.0 г" in text
    assert f"{EMOJI_MAP['carbs']} <b>Углеводы:</b> 5.0 г" in text


def test_weight_editor_text_bolds_title_and_colon_labels():
    text = _render_weight_editor_text({"name": "Творог", "grams": 500}, draft_weight=450)

    assert "<b>✏️ Изменение веса продукта</b>" in text
    assert "<b>Продукт:</b> Творог" in text
    assert "<b>Продукт:</b> Творог\n\n<b>Текущий вес:</b> 500 г" in text
    assert "<b>Текущий вес:</b> 500 г" in text
    assert "<b>Новый вес:</b> 450 г" in text
    assert "<b>Выбери действие:</b>" in text


def test_product_actions_keyboard_has_change_name_button():
    keyboard = _build_product_actions_keyboard(2)
    rows = [[button.text for button in row] for row in keyboard.inline_keyboard]
    callback_rows = [[button.callback_data for button in row] for row in keyboard.inline_keyboard]

    assert rows[0] == ["✏️ Изменить название"]
    assert callback_rows[0] == ["meal_pact_name:2"]
    assert ["⚖️ Изменить вес"] in rows
    assert ["🧮 Изменить КБЖУ"] in rows


def test_custom_product_value_editor_sends_single_inline_editor():
    from unittest.mock import AsyncMock

    import asyncio

    from handlers.meals import _show_custom_product_value_editor

    message = SimpleNamespace(answer=AsyncMock())
    state = SimpleNamespace(set_state=AsyncMock(), update_data=AsyncMock())

    asyncio.run(_show_custom_product_value_editor(message, state, "amount", 100))

    message.answer.assert_awaited_once()
    _, kwargs = message.answer.await_args
    assert "Сколько продукта" in message.answer.await_args.args[0]
    assert kwargs["parse_mode"] == "HTML"
    assert kwargs["reply_markup"].inline_keyboard[0][0].callback_data == "custom_vchg:amount:-100"
