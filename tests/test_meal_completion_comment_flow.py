import asyncio
from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from database.models import Meal, MealCompletionComment
from handlers import meals
import services.ai_quota_service as quota_module


@pytest.fixture
def comment_flow(monkeypatch, isolated_default_ai_quota_store):
    session_provider = quota_module.get_db_session
    monkeypatch.setattr(
        "database.repositories.meal_repository.get_db_session", session_provider
    )
    monkeypatch.setattr(
        "database.repositories.meal_completion_comment_repository.get_db_session",
        session_provider,
    )
    monkeypatch.setattr(quota_module, "AI_QUOTA_COOLDOWN_SECONDS", 0)
    monkeypatch.setattr(meals.WorkoutRepository, "get_workouts_for_day", lambda *args: [])
    generate = Mock(return_value=("<b>Хороший приём пищи.</b>\n\nБелка достаточно.", {}))
    monkeypatch.setattr(meals.deepseek_service, "generate_meal_completion_comment", generate)
    target_date = date(2026, 9, 4)
    user_id = "12345"
    message = SimpleNamespace(
        from_user=SimpleNamespace(id=int(user_id)),
        chat=SimpleNamespace(id=int(user_id)),
        answer=AsyncMock(return_value=SimpleNamespace(message_id=100)),
        edit_reply_markup=AsyncMock(),
        bot=SimpleNamespace(last_meal_ids={}, edit_message_text=AsyncMock()),
    )
    state = SimpleNamespace(
        get_data=AsyncMock(return_value={"entry_date": target_date.isoformat()}),
        clear=AsyncMock(),
    )

    def add_meal(*, meal_type="snack"):
        with session_provider() as session:
            meal = Meal(
                user_id=user_id,
                date=target_date,
                meal_type=meal_type,
                raw_query="Творог",
                calories=150,
                protein=20,
                fat=5,
                carbs=6,
            )
            session.add(meal)
            session.commit()
            return meal

    def finish(meal):
        message.bot.last_meal_ids[user_id] = meal.id
        asyncio.run(meals._finish_current_meal_and_return_to_diary(message, state))
        return meals.MealCompletionCommentRepository.get_by_meal(user_id, meal.id)

    def quota():
        return meals.ai_quota_service.get_status(
            user_id, meals.AIFeature.MEAL_COMPLETION_COMMENT
        )

    return SimpleNamespace(
        add_meal=add_meal,
        finish=finish,
        quota=quota,
        generate=generate,
        message=message,
        state=state,
        user_id=user_id,
        target_date=target_date,
        session=session_provider,
    )


@pytest.mark.parametrize("meal_type", ["breakfast", "snack"])
def test_each_new_meal_gets_comment_but_repeated_finish_does_not_spend_again(
    comment_flow, meal_type
):
    flow = comment_flow
    first_meal = flow.add_meal(meal_type=meal_type)
    first = flow.finish(first_meal)
    repeated = flow.finish(first_meal)
    second = flow.finish(flow.add_meal(meal_type=meal_type))

    assert first.id == repeated.id
    assert first.status == second.status == "success"
    assert first.quota_request_id != second.quota_request_id
    assert flow.generate.call_count == 2
    assert flow.quota().used == 2
    assert flow.quota().remaining == 15
    assert flow.state.clear.await_count == 3
    assert not flow.message.bot.meal_comment_in_progress
    # The newly generated advice sees the updated meal and previous advice.
    prompt = flow.generate.call_args.args[0]
    assert "Итог приёма: 300 ккал" in prompt
    assert first.comment_text in prompt


def test_seventeen_comments_then_local_summary_without_blocking_diary(
    comment_flow, monkeypatch
):
    flow = comment_flow
    for _ in range(17):
        assert flow.finish(flow.add_meal()).status == "success"

    eighteenth_meal = flow.add_meal()
    eighteenth = flow.finish(eighteenth_meal)
    assert eighteenth.status == "fallback"
    assert eighteenth.quota_request_id is None
    assert "Приём пищи завершён" in eighteenth.comment_text
    assert flow.generate.call_count == 17
    assert (flow.quota().used, flow.quota().remaining, flow.quota().reserved) == (17, 0, 0)
    with flow.session() as session:
        assert session.query(Meal).count() == 18
        assert session.query(MealCompletionComment).count() == 18
    for feature in meals.AIFeature:
        if feature != meals.AIFeature.MEAL_COMPLETION_COMMENT:
            assert meals.ai_quota_service.get_status(flow.user_id, feature).used == 0

    keyboard = flow.message.bot.edit_message_text.await_args.kwargs["reply_markup"]
    callback = SimpleNamespace(
        data=keyboard.inline_keyboard[0][0].callback_data,
        from_user=flow.message.from_user,
        message=flow.message,
        answer=AsyncMock(),
    )
    return_to_diary = AsyncMock()
    monkeypatch.setattr(meals, "_return_to_food_diary", return_to_diary)
    asyncio.run(meals.continue_after_meal_comment(callback))
    return_to_diary.assert_awaited_once_with(flow.message, flow.user_id, flow.target_date)
    flow.message.edit_reply_markup.assert_awaited_once_with(reply_markup=None)


@pytest.mark.parametrize("status", ["success", "fallback"])
def test_legacy_comment_is_reused_only_for_its_own_meal(comment_flow, status):
    flow = comment_flow
    old_meal = flow.add_meal()
    old_request_id = meals.build_quota_request_id(
        "meal_comment", flow.user_id, flow.target_date.isoformat(), old_meal.meal_type
    )
    if status == "success":
        meals.ai_quota_service.reserve(
            flow.user_id, meals.AIFeature.MEAL_COMPLETION_COMMENT, old_request_id
        )
        meals.ai_quota_service.consume(old_request_id)
    old_comment = meals.MealCompletionCommentRepository.save(
        flow.user_id,
        old_meal.id,
        flow.target_date,
        old_meal.meal_type,
        comment_text="Сохранённый комментарий",
        model=None,
        status=status,
        quota_request_id=old_request_id if status == "success" else None,
    )

    assert flow.finish(old_meal).id == old_comment.id
    flow.generate.assert_not_called()
    new_comment = flow.finish(flow.add_meal())
    assert new_comment.status == "success"
    assert new_comment.quota_request_id != old_request_id
    flow.generate.assert_called_once()
    assert flow.quota().used == (2 if status == "success" else 1)


def test_provider_failure_does_not_prevent_comment_for_next_meal(comment_flow):
    flow = comment_flow
    flow.generate.side_effect = RuntimeError("provider_unavailable")
    failed = flow.finish(flow.add_meal())
    assert failed.status == "fallback"
    assert flow.quota().used == flow.quota().reserved == 0

    flow.generate.side_effect = None
    assert flow.finish(flow.add_meal()).status == "success"
    assert flow.generate.call_count == 2
    assert flow.quota().used == 1
