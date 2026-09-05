from datetime import date
from types import SimpleNamespace
from unittest.mock import patch

from services.extended_activity_analysis_service import (
    AnalysisPeriod,
    ExtendedActivityAnalysisService,
)


def test_analysis_context_contains_actual_workout_sets_and_repetitions():
    target = date(2026, 9, 4)
    session = SimpleNamespace(
        id=7,
        entry_date=target,
        status="completed",
        duration_seconds=720,
        intensity="moderate",
        gross_calories=55,
    )
    exercise = SimpleNamespace(
        id=11,
        exercise_code="parallel_bar_dips",
        exercise_name_snapshot="Отжимания на брусьях",
    )
    workout_sets = [
        SimpleNamespace(
            session_exercise_id=exercise.id,
            repetitions=20,
            duration_seconds=None,
            distance_meters=None,
            load_kg=None,
            load_kind=None,
        )
        for _ in range(5)
    ]
    empty_energy = SimpleNamespace(gross_calories=55, credited_calories=44)

    with (
        patch("services.extended_activity_analysis_service.MealRepository.get_kbju_settings", return_value=None),
        patch("services.extended_activity_analysis_service.MealRepository.get_meals_for_date", return_value=[]),
        patch("services.extended_activity_analysis_service.WaterRepository.get_daily_total", return_value=0),
        patch("services.extended_activity_analysis_service.ActivityRepository.get_timed_activities_for_period", return_value=[]),
        patch("services.extended_activity_analysis_service.ActivityRepository.get_workout_sessions_for_period", return_value=[session]),
        patch("services.extended_activity_analysis_service.ActivityRepository.get_steps_for_period", return_value=[]),
        patch("services.extended_activity_analysis_service.ActivityRepository.get_session_exercises", return_value=[exercise]),
        patch("services.extended_activity_analysis_service.ActivityRepository.get_session_sets", return_value=workout_sets),
        patch("services.extended_activity_analysis_service.get_daily_activity_energy_summary", return_value=empty_energy),
        patch("services.extended_activity_analysis_service.WeightRepository.get_weights_for_date_range", return_value=[]),
        patch("services.extended_activity_analysis_service.NoteRepository.get_note_for_date", return_value=None),
        patch("services.extended_activity_analysis_service.get_water_recommended", return_value=2000),
    ):
        context = ExtendedActivityAnalysisService().collect_period_context(
            "123",
            AnalysisPeriod(target, target, "за день"),
        )

    workout = context["activity"]["exercises_and_workouts"][0]
    assert workout["exercises"] == ["Отжимания на брусьях"]
    assert workout["exercise_details"] == [{
        "code": "parallel_bar_dips",
        "name": "Отжимания на брусьях",
        "set_count": 5,
        "repetitions": 100,
        "duration_seconds": None,
        "distance_meters": None,
        "loads": [],
    }]
