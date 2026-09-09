"""Exercise routing, replay, cancellation and single-image limits with real FSM."""
import asyncio
from unittest.mock import AsyncMock

import pytest
from aiogram import Bot
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.methods import AnswerCallbackQuery
from aiogram.types import CallbackQuery, Message

from handlers import meals
from states.user_states import MealEntryStates
from user_operation_guard import user_operation_guard


class Flow:
    def __init__(self):
        self.sent = []
        self.bot = Bot("12345:ABC", session=AsyncMock(side_effect=self.request))
        self.state = FSMContext(MemoryStorage(), StorageKey(bot_id=12345, chat_id=42, user_id=42))

    async def request(self, bot, method, **kwargs):
        self.sent.append(method)
        if isinstance(method, AnswerCallbackQuery):
            return True
        return self.message(text=getattr(method, "text", None), message_id=1000 + len(self.sent))

    def message(self, text=None, photo=False, message_id=1, album=None):
        return Message.model_validate({
            "message_id": message_id, "date": 1750000000,
            "chat": {"id": 42, "type": "private"},
            "from_user": {"id": 42, "is_bot": False, "first_name": "User"},
            "text": text, "media_group_id": album,
            "photo": [{"file_id": f"image-{message_id}", "file_unique_id": "unique", "width": 100, "height": 100}] if photo else None,
        }).as_(self.bot)

    async def start(self, target=MealEntryStates.choosing_meal_type):
        await self.state.set_state(target)
        await self.state.update_data(meal_type="lunch", entry_date="2026-09-08", meal_entry_open=True)

    async def route(self, event):
        return await meals.router.propagate_event(
            "callback_query" if isinstance(event, CallbackQuery) else "message",
            event, state=self.state, raw_state=await self.state.get_state(), bot=self.bot,
        )

    def callback(self, pending, action):
        return CallbackQuery(
            id=f"cb-{action}", from_user=self.message().from_user, chat_instance="chat",
            message=self.message(message_id=pending["prompt_id"]),
            data=f"meal_intent:{pending['token']}:{action}",
        ).as_(self.bot)


@pytest.fixture(autouse=True)
def isolated_flow(monkeypatch):
    user_operation_guard.reset_for_testing()
    monkeypatch.setattr(meals.MealRepository, "get_meals_for_date", lambda *a, **k: [])
    monkeypatch.setattr(meals, "format_free_ai_status_block", lambda *a, **k: "")


@pytest.mark.parametrize("action,processor", [
    ("text", "handle_ai_food_input"), ("photo", "handle_photo_input"), ("label", "handle_label_photo"),
])
def test_confirmation_replays_original_message_once(monkeypatch, action, processor):
    async def scenario():
        flow = Flow()
        await flow.start()
        analyze = AsyncMock()
        monkeypatch.setattr(meals, processor, analyze)
        await flow.route(flow.message(text="Курица 200 г" if action == "text" else None, photo=action != "text", message_id=71))
        analyze.assert_not_awaited()
        pending = (await flow.state.get_data())["unsolicited_input"]
        callback = flow.callback(pending, action)
        await asyncio.gather(flow.route(callback), flow.route(callback))
        analyze.assert_awaited_once()
        original, state = analyze.await_args.args
        assert original.from_user.id == 42
        assert original.message_id == 71
        assert original.bot is flow.bot
        if action != "text":
            assert original.photo[-1].file_id == "image-71"
        else:
            assert original.text == "Курица 200 г"
        assert (await state.get_data())["entry_date"] == "2026-09-08"
        assert (await state.get_data())["meal_type"] == "lunch"
        assert (await state.get_data())["unsolicited_input"] is None
    asyncio.run(scenario())


def test_replacement_and_cancel_restore_meal(monkeypatch):
    async def scenario():
        flow = Flow()
        await flow.start()
        analyze = AsyncMock()
        monkeypatch.setattr(meals, "handle_label_photo", analyze)
        await flow.route(flow.message(photo=True))
        first = (await flow.state.get_data())["unsolicited_input"]
        await flow.route(flow.message("Йогурт 100 г", message_id=2))
        latest = (await flow.state.get_data())["unsolicited_input"]
        assert latest["kind"] == "text"
        assert latest["token"] != first["token"]
        await flow.route(flow.callback(first, "label"))
        analyze.assert_not_awaited()
        await flow.route(flow.callback(latest, "cancel"))
        assert await flow.state.get_state() == MealEntryStates.choosing_meal_type.state
        data = await flow.state.get_data()
        assert not data.get("unsolicited_input")
        assert data["entry_date"] == "2026-09-08"
        assert data["meal_type"] == "lunch"
    asyncio.run(scenario())


@pytest.mark.parametrize("target", [
    MealEntryStates.choosing_meal_type, MealEntryStates.waiting_for_photo,
    MealEntryStates.waiting_for_label_photo, MealEntryStates.waiting_for_openai_food_photo,
    MealEntryStates.waiting_for_openai_label_photo, MealEntryStates.waiting_for_food_photo_comment,
])
def test_album_is_rejected_once_without_changing_state(target):
    async def scenario():
        flow = Flow()
        await flow.start(target)
        await flow.state.update_data(food_photo_file_id="keep-this")
        before = await flow.state.get_data()
        meals._meal_input_middleware.albums.clear()
        await asyncio.gather(*[
            flow.route(flow.message(photo=True, message_id=i, album="album")) for i in range(1, 4)
        ])
        assert len(flow.sent) == 1
        assert "Альбом не принят" in flow.sent[0].text
        assert await flow.state.get_data() == before
        assert await flow.state.get_state() == target.state
    asyncio.run(scenario())


