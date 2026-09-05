from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
import os
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from zoneinfo import ZoneInfo

os.environ.setdefault("API_TOKEN", "test-token")
os.environ.setdefault("GEMINI_API_KEY", "test-gemini-key")

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database.models import (
    AIAttemptCounter,
    AIQuotaOperation,
    Base,
    UserPlanAssignment,
)
from services.ai_quota_service import (
    AIFeature,
    AIAttemptLimitExceeded,
    AIOperationInProgress,
    AIQuotaAlreadyConsumed,
    AIQuotaExceeded,
    AIQuotaService,
    FREE_PLAN,
    format_free_ai_status_block,
    quota_period_key,
)


MSK = ZoneInfo("Europe/Moscow")


class AIQuotaServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = TemporaryDirectory()
        db_path = Path(self.temp_dir.name) / "quota.db"
        self.engine = create_engine(
            f"sqlite:///{db_path}",
            connect_args={"check_same_thread": False, "timeout": 15},
        )
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine, expire_on_commit=False)

        @contextmanager
        def session_provider():
            session = self.Session()
            try:
                yield session
                session.commit()
            except Exception:
                session.rollback()
                raise
            finally:
                session.close()

        self.session_patch = patch(
            "services.ai_quota_service.get_db_session",
            session_provider,
        )
        self.cooldown_patch = patch("services.ai_quota_service.AI_QUOTA_COOLDOWN_SECONDS", 0)
        self.session_patch.start()
        self.cooldown_patch.start()
        self.service = AIQuotaService()
        self.now = datetime(2026, 8, 24, 12, 0, tzinfo=MSK)

    def tearDown(self):
        self.cooldown_patch.stop()
        self.session_patch.stop()
        self.engine.dispose()
        self.temp_dir.cleanup()

    def _success(self, user_id: str, feature: AIFeature, index: int):
        request_id = f"{user_id}:{feature.value}:{index}"
        reservation = self.service.reserve(user_id, feature, request_id, now=self.now)
        self.assertTrue(self.service.consume(request_id, result_ref=f"result:{index}"))
        return reservation

    def test_quota_day_changes_exactly_at_03_msk(self):
        self.assertEqual(
            quota_period_key(datetime(2026, 8, 24, 2, 59, 59, tzinfo=MSK)),
            date(2026, 8, 23),
        )
        self.assertEqual(
            quota_period_key(datetime(2026, 8, 24, 3, 0, 0, tzinfo=MSK)),
            date(2026, 8, 24),
        )

    def test_quota_day_handles_month_and_year_boundaries(self):
        self.assertEqual(
            quota_period_key(datetime(2027, 1, 1, 2, 59, tzinfo=MSK)),
            date(2026, 12, 31),
        )
        self.assertEqual(
            quota_period_key(datetime(2026, 3, 1, 2, 59, tzinfo=MSK)),
            date(2026, 2, 28),
        )

    def test_daily_counter_resets_at_03_msk(self):
        before_reset = datetime(2026, 8, 24, 2, 59, 59, tzinfo=MSK)
        after_reset = datetime(2026, 8, 24, 3, 0, 0, tzinfo=MSK)
        request_id = "boundary:before"

        self.service.reserve(
            "boundary-user",
            AIFeature.MEAL_TEXT,
            request_id,
            now=before_reset,
        )
        self.service.consume(request_id)

        assert self.service.get_status(
            "boundary-user", AIFeature.MEAL_TEXT, now=before_reset
        ).used == 1
        assert self.service.get_status(
            "boundary-user", AIFeature.MEAL_TEXT, now=after_reset
        ).used == 0

    def test_every_feature_can_be_reserved_and_consumed_once(self):
        for index, feature in enumerate(AIFeature):
            with self.subTest(feature=feature):
                self._success(f"user-{index}", feature, 1)
                status = self.service.get_status(f"user-{index}", feature, now=self.now)
                self.assertEqual(status.used, 1)
                self.assertEqual(status.reserved, 0)

    def test_meal_completion_comment_daily_limit_is_seventeen(self):
        entitlement = FREE_PLAN.features[AIFeature.MEAL_COMPLETION_COMMENT]
        self.assertEqual(entitlement.limit, 17)
        self.assertEqual(entitlement.attempt_limit, 17)

    def test_user_status_does_not_show_internal_image_attempt_limit(self):
        statuses = {
            AIFeature.MEAL_TEXT: SimpleNamespace(remaining=15, limit=15),
            AIFeature.MEAL_PHOTO: SimpleNamespace(remaining=5, limit=5),
            AIFeature.NUTRITION_LABEL: SimpleNamespace(remaining=10, limit=10),
            AIFeature.MEAL_COMPLETION_COMMENT: SimpleNamespace(remaining=16, limit=17),
            AIFeature.DAILY_ANALYSIS: SimpleNamespace(
                remaining=1,
                limit=1,
                used=0,
                period_key=date(2026, 8, 24),
            ),
        }
        with patch(
            "services.ai_quota_service.ai_quota_service.get_statuses",
            return_value=statuses,
        ), patch(
            "services.ai_quota_service.ai_quota_service.get_attempt_status"
        ) as get_attempt_status, patch(
            "database.repositories.activity_analysis_repository.ActivityAnalysisRepository.get_successful_ai_for_date",
            return_value=None,
        ):
            full = format_free_ai_status_block("status-user")
            compact = format_free_ai_status_block("status-user", compact=True)

        self.assertIn("💬 Советы после еды: осталось 16 из 17", full)
        self.assertIn("💬 Советы после еды: 16/17", compact)
        self.assertLessEqual(len(compact), 200)

        get_attempt_status.assert_not_called()
        self.assertIn("📷 Фото еды: осталось 5 из 5", full)
        self.assertIn("📋 Этикетки: осталось 10 из 10", full)
        self.assertNotIn("Фото + этикетки", full)
        self.assertNotIn("Общие запросы", compact)

    def test_preflight_status_does_not_spend_quota(self):
        status = self.service.get_status("preflight", AIFeature.DAILY_ANALYSIS, now=self.now)
        self.assertEqual(status.used, 0)
        self.assertEqual(status.remaining, 1)

    def test_technical_error_and_no_food_release_visible_quota_but_keep_attempt(self):
        for index, outcome in enumerate(("technical_error", "no_food"), start=1):
            request_id = f"release:{index}"
            self.service.reserve("release-user", AIFeature.MEAL_TEXT, request_id, now=self.now)
            self.assertTrue(self.service.release(request_id, outcome=outcome))
        status = self.service.get_status("release-user", AIFeature.MEAL_TEXT, now=self.now)
        self.assertEqual(status.used, 0)
        self.assertEqual(status.reserved, 0)
        with self.Session() as session:
            attempts = session.query(AIAttemptCounter).one()
            self.assertEqual(attempts.attempt_count, 2)

    def test_provider_fallback_is_one_user_operation(self):
        request_id = "fallback:one-operation"
        self.service.reserve("fallback-user", AIFeature.MEAL_PHOTO, request_id, now=self.now)
        with patch("services.ai_quota_service.quota_period_key", return_value=date(2026, 8, 24)):
            self.service.register_additional_provider_attempt(request_id)
        self.service.consume(request_id, outcome="success_after_fallback")
        status = self.service.get_status("fallback-user", AIFeature.MEAL_PHOTO, now=self.now)
        self.assertEqual(status.used, 1)
        with self.Session() as session:
            self.assertEqual(session.query(AIQuotaOperation).count(), 1)
            operation = session.query(AIQuotaOperation).one()
            self.assertEqual(operation.provider_attempt_count, 2)
            self.assertEqual(session.query(AIAttemptCounter).one().attempt_count, 2)

    def test_photo_and_label_share_twenty_five_provider_attempts(self):
        limit = FREE_PLAN.features[AIFeature.MEAL_PHOTO].attempt_limit
        self.assertEqual(limit, 25)
        with patch("services.ai_quota_service.quota_period_key", return_value=date(2026, 8, 24)):
            for index in range(limit):
                feature = AIFeature.MEAL_PHOTO if index % 2 == 0 else AIFeature.NUTRITION_LABEL
                request_id = f"image-attempt:{index}"
                self.service.reserve("image-attempt-user", feature, request_id, now=self.now)
                self.service.release(request_id, outcome="no_food")
            with self.assertRaises(AIAttemptLimitExceeded):
                self.service.reserve(
                    "image-attempt-user",
                    AIFeature.MEAL_PHOTO,
                    "image-attempt:blocked",
                    now=self.now,
                )

    def test_attempt_status_reports_shared_image_group_for_photo_and_label(self):
        self.service.reserve("attempt-status-user", AIFeature.MEAL_PHOTO, "attempt-status:photo", now=self.now)
        self.service.release("attempt-status:photo", outcome="no_food")
        self.service.reserve("attempt-status-user", AIFeature.NUTRITION_LABEL, "attempt-status:label", now=self.now)
        self.service.release("attempt-status:label", outcome="provider_error")

        photo_status = self.service.get_attempt_status(
            "attempt-status-user",
            AIFeature.MEAL_PHOTO,
            now=self.now,
        )
        label_status = self.service.get_attempt_status(
            "attempt-status-user",
            AIFeature.NUTRITION_LABEL,
            now=self.now,
        )

        self.assertEqual(photo_status.group_key, "meal_image")
        self.assertEqual(label_status.group_key, "meal_image")
        self.assertEqual(photo_status.used, 2)
        self.assertEqual(label_status.used, 2)
        self.assertEqual(photo_status.remaining, photo_status.limit - 2)

    def test_cancel_or_edit_after_use_does_not_return_or_spend_again(self):
        request_id = "draft:use-on-visible-result"
        self.service.reserve("draft-user", AIFeature.MEAL_TEXT, request_id, now=self.now)
        self.service.consume(request_id, outcome="success")
        before = self.service.get_status("draft-user", AIFeature.MEAL_TEXT, now=self.now)
        # Cancel/edit/save are deliberately not quota-service operations.
        after = self.service.get_status("draft-user", AIFeature.MEAL_TEXT, now=self.now)
        self.assertEqual((before.used, before.remaining), (after.used, after.remaining))

    def test_each_free_limit_is_enforced(self):
        for feature, entitlement in FREE_PLAN.features.items():
            user_id = f"limit-{feature.value}"
            for index in range(entitlement.limit):
                self._success(user_id, feature, index)
            with self.subTest(feature=feature):
                with self.assertRaises(AIQuotaExceeded):
                    self.service.reserve(
                        user_id,
                        feature,
                        f"{user_id}:blocked",
                        now=self.now,
                    )

    def test_double_click_and_repeated_consumed_callback_are_idempotent(self):
        request_id = "double-click"
        self.service.reserve("double-user", AIFeature.NUTRITION_LABEL, request_id, now=self.now)
        with self.assertRaises(AIOperationInProgress):
            self.service.reserve("double-user", AIFeature.NUTRITION_LABEL, request_id, now=self.now)
        self.service.consume(request_id)
        with self.assertRaises(AIQuotaAlreadyConsumed):
            self.service.reserve("double-user", AIFeature.NUTRITION_LABEL, request_id, now=self.now)
        self.assertEqual(
            self.service.get_status("double-user", AIFeature.NUTRITION_LABEL, now=self.now).used,
            1,
        )

    def test_parallel_requests_cannot_pass_one_active_operation(self):
        barrier = threading.Barrier(2)

        def reserve(index: int):
            barrier.wait()
            try:
                return self.service.reserve(
                    "parallel-user",
                    AIFeature.DAILY_ANALYSIS,
                    f"parallel:{index}",
                    now=self.now,
                )
            except Exception as error:
                return error

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(reserve, range(2)))
        self.assertEqual(sum(not isinstance(result, Exception) for result in results), 1)
        self.assertEqual(sum(isinstance(result, AIOperationInProgress) for result in results), 1)

    def test_stale_reservation_is_released_before_next_request(self):
        self.service.reserve("stale-user", AIFeature.DAILY_ANALYSIS, "stale:first", now=self.now)
        with self.Session() as session:
            operation = session.query(AIQuotaOperation).filter_by(request_id="stale:first").one()
            operation.expires_at = datetime(2026, 8, 24, 8, 59, 59)
            session.commit()
        reservation = self.service.reserve(
            "stale-user",
            AIFeature.DAILY_ANALYSIS,
            "stale:second",
            now=self.now,
        )
        self.assertEqual(reservation.request_id, "stale:second")
        with self.Session() as session:
            first = session.query(AIQuotaOperation).filter_by(request_id="stale:first").one()
            self.assertEqual(first.status, "expired")

    def test_plan_assignment_obeys_start_and_end_dates(self):
        with self.Session() as session:
            session.add(
                UserPlanAssignment(
                    user_id="plan-user",
                    plan_key="free",
                    starts_at=datetime(2026, 8, 1),
                    ends_at=datetime(2026, 9, 1),
                )
            )
            session.commit()
        self.assertEqual(self.service.get_plan_key("plan-user", now=self.now), "free")

    def test_quota_ledger_contains_no_user_text_or_sensitive_payload(self):
        secret = "private meal description and medical secret"
        request_id = "privacy:opaque-id"
        self.service.reserve("privacy-user", AIFeature.MEAL_TEXT, request_id, now=self.now)
        self.service.release(request_id, outcome="sensitive_input_rejected")
        with self.Session() as session:
            operation = session.query(AIQuotaOperation).one()
            serialized = " ".join(
                str(value)
                for value in (
                    operation.request_id,
                    operation.outcome,
                    operation.result_ref,
                    operation.feature_key,
                )
            )
        self.assertNotIn(secret, serialized)


if __name__ == "__main__":
    unittest.main()
