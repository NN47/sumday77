"""SQLAlchemy модели для базы данных."""
from sqlalchemy.orm import declarative_base
from sqlalchemy import (
    Column,
    Integer,
    String,
    Date,
    Float,
    DateTime,
    Text,
    Boolean,
    Index,
    UniqueConstraint,
    JSON,
    ForeignKey,
    Numeric,
    CheckConstraint,
)
from sqlalchemy.orm import relationship
from datetime import date, datetime, timezone
import json

Base = declarative_base()


class User(Base):
    """Модель пользователя."""
    __tablename__ = "users"
    id = Column(Integer, primary_key=True)
    user_id = Column(String, unique=True, nullable=False, index=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    last_seen_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)
    target_weight = Column(Float, nullable=True)
    timezone = Column(String, default="Europe/Moscow", nullable=False)
    notifications_enabled = Column(Boolean, default=True, nullable=False)
    # NULL means that the one-time 18+ gate has not been completed yet.
    # We intentionally do not store a birth date or an exact age.
    age_verified = Column(Boolean, nullable=True)
    accepted_terms_version = Column(String(40), nullable=True)
    acknowledged_privacy_version = Column(String(40), nullable=True)
    terms_accepted_at = Column(DateTime, nullable=True)
    privacy_acknowledged_at = Column(DateTime, nullable=True)


class EveningAnalysisNotificationState(Base):
    """Состояние вечерних уведомлений ИИ-анализа дня по пользователю."""
    __tablename__ = "evening_analysis_notification_states"

    id = Column(Integer, primary_key=True)
    user_id = Column(String, unique=True, nullable=False, index=True)
    last_evening_notification_date = Column(Date, nullable=True)
    last_daily_analysis_date = Column(Date, nullable=True)
    remind_later_count = Column(Integer, default=0, nullable=False)
    remind_later_date = Column(Date, nullable=True)
    reminder_due_at = Column(DateTime, nullable=True)
    updated_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class Workout(Base):
    """Модель тренировки."""
    __tablename__ = "workouts"
    id = Column(Integer, primary_key=True)
    user_id = Column(String, nullable=False)
    exercise = Column(String, nullable=False)
    variant = Column(String)
    count = Column(Float)
    date = Column(Date, default=date.today)
    calories = Column(Float, default=0)
    input_method = Column(String, nullable=True)
    duration_minutes = Column(Float, nullable=True)
    distance_km = Column(Float, nullable=True)
    jumps_count = Column(Integer, nullable=True)
    working_weight = Column(Float, nullable=True)


class ActivityCategory(Base):
    """Стабильная категория справочника поминутной активности."""

    __tablename__ = "activity_categories"

    id = Column(Integer, primary_key=True)
    code = Column(String(64), nullable=False, unique=True, index=True)
    name = Column(String(120), nullable=False)
    icon = Column(String(16), nullable=True)
    sort_order = Column(Integer, nullable=False, default=0)
    is_active = Column(Boolean, nullable=False, default=True)


class ExerciseCategory(Base):
    """Стабильная категория справочника тренировочных упражнений."""

    __tablename__ = "exercise_categories"

    id = Column(Integer, primary_key=True)
    code = Column(String(64), nullable=False, unique=True, index=True)
    name = Column(String(120), nullable=False)
    icon = Column(String(16), nullable=True)
    sort_order = Column(Integer, nullable=False, default=0)
    is_active = Column(Boolean, nullable=False, default=True)


class TimedActivityDefinition(Base):
    """Версионируемая запись справочника поминутных активностей."""

    __tablename__ = "timed_activity_definitions"

    id = Column(Integer, primary_key=True)
    code = Column(String(64), nullable=False, unique=True, index=True)
    category_code = Column(
        String(64), ForeignKey("activity_categories.code"), nullable=False, index=True,
    )
    name = Column(String(160), nullable=False)
    emoji = Column(String(16), nullable=True)
    intensity_mets_json = Column(Text, nullable=False, default="{}")
    cadence_steps_per_minute = Column(Float, nullable=True)
    source_name = Column(String(160), nullable=False)
    source_version = Column(String(32), nullable=False)
    source_updated_at = Column(Date, nullable=False)
    source_url = Column(String(500), nullable=True)
    sort_order = Column(Integer, nullable=False, default=0)
    is_active = Column(Boolean, nullable=False, default=True)


