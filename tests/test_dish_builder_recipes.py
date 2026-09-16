import asyncio
from contextlib import contextmanager, nullcontext
from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from sqlalchemy import create_engine, text, inspect
from sqlalchemy.orm import sessionmaker

from handlers import meals
from database.models import Base, Dish, Meal
from database.recipe_migration import migrate_recipe_metadata
from services import recipe_service, dish_service
from database.repositories import dish_repository


class State:
    def __init__(self, **data):
        self.data = data
        self.current = None

    async def get_data(self):
        return dict(self.data)

    async def update_data(self, **data):
        self.data.update(data)

    async def set_state(self, state):
        self.current = state

    async def clear(self):
        self.data.clear()
        self.current = None


def message(text_value=""):
    return SimpleNamespace(text=text_value, from_user=SimpleNamespace(id=42),
                           chat=SimpleNamespace(id=42), message_id=123,
                           answer=AsyncMock(), edit_text=AsyncMock(),
                           bot=SimpleNamespace(menu_stack=[]))


def item():
    return {"name": "Рис", "grams": 100, "kcal": 350, "protein": 7, "fat": 1, "carbs": 78}


def builder_state(**extra):
    return State(meal_type="lunch", entry_date="2026-09-12", dish_builder_mode=True,
                 dish_builder={"token": "B" * 22, "items": [], "meal_type": "lunch", "entry_date": "2026-09-12"}, **extra)


@pytest.fixture
def db(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)

    @contextmanager
    def session_provider():
        with factory() as session:
            try:
                yield session
                session.commit()
            except Exception:
                session.rollback()
                raise

    for module in (recipe_service, dish_service, dish_repository):
        monkeypatch.setattr(module, "get_db_session", session_provider)
    yield factory
    engine.dispose()


def test_recipe_save_edit_and_cooked_portion_preserve_history(db):
    recipe = recipe_service.save_recipe(user_id="42", token="recipe-1", name="Рисовая каша", items=[item()],
                                        cooking_method="boil", cooked_weight_g=300, preparation="Сварить рис.")
    repeated = recipe_service.save_recipe(user_id="42", token="recipe-1", name="Рисовая каша", items=[item()],
                                          cooking_method="boil", cooked_weight_g=300)
    assert repeated.id == recipe.id
    cooked = dish_service.dish_to_snapshot(recipe)
    assert cooked[0]["grams"] == 300
    assert cooked[0]["kcal"] == 350
    assert dish_service.dish_to_snapshot(recipe, cooked=False)[0]["grams"] == 100
    portion = dish_service.scale_dish_snapshot(cooked, 150)
    result = dish_service.DishService.add_saved_dish_to_diary(
        save_token="portion-1", user_id="42", dish_id=recipe.id, entry_date=date(2026, 9, 12),
        meal_type="lunch", items=portion)
    assert result.meal.calories == 175
    recipe_service.save_recipe(user_id="42", token="recipe-edit", recipe_id=recipe.id,
                               name="Новая каша", items=[item()], cooking_method="boil", cooked_weight_g=400)
    with db() as session:
        assert session.query(Dish).count() == 1
        assert session.query(Meal).count() == 1
        assert session.query(Meal).one().calories == 175
    with pytest.raises(ValueError, match="не найден"):
        recipe_service.save_recipe(user_id="other", token="foreign", recipe_id=recipe.id,
                                   name="Чужая каша", items=[item()], cooking_method=None, cooked_weight_g=None)


def test_recipe_migration_is_repeatable_and_preserves_legacy_rows():
    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE dishes (id INTEGER PRIMARY KEY, name TEXT)"))
        connection.execute(text("INSERT INTO dishes VALUES (1, 'old')"))
    migrate_recipe_metadata(engine)
    migrate_recipe_metadata(engine)
    assert {"cooking_method", "cooked_weight_g", "preparation"} <= {c["name"] for c in inspect(engine).get_columns("dishes")}
    with engine.connect() as connection:
        assert connection.execute(text("SELECT name, cooked_weight_g FROM dishes")).one() == ("old", None)
    engine.dispose()


