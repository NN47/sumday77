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
    UniqueConstraint,
)
from datetime import date, datetime
import json

Base = declarative_base()


class User(Base):
    """Модель пользователя."""
    __tablename__ = "users"
    id = Column(Integer, primary_key=True)
    user_id = Column(String, unique=True, nullable=False)
    target_weight = Column(Float, nullable=True)


class Workout(Base):
    """Модель тренировки."""
    __tablename__ = "workouts"
    id = Column(Integer, primary_key=True)
    user_id = Column(String, nullable=False)
    exercise = Column(String, nullable=False)
    variant = Column(String)
    count = Column(Integer)
    date = Column(Date, default=date.today)
    calories = Column(Float, default=0)


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
    date = Column(Date, default=date.today)


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
    created_at = Column(DateTime, default=datetime.utcnow)
