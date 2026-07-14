import os
import sys
from pathlib import Path

os.environ.setdefault("API_TOKEN", "test-token")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from handlers.meals import _sanitize_meal_comment_html


def test_meal_comment_sanitizer_keeps_only_title_bold_when_model_bolds_everything() -> None:
    raw = "<b>☕ Лёгкий перекус.\n\n80 ккал — небольшая часть дневной нормы. Основные калории остаются на следующие приёмы пищи.</b>"

    text = _sanitize_meal_comment_html(raw)

    assert text == (
        "<b>☕ Лёгкий перекус.</b>\n\n"
        "80 ккал — небольшая часть дневной нормы. Основные калории остаются на следующие приёмы пищи."
    )
    assert text.count("<b>") == 1
    assert text.count("</b>") == 1
    assert text.split("\n", 1)[0].endswith("</b>")


def test_meal_comment_sanitizer_removes_markdown_limits_sentences_and_length() -> None:
    raw = "**🥠 <b>Небольшая пауза с кофе</b>**\n\nПерекус лёгкий. Белка мало. Третье предложение лишнее. " + "слово " * 80

    text = _sanitize_meal_comment_html(raw)

    assert "**" not in text
    assert "__" not in text
    assert len(text) <= 350
    assert text == "<b>🥠 Небольшая пауза с кофе.</b>\n\nПерекус лёгкий. Белка мало."