@pytest.mark.parametrize("source", ["manual", "text", "photo", "label"])
def test_all_input_saves_go_to_composition_without_diary_write(monkeypatch, source):
    state = builder_state()
    msg = message()
    show = AsyncMock()
    monkeypatch.setattr(meals, "_show_dish_builder", show)
    diary = Mock(side_effect=AssertionError("ingredient must not be a diary entry"))
    monkeypatch.setattr(meals.MealRepository, "save_meal_idempotent", diary)
    monkeypatch.setattr(meals.DishService, "save_photo_dish_entry", diary)

    async def run():
        if source == "manual":
            await state.update_data(custom_product={"name": "Рис", "amount": 100, "calories": 350,
                                                    "protein": 7, "fat": 1, "carbs": 78}, custom_product_save_token="S" * 22)
            result = await meals._save_custom_product(msg, state)
        elif source == "text":
            await state.update_data(ai_pending_meal={"items": [item()], "save_token": "S" * 22})
            result = await meals._save_ai_meal_draft(msg, state, user_id="42")
        elif source == "photo":
            await state.update_data(photo_analysis_items=[item()], photo_save_token="S" * 22)
            result = await meals._save_photo_analysis_confirmation(msg, state, "42", await state.get_data())
        else:
            await state.update_data(label_save_token="S" * 22, product_name="Рис",
                                    kbju_per_100g={"kcal": 350, "protein": 7, "fat": 1, "carbs": 78})
            result = await meals._save_label_analysis_draft(msg, state, user_id="42", data=await state.get_data(), weight_grams=100)
        assert result.status is meals.MealSaveStatus.SAVED
        assert len(state.data["dish_builder"]["items"]) == 1
        saved = state.data["dish_builder"]["items"][0]
        assert saved["name"] == "Рис"
        assert saved["grams"] == 100
        assert meals.calculate_dish_totals([saved])["calories"] == 350
        assert not state.data.get("ai_pending_meal")
        assert not state.data.get("custom_product")
        show.assert_awaited_once()
        diary.assert_not_called()
    asyncio.run(run())


def test_builder_hides_old_reply_and_ingredient_screen_shares_methods(monkeypatch):
    state = builder_state()
    msg = message()
    hide = AsyncMock()
    monkeypatch.setattr(meals, "_hide_meal_reply_keyboard", hide)
    monkeypatch.setattr(meals, "format_free_ai_status_block", lambda user_id: "limits")

    async def run():
        await meals._show_dish_builder(msg, state)
        hide.assert_awaited_once_with(msg)
        await meals._show_ingredient_input_methods(msg, state, user_id="42")
        keyboard = msg.answer.await_args.kwargs["reply_markup"]
        buttons = [button.text for row in keyboard.keyboard for button in row]
        for row in meals.kbju_add_menu.keyboard:
            for button in row:
                if button.text not in meals.MEAL_FINISH_BUTTON_TEXTS:
                    assert button.text in buttons
        assert "➕ Добавить блюдо" not in buttons
        assert "🍽 Мои блюда" not in buttons
        assert buttons.count("⬅️ Назад") == 1
    asyncio.run(run())


def test_recipe_builder_uses_same_compact_summary_as_new_dish(monkeypatch):
    state = builder_state()
    state.data["dish_builder"].update(kind="recipe", items=[{
        "name": "ПРОДУКТ МЯСНОЙ ИЗ СВИНИНЫ",
        "grams": 25,
        "kcal": 60,
        "protein": 3.8,
        "fat": 4.8,
        "carbs": 0.5,
    }])
    msg = message()
    monkeypatch.setattr(meals, "_hide_meal_reply_keyboard", AsyncMock())

    asyncio.run(meals._show_dish_builder(msg, state))

    assert msg.answer.await_args.args[0] == (
        "🥣 <b>Новое блюдо</b>\n\n"
        "Добавленные ингредиенты:\n\n"
        "1️⃣ <b>ПРОДУКТ МЯСНОЙ ИЗ СВИНИНЫ (25 г)</b>\n"
        "60 ккал (Б 3.8 / Ж 4.8 / У 0.5)\n\n"
        "<b>Итого по блюду:</b>\n"
        "🔥 <b>Калории:</b> 60 ккал\n"
        "🥩 <b>Белки:</b> 3.8 г\n"
        "🥑 <b>Жиры:</b> 4.8 г\n"
        "🍚 <b>Углеводы:</b> 0.5 г"
    )


