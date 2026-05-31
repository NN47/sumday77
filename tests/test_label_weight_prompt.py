import os

os.environ.setdefault("API_TOKEN", "test-token")
os.environ.setdefault("GEMINI_API_KEY", "test-gemini-key")

from handlers.meals import _format_label_weight_prompt
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
    assert "💪 <b>Белки:</b> 9.7 г" in text
    assert "🥑 <b>Жиры:</b> 29.6 г" in text
    assert "🍩 <b>Углеводы:</b> 28.7 г" in text
    assert "📦 <b>В упаковке 35 г, сколько Вы съели?</b>" in text


def test_kbju_weight_input_menu_contains_15_to_85_quick_buttons() -> None:
    button_texts = [
        button.text
        for row in kbju_weight_input_menu.keyboard
        for button in row
    ]

    for value in ["15", "25", "35", "45", "55", "65", "75", "85"]:
        assert value in button_texts
