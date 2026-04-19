import asyncio
from datetime import date
from types import SimpleNamespace
from unittest.mock import patch

from handlers.activity import generate_activity_analysis, _is_valid_daily_analysis_text


def test_daily_analysis_validator_accepts_required_format():
    text = (
        "<b>🏋️ Тренировки</b>\n"
        "Выполнено: 8500 шагов и силовая 20 минут. Оценка: нормально. Рекомендация: добавить растяжку 10 минут.\n\n"
        "<b>🍽️ Питание</b>\n"
        "Факт: 1850/2000 ккал, Б 130/140 г, Ж 60/70 г, У 180/210 г. Отклонение: -8%. Рекомендация: добрать 10–15 г белка.\n\n"
        "<b>⚖️ Вес</b>\n"
        "Текущий вес 78.4 кг, к вчера +0.2 кг. Вероятная причина — вода после соли и углеводов, вывод делаем по тренду недели.\n\n"
        "<b>📈 Гипотеза</b>\n"
        "Если удерживать калории в коридоре цели и закрывать белок, то вес будет снижаться стабильнее, потому что дефицит станет предсказуемым.\n\n"
        "<b>Краткий вывод</b>\n"
        "День близок к плану: калории контролируемы, ключевая задача — стабилизировать белок.\n\n"
        "<b>План на завтра</b>\n"
        "1. Пройти 9000 шагов.\n"
        "2. Уложиться в 1900–2100 ккал.\n"
        "3. Добрать минимум 35 г белка до 15:00."
    )
    assert _is_valid_daily_analysis_text(text)


def test_daily_analysis_fallback_used_after_invalid_regeneration():
    target_date = date(2026, 4, 8)
    invalid = "Короткий ответ"

    with (
        patch("database.repositories.WorkoutRepository.get_workouts_for_period", return_value=[]),
        patch("database.repositories.WorkoutRepository.get_workouts_for_day", return_value=[]),
        patch("database.repositories.MealRepository.get_kbju_settings", return_value=None),
        patch("database.repositories.MealRepository.get_meals_for_date", return_value=[]),
        patch("database.repositories.WeightRepository.get_weights_for_date_range", return_value=[]),
        patch("database.repositories.WaterRepository.get_daily_total", return_value=0),
        patch("database.repositories.SupplementRepository.get_supplements", return_value=[]),
        patch("database.repositories.ProcedureRepository.get_procedures_for_day", return_value=[]),
        patch("database.repositories.WellbeingRepository.get_entries_for_period", return_value=[]),
        patch("database.repositories.NoteRepository.get_note_for_date", return_value=None),
        patch(
            "handlers.activity.openrouter_service",
            new=SimpleNamespace(analyze_activity_prompt=lambda _prompt: invalid),
        ),
    ):
        result = asyncio.run(
            generate_activity_analysis("123", target_date, target_date, "за день", backend="openrouter")
        )

    assert "🏋️ Тренировки" in result
    assert "🍽️ Питание" in result
    assert "⚖️ Вес" in result
    assert "📈 Гипотеза" in result
    assert "Краткий вывод" in result
    assert "План на завтра" in result