def test_recipe_builder_offers_saved_dishes_with_stable_callback():
    keyboard = meals._build_dish_builder_keyboard([], "B" * 22)
    buttons = [button for row in keyboard.inline_keyboard for button in row]
    saved_dishes = next(button for button in buttons if button.text == "🍽 Выбрать из моих блюд")
    assert saved_dishes.callback_data == "dish_from_saved:" + "B" * 12 + ":1"


def test_saved_dish_is_added_to_recipe_as_aggregate_ingredient(db, monkeypatch):
    saved = dish_service.DishService.save_photo_dish_entry(
        save_token="saved-dish", user_id="42", dish_name="Рис с овощами",
        items=[item()], entry_date=date(2026, 9, 12), meal_type="lunch",
    ).dish
    state = builder_state()
    state.data["dish_builder"]["kind"] = "recipe"
    cb = SimpleNamespace(
        data=f"dish_saved_pick:{'B' * 12}:{saved.id}",
        message=message(), from_user=SimpleNamespace(id=42), answer=AsyncMock(),
    )
    show = AsyncMock()
    monkeypatch.setattr(meals, "_show_dish_builder", show)

    asyncio.run(meals.dish_builder_saved_dish_pick(cb, state))

    assert state.data["dish_builder"]["items"] == [{
        "name": "Рис с овощами", "grams": 100.0, "kcal": 350.0,
        "protein": 7.0, "fat": 1.0, "carbs": 78.0,
    }]
    show.assert_awaited_once_with(cb.message, state, edit=True)


def test_saved_dish_picker_excludes_recipe_being_edited(db, monkeypatch):
    recipe = recipe_service.save_recipe(
        user_id="42", token="recipe", name="Каша", items=[item()],
        cooking_method=None, cooked_weight_g=None,
    )
    state = builder_state()
    state.data["dish_builder"].update(kind="recipe", recipe_id=recipe.id)
    msg = message()
    monkeypatch.setattr(meals, "_edit_or_send_photo_analysis_message", AsyncMock())

    asyncio.run(meals._show_builder_saved_dishes(msg, state, user_id="42", page=1))

    markup = meals._edit_or_send_photo_analysis_message.await_args.kwargs["reply_markup"]
    callbacks = [button.callback_data for row in markup.inline_keyboard for button in row]
    assert not any(value.startswith("dish_saved_pick:") for value in callbacks)
    assert callbacks[-1] == "dish_saved_back:" + "B" * 12


def test_back_discards_nested_draft_and_keeps_ingredients(monkeypatch):
    state = builder_state(ai_pending_meal={"items": [item()]}, photo_save_token="old")
    state.data["dish_builder"]["items"] = [item()]
    monkeypatch.setattr(meals, "_show_dish_builder", AsyncMock())
    asyncio.run(meals._return_to_add_methods_from_method_input(message(), state))
    assert state.data["dish_builder"]["items"] == [item()]
    assert "ai_pending_meal" not in state.data
    assert "photo_save_token" not in state.data


def test_name_generation_does_not_use_meal_text_quota(monkeypatch):
    state = builder_state()
    state.data["dish_builder"]["items"] = [item()]
    reserve = AsyncMock(side_effect=AssertionError("recipe names must not use meal text quota"))
    consume = Mock(side_effect=AssertionError("recipe names must not consume meal text quota"))
    generate = Mock(return_value="Рисовая каша")
    monkeypatch.setattr(meals, "_reserve_meal_ai_quota", reserve)
    monkeypatch.setattr(meals.ai_quota_service, "consume", consume)
    monkeypatch.setattr(meals, "generate_recipe_name", generate)
    asyncio.run(meals.dish_builder_name_input(message("✨ Сгенерировать название"), state))
    reserve.assert_not_awaited()
    consume.assert_not_called()
    generate.assert_called_once()
    assert state.data["dish_builder"]["name_generation_count"] == 1