class ExerciseDefinition(Base):
    """Централизованная конфигурация упражнения и поддерживаемого ввода."""

    __tablename__ = "exercise_definitions"

    id = Column(Integer, primary_key=True)
    code = Column(String(64), nullable=False, unique=True, index=True)
    category_code = Column(
        String(64), ForeignKey("exercise_categories.code"), nullable=False, index=True,
    )
    name = Column(String(160), nullable=False)
    measurement_type = Column(String(32), nullable=False)  # repetitions | repetitions_load | duration | load_duration_distance
    load_input_mode = Column(String(32), nullable=False, default="none")  # none | total | per_item | optional | assistance
    tempo_seconds_per_rep = Column(Float, nullable=False, default=3.0)
    sort_order = Column(Integer, nullable=False, default=0)
    is_active = Column(Boolean, nullable=False, default=True)


class TimedActivityEntry(Base):
    """Поминутная активность со снимком всех исходных данных расчёта."""

    __tablename__ = "timed_activity_entries"
    __table_args__ = (
        CheckConstraint("duration_minutes > 0", name="ck_timed_activity_positive_duration"),
        CheckConstraint("met_value >= 0", name="ck_timed_activity_nonnegative_met"),
        Index("ix_timed_activity_user_date", "user_id", "entry_date"),
    )

    id = Column(Integer, primary_key=True)
    user_id = Column(String, nullable=False, index=True)
    activity_code = Column(String(64), nullable=False, index=True)
    activity_name_snapshot = Column(String(160), nullable=False)
    entry_date = Column(Date, nullable=False, default=date.today, index=True)
    duration_minutes = Column(Float, nullable=False)
    intensity = Column(String(16), nullable=False)
    met_value = Column(Float, nullable=False)
    weight_kg_snapshot = Column(Float, nullable=False)
    weight_source = Column(String(24), nullable=False, default="profile")
    gross_calories = Column(Float, nullable=False)
    credited_calories = Column(Float, nullable=False)
    duration_source = Column(String(24), nullable=False, default="entered")
    calculation_version = Column(String(32), nullable=False)
    source_version = Column(String(32), nullable=False)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow)


class DailySteps(Base):
    """Единое суточное значение шагов, не смешанное с упражнениями."""

    __tablename__ = "daily_steps"
    __table_args__ = (
        UniqueConstraint("user_id", "entry_date", name="uq_daily_steps_user_date"),
        CheckConstraint("steps >= 0", name="ck_daily_steps_nonnegative"),
    )

    id = Column(Integer, primary_key=True)
    user_id = Column(String, nullable=False, index=True)
    entry_date = Column(Date, nullable=False, default=date.today, index=True)
    steps = Column(Integer, nullable=False)
    weight_kg_snapshot = Column(Float, nullable=False)
    weight_source = Column(String(24), nullable=False, default="profile")
    gross_calories = Column(Float, nullable=False)
    credited_calories = Column(Float, nullable=False)
    calculation_version = Column(String(32), nullable=False)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow)


