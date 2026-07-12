"""Централизованная конфигурация способов ввода активности."""
from enum import StrEnum
from dataclasses import dataclass


class ActivityInputMethod(StrEnum):
    TIME = "time"
    DISTANCE = "distance"
    JUMPS = "jumps"
    REPETITIONS = "repetitions"
    STEPS = "steps"


@dataclass(frozen=True)
class ActivityInputConfig:
    activity_id: str
    title: str
    exercise: str
    input_methods: tuple[ActivityInputMethod, ...]


ACTIVITY_INPUT_CONFIG: dict[str, ActivityInputConfig] = {
    "running": ActivityInputConfig("running", "🏃 Бег", "Бег", (ActivityInputMethod.TIME, ActivityInputMethod.DISTANCE)),
    "jump_rope": ActivityInputConfig("jump_rope", "🪢 Скакалка", "Скакалка", (ActivityInputMethod.TIME, ActivityInputMethod.JUMPS)),
    "plank": ActivityInputConfig("plank", "Планка", "Планка", (ActivityInputMethod.TIME,)),
    "yoga": ActivityInputConfig("yoga", "Йога", "Йога", (ActivityInputMethod.TIME,)),
    "sup_boarding": ActivityInputConfig("sup_boarding", "🏄 Сапбординг", "🏄 Сапбординг", (ActivityInputMethod.TIME,)),
    "pushups": ActivityInputConfig("pushups", "Отжимания", "Отжимания", (ActivityInputMethod.REPETITIONS,)),
    "pullups": ActivityInputConfig("pullups", "Подтягивания", "Подтягивания", (ActivityInputMethod.REPETITIONS,)),
    "squats": ActivityInputConfig("squats", "Приседания", "Приседания", (ActivityInputMethod.REPETITIONS,)),
    "abs": ActivityInputConfig("abs", "Пресс", "Пресс", (ActivityInputMethod.REPETITIONS,)),
    "burpee": ActivityInputConfig("burpee", "Берпи", "Берпи", (ActivityInputMethod.REPETITIONS,)),
    "steps": ActivityInputConfig("steps", "Шаги", "Шаги", (ActivityInputMethod.STEPS,)),
}

_EXERCISE_TO_ID = {config.exercise.casefold().replace("ё", "е"): activity_id for activity_id, config in ACTIVITY_INPUT_CONFIG.items()}
_EXERCISE_TO_ID["пробежка"] = "running"


def exercise_key(exercise: str) -> str:
    return (exercise or "").strip().casefold().replace("ё", "е")


def get_activity_config_by_exercise(exercise: str) -> ActivityInputConfig | None:
    activity_id = _EXERCISE_TO_ID.get(exercise_key(exercise))
    return ACTIVITY_INPUT_CONFIG.get(activity_id) if activity_id else None


def get_activity_methods(exercise: str) -> tuple[ActivityInputMethod, ...]:
    config = get_activity_config_by_exercise(exercise)
    return config.input_methods if config else (ActivityInputMethod.REPETITIONS,)


def infer_input_method(exercise: str, variant: str | None = None) -> ActivityInputMethod:
    variant_key = (variant or "").strip().casefold()
    if variant_key in {"мин", "мин.", "минуты", "minutes", "time"}:
        return ActivityInputMethod.TIME
    if variant_key in {"км", "километры", "distance"}:
        return ActivityInputMethod.DISTANCE
    if variant_key in {"прыжки", "jumps"}:
        return ActivityInputMethod.JUMPS
    if variant_key in {"количество шагов", "шаги", "steps"}:
        return ActivityInputMethod.STEPS
    methods = get_activity_methods(exercise)
    return methods[0]