def test_food_photo_keeps_first_image_and_requires_existing_comment_step():
    async def scenario():
        flow = Flow()
        await flow.start()
        await flow.route(flow.message(photo=True, message_id=10))
        pending = (await flow.state.get_data())["unsolicited_input"]
        await flow.route(flow.callback(pending, "photo"))
        assert await flow.state.get_state() == MealEntryStates.waiting_for_food_photo_comment.state
        await flow.route(flow.message(photo=True, message_id=11))
        assert (await flow.state.get_data())["food_photo_file_id"] == "image-10"
        assert "Новое фото не принято" in flow.sent[-1].text
    asyncio.run(scenario())


@pytest.mark.parametrize("text", ["⬅️ Назад", meals.ADD_METHOD_TEXTS["ai"]])
def test_navigation_dismisses_pending_prompt(text):
    async def scenario():
        flow = Flow()
        await flow.start()
        await flow.route(flow.message("Банан"))
        pending = (await flow.state.get_data())["unsolicited_input"]
        await flow.route(flow.message(text, message_id=2))
        assert not (await flow.state.get_data()).get("unsolicited_input")
        await flow.route(flow.callback(pending, "text"))
        assert "устарел" in flow.sent[-1].text
    asyncio.run(scenario())


@pytest.mark.parametrize("text", ["/start", "⬅️ Назад", meals.ADD_METHOD_TEXTS["ai"], "📊 Дневной отчёт"])
def test_navigation_is_not_food_text(text):
    assert not meals._is_unsolicited_meal_content(Flow().message(text))


def test_editing_weight_uses_existing_handler(monkeypatch):
    async def scenario():
        flow = Flow()
        await flow.start(MealEntryStates.waiting_for_weight_input)
        await flow.state.update_data(product_name="Йогурт", kbju_per_100g={"calories": 100})
        await flow.route(flow.message("150"))
        assert await flow.state.get_state() == MealEntryStates.confirming_label_weight.state
        assert not (await flow.state.get_data()).get("unsolicited_input")
    asyncio.run(scenario())


def test_quick_separate_label_photos_start_only_one_analysis(monkeypatch):
    async def scenario():
        flow = Flow()
        await flow.start(MealEntryStates.waiting_for_label_photo)

        async def analyze(message, state, **kwargs):
            await asyncio.sleep(0)
            await state.set_state(MealEntryStates.waiting_for_weight_input)

        processor = AsyncMock(side_effect=analyze)
        monkeypatch.setattr(meals, "_handle_label_photo_analysis", processor)
        await asyncio.gather(flow.route(flow.message(photo=True)), flow.route(flow.message(photo=True, message_id=2)))
        processor.assert_awaited_once()
        assert "Новое фото не принято" in flow.sent[-1].text
    asyncio.run(scenario())


def test_photo_button_first_skips_intent_question():
    async def scenario():
        flow = Flow()
        await flow.start(MealEntryStates.waiting_for_photo)
        await flow.route(flow.message(photo=True))
        assert await flow.state.get_state() == MealEntryStates.waiting_for_food_photo_comment.state
        assert not (await flow.state.get_data()).get("unsolicited_input")
    asyncio.run(scenario())


def test_old_food_photo_buttons_cannot_cancel_or_analyze_new_image(monkeypatch):
    async def scenario():
        flow = Flow()
        await flow.start(MealEntryStates.waiting_for_photo)
        await flow.route(flow.message(photo=True))
        current = (await flow.state.get_data())["food_photo_prompt_id"]
        analyze = AsyncMock()
        monkeypatch.setattr(meals, "_run_pending_food_photo_analysis", analyze)
        for action in ("food_photo_analyze_now", "food_photo_cancel"):
            callback = CallbackQuery(
                id=action, from_user=flow.message().from_user, chat_instance="chat",
                message=flow.message(message_id=current - 1), data=action,
            ).as_(flow.bot)
            await flow.route(callback)
        analyze.assert_not_awaited()
        assert (await flow.state.get_data())["food_photo_file_id"] == "image-1"
        assert await flow.state.get_state() == MealEntryStates.waiting_for_food_photo_comment.state
    asyncio.run(scenario())


def test_album_does_not_replace_pending_single_image():
    async def scenario():
        flow = Flow()
        await flow.start()
        await flow.route(flow.message(photo=True))
        before = await flow.state.get_data()
        await flow.route(flow.message(photo=True, message_id=2, album="new-album"))
        assert await flow.state.get_data() == before
    asyncio.run(scenario())


def test_other_section_does_not_trigger_meal_question():
    async def scenario():
        flow = Flow()
        await flow.state.set_state("OtherSection:input")
        await flow.route(flow.message(photo=True))
        await flow.route(flow.message("Йогурт"))
        assert not flow.sent
        assert await flow.state.get_state() == "OtherSection:input"
    asyncio.run(scenario())