class WorkoutSession(Base):
    """Ручной дневник тренировки; калории считаются за него целиком."""

    __tablename__ = "workout_sessions"
    __table_args__ = (
        Index("ix_workout_sessions_user_date", "user_id", "entry_date"),
        Index("ix_workout_sessions_user_status", "user_id", "status"),
    )

    id = Column(Integer, primary_key=True)
    user_id = Column(String, nullable=False, index=True)
    entry_date = Column(Date, nullable=False, default=date.today, index=True)
    status = Column(String(24), nullable=False, default="draft")  # draft | completed
    # Старые базы требуют эти NOT NULL поля без SQL-default. Оставляем их
    # только для совместимости INSERT; ручной дневник не использует таймер.
    _legacy_session_kind = Column("session_kind", String(24), nullable=False, default="workout", server_default="workout")
    _legacy_paused_seconds = Column("paused_seconds", Integer, nullable=False, default=0, server_default="0")
    duration_seconds = Column(Integer, nullable=True)
    duration_source = Column(String(24), nullable=False, default="estimated")
    intensity = Column(String(16), nullable=True)
    met_value = Column(Float, nullable=True)
    exercise_count = Column(Integer, nullable=False, default=0)
    set_count = Column(Integer, nullable=False, default=0)
    training_volume_kg = Column(Float, nullable=False, default=0)
    weight_kg_snapshot = Column(Float, nullable=False)
    weight_source = Column(String(24), nullable=False, default="profile")
    gross_calories = Column(Float, nullable=False, default=0)
    credited_calories = Column(Float, nullable=False, default=0)
    calculation_version = Column(String(32), nullable=False)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow)


class WorkoutSessionExercise(Base):
    """Упражнение внутри сессии с сохранённым названием и конфигурацией."""

    __tablename__ = "workout_session_exercises"
    __table_args__ = (
        UniqueConstraint("session_id", "position", name="uq_workout_session_exercise_position"),
    )

    id = Column(Integer, primary_key=True)
    user_id = Column(String, nullable=False, index=True)
    session_id = Column(Integer, ForeignKey("workout_sessions.id", ondelete="CASCADE"), nullable=False, index=True)
    exercise_code = Column(String(64), nullable=False, index=True)
    exercise_name_snapshot = Column(String(160), nullable=False)
    measurement_type_snapshot = Column(String(32), nullable=False)
    load_input_mode_snapshot = Column(String(32), nullable=False, default="none")
    tempo_seconds_per_rep_snapshot = Column(Float, nullable=False, default=3.0)
    position = Column(Integer, nullable=False)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)


class WorkoutSet(Base):
    """Отдельный подход упражнения."""

    __tablename__ = "workout_sets"
    __table_args__ = (
        UniqueConstraint("session_exercise_id", "position", name="uq_workout_set_position"),
        CheckConstraint("repetitions IS NULL OR repetitions > 0", name="ck_workout_set_positive_repetitions"),
        CheckConstraint("load_kg IS NULL OR load_kg >= 0", name="ck_workout_set_nonnegative_load"),
        CheckConstraint("duration_seconds IS NULL OR duration_seconds > 0", name="ck_workout_set_positive_duration"),
        CheckConstraint("distance_meters IS NULL OR distance_meters > 0", name="ck_workout_set_positive_distance"),
    )

    id = Column(Integer, primary_key=True)
    user_id = Column(String, nullable=False, index=True)
    session_id = Column(Integer, ForeignKey("workout_sessions.id", ondelete="CASCADE"), nullable=False, index=True)
    session_exercise_id = Column(Integer, ForeignKey("workout_session_exercises.id", ondelete="CASCADE"), nullable=False, index=True)
    position = Column(Integer, nullable=False)
    repetitions = Column(Integer, nullable=True)
    load_kg = Column(Float, nullable=True)
    load_kind = Column(String(24), nullable=True)  # working | additional | assistance
    duration_seconds = Column(Integer, nullable=True)
    distance_meters = Column(Float, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)


class CustomWorkoutExercise(Base):
    """Модель пользовательского упражнения для тренировок."""
    __tablename__ = "custom_workout_exercises"

    id = Column(Integer, primary_key=True)
    user_id = Column(String, nullable=False, index=True)
    name = Column(String, nullable=False)
    category = Column(String, nullable=False)  # bodyweight | weighted


class Weight(Base):
    """Модель веса."""
    __tablename__ = "weights"
    id = Column(Integer, primary_key=True)
    user_id = Column(String, nullable=False)
    value = Column(String, nullable=False)
    date = Column(Date, default=date.today)