def test_name_generation_uses_openai_before_deepseek(monkeypatch):
    budget = Mock(return_value=nullcontext())
    monkeypatch.setattr(meals.openai_token_budget_service, "reservation", budget)
    openai = Mock(return_value="Рисовая каша")
    deepseek = Mock(side_effect=AssertionError("DeepSeek must not run after OpenAI success"))
    monkeypatch.setattr(meals.openai_text_service, "analyze_activity_prompt", openai)
    monkeypatch.setattr(meals, "generate_recipe_name", deepseek)

    result = asyncio.run(
        meals._generate_recipe_name_with_text_fallbacks(
            [item()], None, user_id="42", previous_name=None,
        )
    )

    assert result == "Рисовая каша"
    assert budget.call_args.kwargs["feature"] == "recipe_name"
    assert openai.call_args.kwargs["feature"] == "recipe_name"
    deepseek.assert_not_called()


def test_name_generation_uses_yandex_after_openai_and_deepseek_fail(monkeypatch):
    monkeypatch.setattr(meals.openai_token_budget_service, "reservation", lambda **_: nullcontext())
    monkeypatch.setattr(
        meals.openai_text_service,
        "analyze_activity_prompt",
        Mock(side_effect=RuntimeError("openai unavailable")),
    )
    monkeypatch.setattr(
        meals,
        "generate_recipe_name",
        Mock(side_effect=RuntimeError("deepseek unavailable")),
    )
    yandex = AsyncMock(return_value="Рисовая каша")
    monkeypatch.setattr(meals.yandex_ai_service, "analyze_activity_prompt", yandex)

    result = asyncio.run(
        meals._generate_recipe_name_with_text_fallbacks(
            [item()], "boil", user_id="42", previous_name="Рис с молоком",
        )
    )

    assert result == "Рисовая каша"
    assert yandex.await_args.kwargs["feature"] == "recipe_name"


def test_recipe_full_handler_flow_saves_template_only(db, monkeypatch):
    state = builder_state()
    state.data["dish_builder"].update(kind="recipe", items=[item()])
    monkeypatch.setattr(meals, "_hide_meal_reply_keyboard", AsyncMock())
    monkeypatch.setattr(meals, "_show_recipes", AsyncMock())

    async def run():
        await meals._save_composed_dish(message(), state, "Рисовая каша")
        assert state.current == meals.DishBuilderStates.cooking_method
        cb = SimpleNamespace(data="recipe_method:" + "B" * 12 + ":boil", answer=AsyncMock(), message=message())
        await meals.recipe_method_selected(cb, state)
        await meals.recipe_weight_input(message("300"), state)
        final_message = message("Сварить рис.")
        await meals.recipe_preparation_input(final_message, state)
        with db() as session:
            assert session.query(Meal).count() == 0
            saved = session.query(Dish).one()
            assert saved.cooked_weight_g == 300
            assert saved.preparation == "Сварить рис."
        assert "dish_builder" not in state.data
        meals._show_recipes.assert_not_awaited()
        result_message = final_message.answer.await_args.args[0]
        assert result_message.startswith("🎉 <b>Рецепт создан!</b>")
        assert "🥣 <b>Рисовая каша</b>" in result_message
        assert "1️⃣ <b>Рис (300 г)</b>" in result_message
        assert "📦 <b>Общий вес:</b> 300 г" in result_message
        assert "🔥 <b>Калории:</b> 350 ккал" in result_message
        assert "🥩 <b>Белки:</b> 7.0 г" in result_message
        assert "🥑 <b>Жиры:</b> 1.0 г" in result_message
        assert "🍚 <b>Углеводы:</b> 78.0 г" in result_message
        assert "📖 <b>Приготовление:</b>\nСварить рис." in result_message
        keyboard = final_message.answer.await_args.kwargs["reply_markup"]
        assert [(button.text, button.callback_data) for row in keyboard.inline_keyboard for button in row] == [
            ("✏️ Редактировать", f"my_dish_edit:{saved.id}"),
            ("⬅️ Назад к рецептам", "recipes:1"),
        ]
        assert state.data["recipe_catalog"] is True
    asyncio.run(run())


