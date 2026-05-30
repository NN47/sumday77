import os

os.environ.setdefault("API_TOKEN", "test-token")

from handlers.water import build_water_added_text


def test_build_water_added_text_bolds_confirmation_and_labels():
    text = build_water_added_text(300, 1850, 2476, "🟦🟦🟦🟦🟦🟦🟦⬜⬜⬜")

    assert text == (
        "<b>✅ Добавил 300 мл воды</b>\n\n"
        "<b>💧 Всего за сегодня</b>: 1850 мл\n"
        "<b>🎯 Норма</b>: 2476 мл\n"
        "<b>📈 Прогресс</b>: 75%\n"
        "🟦🟦🟦🟦🟦🟦🟦⬜⬜⬜"
    )