class Measurement(Base):
    """Модель замеров тела."""
    __tablename__ = "measurements"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(String, index=True)
    chest = Column(Float, nullable=True)
    waist = Column(Float, nullable=True)
    hips = Column(Float, nullable=True)
    biceps = Column(Float, nullable=True)
    thigh = Column(Float, nullable=True)
    date = Column(Date, default=date.today)


class Meal(Base):
    """Модель приёма пищи."""
    __tablename__ = "meals"
    __table_args__ = (Index("uq_meals_save_token", "save_token", unique=True),)

    id = Column(Integer, primary_key=True)
    user_id = Column(String, nullable=False)
    description = Column(String, nullable=True)
    raw_query = Column(String)
    products_json = Column(Text, default="[]")
    api_details = Column(Text, nullable=True)
    calories = Column(Float, default=0)
    protein = Column(Float, default=0)
    fat = Column(Float, default=0)
    carbs = Column(Float, default=0)
    is_manually_corrected = Column(Boolean, default=False, nullable=False)
    meal_type = Column(String, nullable=False, default="snack", index=True)
    date = Column(Date, default=date.today)
    save_token = Column(String(64), nullable=True)
    # ``Meal`` is a diary entry, not a reusable recipe.  Legacy entries contain
    # ordinary product snapshots; dish entries additionally point at the
    # reusable template they were created from while keeping their own snapshot.
    entry_kind = Column(String, nullable=False, default="products", index=True)
    dish_id = Column(Integer, ForeignKey("dishes.id", ondelete="SET NULL"), nullable=True, index=True)
    dish_name_snapshot = Column(String(80), nullable=True)
    entry_source = Column(String, nullable=True, index=True)


