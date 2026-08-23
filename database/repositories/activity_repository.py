"""Хранилище новой модели активности, шагов и тренировочных сессий."""
from __future__ import annotations

from datetime import date, datetime
import json

from sqlalchemy import func

from database.models import (
    ActivityCategory,
    DailySteps,
    ExerciseCategory,
    ExerciseDefinition,
    TimedActivityDefinition,
    TimedActivityEntry,
    WorkoutSession,
    WorkoutSessionExercise,
    WorkoutSet,
)
from database.session import get_db_session
from utils.activity_catalog import (
    CALCULATION_VERSION,
    COMPENDIUM_SOURCE_NAME,
    COMPENDIUM_SOURCE_DATE,
    COMPENDIUM_SOURCE_URL,
    COMPENDIUM_SOURCE_VERSION,
    EXERCISE_CATEGORIES,
    EXERCISES,
    TIMED_ACTIVITIES,
    TIMED_CATEGORIES,
)


DRAFT_WORKOUT_STATUSES = {"draft"}


class WorkoutDraftExistsError(RuntimeError):
    pass


class ActivityRepository:
    """Единая точка доступа к физической активности."""

    @staticmethod
    def _refresh_workout_aggregates(session, session_id: int) -> None:
        """Фиксирует дневниковые итоги; они никогда не участвуют в kcal."""
        workout = session.query(WorkoutSession).filter(WorkoutSession.id == session_id).first()
        if workout is None:
            return
        sets = session.query(WorkoutSet).filter(WorkoutSet.session_id == session_id).all()
        workout.exercise_count = len({item.session_exercise_id for item in sets})
        workout.set_count = len(sets)
        workout.training_volume_kg = sum(
            float(item.load_kg or 0) * int(item.repetitions or 0)
            for item in sets
            if item.load_kind != "assistance"
        )

    @staticmethod
    def seed_catalog(session_provider=None) -> None:
        """Идемпотентно синхронизирует встроенный версионируемый справочник."""
        provider = session_provider or get_db_session
        with provider() as session:
            for category in TIMED_CATEGORIES:
                row = (
                    session.query(ActivityCategory)
                    .filter(ActivityCategory.code == category.code)
                    .first()
                )
                if row is None:
                    row = ActivityCategory(code=category.code)
                    session.add(row)
                row.name = category.name
                row.icon = category.icon
                row.sort_order = category.sort_order
                row.is_active = True

            for category in EXERCISE_CATEGORIES:
                row = (
                    session.query(ExerciseCategory)
                    .filter(ExerciseCategory.code == category.code)
                    .first()
                )
                if row is None:
                    row = ExerciseCategory(code=category.code)
                    session.add(row)
                row.name = category.name
                row.icon = category.icon
                row.sort_order = category.sort_order
                row.is_active = True

            for position, config in enumerate(TIMED_ACTIVITIES, start=1):
                row = (
                    session.query(TimedActivityDefinition)
                    .filter(TimedActivityDefinition.code == config.code)
                    .first()
                )
                if row is None:
                    row = TimedActivityDefinition(code=config.code)
                    session.add(row)
                row.category_code = config.category_code
                row.name = config.name
                row.emoji = config.emoji
                row.intensity_mets_json = json.dumps(config.intensity_mets, ensure_ascii=False, sort_keys=True)
                row.cadence_steps_per_minute = config.cadence_steps_per_minute
                row.source_name = COMPENDIUM_SOURCE_NAME
                row.source_version = COMPENDIUM_SOURCE_VERSION
                row.source_updated_at = COMPENDIUM_SOURCE_DATE
                row.source_url = COMPENDIUM_SOURCE_URL
                row.sort_order = position
                row.is_active = True

            for position, config in enumerate(EXERCISES, start=1):
                row = (
                    session.query(ExerciseDefinition)
                    .filter(ExerciseDefinition.code == config.code)
                    .first()
                )
                if row is None:
                    row = ExerciseDefinition(code=config.code)
                    session.add(row)
                row.category_code = config.category_code
                row.name = config.name
                row.measurement_type = config.measurement_type
                row.load_input_mode = config.load_input_mode
                row.tempo_seconds_per_rep = config.tempo_seconds_per_rep
                row.sort_order = position
                row.is_active = True

    @staticmethod
    def save_timed_activity(
        *,
        user_id: str,
        activity_code: str,
        activity_name: str,
        entry_date: date,
        duration_minutes: float,
        intensity: str,
        met_value: float,
        weight_kg: float,
        weight_source: str,
        gross_calories: float,
        credited_calories: float,
        duration_source: str = "entered",
        source_version: str = COMPENDIUM_SOURCE_VERSION,
    ) -> TimedActivityEntry:
        with get_db_session() as session:
            row = TimedActivityEntry(
                user_id=str(user_id),
                activity_code=activity_code,
                activity_name_snapshot=activity_name,
                entry_date=entry_date,
                duration_minutes=duration_minutes,
                intensity=intensity,
                met_value=met_value,
                weight_kg_snapshot=weight_kg,
                weight_source=weight_source,
                gross_calories=gross_calories,
                credited_calories=credited_calories,
                duration_source=duration_source,
                calculation_version=CALCULATION_VERSION,
                source_version=source_version,
            )
            session.add(row)
            session.flush()
            session.refresh(row)
            return row

    @staticmethod
    def get_timed_activity(entry_id: int, user_id: str) -> TimedActivityEntry | None:
        with get_db_session() as session:
            return (
                session.query(TimedActivityEntry)
                .filter(TimedActivityEntry.id == entry_id, TimedActivityEntry.user_id == str(user_id))
                .first()
            )

    @staticmethod
    def get_timed_activities_for_day(user_id: str, target_date: date) -> list[TimedActivityEntry]:
        with get_db_session() as session:
            return (
                session.query(TimedActivityEntry)
                .filter(TimedActivityEntry.user_id == str(user_id), TimedActivityEntry.entry_date == target_date)
                .order_by(TimedActivityEntry.id.asc())
                .all()
            )

    @staticmethod
    def get_timed_activities_for_period(user_id: str, start_date: date, end_date: date) -> list[TimedActivityEntry]:
        with get_db_session() as session:
            return session.query(TimedActivityEntry).filter(
                TimedActivityEntry.user_id == str(user_id),
                TimedActivityEntry.entry_date >= start_date,
                TimedActivityEntry.entry_date <= end_date,
            ).order_by(TimedActivityEntry.entry_date.asc(), TimedActivityEntry.id.asc()).all()

    @staticmethod
    def get_recent_timed_activity_codes(user_id: str, limit: int = 8) -> list[str]:
        """Возвращает уникальные виды по частоте, затем по последнему использованию."""
        with get_db_session() as session:
            rows = (
                session.query(
                    TimedActivityEntry.activity_code,
                    func.count(TimedActivityEntry.id).label("uses"),
                    func.max(TimedActivityEntry.created_at).label("last_used"),
                )
                .filter(TimedActivityEntry.user_id == str(user_id))
                .group_by(TimedActivityEntry.activity_code)
                .order_by(func.count(TimedActivityEntry.id).desc(), func.max(TimedActivityEntry.created_at).desc())
                .limit(limit)
                .all()
            )
            return [row.activity_code for row in rows]

    @staticmethod
    def update_timed_activity(
        *,
        entry_id: int,
        user_id: str,
        duration_minutes: float,
        intensity: str,
        met_value: float,
        gross_calories: float,
        credited_calories: float,
    ) -> bool:
        with get_db_session() as session:
            row = (
                session.query(TimedActivityEntry)
                .filter(TimedActivityEntry.id == entry_id, TimedActivityEntry.user_id == str(user_id))
                .first()
            )
            if row is None:
                return False
            row.duration_minutes = duration_minutes
            row.intensity = intensity
            row.met_value = met_value
            row.gross_calories = gross_calories
            row.credited_calories = credited_calories
            row.updated_at = datetime.utcnow()
            return True

    @staticmethod
    def delete_timed_activity(entry_id: int, user_id: str) -> bool:
        with get_db_session() as session:
            row = (
                session.query(TimedActivityEntry)
                .filter(TimedActivityEntry.id == entry_id, TimedActivityEntry.user_id == str(user_id))
                .first()
            )
            if row is None:
                return False
            session.delete(row)
            return True

    @staticmethod
    def upsert_steps(
        *, user_id: str, entry_date: date, steps: int, weight_kg: float,
        weight_source: str, gross_calories: float, credited_calories: float,
    ) -> DailySteps:
        with get_db_session() as session:
            row = (
                session.query(DailySteps)
                .filter(DailySteps.user_id == str(user_id), DailySteps.entry_date == entry_date)
                .first()
            )
            if row is None:
                row = DailySteps(user_id=str(user_id), entry_date=entry_date, created_at=datetime.utcnow())
                session.add(row)
            row.steps = steps
            row.weight_kg_snapshot = weight_kg
            row.weight_source = weight_source
            row.gross_calories = gross_calories
            row.credited_calories = credited_calories
            row.calculation_version = CALCULATION_VERSION
            row.updated_at = datetime.utcnow()
            session.flush()
            session.refresh(row)
            return row

    @staticmethod
    def get_steps_for_day(user_id: str, target_date: date) -> DailySteps | None:
        with get_db_session() as session:
            return (
                session.query(DailySteps)
                .filter(DailySteps.user_id == str(user_id), DailySteps.entry_date == target_date)
                .first()
            )

    @staticmethod
    def get_steps_for_period(user_id: str, start_date: date, end_date: date) -> list[DailySteps]:
        with get_db_session() as session:
            return session.query(DailySteps).filter(
                DailySteps.user_id == str(user_id),
                DailySteps.entry_date >= start_date,
                DailySteps.entry_date <= end_date,
            ).order_by(DailySteps.entry_date.asc()).all()

    @staticmethod
    def delete_steps(user_id: str, target_date: date) -> bool:
        with get_db_session() as session:
            row = session.query(DailySteps).filter(
                DailySteps.user_id == str(user_id), DailySteps.entry_date == target_date
            ).first()
            if row is None:
                return False
            session.delete(row)
            return True

    @staticmethod
    def create_workout_session(
        *, user_id: str, entry_date: date, weight_kg: float, weight_source: str,
    ) -> WorkoutSession:
        with get_db_session() as session:
            existing = (
                session.query(WorkoutSession)
                .filter(
                    WorkoutSession.user_id == str(user_id),
                    WorkoutSession.status.in_(DRAFT_WORKOUT_STATUSES),
                )
                .order_by(WorkoutSession.id.desc())
                .first()
            )
            if existing is not None:
                raise WorkoutDraftExistsError(str(existing.id))
            row = WorkoutSession(
                user_id=str(user_id),
                entry_date=entry_date,
                status="draft",
                duration_source="estimated",
                weight_kg_snapshot=weight_kg,
                weight_source=weight_source,
                calculation_version=CALCULATION_VERSION,
            )
            session.add(row)
            session.flush()
            session.refresh(row)
            return row

    @staticmethod
    def get_workout_draft(user_id: str) -> WorkoutSession | None:
        with get_db_session() as session:
            return (
                session.query(WorkoutSession)
                .filter(
                    WorkoutSession.user_id == str(user_id),
                    WorkoutSession.status.in_(DRAFT_WORKOUT_STATUSES),
                )
                .order_by(WorkoutSession.id.desc())
                .first()
            )

    @staticmethod
    def get_workout_session(session_id: int, user_id: str) -> WorkoutSession | None:
        with get_db_session() as session:
            return (
                session.query(WorkoutSession)
                .filter(WorkoutSession.id == session_id, WorkoutSession.user_id == str(user_id))
                .first()
            )

    @staticmethod
    def get_workout_sessions_for_day(user_id: str, target_date: date) -> list[WorkoutSession]:
        with get_db_session() as session:
            return (
                session.query(WorkoutSession)
                .filter(WorkoutSession.user_id == str(user_id), WorkoutSession.entry_date == target_date)
                .order_by(WorkoutSession.id.asc())
                .all()
            )

    @staticmethod
    def get_workout_sessions_for_period(user_id: str, start_date: date, end_date: date) -> list[WorkoutSession]:
        with get_db_session() as session:
            return session.query(WorkoutSession).filter(
                WorkoutSession.user_id == str(user_id),
                WorkoutSession.entry_date >= start_date,
                WorkoutSession.entry_date <= end_date,
            ).order_by(WorkoutSession.entry_date.asc(), WorkoutSession.id.asc()).all()

    @staticmethod
    def finish_workout(
        *, session_id: int, user_id: str, intensity: str, met_value: float,
        duration_seconds: int, gross_calories: float, credited_calories: float,
    ) -> WorkoutSession | None:
        with get_db_session() as session:
            row = session.query(WorkoutSession).filter(
                WorkoutSession.id == session_id,
                WorkoutSession.user_id == str(user_id),
                WorkoutSession.status == "draft",
            ).first()
            if row is None:
                return None
            row.status = "completed"
            row.intensity = intensity
            row.met_value = met_value
            row.duration_seconds = max(int(duration_seconds), 1)
            row.duration_source = "estimated"
            row.gross_calories = gross_calories
            row.credited_calories = credited_calories
            row.updated_at = datetime.utcnow()
            ActivityRepository._refresh_workout_aggregates(session, row.id)
            session.flush()
            session.refresh(row)
            return row

    @staticmethod
    def update_completed_workout(
        *, session_id: int, user_id: str, duration_seconds: int | None = None,
        intensity: str | None = None, met_value: float | None = None,
        gross_calories: float, credited_calories: float,
    ) -> bool:
        with get_db_session() as session:
            row = session.query(WorkoutSession).filter(
                WorkoutSession.id == session_id,
                WorkoutSession.user_id == str(user_id),
                WorkoutSession.status == "completed",
            ).first()
            if row is None:
                return False
            if duration_seconds is not None:
                row.duration_seconds = duration_seconds
                row.duration_source = "estimated"
            if intensity is not None:
                row.intensity = intensity
            if met_value is not None:
                row.met_value = met_value
            row.gross_calories = gross_calories
            row.credited_calories = credited_calories
            row.updated_at = datetime.utcnow()
            return True

    @staticmethod
    def cancel_workout(session_id: int, user_id: str) -> bool:
        return ActivityRepository.delete_workout_session(session_id, user_id)

    @staticmethod
    def delete_workout_session(session_id: int, user_id: str) -> bool:
        with get_db_session() as session:
            row = session.query(WorkoutSession).filter(
                WorkoutSession.id == session_id, WorkoutSession.user_id == str(user_id)
            ).first()
            if row is None:
                return False
            session.query(WorkoutSet).filter(WorkoutSet.session_id == session_id).delete(synchronize_session=False)
            session.query(WorkoutSessionExercise).filter(WorkoutSessionExercise.session_id == session_id).delete(synchronize_session=False)
            session.delete(row)
            return True

    @staticmethod
    def add_session_exercise(
        *, session_id: int, user_id: str, exercise_code: str, exercise_name: str,
        measurement_type: str, load_input_mode: str, tempo_seconds_per_rep: float,
    ) -> WorkoutSessionExercise:
        with get_db_session() as session:
            session_row = session.query(WorkoutSession).filter(
                WorkoutSession.id == session_id, WorkoutSession.user_id == str(user_id),
                WorkoutSession.status.in_(DRAFT_WORKOUT_STATUSES),
            ).first()
            if session_row is None:
                raise LookupError("Тренировка не найдена")
            position = int(session.query(func.max(WorkoutSessionExercise.position)).filter(
                WorkoutSessionExercise.session_id == session_id
            ).scalar() or 0) + 1
            row = WorkoutSessionExercise(
                user_id=str(user_id), session_id=session_id,
                exercise_code=exercise_code, exercise_name_snapshot=exercise_name,
                measurement_type_snapshot=measurement_type,
                load_input_mode_snapshot=load_input_mode,
                tempo_seconds_per_rep_snapshot=tempo_seconds_per_rep,
                position=position,
            )
            session.add(row)
            session.flush()
            session.refresh(row)
            return row

    @staticmethod
    def add_workout_set(
        *, session_id: int, session_exercise_id: int, user_id: str,
        repetitions: int | None = None, load_kg: float | None = None,
        load_kind: str | None = None, duration_seconds: int | None = None,
        distance_meters: float | None = None,
    ) -> WorkoutSet:
        with get_db_session() as session:
            session_row = session.query(WorkoutSession).filter(
                WorkoutSession.id == session_id,
                WorkoutSession.user_id == str(user_id),
                WorkoutSession.status.in_(DRAFT_WORKOUT_STATUSES),
            ).first()
            if session_row is None:
                raise LookupError("Активная тренировка не найдена")
            exercise = session.query(WorkoutSessionExercise).filter(
                WorkoutSessionExercise.id == session_exercise_id,
                WorkoutSessionExercise.session_id == session_id,
                WorkoutSessionExercise.user_id == str(user_id),
            ).first()
            if exercise is None:
                raise LookupError("Упражнение не найдено")
            position = int(session.query(func.max(WorkoutSet.position)).filter(
                WorkoutSet.session_exercise_id == session_exercise_id
            ).scalar() or 0) + 1
            row = WorkoutSet(
                user_id=str(user_id), session_id=session_id, session_exercise_id=session_exercise_id,
                position=position, repetitions=repetitions, load_kg=load_kg,
                load_kind=load_kind, duration_seconds=duration_seconds,
                distance_meters=distance_meters,
            )
            session.add(row)
            session.flush()
            session.refresh(row)
            return row

    @staticmethod
    def repeat_last_set(session_id: int, user_id: str) -> WorkoutSet | None:
        with get_db_session() as session:
            session_row = session.query(WorkoutSession).filter(
                WorkoutSession.id == session_id,
                WorkoutSession.user_id == str(user_id),
                WorkoutSession.status.in_(DRAFT_WORKOUT_STATUSES),
            ).first()
            if session_row is None:
                return None
            previous = session.query(WorkoutSet).filter(
                WorkoutSet.session_id == session_id, WorkoutSet.user_id == str(user_id)
            ).order_by(WorkoutSet.id.desc()).first()
            if previous is None:
                return None
            position = int(session.query(func.max(WorkoutSet.position)).filter(
                WorkoutSet.session_exercise_id == previous.session_exercise_id
            ).scalar() or 0) + 1
            row = WorkoutSet(
                user_id=str(user_id), session_id=session_id,
                session_exercise_id=previous.session_exercise_id, position=position,
                repetitions=previous.repetitions, load_kg=previous.load_kg,
                load_kind=previous.load_kind,
                duration_seconds=previous.duration_seconds, distance_meters=previous.distance_meters,
            )
            session.add(row)
            session.flush()
            session.refresh(row)
            return row

    @staticmethod
    def get_session_exercises(session_id: int, user_id: str) -> list[WorkoutSessionExercise]:
        with get_db_session() as session:
            return session.query(WorkoutSessionExercise).filter(
                WorkoutSessionExercise.session_id == session_id,
                WorkoutSessionExercise.user_id == str(user_id),
            ).order_by(WorkoutSessionExercise.position.asc()).all()

    @staticmethod
    def get_session_sets(session_id: int, user_id: str) -> list[WorkoutSet]:
        with get_db_session() as session:
            sets = session.query(WorkoutSet).filter(
                WorkoutSet.session_id == session_id, WorkoutSet.user_id == str(user_id),
            ).order_by(WorkoutSet.id.asc()).all()
            codes = {
                row.id: row.exercise_code
                for row in session.query(WorkoutSessionExercise).filter(
                    WorkoutSessionExercise.session_id == session_id,
                    WorkoutSessionExercise.user_id == str(user_id),
                ).all()
            }
            for item in sets:
                item.exercise_code = codes.get(item.session_exercise_id)
            return sets

    @staticmethod
    def remove_empty_session_exercises(session_id: int, user_id: str) -> int:
        """Удаляет незаполненные упражнения, оставшиеся после отмены ввода."""
        with get_db_session() as session:
            used_ids = {
                row[0] for row in session.query(WorkoutSet.session_exercise_id).filter(
                    WorkoutSet.session_id == session_id,
                    WorkoutSet.user_id == str(user_id),
                ).distinct().all()
            }
            query = session.query(WorkoutSessionExercise).filter(
                WorkoutSessionExercise.session_id == session_id,
                WorkoutSessionExercise.user_id == str(user_id),
            )
            if used_ids:
                query = query.filter(~WorkoutSessionExercise.id.in_(used_ids))
            empty_rows = query.all()
            for row in empty_rows:
                session.delete(row)
            if empty_rows:
                session.flush()
                ActivityRepository._refresh_workout_aggregates(session, session_id)
            return len(empty_rows)

    @staticmethod
    def delete_workout_set(set_id: int, user_id: str) -> bool:
        with get_db_session() as session:
            row = session.query(WorkoutSet).filter(
                WorkoutSet.id == set_id, WorkoutSet.user_id == str(user_id)
            ).first()
            if row is None:
                return False
            session_id = row.session_id
            session.delete(row)
            session.flush()
            ActivityRepository._refresh_workout_aggregates(session, session_id)
            return True

    @staticmethod
    def get_workout_set(set_id: int, user_id: str) -> WorkoutSet | None:
        with get_db_session() as session:
            return session.query(WorkoutSet).filter(
                WorkoutSet.id == set_id, WorkoutSet.user_id == str(user_id)
            ).first()

    @staticmethod
    def update_workout_set(
        *, set_id: int, user_id: str, repetitions: int | None = None,
        load_kg: float | None = None, update_load: bool = False,
    ) -> bool:
        with get_db_session() as session:
            row = session.query(WorkoutSet).filter(
                WorkoutSet.id == set_id, WorkoutSet.user_id == str(user_id)
            ).first()
            if row is None:
                return False
            if repetitions is not None:
                row.repetitions = repetitions
            if update_load:
                row.load_kg = load_kg
            session.flush()
            ActivityRepository._refresh_workout_aggregates(session, row.session_id)
            return True

    @staticmethod
    def recent_history(user_id: str, limit: int = 50) -> tuple[list[TimedActivityEntry], list[WorkoutSession]]:
        with get_db_session() as session:
            timed = session.query(TimedActivityEntry).filter(
                TimedActivityEntry.user_id == str(user_id)
            ).order_by(TimedActivityEntry.entry_date.desc(), TimedActivityEntry.id.desc()).limit(limit).all()
            workouts = session.query(WorkoutSession).filter(
                WorkoutSession.user_id == str(user_id), WorkoutSession.status == "completed"
            ).order_by(WorkoutSession.entry_date.desc(), WorkoutSession.id.desc()).limit(limit).all()
            return timed, workouts
