from types import SimpleNamespace

from utils.activity_input_config import ActivityInputMethod, get_activity_methods, infer_input_method
from utils.workout_formatters import format_activity_summary


def workout(**kwargs):
    defaults = dict(exercise="Бег", variant=None, count=0, calories=0)
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def test_running_supports_time_and_distance():
    assert get_activity_methods("Бег") == (ActivityInputMethod.TIME, ActivityInputMethod.DISTANCE)


def test_jump_rope_supports_time_and_jumps():
    assert get_activity_methods("Скакалка") == (ActivityInputMethod.TIME, ActivityInputMethod.JUMPS)


def test_single_method_activity_has_no_intermediate_choice_requirement():
    assert get_activity_methods("Отжимания") == (ActivityInputMethod.REPETITIONS,)


def test_two_method_activity_has_choice_requirement():
    assert len(get_activity_methods("Бег")) == 2


def test_infer_legacy_time_record_without_input_method():
    assert infer_input_method("Бег", "Минуты") == ActivityInputMethod.TIME


def test_format_running_by_time():
    entry = workout(input_method="time", duration_minutes=35, count=35, calories=310)
    assert format_activity_summary(entry) == "Бег — 35 мин (~310 ккал)"


def test_format_running_by_distance():
    entry = workout(input_method="distance", distance_km=5, count=5, calories=310)
    assert format_activity_summary(entry) == "Бег — 5 км (~310 ккал)"


def test_format_jump_rope_by_time():
    entry = workout(exercise="Скакалка", input_method="time", duration_minutes=20, count=20, calories=180)
    assert format_activity_summary(entry) == "Скакалка — 20 мин (~180 ккал)"


def test_format_jump_rope_by_jumps():
    entry = workout(exercise="Скакалка", input_method="jumps", jumps_count=1500, count=1500, calories=180)
    assert format_activity_summary(entry) == "Скакалка — 1 500 прыжков (~180 ккал)"


def test_old_record_without_input_method_still_formats():
    entry = workout(exercise="Планка", variant="Минуты", count=3, calories=10)
    assert format_activity_summary(entry) == "Планка — 3 мин (~10 ккал)"
