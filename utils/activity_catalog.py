"""Единый справочник поминутных активностей и тренировочных упражнений.

Отображаемые названия не используются как идентификаторы. Значения MET взяты
из близких по описанию позиций 2024 Adult Compendium и намеренно хранятся
централизованно, чтобы обработчики не содержали физиологических коэффициентов.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from datetime import date


COMPENDIUM_SOURCE_NAME = "2024 Adult Compendium of Physical Activities"
COMPENDIUM_SOURCE_VERSION = "2024"
COMPENDIUM_SOURCE_DATE = date(2024, 1, 1)
COMPENDIUM_SOURCE_URL = (
    "https://pacompendium.com/wp-content/uploads/2024/01/"
    "2024-adult-compendium_1_2024.pdf"
)
CALCULATION_VERSION = "met-net-v1"


class IntensityLevel(StrEnum):
    LIGHT = "light"
    MODERATE = "moderate"
    HIGH = "high"


INTENSITY_LABELS = {
    IntensityLevel.LIGHT.value: "🙂 Лёгкая",
    IntensityLevel.MODERATE.value: "💪 Средняя",
    IntensityLevel.HIGH.value: "🔥 Высокая",
}

WORKOUT_INTENSITY_LABELS = {
    IntensityLevel.LIGHT.value: "🙂 Спокойно",
    IntensityLevel.MODERATE.value: "💪 Обычно",
    IntensityLevel.HIGH.value: "🔥 Интенсивно",
}

WORKOUT_INTENSITY_METS = {
    IntensityLevel.LIGHT.value: 3.5,
    IntensityLevel.MODERATE.value: 5.0,
    IntensityLevel.HIGH.value: 6.0,
}


@dataclass(frozen=True)
class CatalogCategory:
    code: str
    name: str
    icon: str
    kind: str
    sort_order: int


@dataclass(frozen=True)
class TimedActivityConfig:
    code: str
    name: str
    category_code: str
    mets: tuple[tuple[str, float], ...]
    emoji: str = ""
    cadence_steps_per_minute: float | None = None

    @property
    def intensity_mets(self) -> dict[str, float]:
        return dict(self.mets)


@dataclass(frozen=True)
class ExerciseConfig:
    code: str
    name: str
    category_code: str
    measurement_type: str
    load_input_mode: str = "none"
    tempo_seconds_per_rep: float = 3.0


TIMED_CATEGORIES: tuple[CatalogCategory, ...] = (
    CatalogCategory("walking_running", "Ходьба и бег", "🏃", "timed", 10),
    CatalogCategory("cycling_cardio", "Велосипед и кардиотренажёры", "🚴", "timed", 20),
    CatalogCategory("water", "Плавание и водные занятия", "🏊", "timed", 30),
    CatalogCategory("team_sports", "Командные виды спорта", "⚽", "timed", 40),
    CatalogCategory("racket_sports", "Ракетки", "🎾", "timed", 50),
    CatalogCategory("martial_arts", "Единоборства", "🥊", "timed", 60),
    CatalogCategory("dance_group", "Танцы и групповые занятия", "💃", "timed", 70),
    CatalogCategory("mobility_recovery", "Гибкость и восстановление", "🧘", "timed", 80),
    CatalogCategory("winter", "Зимние занятия", "⛷", "timed", 90),
    CatalogCategory("outdoors", "На свежем воздухе", "🌲", "timed", 100),
    CatalogCategory("home_garden", "Дом и участок", "🏡", "timed", 110),
)

EXERCISE_CATEGORIES: tuple[CatalogCategory, ...] = (
    CatalogCategory("bodyweight", "Собственный вес", "💪", "exercise", 10),
    CatalogCategory("free_weights", "Свободные веса", "🏋️", "exercise", 20),
    CatalogCategory("machines", "Тренажёры", "⚙️", "exercise", 30),
    CatalogCategory("cables", "Блочные тренажёры", "🔗", "exercise", 40),
    CatalogCategory("functional", "Функциональные упражнения", "🔥", "exercise", 50),
)


def _m(light: float, moderate: float | None = None, high: float | None = None) -> tuple[tuple[str, float], ...]:
    if moderate is None:
        return ((IntensityLevel.MODERATE.value, light),)
    return (
        (IntensityLevel.LIGHT.value, light),
        (IntensityLevel.MODERATE.value, moderate),
        (IntensityLevel.HIGH.value, high if high is not None else moderate),
    )


TIMED_ACTIVITIES: tuple[TimedActivityConfig, ...] = (
    # Ходьба и бег
    TimedActivityConfig("walking_leisure", "Прогулка", "walking_running", _m(2.3, 3.0, 3.8), "🚶", 80),
    TimedActivityConfig("walking_easy", "Ходьба в спокойном темпе", "walking_running", _m(2.5), "🚶", 75),
    TimedActivityConfig("walking_brisk", "Быстрая ходьба", "walking_running", _m(3.8, 4.8, 5.5), "🚶", 110),
    TimedActivityConfig("race_walking", "Спортивная ходьба", "walking_running", _m(6.5, 8.5, 10.5), "🚶", 130),
    TimedActivityConfig("nordic_walking", "Скандинавская ходьба", "walking_running", _m(4.8, 6.0, 7.0), "🥢", 105),
    TimedActivityConfig("walking_uphill", "Ходьба в гору", "walking_running", _m(4.0, 6.0, 8.0), "⛰", 95),
    TimedActivityConfig("stair_climbing", "Ходьба по лестнице", "walking_running", _m(4.5, 6.8, 9.3), "🪜", 90),
    TimedActivityConfig("running_general", "Бег", "walking_running", _m(6.0, 8.5, 11.5), "🏃", 165),
    TimedActivityConfig("trail_running", "Бег по пересечённой местности", "walking_running", _m(7.0, 9.0, 11.5), "🏃", 160),
    TimedActivityConfig("treadmill_running", "Беговая дорожка", "walking_running", _m(6.0, 8.3, 11.0), "🏃", 165),
    # Велосипед и кардио
    TimedActivityConfig("cycling_general", "Велосипед", "cycling_cardio", _m(4.0, 6.8, 10.0), "🚴"),
    TimedActivityConfig("mountain_biking", "Горный велосипед", "cycling_cardio", _m(5.8, 8.5, 12.0), "🚵"),
    TimedActivityConfig("stationary_cycling", "Велотренажёр", "cycling_cardio", _m(3.5, 6.8, 10.5), "🚲"),
    TimedActivityConfig("elliptical", "Эллиптический тренажёр", "cycling_cardio", _m(4.0, 5.0, 8.0), "🏃"),
    TimedActivityConfig("rowing_machine", "Гребной тренажёр", "cycling_cardio", _m(4.8, 7.0, 12.0), "🚣"),
    TimedActivityConfig("stair_stepper", "Степпер", "cycling_cardio", _m(4.0, 6.0, 9.0), "🪜"),
    TimedActivityConfig("aerobics", "Аэробика", "cycling_cardio", _m(5.0, 7.3, 9.5), "🤸"),
    TimedActivityConfig("step_aerobics", "Степ-аэробика", "cycling_cardio", _m(5.5, 7.5, 10.0), "🤸"),
    TimedActivityConfig("jump_rope", "Скакалка", "cycling_cardio", _m(8.3, 11.8, 12.3), "🪢"),
    # Вода
    TimedActivityConfig("swimming_general", "Плавание", "water", _m(4.8, 6.0, 8.3), "🏊"),
    TimedActivityConfig("swimming_freestyle", "Плавание вольным стилем", "water", _m(5.8, 8.3, 10.0), "🏊"),
    TimedActivityConfig("swimming_breaststroke", "Брасс", "water", _m(5.3, 8.3, 10.3), "🏊"),
    TimedActivityConfig("swimming_backstroke", "Плавание на спине", "water", _m(4.8, 7.0, 9.5), "🏊"),
    TimedActivityConfig("swimming_butterfly", "Баттерфляй", "water", _m(8.0, 11.0, 13.8), "🏊"),
    TimedActivityConfig("water_aerobics", "Аквааэробика", "water", _m(3.5, 5.5, 7.5), "💧"),
    TimedActivityConfig("water_polo", "Водное поло", "water", _m(8.0, 10.0, 12.0), "🤽"),
    TimedActivityConfig("rowing", "Гребля", "water", _m(3.5, 6.0, 10.0), "🚣"),
    TimedActivityConfig("kayaking", "Каякинг", "water", _m(3.5, 5.0, 8.0), "🛶"),
    TimedActivityConfig("canoeing", "Каноэ", "water", _m(3.5, 5.8, 9.0), "🛶"),
    TimedActivityConfig("sup_boarding", "Сапбординг", "water", _m(2.8, 6.5, 11.0), "🏄"),
    TimedActivityConfig("surfing", "Сёрфинг", "water", _m(3.0, 5.0, 8.0), "🏄"),
    TimedActivityConfig("scuba_diving", "Дайвинг", "water", _m(5.0, 7.0, 9.0), "🤿"),
    # Командные виды спорта
    TimedActivityConfig("football", "Футбол", "team_sports", _m(5.0, 7.0, 10.0), "⚽"),
    TimedActivityConfig("futsal", "Мини-футбол", "team_sports", _m(6.0, 8.0, 10.0), "⚽"),
    TimedActivityConfig("basketball", "Баскетбол", "team_sports", _m(4.5, 6.5, 8.0), "🏀"),
    TimedActivityConfig("volleyball", "Волейбол", "team_sports", _m(3.0, 4.0, 6.0), "🏐"),
    TimedActivityConfig("beach_volleyball", "Пляжный волейбол", "team_sports", _m(5.0, 8.0, 10.0), "🏐"),
    TimedActivityConfig("handball", "Гандбол", "team_sports", _m(6.0, 8.0, 10.0), "🤾"),
    TimedActivityConfig("ice_hockey", "Хоккей", "team_sports", _m(6.0, 8.0, 10.0), "🏒"),
    TimedActivityConfig("floorball", "Флорбол", "team_sports", _m(5.0, 7.5, 9.5), "🏑"),
    TimedActivityConfig("rugby", "Регби", "team_sports", _m(6.0, 8.3, 10.0), "🏉"),
    TimedActivityConfig("american_football", "Американский футбол", "team_sports", _m(5.0, 7.0, 9.0), "🏈"),
    TimedActivityConfig("baseball", "Бейсбол", "team_sports", _m(3.5, 5.0, 6.0), "⚾"),
    TimedActivityConfig("softball", "Софтбол", "team_sports", _m(3.5, 5.0, 6.0), "🥎"),
    TimedActivityConfig("cricket", "Крикет", "team_sports", _m(4.0, 5.0, 7.0), "🏏"),
    TimedActivityConfig("ultimate_frisbee", "Алтимат-фрисби", "team_sports", _m(5.0, 8.0, 10.0), "🥏"),
    # Ракетки
    TimedActivityConfig("tennis", "Большой теннис", "racket_sports", _m(5.0, 7.3, 9.0), "🎾"),
    TimedActivityConfig("table_tennis", "Настольный теннис", "racket_sports", _m(3.5, 4.0, 5.5), "🏓"),
    TimedActivityConfig("badminton", "Бадминтон", "racket_sports", _m(4.5, 5.5, 7.0), "🏸"),
    TimedActivityConfig("squash", "Сквош", "racket_sports", _m(6.0, 7.3, 10.0), "🎾"),
    TimedActivityConfig("padel", "Падел", "racket_sports", _m(4.0, 6.0, 8.0), "🎾"),
    TimedActivityConfig("pickleball", "Пиклбол", "racket_sports", _m(3.5, 5.0, 7.0), "🏓"),
    TimedActivityConfig("racquetball", "Ракетбол", "racket_sports", _m(5.0, 7.0, 10.0), "🎾"),
    # Единоборства
    TimedActivityConfig("boxing", "Бокс", "martial_arts", _m(5.5, 7.8, 12.8), "🥊"),
    TimedActivityConfig("kickboxing", "Кикбоксинг", "martial_arts", _m(6.0, 8.0, 11.0), "🥊"),
    TimedActivityConfig("muay_thai", "Тайский бокс", "martial_arts", _m(6.0, 9.0, 12.0), "🥊"),
    TimedActivityConfig("mma", "Смешанные единоборства", "martial_arts", _m(6.0, 9.0, 12.0), "🥋"),
    TimedActivityConfig("wrestling", "Борьба", "martial_arts", _m(5.0, 7.8, 10.3), "🤼"),
    TimedActivityConfig("judo", "Дзюдо", "martial_arts", _m(5.0, 7.8, 10.3), "🥋"),
    TimedActivityConfig("sambo", "Самбо", "martial_arts", _m(5.0, 7.8, 10.3), "🥋"),
    TimedActivityConfig("karate", "Карате", "martial_arts", _m(4.0, 6.5, 10.3), "🥋"),
    TimedActivityConfig("taekwondo", "Тхэквондо", "martial_arts", _m(5.0, 7.5, 10.3), "🥋"),
    TimedActivityConfig("jiu_jitsu", "Джиу-джитсу", "martial_arts", _m(5.0, 7.8, 10.3), "🥋"),
    TimedActivityConfig("fencing", "Фехтование", "martial_arts", _m(4.0, 6.0, 8.0), "🤺"),
    # Танцы
    TimedActivityConfig("dancing_general", "Танцы", "dance_group", _m(3.0, 5.0, 7.5), "💃"),
    TimedActivityConfig("ballroom_dancing", "Бальные танцы", "dance_group", _m(3.0, 4.8, 6.5), "💃"),
    TimedActivityConfig("latin_dancing", "Латиноамериканские танцы", "dance_group", _m(4.0, 5.5, 7.5), "💃"),
    TimedActivityConfig("hip_hop_dancing", "Хип-хоп", "dance_group", _m(4.0, 6.0, 8.0), "🕺"),
    TimedActivityConfig("zumba", "Зумба", "dance_group", _m(4.5, 6.5, 8.5), "💃"),
    TimedActivityConfig("ballet", "Балет", "dance_group", _m(4.0, 5.5, 7.0), "🩰"),
    TimedActivityConfig("dance_aerobics", "Танцевальная аэробика", "dance_group", _m(5.0, 7.0, 9.0), "💃"),
    TimedActivityConfig("pole_dance", "Занятия на пилоне", "dance_group", _m(4.0, 5.5, 7.5), "🤸"),
    # Восстановление
    TimedActivityConfig("yoga", "Йога", "mobility_recovery", _m(2.3), "🧘"),
    TimedActivityConfig("power_yoga", "Силовая йога", "mobility_recovery", _m(4.0), "🧘"),
    TimedActivityConfig("pilates", "Пилатес", "mobility_recovery", _m(2.8, 3.5, 4.5), "🤸"),
    TimedActivityConfig("stretching", "Растяжка", "mobility_recovery", _m(2.3), "🤸"),
    TimedActivityConfig("joint_gymnastics", "Суставная гимнастика", "mobility_recovery", _m(2.5), "🤸"),
    TimedActivityConfig("mobility", "Упражнения на подвижность", "mobility_recovery", _m(2.5, 3.0, 4.0), "🤸"),
    TimedActivityConfig("tai_chi", "Тайцзицюань", "mobility_recovery", _m(3.0), "🧘"),
    TimedActivityConfig("qigong", "Цигун", "mobility_recovery", _m(2.5), "🧘"),
    # Зима
    TimedActivityConfig("cross_country_skiing", "Беговые лыжи", "winter", _m(6.8, 9.0, 12.5), "⛷"),
    TimedActivityConfig("downhill_skiing", "Горные лыжи", "winter", _m(4.3, 6.0, 8.0), "⛷"),
    TimedActivityConfig("snowboarding", "Сноуборд", "winter", _m(4.3, 6.0, 8.0), "🏂"),
    TimedActivityConfig("ice_skating", "Коньки", "winter", _m(5.0, 7.0, 10.0), "⛸"),
    TimedActivityConfig("snowshoeing", "Ходьба на снегоступах", "winter", _m(5.3, 7.5, 10.0), "🥾"),
    TimedActivityConfig("sledding", "Катание на санках", "winter", _m(4.0, 5.3, 7.0), "🛷"),
    TimedActivityConfig("snow_shoveling", "Уборка снега", "winter", _m(5.0, 6.0, 7.5), "❄️"),
    # На свежем воздухе
    TimedActivityConfig("hiking", "Пеший поход", "outdoors", _m(4.0, 6.0, 8.0), "🥾", 95),
    TimedActivityConfig("mountain_hiking", "Горный поход", "outdoors", _m(5.0, 7.0, 9.0), "⛰", 90),
    TimedActivityConfig("mountaineering", "Альпинизм", "outdoors", _m(6.0, 8.0, 10.0), "🧗"),
    TimedActivityConfig("rock_climbing", "Скалолазание", "outdoors", _m(5.0, 7.5, 10.0), "🧗"),
    TimedActivityConfig("bouldering", "Боулдеринг", "outdoors", _m(5.0, 8.0, 10.0), "🧗"),
    TimedActivityConfig("orienteering", "Спортивное ориентирование", "outdoors", _m(6.0, 9.0, 11.0), "🧭", 140),
    TimedActivityConfig("roller_skating", "Роликовые коньки", "outdoors", _m(5.0, 7.0, 9.0), "🛼"),
    TimedActivityConfig("skateboarding", "Скейтборд", "outdoors", _m(4.0, 5.0, 7.0), "🛹"),
    TimedActivityConfig("scooter", "Самокат", "outdoors", _m(3.5, 5.0, 7.0), "🛴"),
    TimedActivityConfig("horseback_riding", "Верховая езда", "outdoors", _m(3.5, 5.5, 7.3), "🐎"),
    TimedActivityConfig("active_fishing", "Активная рыбалка", "outdoors", _m(2.5, 3.5, 5.0), "🎣"),
    TimedActivityConfig("hunting", "Охота", "outdoors", _m(3.0, 5.0, 7.0), "🌲"),
    TimedActivityConfig("frisbee", "Фрисби", "outdoors", _m(3.0, 4.0, 6.0), "🥏"),
    # Дом и участок
    TimedActivityConfig("house_cleaning_light", "Лёгкая уборка", "home_garden", _m(2.3), "🧹"),
    TimedActivityConfig("house_cleaning_general", "Обычная уборка", "home_garden", _m(3.3), "🧹"),
    TimedActivityConfig("house_cleaning_intense", "Интенсивная уборка", "home_garden", _m(4.5), "🧹"),
    TimedActivityConfig("mopping", "Мытьё полов", "home_garden", _m(3.5), "🧽"),
    TimedActivityConfig("vacuuming", "Работа с пылесосом", "home_garden", _m(3.3), "🧹"),
    TimedActivityConfig("window_cleaning", "Мытьё окон", "home_garden", _m(3.2), "🪟"),
    TimedActivityConfig("cooking", "Приготовление еды", "home_garden", _m(2.0), "🍳"),
    TimedActivityConfig("moving_furniture", "Перестановка мебели", "home_garden", _m(5.8), "🛋"),
    TimedActivityConfig("home_repair", "Ремонт", "home_garden", _m(3.0, 4.5, 6.0), "🔨"),
    TimedActivityConfig("car_washing", "Мойка автомобиля", "home_garden", _m(3.5), "🚗"),
    TimedActivityConfig("gardening", "Уход за садом", "home_garden", _m(3.0, 4.0, 5.5), "🌱"),
    TimedActivityConfig("vegetable_gardening", "Работа в огороде", "home_garden", _m(3.5, 4.5, 6.0), "🌱"),
    TimedActivityConfig("digging", "Копание земли", "home_garden", _m(5.0, 6.0, 7.5), "⛏"),
    TimedActivityConfig("lawn_mowing", "Стрижка газона", "home_garden", _m(4.0, 5.5, 6.5), "🌿"),
    TimedActivityConfig("carrying_items", "Перенос вещей", "home_garden", _m(3.5, 5.5, 8.0), "📦"),
    TimedActivityConfig("active_child_play", "Активные игры с детьми", "home_garden", _m(3.5, 5.0, 7.0), "🧒"),
    TimedActivityConfig("dog_walking", "Прогулка с собакой", "home_garden", _m(2.8, 3.5, 4.5), "🐕", 85),
)


def _e(code: str, name: str, category: str, measurement: str, load: str = "none", tempo: float = 3.0) -> ExerciseConfig:
    return ExerciseConfig(code, name, category, measurement, load, tempo)


EXERCISES: tuple[ExerciseConfig, ...] = (
    # Собственный вес
    _e("pushups", "Отжимания", "bodyweight", "repetitions"),
    _e("knee_pushups", "Отжимания с колен", "bodyweight", "repetitions"),
    _e("close_grip_pushups", "Узкие отжимания", "bodyweight", "repetitions"),
    _e("wide_pushups", "Широкие отжимания", "bodyweight", "repetitions"),
    _e("pullups", "Подтягивания", "bodyweight", "repetitions", "optional"),
    _e("chinups", "Подтягивания обратным хватом", "bodyweight", "repetitions", "optional"),
    _e("parallel_bar_dips", "Отжимания на брусьях", "bodyweight", "repetitions", "optional"),
    _e("bodyweight_squats", "Приседания", "bodyweight", "repetitions"),
    _e("lunges", "Выпады", "bodyweight", "repetitions"),
    _e("reverse_lunges", "Обратные выпады", "bodyweight", "repetitions"),
    _e("bulgarian_split_squats", "Болгарские приседания", "bodyweight", "repetitions"),
    _e("glute_bridge", "Ягодичный мост", "bodyweight", "repetitions"),
    _e("calf_raises", "Подъёмы на носки", "bodyweight", "repetitions"),
    _e("crunches", "Скручивания", "bodyweight", "repetitions"),
    _e("reverse_crunches", "Обратные скручивания", "bodyweight", "repetitions"),
    _e("lying_leg_raises", "Подъём ног лёжа", "bodyweight", "repetitions"),
    _e("hanging_leg_raises", "Подъём ног в висе", "bodyweight", "repetitions"),
    _e("burpees", "Берпи", "bodyweight", "repetitions", tempo=4.0),
    _e("mountain_climbers", "Альпинист", "bodyweight", "repetitions", tempo=1.5),
    _e("hyperextensions", "Гиперэкстензия", "bodyweight", "repetitions", "optional"),
    _e("superman", "Упражнение «Супермен»", "bodyweight", "repetitions"),
    _e("plank", "Планка", "bodyweight", "duration"),
    _e("side_plank", "Боковая планка", "bodyweight", "duration"),
    # Свободные веса
    _e("barbell_bench_press", "Жим штанги лёжа", "free_weights", "repetitions_load", "total", 4.0),
    _e("incline_barbell_bench_press", "Жим штанги на наклонной скамье", "free_weights", "repetitions_load", "total", 4.0),
    _e("dumbbell_bench_press", "Жим гантелей лёжа", "free_weights", "repetitions_load", "per_item", 4.0),
    _e("incline_dumbbell_bench_press", "Жим гантелей на наклонной скамье", "free_weights", "repetitions_load", "per_item", 4.0),
    _e("standing_barbell_press", "Жим штанги стоя", "free_weights", "repetitions_load", "total", 4.0),
    _e("seated_dumbbell_press", "Жим гантелей сидя", "free_weights", "repetitions_load", "per_item", 4.0),
    _e("barbell_bent_over_row", "Тяга штанги в наклоне", "free_weights", "repetitions_load", "total", 4.0),
    _e("one_arm_dumbbell_row", "Тяга гантели одной рукой", "free_weights", "repetitions_load", "per_item", 4.0),
    _e("deadlift", "Становая тяга", "free_weights", "repetitions_load", "total", 5.0),
    _e("romanian_deadlift", "Румынская тяга", "free_weights", "repetitions_load", "total", 5.0),
    _e("barbell_back_squat", "Приседания со штангой", "free_weights", "repetitions_load", "total", 5.0),
    _e("front_squat", "Фронтальные приседания", "free_weights", "repetitions_load", "total", 5.0),
    _e("goblet_squat", "Приседания с гантелью", "free_weights", "repetitions_load", "total", 4.0),
    _e("dumbbell_lunges", "Выпады с гантелями", "free_weights", "repetitions_load", "per_item", 4.0),
    _e("dumbbell_bulgarian_split_squat", "Болгарские приседания с гантелями", "free_weights", "repetitions_load", "per_item", 4.0),
    _e("barbell_hip_thrust", "Ягодичный мост со штангой", "free_weights", "repetitions_load", "total", 4.0),
    _e("barbell_biceps_curl", "Подъём штанги на бицепс", "free_weights", "repetitions_load", "total"),
    _e("dumbbell_biceps_curl", "Подъём гантелей на бицепс", "free_weights", "repetitions_load", "per_item"),
    _e("french_press", "Французский жим", "free_weights", "repetitions_load", "total"),
    _e("overhead_dumbbell_triceps_extension", "Разгибание гантели из-за головы", "free_weights", "repetitions_load", "total"),
    _e("dumbbell_fly", "Разведение гантелей лёжа", "free_weights", "repetitions_load", "per_item"),
    _e("dumbbell_lateral_raise", "Подъём гантелей в стороны", "free_weights", "repetitions_load", "per_item"),
    _e("dumbbell_front_raise", "Подъём гантелей перед собой", "free_weights", "repetitions_load", "per_item"),
    _e("bent_over_reverse_fly", "Разведение гантелей в наклоне", "free_weights", "repetitions_load", "per_item"),
    _e("shrugs", "Шраги", "free_weights", "repetitions_load", "total"),
    _e("weighted_calf_raise", "Подъёмы на носки с весом", "free_weights", "repetitions_load", "total"),
    # Тренажёры
    _e("leg_press", "Жим ногами", "machines", "repetitions_load", "total", 4.0),
    _e("leg_extension", "Разгибание ног", "machines", "repetitions_load", "total"),
    _e("leg_curl", "Сгибание ног", "machines", "repetitions_load", "total"),
    _e("hack_squat", "Гакк-приседания", "machines", "repetitions_load", "total", 4.0),
    _e("machine_chest_press", "Жим в тренажёре на грудь", "machines", "repetitions_load", "total"),
    _e("machine_shoulder_press", "Жим в тренажёре на плечи", "machines", "repetitions_load", "total"),
    _e("pec_deck", "Баттерфляй", "machines", "repetitions_load", "total"),
    _e("reverse_pec_deck", "Обратный баттерфляй", "machines", "repetitions_load", "total"),
    _e("machine_seated_row", "Горизонтальная тяга в тренажёре", "machines", "repetitions_load", "total"),
    _e("machine_lat_pulldown", "Вертикальная тяга в тренажёре", "machines", "repetitions_load", "total"),
    _e("hip_adduction_machine", "Сведение ног", "machines", "repetitions_load", "total"),
    _e("hip_abduction_machine", "Разведение ног", "machines", "repetitions_load", "total"),
    _e("machine_calf_raise", "Подъём на носки в тренажёре", "machines", "repetitions_load", "total"),
    _e("smith_machine_exercise", "Тренажёр Смита", "machines", "repetitions_load", "total", 4.0),
    # Блоки
    _e("lat_pulldown", "Тяга верхнего блока", "cables", "repetitions_load", "total"),
    _e("seated_cable_row", "Тяга нижнего блока сидя", "cables", "repetitions_load", "total"),
    _e("triceps_pushdown", "Разгибание рук на блоке", "cables", "repetitions_load", "total"),
    _e("cable_biceps_curl", "Сгибание рук на блоке", "cables", "repetitions_load", "total"),
    _e("cable_crossover", "Кроссовер", "cables", "repetitions_load", "total"),
    _e("cable_fly", "Сведение рук на блоках", "cables", "repetitions_load", "total"),
    _e("face_pull", "Тяга каната к лицу", "cables", "repetitions_load", "total"),
    _e("cable_lateral_raise", "Подъём руки в сторону на блоке", "cables", "repetitions_load", "total"),
    _e("single_arm_cable_extension", "Разгибание руки на блоке", "cables", "repetitions_load", "total"),
    _e("cable_leg_abduction", "Отведение ноги на блоке", "cables", "repetitions_load", "total"),
    _e("cable_crunch", "Скручивания на верхнем блоке", "cables", "repetitions_load", "total"),
    # Функциональные
    _e("kettlebell_swing", "Махи гирей", "functional", "repetitions_load", "total", 2.5),
    _e("kettlebell_snatch", "Рывок гири", "functional", "repetitions_load", "total", 3.0),
    _e("kettlebell_jerk", "Толчок гири", "functional", "repetitions_load", "total", 3.0),
    _e("kettlebell_clean", "Взятие гири на грудь", "functional", "repetitions_load", "total", 3.0),
    _e("farmers_walk", "Фермерская прогулка", "functional", "load_duration_distance", "per_item"),
    _e("sled_push", "Толкание саней", "functional", "load_duration_distance", "total"),
    _e("sled_pull", "Тяга саней", "functional", "load_duration_distance", "total"),
    _e("battle_ropes", "Упражнения с канатами", "functional", "duration"),
    _e("medicine_ball_throws", "Броски медицинского мяча", "functional", "repetitions_load", "total"),
    _e("box_jumps", "Запрыгивания на тумбу", "functional", "repetitions", tempo=4.0),
    _e("suspension_training", "Упражнения с петлями", "functional", "repetitions"),
    _e("turkish_get_up", "Турецкий подъём", "functional", "repetitions_load", "total", 8.0),
)


TIMED_ACTIVITY_BY_CODE = {item.code: item for item in TIMED_ACTIVITIES}
EXERCISE_BY_CODE = {item.code: item for item in EXERCISES}
TIMED_CATEGORY_BY_CODE = {item.code: item for item in TIMED_CATEGORIES}
EXERCISE_CATEGORY_BY_CODE = {item.code: item for item in EXERCISE_CATEGORIES}


def timed_activities_for_category(category_code: str) -> list[TimedActivityConfig]:
    return sorted(
        (item for item in TIMED_ACTIVITIES if item.category_code == category_code),
        key=lambda item: item.name.casefold().replace("ё", "е"),
    )


def exercises_for_category(category_code: str) -> list[ExerciseConfig]:
    return sorted(
        (item for item in EXERCISES if item.category_code == category_code),
        key=lambda item: item.name.casefold().replace("ё", "е"),
    )