@pytest.mark.parametrize("method", ["fry", "bake", "none"])
def test_only_boiling_requests_cooked_weight(monkeypatch, method):
    state = builder_state()
    state.data["dish_builder"].update(kind="recipe", cooked_weight_g=500)
    monkeypatch.setattr(meals, "_hide_meal_reply_keyboard", AsyncMock())
    msg = message()
    cb = SimpleNamespace(data=f"recipe_method:{'B' * 12}:{method}", answer=AsyncMock(), message=msg)

    asyncio.run(meals.recipe_method_selected(cb, state))

    assert state.current == meals.DishBuilderStates.preparation
    assert state.data["dish_builder"]["cooked_weight_g"] is None
    assert "Опиши приготовление" in msg.answer.await_args.args[0]


def test_boiling_explains_when_to_enter_total_cooked_weight(monkeypatch):
    state = builder_state()
    state.data["dish_builder"]["kind"] = "recipe"
    monkeypatch.setattr(meals, "_hide_meal_reply_keyboard", AsyncMock())
    msg = message()
    cb = SimpleNamespace(data=f"recipe_method:{'B' * 12}:boil", answer=AsyncMock(), message=msg)

    asyncio.run(meals.recipe_method_selected(cb, state))

    assert state.current == meals.DishBuilderStates.cooked_weight
    prompt = msg.answer.await_args.args[0]
    assert "Если при варке ты добавлял воду" in prompt
    assert "общий вес блюда после приготовления" in prompt
    assert "КБЖУ продуктов будут рассчитаны на этот общий вес" in prompt


def test_preparation_back_skips_weight_for_non_boiling_method(monkeypatch):
    state = builder_state()
    state.data["dish_builder"].update(kind="recipe", cooking_method="fry")
    prompt = AsyncMock()
    monkeypatch.setattr(meals, "_recipe_method_prompt", prompt)

    asyncio.run(meals.recipe_preparation_input(message("⬅️ Назад"), state))

    prompt.assert_awaited_once()


def test_catalog_return_keeps_destination_and_uses_real_user(monkeypatch):
    state = builder_state()
    cb = SimpleNamespace(message=message(), from_user=SimpleNamespace(id=42), answer=AsyncMock())
    cb.message.from_user.id = 999
    screen = AsyncMock()
    monkeypatch.setattr(meals, "_show_ingredient_input_methods", screen)
    asyncio.run(meals.my_products_back_to_current_meal(cb, state))
    assert screen.await_args.kwargs["user_id"] == "42"
    assert state.data["dish_builder"]["token"] == "B" * 22


def test_stale_builder_action_does_not_replace_current_draft(monkeypatch):
    state = builder_state()
    cb = SimpleNamespace(data="dish_more:" + "X" * 12, answer=AsyncMock(), message=message())
    screen = AsyncMock()
    monkeypatch.setattr(meals, "_show_ingredient_input_methods", screen)
    asyncio.run(meals.dish_builder_more(cb, state))
    screen.assert_not_awaited()
    assert cb.answer.await_args.kwargs["show_alert"] is True


