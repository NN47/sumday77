from utils.keyboards import kbju_add_menu
from utils.admin_formatters import format_openrouter


def _reply_keyboard_texts(markup) -> list[str]:
    return [button.text for row in markup.keyboard for button in row]


def test_kbju_add_menu_has_openrouter_button():
    texts = _reply_keyboard_texts(kbju_add_menu)
    assert "🧪 Ввести текст через OpenRouter" in texts


def test_openrouter_formatter_shows_free_model():
    text = format_openrouter({"model_name": "openrouter/free", "tariff": "free"})
    assert "openrouter/free" in text
    assert "free" in text
