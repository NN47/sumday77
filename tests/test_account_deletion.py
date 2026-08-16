from contextlib import contextmanager
from datetime import date, datetime
import unittest

from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from database.account_deletion import USER_LINKED_MODELS, delete_user_account
from database.models import (
    AIUsageLog,
    ActivityAnalysisEntry,
    Base,
    CustomWorkoutExercise,
    Dish,
    DishIngredient,
    ErrorLog,
    EveningAnalysisNotificationState,
    GeminiAccount,
    GeminiRequestLog,
    KbjuSettings,
    Meal,
    MealCompletionComment,
    Measurement,
    NoteEntry,
    Procedure,
    QuickWaterMessage,
    SavedProduct,
    Supplement,
    SupplementEntry,
    SupplementNotificationState,
    SupportMessage,
    User,
    UserEvent,
    WaterEntry,
    Weight,
    WellbeingEntry,
    Workout,
)


class AccountDeletionTests(unittest.TestCase):
    target_user_id = "1001"
    other_user_id = "2002"

    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine, expire_on_commit=False)
        self._seed_database()

    def tearDown(self):
        self.engine.dispose()

    @contextmanager
    def _session_provider(self):
        session = self.Session()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def _seed_user_data(self, session, user_id: str):
        session.add(User(user_id=user_id))
        session.add(EveningAnalysisNotificationState(user_id=user_id))
        session.add(Workout(user_id=user_id, exercise="Бег"))
        session.add(
            CustomWorkoutExercise(
                user_id=user_id,
                name=f"Упражнение {user_id}",
                category="bodyweight",
            )
        )
        session.add(Weight(user_id=user_id, value="70.0"))
        session.add(Measurement(user_id=user_id, waist=80.0))
        session.add(
            SavedProduct(
                user_id=user_id,
                normalized_name=f"product-{user_id}",
                name=f"Продукт {user_id}",
                last_weight_g=100,
            )
        )
        dish = Dish(
            user_id=user_id,
            name=f"Блюдо {user_id}",
            normalized_name=f"блюдо {user_id}",
        )
        dish.ingredients.append(
            DishIngredient(
                position=0,
                name_snapshot="Ингредиент",
                weight_g=100,
                calories_per_100g=100,
                protein_per_100g=10,
                fat_per_100g=5,
                carbs_per_100g=7,
            )
        )
        session.add(dish)

        meal = Meal(user_id=user_id, description="Тестовый приём пищи")
        session.add(meal)
        session.flush()
        session.add(
            MealCompletionComment(
                user_id=user_id,
                meal_id=meal.id,
                date=date(2026, 1, 1),
                meal_type="snack",
                comment_text="Комментарий",
            )
        )

        session.add(
            KbjuSettings(
                user_id=user_id,
                calories=2000,
                protein=100,
                fat=70,
                carbs=250,
            )
        )

        supplement = Supplement(user_id=user_id, name="Витамин D")
        session.add(supplement)
        session.flush()
        session.add(
            SupplementEntry(
                user_id=user_id,
                supplement_id=supplement.id,
                timestamp=datetime(2026, 1, 1, 9, 0),
            )
        )
        session.add(
            SupplementNotificationState(
                user_id=user_id,
                supplement_id=supplement.id,
                scheduled_time="09:00",
                target_date=date(2026, 1, 1),
                reminder_due_at=datetime(2026, 1, 1, 9, 15),
            )
        )

        session.add(Procedure(user_id=user_id, name="Массаж"))
        session.add(WaterEntry(user_id=user_id, amount=250))
        session.add(
            QuickWaterMessage(
                user_id=user_id,
                chat_id=f"chat-{user_id}",
                message_id=int(user_id),
            )
        )
        session.add(WellbeingEntry(user_id=user_id, entry_type="morning"))
        session.add(
            NoteEntry(
                user_id=user_id,
                date=date(2026, 1, 1),
                day_rating=7,
            )
        )
        session.add(
            ActivityAnalysisEntry(
                user_id=user_id,
                analysis_text="Анализ активности",
            )
        )
        session.add(UserEvent(user_id=user_id, event_name="test_event"))
        session.add(
            SupportMessage(
                user_id=user_id,
                username=f"user_{user_id}",
                message_text="Сообщение в поддержку",
            )
        )
        session.add(ErrorLog(user_id=user_id, error_type="TestError"))
        session.add(
            AIUsageLog(
                user_id=user_id,
                provider="openai",
                feature="test",
                model="test-model",
                status="success",
            )
        )

        return meal, supplement

    def _seed_database(self):
        with self.Session() as session:
            self._seed_user_data(session, self.target_user_id)
            self._seed_user_data(session, self.other_user_id)

            # Имитируем старые/несогласованные дочерние записи: их связь с
            # удаляемым пользователем определяется только через родителя.
            linked_meal = Meal(
                user_id=self.target_user_id,
                description="Приём пищи с косвенным комментарием",
            )
            session.add(linked_meal)
            linked_supplement = Supplement(
                user_id=self.target_user_id,
                name="Добавка с косвенными записями",
            )
            session.add(linked_supplement)
            session.flush()
            session.add(
                MealCompletionComment(
                    user_id="legacy-owner",
                    meal_id=linked_meal.id,
                    date=date(2026, 1, 2),
                    meal_type="snack",
                )
            )
            session.add(
                SupplementEntry(
                    user_id="legacy-owner",
                    supplement_id=linked_supplement.id,
                    timestamp=datetime(2026, 1, 2, 9, 0),
                )
            )
            session.add(
                SupplementNotificationState(
                    user_id="legacy-owner",
                    supplement_id=linked_supplement.id,
                    scheduled_time="09:00",
                    target_date=date(2026, 1, 2),
                    reminder_due_at=datetime(2026, 1, 2, 9, 15),
                )
            )

            account = GeminiAccount(
                account_name="global-account",
                api_key_masked="***",
                priority_order=1,
            )
            session.add(account)
            session.flush()
            session.add(
                GeminiRequestLog(
                    account_id=account.id,
                    status="request_success",
                )
            )
            session.add(ErrorLog(user_id=None, error_type="GlobalError"))
            session.add(
                AIUsageLog(
                    user_id=None,
                    provider="openai",
                    feature="global_test",
                    model="test-model",
                    status="success",
                )
            )
            session.commit()

            self.indirect_meal_id = linked_meal.id
            self.indirect_supplement_id = linked_supplement.id

    def test_model_inventory_covers_every_table_with_user_id(self):
        models_with_user_id = {
            mapper.class_
            for mapper in Base.registry.mappers
            if "user_id" in mapper.local_table.c
        }

        self.assertEqual(models_with_user_id, set(USER_LINKED_MODELS))

    def test_deletes_all_target_data_and_preserves_other_user_and_global_data(self):
        success = delete_user_account(
            self.target_user_id,
            session_provider=self._session_provider,
        )

        self.assertTrue(success)
        with self.Session() as session:
            for model in USER_LINKED_MODELS:
                with self.subTest(table=model.__tablename__):
                    self.assertEqual(
                        session.query(model)
                        .filter(model.user_id == self.target_user_id)
                        .count(),
                        0,
                    )
                    self.assertGreater(
                        session.query(model)
                        .filter(model.user_id == self.other_user_id)
                        .count(),
                        0,
                    )

            self.assertEqual(
                session.query(MealCompletionComment)
                .filter(MealCompletionComment.meal_id == self.indirect_meal_id)
                .count(),
                0,
            )
            self.assertEqual(
                session.query(SupplementEntry)
                .filter(
                    SupplementEntry.supplement_id == self.indirect_supplement_id
                )
                .count(),
                0,
            )
            self.assertEqual(
                session.query(SupplementNotificationState)
                .filter(
                    SupplementNotificationState.supplement_id
                    == self.indirect_supplement_id
                )
                .count(),
                0,
            )

            self.assertEqual(session.query(GeminiAccount).count(), 1)
            self.assertEqual(session.query(GeminiRequestLog).count(), 1)
            self.assertEqual(
                session.query(ErrorLog).filter(ErrorLog.user_id.is_(None)).count(),
                1,
            )
            self.assertEqual(
                session.query(AIUsageLog)
                .filter(AIUsageLog.user_id.is_(None))
                .count(),
                1,
            )

    def test_rolls_back_every_deletion_when_one_step_fails(self):
        def fail_during_deletion(
            _connection,
            _cursor,
            statement,
            _parameters,
            _context,
            _executemany,
        ):
            if statement.lstrip().upper().startswith("DELETE FROM MEASUREMENTS"):
                raise RuntimeError("simulated deletion failure")

        event.listen(self.engine, "before_cursor_execute", fail_during_deletion)
        try:
            with self.assertLogs("database.account_deletion", level="ERROR"):
                success = delete_user_account(
                    self.target_user_id,
                    session_provider=self._session_provider,
                )
        finally:
            event.remove(self.engine, "before_cursor_execute", fail_during_deletion)

        self.assertFalse(success)
        with self.Session() as session:
            for model in USER_LINKED_MODELS:
                with self.subTest(table=model.__tablename__):
                    self.assertGreater(
                        session.query(model)
                        .filter(model.user_id == self.target_user_id)
                        .count(),
                        0,
                    )
            self.assertEqual(
                session.query(MealCompletionComment)
                .filter(MealCompletionComment.meal_id == self.indirect_meal_id)
                .count(),
                1,
            )
            self.assertEqual(
                session.query(SupplementEntry)
                .filter(
                    SupplementEntry.supplement_id == self.indirect_supplement_id
                )
                .count(),
                1,
            )
            self.assertEqual(
                session.query(SupplementNotificationState)
                .filter(
                    SupplementNotificationState.supplement_id
                    == self.indirect_supplement_id
                )
                .count(),
                1,
            )


if __name__ == "__main__":
    unittest.main()