class Dish(Base):
    """Reusable user-owned dish template.

    Nutrition is intentionally derived from ``DishIngredient`` rows.  Diary
    history never reads current ingredient rows as its source of truth.
    """

    __tablename__ = "dishes"
    __table_args__ = (
        Index("ix_dishes_user_normalized_name", "user_id", "normalized_name"),
        Index("uq_dishes_save_token", "save_token", unique=True),
    )

    id = Column(Integer, primary_key=True)
    user_id = Column(String, nullable=False, index=True)
    name = Column(String(80), nullable=False)
    normalized_name = Column(String(80), nullable=False)
    source = Column(String, nullable=False, default="photo_analysis", index=True)
    source_provider = Column(String, nullable=True)
    composition_fingerprint = Column(String(64), nullable=True, index=True)
    save_token = Column(String(64), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    archived_at = Column(DateTime, nullable=True, index=True)

    ingredients = relationship(
        "DishIngredient",
        back_populates="dish",
        cascade="all, delete-orphan",
        order_by="DishIngredient.position",
        lazy="selectin",
    )


class DishIngredient(Base):
    """Nutrition snapshot belonging to a reusable dish template."""

    __tablename__ = "dish_ingredients"
    __table_args__ = (
        UniqueConstraint("dish_id", "position", name="uq_dish_ingredients_position"),
        CheckConstraint("weight_g > 0", name="ck_dish_ingredients_positive_weight"),
        CheckConstraint("calories_per_100g >= 0", name="ck_dish_ingredients_nonnegative_calories"),
        CheckConstraint("protein_per_100g >= 0", name="ck_dish_ingredients_nonnegative_protein"),
        CheckConstraint("fat_per_100g >= 0", name="ck_dish_ingredients_nonnegative_fat"),
        CheckConstraint("carbs_per_100g >= 0", name="ck_dish_ingredients_nonnegative_carbs"),
    )

    id = Column(Integer, primary_key=True)
    dish_id = Column(Integer, ForeignKey("dishes.id", ondelete="CASCADE"), nullable=False, index=True)
    position = Column(Integer, nullable=False)
    name_snapshot = Column(String(160), nullable=False)
    weight_g = Column(Numeric(12, 4), nullable=False)
    calories_per_100g = Column(Numeric(12, 4), nullable=False, default=0)
    protein_per_100g = Column(Numeric(12, 4), nullable=False, default=0)
    fat_per_100g = Column(Numeric(12, 4), nullable=False, default=0)
    carbs_per_100g = Column(Numeric(12, 4), nullable=False, default=0)
    is_manually_corrected = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    dish = relationship("Dish", back_populates="ingredients")


class SavedProduct(Base):
    """Постоянные справочные данные продукта пользователя.

    История фактических порций по-прежнему хранится в ``Meal.products_json``.
    Эта сущность отделяет последнюю порцию от неизменяемых характеристик
    единицы и упаковки.
    """

    __tablename__ = "saved_products"
    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "normalized_name",
            name="uq_saved_products_user_normalized_name",
        ),
    )

    id = Column(Integer, primary_key=True)
    user_id = Column(String, nullable=False, index=True)
    normalized_name = Column(String, nullable=False)
    name = Column(String, nullable=False)
    last_weight_g = Column(Float, nullable=True)
    unit_weight_g = Column(Float, nullable=True)
    unit_name = Column(String, nullable=True)
    package_weight_g = Column(Float, nullable=True)
    package_units = Column(Integer, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class MealCompletionComment(Base):
    """Короткий AI-комментарий к завершённому приёму пищи."""
    __tablename__ = "meal_completion_comments"

    id = Column(Integer, primary_key=True)
    user_id = Column(String, nullable=False, index=True)
    meal_id = Column(Integer, nullable=False, unique=True, index=True)
    date = Column(Date, nullable=False, index=True)
    meal_type = Column(String, nullable=False, index=True)
    comment_text = Column(Text, nullable=True)
    model = Column(String, nullable=True)
    input_tokens = Column(Integer, nullable=True)
    output_tokens = Column(Integer, nullable=True)
    total_tokens = Column(Integer, nullable=True)
    estimated_cost_usd = Column(Float, nullable=True)
    status = Column(String, nullable=False, default="success", index=True)
    error_message = Column(Text, nullable=True)
    quota_request_id = Column(String(160), nullable=True, index=True)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)


class KbjuSettings(Base):
    """Модель настроек КБЖУ."""
    __tablename__ = "kbju_settings"
    id = Column(Integer, primary_key=True)
    user_id = Column(String, nullable=False, unique=True, index=True)
    calories = Column(Float, nullable=False)
    protein = Column(Float, nullable=False)
    fat = Column(Float, nullable=False)
    carbs = Column(Float, nullable=False)
    goal = Column(String, nullable=True)  # "loss" / "maintain" / "gain"
    activity = Column(String, nullable=True)  # "low" / "medium" / "high"
    gender = Column(String, nullable=True)  # "male" / "female"
    updated_at = Column(DateTime, default=datetime.utcnow)


class Supplement(Base):
    """Модель добавки."""
    __tablename__ = "supplements"
    id = Column(Integer, primary_key=True)
    user_id = Column(String, nullable=False, index=True)
    name = Column(String, nullable=False)
    times_json = Column(Text, default="[]")
    days_json = Column(Text, default="[]")
    duration = Column(String, default="постоянно")
    notifications_enabled = Column(Boolean, default=True, nullable=True)


