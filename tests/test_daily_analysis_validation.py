import asyncio
from datetime import date
from types import SimpleNamespace
from unittest.mock import patch

from handlers.activity import generate_activity_analysis, _is_valid_daily_analysis_text, _sanitize_daily_analysis_text


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


def test_daily_analysis_sanitizer_renames_energy_and_removes_plan_kcal_range():
    text = (
        "<b>🏋️ Тренировки</b>\n"
        "• Тип дня: смешанный\n"
        "• Нагрузка: высокая (кардио и шаги)\n"
        "• Ключевое: хорошая комбинация силовой и ходьбы.\n"
        "• Энергия: ~631 ккал (оценка)\n"
        "• Совет на завтра: восстановление.\n\n"
        "<b>План на завтра</b>\n"
        "1. Постарайся стабилизировать потребление калорий чуть ближе к скорректированной норме, примерно в диапазоне 2300-2400 ккал.\n"
        "2. Продолжай поддерживать белок."
    )

    result = _sanitize_daily_analysis_text(text)

    assert "• Сожжённые калории: ~631 ккал (оценка)" in result
    assert "• Энергия:" not in result
    assert "2300-2400 ккал" not in result
    assert "ближе к норме без привязки к сегодняшним цифрам" in result


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


def test_daily_analysis_uses_previous_weight_older_than_week_in_prompt():
    target_date = date(2026, 6, 15)
    current_weight = SimpleNamespace(id=2, date=target_date, value="76.5")
    older_weight = SimpleNamespace(id=1, date=date(2026, 6, 1), value="77.2")

    def fake_weights_for_range(_user_id, start, end):
        if start == target_date and end == target_date:
            return [current_weight]
        return [current_weight]

    with (
        patch("database.repositories.WorkoutRepository.get_workouts_for_period", return_value=[]),
        patch("database.repositories.WorkoutRepository.get_workouts_for_day", return_value=[]),
        patch("database.repositories.MealRepository.get_kbju_settings", return_value=None),
        patch("database.repositories.MealRepository.get_meals_for_date", return_value=[]),
        patch("database.repositories.WeightRepository.get_weights_for_date_range", side_effect=fake_weights_for_range),
        patch("database.repositories.WeightRepository.get_weights", return_value=[current_weight, older_weight]),
        patch("database.repositories.WaterRepository.get_daily_total", return_value=0),
        patch("database.repositories.SupplementRepository.get_supplements", return_value=[]),
        patch("database.repositories.SupplementRepository.get_entries_for_day", return_value=[]),
        patch("database.repositories.ProcedureRepository.get_procedures_for_day", return_value=[]),
        patch("database.repositories.WellbeingRepository.get_entries_for_period", return_value=[]),
        patch("database.repositories.NoteRepository.get_note_for_date", return_value=None),
        patch("handlers.activity.gemini_service", new=SimpleNamespace(analyze=lambda prompt: prompt)),
    ):
        result = asyncio.run(
            generate_activity_analysis("123", target_date, target_date, "за день")
        )

    assert "Текущий вес: 76.5 кг (от 15.06.2026) (-0.7 кг" in result
    assert "01.06: 77.2 кг" in result
    assert "Изменение относительно предыдущего замера: -0.7 кг" in result
