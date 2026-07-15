"""Централизованная конфигурация оборудования для силовых упражнений."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ExerciseEquipmentConfig:
    equipment_type: str
    weight_label: str
    saved_weight_description: str
    weight_input_hint: str = ""


EQUIPMENT_LABELS = {
    "dumbbell": ExerciseEquipmentConfig(
        equipment_type="dumbbell",
        weight_label="Вес одной гантели",
        saved_weight_description="вес одной гантели",
        weight_input_hint=(
            "\n\nУкажи вес одной гантели.\n"
            "Если выполняешь упражнение с двумя гантелями по 15 кг, введи 15 кг."
        ),
    ),
    "barbell": ExerciseEquipmentConfig(
        equipment_type="barbell",
        weight_label="Общий вес штанги",
        saved_weight_description="общий вес штанги",
    ),
    "machine": ExerciseEquipmentConfig(
        equipment_type="machine",
        weight_label="Рабочий вес",
        saved_weight_description="рабочий вес",
    ),
    "bodyweight": ExerciseEquipmentConfig(
        equipment_type="bodyweight",
        weight_label="Рабочий вес",
        saved_weight_description="рабочий вес",
    ),
}


EXERCISE_EQUIPMENT_TYPES = {
    "Армейский жим с гантелями": "dumbbell",
    "Жим гантелей лёжа": "dumbbell",
    "Жим гантелей сидя": "dumbbell",
    "Подъёмы гантелей на бицепс": "dumbbell",
    "Разведения гантелей": "dumbbell",
    "Молот на бицепс": "dumbbell",
    "Приседания со штангой": "barbell",
    "Жим штанги лёжа": "barbell",
    "Тяга штанги в наклоне": "barbell",
    "Тяга верхнего блока": "machine",
    "Тяга горизонтального блока": "machine",
    "Тяга нижнего блока": "machine",
    "Разгибание ног в тренажёре": "machine",
    "Сгибание ног в тренажёре": "machine",
    "Жим ногами": "machine",
}


def get_equipment_type(exercise: str | None) -> str:
    return EXERCISE_EQUIPMENT_TYPES.get((exercise or "").strip(), "machine")


def get_equipment_config(exercise: str | None) -> ExerciseEquipmentConfig:
    return EQUIPMENT_LABELS[get_equipment_type(exercise)]