def test_name_generation_allows_five_attempts_and_blocks_sixth(monkeypatch):
    state = builder_state()
    state.data["dish_builder"]["items"] = [item()]
    cb = SimpleNamespace(data="dish_ngen:" + "B" * 12, id="callback-2", message=message(),
                         from_user=SimpleNamespace(id=42), answer=AsyncMock())
    cb.message.from_user.id = 999
    generate = Mock(return_value="Рисовая каша")
    monkeypatch.setattr(meals, "generate_recipe_name", generate)

    for _ in range(meals.RECIPE_NAME_GENERATION_LIMIT):
        asyncio.run(meals.dish_builder_generated_name_action(cb, state))

    assert generate.call_args.kwargs["user_id"] == "42"
    assert generate.call_count == 5
    assert state.data["generated_dish_name"] == "Рисовая каша"
    assert state.data["dish_builder"]["name_generation_count"] == 5

    asyncio.run(meals.dish_builder_generated_name_action(cb, state))

    assert generate.call_count == 5
    assert cb.message.answer.await_args.args[0] == meals.RECIPE_NAME_GENERATION_LIMIT_TEXT
    assert cb.message.answer.await_args.kwargs["reply_markup"]


def test_name_generation_limit_is_scoped_to_recipe_draft(monkeypatch):
    generate = Mock(return_value="Рисовая каша")
    monkeypatch.setattr(meals, "generate_recipe_name", generate)
    exhausted = builder_state()
    exhausted.data["dish_builder"].update(items=[item()], name_generation_count=5)
    fresh = builder_state()
    fresh.data["dish_builder"].update(token="C" * 22, items=[item()])

    asyncio.run(meals.dish_builder_name_input(message("✨ Сгенерировать название"), exhausted))
    asyncio.run(meals.dish_builder_name_input(message("✨ Сгенерировать название"), fresh))

    generate.assert_called_once()
    assert exhausted.data["dish_builder"]["name_generation_count"] == 5
    assert fresh.data["dish_builder"]["name_generation_count"] == 1


def test_empty_recipe_list_has_create_and_back_actions(monkeypatch):
    monkeypatch.setattr(meals.DishRepository, "list_active", lambda *args, **kwargs: [])
    monkeypatch.setattr(meals, "_hide_meal_reply_keyboard", AsyncMock())
    msg = message()
    asyncio.run(meals._show_recipes(msg, State(meal_type="lunch"), user_id="42"))
    buttons = msg.answer.await_args.kwargs["reply_markup"].inline_keyboard
    callbacks = [b.callback_data for row in buttons for b in row]
    assert "recipe_create" in callbacks
    assert "recipes_back_to_diary" in callbacks


def test_recipe_card_back_returns_to_recipe_page():
    keyboard = meals._build_saved_dish_card_keyboard("lunch", 2, 7, return_to_recipes=True)
    assert keyboard.inline_keyboard[-1][0].callback_data == "recipes:2"


def test_recipe_portion_asks_for_meal_type_before_saving(db, monkeypatch):
    recipe = recipe_service.save_recipe(
        user_id="42", token="recipe", name="Рисовая каша", items=[item()],
        cooking_method="boil", cooked_weight_g=300,
    )
    state = State(
        recipe_catalog=True,
        meal_type="snack",
        my_dish_id=recipe.id,
        my_dish_items=dish_service.dish_to_snapshot(recipe),
        my_dish_save_token="S" * 22,
        my_dishes_return_entry_date="2026-09-12",
    )
    callback = SimpleNamespace(
        data=f"my_dish_add:{recipe.id}", message=message(),
        from_user=SimpleNamespace(id=42), answer=AsyncMock(),
    )
    keep_open = AsyncMock()
    monkeypatch.setattr(meals, "_keep_meal_entry_open_after_save", keep_open)

    async def scenario():
        await meals.my_dish_add(callback, state)
        assert callback.message.edit_text.await_args.args[0] == "К какому приёму пищи добавить порцию?"
        callbacks = [
            button.callback_data
            for row in callback.message.edit_text.await_args.kwargs["reply_markup"].inline_keyboard
            for button in row
        ]
        assert f"my_dish_add_to:lunch:{recipe.id}" in callbacks
        with db() as session:
            assert session.query(Meal).count() == 0

        callback.data = f"my_dish_add_to:lunch:{recipe.id}"
        await meals.my_dish_add_to_meal(callback, state)

    asyncio.run(scenario())

    keep_open.assert_awaited_once()
    with db() as session:
        saved = session.query(Meal).one()
        assert saved.meal_type == "lunch"
        assert saved.date == date(2026, 9, 12)
