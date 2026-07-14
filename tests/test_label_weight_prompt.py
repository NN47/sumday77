import os

os.environ.setdefault("API_TOKEN", "test-token")
os.environ.setdefault("GEMINI_API_KEY", "test-gemini-key")

from handlers.meals import (
    _build_label_weight_confirm_menu,
    _build_label_weight_input_menu,
    _format_label_weight_confirmation_text,
    _format_label_weight_prompt,
)
from utils.keyboards import kbju_weight_input_menu


def test_format_label_weight_prompt_bolds_requested_labels() -> None:
    text = _format_label_weight_prompt(
        product_name="SHOCKS! Peanut + Chocolate",
        kcal_100g=454,
        protein_100g=9.7,
        fat_100g=29.6,
        carbs_100g=28.7,
        package_weight=35,
    )

    assert text.startswith("✅ <b>Нашёл КБЖУ на этикетке!</b>")
    assert "📦 <b>Продукт:</b> SHOCKS! Peanut + Chocolate" in text
    assert "📊 <b>КБЖУ на 100 г:</b>" in text
    assert "🔥 <b>Калории:</b> 454 ккал" in text
    assert "🥩 <b>Белки:</b> 9.7 г" in text
    assert "🥑 <b>Жиры:</b> 29.6 г" in text
    assert "🍞 <b>Углеводы:</b> 28.7 г" in text
    assert "📦 <b>В упаковке 35 г, сколько Вы съели?</b>" in text


def test_kbju_weight_input_menu_contains_15_to_85_quick_buttons() -> None:
    button_texts = [
        button.text
        for row in kbju_weight_input_menu.keyboard
        for button in row
    ]

    for value in ["15", "25", "35", "45", "55", "65", "75", "85"]:
        assert value in button_texts


def test_label_weight_input_menu_puts_package_weight_first_without_duplicate() -> None:
    menu = _build_label_weight_input_menu(240)

    rows = [[button.text for button in row] for row in menu.keyboard]

    assert rows[0] == ["240"]
    assert rows[1][:4] == ["10", "15", "20", "25"]
    assert sum(text == "240" for row in rows for text in row) == 1


def test_label_weight_input_menu_does_not_duplicate_standard_package_weight() -> None:
    menu = _build_label_weight_input_menu(250)
    button_texts = [button.text for row in menu.keyboard for button in row]

    assert button_texts.count("250") == 1
    assert menu.keyboard[0][0].text == "10"


def test_label_weight_confirmation_menu_contains_adjustments_save_and_back() -> None:
    rows = [[button.text for button in row] for row in _build_label_weight_confirm_menu().keyboard]

    assert rows[0] == ["+1", "+5", "+10", "+20", "+50", "+100"]
    assert rows[1] == ["-1", "-5", "-10", "-20", "-50", "-100"]
    assert rows[2] == ["✅ Сохранить", "⬅️ Назад"]
    assert len(rows) == 3


def test_format_label_weight_confirmation_text_recalculates_kbju() -> None:
    text = _format_label_weight_confirmation_text(
        {
            "product_name": "Творог",
            "kbju_per_100g": {"kcal": 100, "protein": 10, "fat": 5, "carbs": 3},
        },
        240,
    )

    assert "✅ <b>Вы выбрали:</b> 240 г" in text
    assert "🔥 <b>Калории:</b> 240 ккал" in text
    assert "🥩 <b>Белки:</b> 24.0 г" in text