class SupplementNotificationState(Base):
    """Состояние отложенного уведомления о приёме добавки."""
    __tablename__ = "supplement_notification_states"

    id = Column(Integer, primary_key=True)
    user_id = Column(String, nullable=False, index=True)
    supplement_id = Column(Integer, nullable=False, index=True)
    scheduled_time = Column(String, nullable=False)
    target_date = Column(Date, nullable=False)
    reminder_due_at = Column(DateTime, nullable=False, index=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    __table_args__ = (
        UniqueConstraint("user_id", "supplement_id", name="uq_supplement_notification_user_supplement"),
    )


class SupplementEntry(Base):
    """Модель записи приёма добавки."""
    __tablename__ = "supplement_entries"
    id = Column(Integer, primary_key=True)
    user_id = Column(String, nullable=False, index=True)
    supplement_id = Column(Integer, nullable=False)
    timestamp = Column(DateTime, nullable=False)
    amount = Column(Float, nullable=True)


class Procedure(Base):
    """Модель процедуры."""
    __tablename__ = "procedures"
    id = Column(Integer, primary_key=True)
    user_id = Column(String, nullable=False, index=True)
    name = Column(String, nullable=False)
    date = Column(Date, default=date.today)
    notes = Column(String, nullable=True)


class WaterEntry(Base):
    """Модель записи воды."""
    __tablename__ = "water_entries"
    id = Column(Integer, primary_key=True)
    user_id = Column(String, nullable=False, index=True)
    amount = Column(Float, nullable=False)  # количество воды в мл
    date = Column(Date, default=date.today)
    timestamp = Column(DateTime, default=datetime.utcnow)


class QuickWaterMessage(Base):
    """Актуальное сообщение быстрого добавления воды пользователя."""
    __tablename__ = "quick_water_messages"
    id = Column(Integer, primary_key=True)
    user_id = Column(String, unique=True, nullable=False, index=True)
    chat_id = Column(String, nullable=False)
    message_id = Column(Integer, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class WellbeingEntry(Base):
    """Модель отметки самочувствия."""
    __tablename__ = "wellbeing_entries"
    id = Column(Integer, primary_key=True)
    user_id = Column(String, nullable=False, index=True)
    entry_type = Column(String, nullable=False)
    mood = Column(String, nullable=True)
    influence = Column(String, nullable=True)
    difficulty = Column(String, nullable=True)
    comment = Column(Text, nullable=True)
    date = Column(Date, default=date.today)
    created_at = Column(DateTime, default=datetime.utcnow)


class NoteEntry(Base):
    """Модель дневной заметки состояния."""
    __tablename__ = "notes"
    __table_args__ = (UniqueConstraint("user_id", "date", name="uq_notes_user_date"),)

    id = Column(Integer, primary_key=True)
    user_id = Column(String, nullable=False, index=True)
    date = Column(Date, nullable=False, index=True)
    day_rating = Column(Integer, nullable=False)
    factors_json = Column(Text, default="[]", nullable=False)
    text = Column(String(500), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

    @property
    def factors(self) -> list[str]:
        """Десериализованные факторы дня."""
        return self.deserialize_factors(self.factors_json)

    @staticmethod
    def serialize_factors(factors: list[str]) -> str:
        """Сериализует факторы в JSON-строку."""
        return json.dumps(list(dict.fromkeys(factors or [])), ensure_ascii=False)

    @staticmethod
    def deserialize_factors(payload: str | None) -> list[str]:
        """Десериализует JSON-строку факторов."""
        if not payload:
            return []
        try:
            data = json.loads(payload)
            return [str(item) for item in data if isinstance(item, (str, int, float))]
        except Exception:
            return []


class ActivityAnalysisEntry(Base):
    """Модель сохранённого ИИ-анализа деятельности."""
    __tablename__ = "activity_analysis_entries"

    id = Column(Integer, primary_key=True)
    user_id = Column(String, nullable=False, index=True)
    analysis_text = Column(Text, nullable=False)
    date = Column(Date, default=date.today)
    source = Column(String, nullable=False, default="manual")
    status = Column(String(24), nullable=False, default="success", index=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    analyzed_at = Column(DateTime, nullable=True)
    plan_key = Column(String, nullable=True)
    quota_request_id = Column(String(160), nullable=True, index=True)
    data_snapshot_hash = Column(String(64), nullable=True)


class DailyAnalysisPreparationSession(Base):
    """Устойчивая навигационная сессия preflight анализа дня."""

    __tablename__ = "daily_analysis_preparation_sessions"

    id = Column(Integer, primary_key=True)
    user_id = Column(String, unique=True, nullable=False, index=True)
    target_date = Column(Date, nullable=False, index=True)
    origin = Column(String(32), nullable=False, default="menu")
    active = Column(Boolean, nullable=False, default=True, index=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class UserEvent(Base):
    """События активности пользователей."""
    __tablename__ = "user_events"

    id = Column(Integer, primary_key=True)
    user_id = Column(String, nullable=False, index=True)
    event_name = Column(String, nullable=False, index=True)
    section = Column(String, nullable=True, index=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)


class SupportMessage(Base):
    """Сообщения в поддержку."""
    __tablename__ = "support_messages"

    id = Column(Integer, primary_key=True)
    user_id = Column(String, nullable=False, index=True)
    message_text = Column(Text, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)
    is_read = Column(Boolean, default=False, nullable=False, index=True)


class ErrorLog(Base):
    """Логи ошибок в БД."""
    __tablename__ = "error_logs"

    id = Column(Integer, primary_key=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)
    source = Column(String, nullable=True, index=True)
    error_type = Column(String, nullable=False, index=True)
    message = Column(Text, nullable=True)
    user_id = Column(String, nullable=True, index=True)
    context = Column(String, nullable=True, index=True)
    severity = Column(String, nullable=True, index=True)

    # Backward-compatible поля (старый формат)
    error_message = Column(Text, nullable=True)
    module = Column(String, nullable=True, index=True)
    function_name = Column(String, nullable=True)
    traceback_text = Column(Text, nullable=True)


class AIUsageLog(Base):
    """Универсальный лог usage/tokens/cost для AI-провайдеров."""
    __tablename__ = "ai_usage_logs"

    id = Column(Integer, primary_key=True)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False, index=True)
    user_id = Column(String, nullable=True, index=True)
    provider = Column(String, nullable=False, index=True)  # openai | deepseek
    feature = Column(String, nullable=False, index=True)
    model = Column(String, nullable=False, index=True)
    status = Column(String, nullable=False, index=True)  # success | error
    latency_ms = Column(Integer, nullable=True)
    input_tokens = Column(Integer, nullable=True)
    output_tokens = Column(Integer, nullable=True)
    total_tokens = Column(Integer, nullable=True)
    estimated_cost_usd = Column(Float, nullable=True)
    error_message = Column(Text, nullable=True)
    raw_metadata = Column(JSON, nullable=True)


class UserPlanAssignment(Base):
    """Тариф пользователя; отсутствие строки означает бесплатный тариф."""

    __tablename__ = "user_plan_assignments"

    id = Column(Integer, primary_key=True)
    user_id = Column(String, nullable=False, index=True)
    plan_key = Column(String(40), nullable=False, default="free", index=True)
    status = Column(String(24), nullable=False, default="active", index=True)
    starts_at = Column(DateTime, nullable=True)
    ends_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class AIQuotaCounter(Base):
    """Атомарный счётчик пользовательской квоты за один лимитный день."""

    __tablename__ = "ai_quota_counters"
    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "feature_key",
            "period_key",
            name="uq_ai_quota_counter_user_feature_period",
        ),
    )

    id = Column(Integer, primary_key=True)
    user_id = Column(String, nullable=False, index=True)
    plan_key = Column(String(40), nullable=False, index=True)
    feature_key = Column(String(64), nullable=False, index=True)
    period_key = Column(Date, nullable=False, index=True)
    limit_value = Column(Integer, nullable=False)
    used_count = Column(Integer, nullable=False, default=0)
    reserved_count = Column(Integer, nullable=False, default=0)
    blocked_count = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class AIAttemptCounter(Base):
    """Счётчик отправленных провайдеру попыток по антиспам-группе."""

    __tablename__ = "ai_attempt_counters"
    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "group_key",
            "period_key",
            name="uq_ai_attempt_counter_user_group_period",
        ),
    )

    id = Column(Integer, primary_key=True)
    user_id = Column(String, nullable=False, index=True)
    group_key = Column(String(64), nullable=False, index=True)
    period_key = Column(Date, nullable=False, index=True)
    attempt_limit = Column(Integer, nullable=False)
    attempt_count = Column(Integer, nullable=False, default=0)
    blocked_count = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class AIGlobalDailyCounter(Base):
    """Глобальный аварийный счётчик пользовательских AI-операций."""

    __tablename__ = "ai_global_daily_counters"

    id = Column(Integer, primary_key=True)
    period_key = Column(Date, unique=True, nullable=False, index=True)
    attempt_limit = Column(Integer, nullable=False)
    attempt_count = Column(Integer, nullable=False, default=0)
    blocked_count = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class AIQuotaOperation(Base):
    """Идемпотентная квотная операция: reserve -> consumed/released/expired."""

    __tablename__ = "ai_quota_operations"

    id = Column(Integer, primary_key=True)
    request_id = Column(String(160), unique=True, nullable=False, index=True)
    user_id = Column(String, nullable=False, index=True)
    plan_key = Column(String(40), nullable=False, index=True)
    feature_key = Column(String(64), nullable=False, index=True)
    period_key = Column(Date, nullable=False, index=True)
    status = Column(String(24), nullable=False, default="reserved", index=True)
    outcome = Column(String(64), nullable=True, index=True)
    provider_started = Column(Boolean, nullable=False, default=False)
    provider_attempt_count = Column(Integer, nullable=False, default=0)
    result_ref = Column(String(160), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    expires_at = Column(DateTime, nullable=False, index=True)
    completed_at = Column(DateTime, nullable=True)


class AIQuotaActiveLock(Base):
    """Межпроцессная блокировка: у пользователя только одна активная AI-операция."""

    __tablename__ = "ai_quota_active_locks"

    id = Column(Integer, primary_key=True)
    user_id = Column(String, unique=True, nullable=False, index=True)
    request_id = Column(String(160), nullable=False, index=True)
    expires_at = Column(DateTime, nullable=False, index=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class GeminiAccount(Base):
    """Статистика и состояние Gemini-аккаунтов."""
    __tablename__ = "gemini_accounts"

    id = Column(Integer, primary_key=True)
    account_name = Column(String, nullable=False, unique=True, index=True)
    api_key_masked = Column(String, nullable=False)
    priority_order = Column(Integer, nullable=False, index=True)
    is_active = Column(Boolean, default=False, nullable=False, index=True)
    total_requests = Column(Integer, default=0, nullable=False)
    success_requests = Column(Integer, default=0, nullable=False)
    error_requests = Column(Integer, default=0, nullable=False)
    limit_switches = Column(Integer, default=0, nullable=False)
    temporary_failover_count = Column(Integer, default=0, nullable=False)
    temporary_errors_count = Column(Integer, default=0, nullable=False)
    quota_errors_count = Column(Integer, default=0, nullable=False)
    auth_errors_count = Column(Integer, default=0, nullable=False)
    unknown_errors_count = Column(Integer, default=0, nullable=False)
    status = Column(String, default="active", nullable=False, index=True)
    disabled_reason = Column(String, nullable=True)
    rate_limited_until = Column(DateTime, nullable=True)
    temporary_unavailable_until = Column(DateTime, nullable=True)
    last_error_type = Column(String, nullable=True, index=True)
    last_request_at = Column(DateTime, nullable=True)
    last_error_at = Column(DateTime, nullable=True)
    last_error_message = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class GeminiRequestLog(Base):
    """Лог отдельных запросов к Gemini."""
    __tablename__ = "gemini_request_logs"

    id = Column(Integer, primary_key=True)
    account_id = Column(Integer, nullable=False, index=True)
    status = Column(String, nullable=False, index=True)  # request_success | error categories | switch events
    event_type = Column(String, nullable=True, index=True)
    reason = Column(String, nullable=True, index=True)
    model_name = Column(String, nullable=True, index=True)
    error_message = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)
