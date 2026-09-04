import asyncio
import json
from contextlib import contextmanager
from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from database.models import Base, Meal
import database.repositories.dish_repository as dish_repository
import database.repositories.meal_repository as meal_repository
import services.dish_service as dish_service
from handlers import meals
from utils.meal_formatters import format_meal_message
from utils.pagination import PAGINATION_NOOP_CALLBACK


class State:
    def __init__(self, **data):
        self.data = data
        self.set_state = AsyncMock()

    async def get_data(self):
        return dict(self.data)

    async def update_data(self, **data):
        self.data.update(data)


def callback(data):
    return SimpleNamespace(
        data=data, from_user=SimpleNamespace(id=42), answer=AsyncMock(),
        message=SimpleNamespace(
            answer=AsyncMock(), edit_text=AsyncMock(), bot=SimpleNamespace(),
        ),
    )


def indicator(markup):
    return next(
        (button.text for row in markup.inline_keyboard for button in row
         if button.callback_data == PAGINATION_NOOP_CALLBACK), None,
    )


@pytest.fixture
def history(monkeypatch):
    # Duplicates cross database batches; each entry contains different sources.
    records = [SimpleNamespace(
        id=index, entry_kind="products",
        products_json=json.dumps([
            {"name": f"Продукт {index % 74}", "source": "manual", "grams": 100},
            {"name": "Фото-продукт", "source": "food_photo", "grams": 120},
        ]),
    ) for index in range(148)]
    monkeypatch.setattr(meals, "MY_PRODUCTS_HISTORY_BATCH_SIZE", 20)
    monkeypatch.setattr(meals.DishRepository, "list_active", lambda *args, **kwargs: [])
    monkeypatch.setattr(
        meals.MealRepository, "get_user_meal_history_page",
        lambda _user, *, offset, limit: records[offset:offset + limit],
    )
    return records


@pytest.mark.parametrize("requested_page,expected_page", [(1, 1), (2, 2), (3, 3), (10, 10), (999, 10), (0, 1)])
def test_catalog_pages_use_complete_filtered_unique_count(history, requested_page, expected_page):
    state = State()
    message = callback("").message
    asyncio.run(meals._show_my_products_page(
        message, state, user_id="42", meal_type="lunch", page=requested_page,
        source_filter="manual", edit_message=True,
    ))
    markup = message.edit_text.await_args.kwargs["reply_markup"]
    assert indicator(markup) == f"{expected_page}/10"
    assert state.data["my_products_page"] == expected_page
    product_buttons = [b for row in markup.inline_keyboard for b in row if b.callback_data.startswith("my_product_pick:")]
    assert len(product_buttons) == (2 if expected_page == 10 else 8)
    assert "Фото-продукт" not in message.edit_text.await_args.args[0]
    callbacks = [b.callback_data for row in markup.inline_keyboard for b in row]
    assert (f"my_products_page:lunch:{expected_page + 1}" in callbacks) == (expected_page < 10)
    message.answer.assert_not_awaited()


@pytest.mark.parametrize("page", [1, 2, 3, 10])
def test_search_and_manual_catalog_show_exact_totals(history, page):
    state = State(my_products_source_filter="manual")
    message = callback("").message
    asyncio.run(meals._show_my_products_search_results(
        message, state, user_id="42", meal_type="dinner", query="Продукт", page=page,
    ))
    assert indicator(message.answer.await_args.kwargs["reply_markup"]) == f"{page}/10"

    query = callback(f"custom_product_page:dinner:{page}")
    asyncio.run(meals.custom_product_page(query, state))
    assert indicator(query.message.edit_text.await_args.kwargs["reply_markup"]) == f"{page}/10"
    assert "Фото-продукт" not in query.message.edit_text.await_args.args[0]


@pytest.mark.parametrize("count,total", [(0, 1), (1, 1), (8, 1), (9, 2), (16, 2)])
def test_page_boundaries_and_empty_history(history, count, total):
    del history[count:]
    items, page, pages = meals._get_my_products_page_items("42", 99, source_filter="manual")
    assert page == pages == total
    assert len(items) == (count - (total - 1) * 8)


@pytest.fixture
def catalog_db(monkeypatch):
    engine = create_engine("sqlite://", poolclass=StaticPool)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)

    @contextmanager
    def session_provider():
        with factory() as session:
            yield session

    for module in (meal_repository, dish_repository, dish_service):
        monkeypatch.setattr(module, "get_db_session", session_provider)
    monkeypatch.setattr(meals.MealRepository, "_track_saved_meal", lambda _user: None)
    yield factory
    engine.dispose()


def save_photo(name="Борщ", *, user_id="42", token="photo-borscht", items=None):
    result = meals.DishService.save_photo_dish_entry(
        save_token=token, user_id=user_id, dish_name=name,
        items=items or [
            {"name": "Свёкла", "grams": 100, "kcal": 40, "protein": 2, "fat": 0, "carbs": 8},
            {"name": "Картофель", "grams": 100, "kcal": 80, "protein": 2, "fat": 0, "carbs": 16},
        ],
        entry_date=date(2026, 8, 15), meal_type="lunch", provider="gemini",
    )
    assert result.status is meals.MealSaveStatus.SAVED
    return result


def test_photo_catalog_contains_whole_dishes_and_products_from_old_and_new_analysis(catalog_db):
    borscht = save_photo()
    banana = save_photo("Банан", token="photo-banana", items=[
        {"name": "Банан", "grams": 120, "kcal": 110, "protein": 1, "fat": 0, "carbs": 26},
    ])
    save_photo("Чужое блюдо", user_id="43", token="other-user")
    archived = save_photo("Архивное блюдо", token="archived")
    assert meals.DishRepository.archive("42", archived.dish.id)
    repeated = meals.DishService.add_saved_dish_to_diary(
        user_id="42", dish_id=borscht.dish.id, save_token="repeat-borscht",
        entry_date=date(2026, 8, 16), meal_type="dinner",
    )
    assert repeated.status is meals.MealSaveStatus.SAVED
    with catalog_db() as session:
        session.add(Meal(user_id="42", raw_query="Старый фотоанализ", products_json=json.dumps([
            {"name": "Яблоко", "source": "gemini", "grams": 100, "kcal": 52},
            {"name": "Груша", "source": "openai", "grams": 100, "kcal": 57},
            {"name": "Ручной продукт", "source": "manual", "grams": 100},
        ])))
        # The standalone template remains in the catalog without a diary row.
        session.query(Meal).filter(Meal.id == banana.meal.id).delete()
        session.commit()

    items = meals._get_my_products_catalog("42", "photo_analysis")
    assert {item.title for item in items} == {"Борщ", "Банан", "Яблоко", "Груша"}
    assert len(items) == 4
    dish_item = next(item for item in items if item.dish_id == borscht.dish.id)
    assert (dish_item.amount_g, dish_item.calories, dish_item.protein, dish_item.carbs) == (200, 120, 4, 24)
    assert [item.title for item in meals._get_my_products_catalog("42", "manual")] == ["Ручной продукт"]
    assert {item.title for item in meals._get_my_products_catalog("42", "all")} == {
        "Борщ", "Банан", "Яблоко", "Груша", "Ручной продукт",
    }
    assert meals._get_all_my_products_for_search("42", "photo_analysis") == items
    keyboard = meals._build_my_products_keyboard(items, "lunch", 1, 1)
    assert any(b.callback_data == f"my_dish_pick:lunch:1:{borscht.dish.id}:products"
               for row in keyboard.inline_keyboard for b in row)
    assert any(b.callback_data.startswith("my_product_pick:lunch:1:")
               for row in keyboard.inline_keyboard for b in row)


@pytest.mark.parametrize("origin", ["products", "search"])
@pytest.mark.parametrize("extra_count", [0, 16])
def test_dish_card_weight_back_and_archive_return_to_original_catalog(catalog_db, origin, extra_count):
    result = save_photo()
    for index in range(extra_count):
        save_photo(f"Борщ {index}", token=f"extra-{index}")
    page = extra_count // 8 + 1
    state = State(
        entry_date="2026-08-20", my_products_source_filter="photo_analysis",
        my_products_search_query="Борщ", my_dishes_return_entry_date="2020-01-01",
    )
    query = callback(f"my_dish_pick:dinner:{page}:{result.dish.id}:{origin}")

    async def scenario():
        await meals.my_dish_pick(query, state)
        assert state.data["my_dishes_return_entry_date"] == "2026-08-20"
        assert "Состав:" in query.message.edit_text.await_args.args[0]
        for handler, command in [
            (meals.my_dish_weight_open, "my_dish_weight"),
            (meals.my_dish_weight_change, "my_dish_wchg"),
            (meals.my_dish_weight_save, "my_dish_wsave"),
        ]:
            query.data = f"{command}:{result.dish.id}" + (":100" if command == "my_dish_wchg" else "")
            await handler(query, state)
        markup = query.message.edit_text.await_args.kwargs["reply_markup"]
        assert markup.inline_keyboard[-1][0].callback_data == "my_dish_catalog_back"
        assert meals.calculate_dish_weight(state.data["my_dish_items"]) == 300
        await meals.my_dish_catalog_back(query, state)
        text = query.message.edit_text.await_args.args[0]
        assert ("Результаты поиска" if origin == "search" else "Мои продукты из анализа еды по фото") in text
        assert state.data["my_products_source_filter"] == "photo_analysis"
        if extra_count:
            assert indicator(query.message.edit_text.await_args.kwargs["reply_markup"]) == "3/3"
        query.data = f"my_dish_archive:{result.dish.id}"
        await meals.my_dish_archive(query, state)
        if extra_count:
            assert indicator(query.message.edit_text.await_args.kwargs["reply_markup"]) == "2/2"
        else:
            assert ("Ничего не нашёл" if origin == "search" else "пока нет продуктов и блюд") in query.message.edit_text.await_args.args[0]

    asyncio.run(scenario())
    assert len(meals._get_my_products_catalog("42", "photo_analysis")) == extra_count
    with catalog_db() as session:
        assert session.query(Meal).count() == extra_count + 1


def test_add_dish_from_photo_folder_preserves_snapshot_and_separate_dishes_button(catalog_db, monkeypatch):
    result = save_photo()
    state = State(entry_date="2026-08-20", my_products_source_filter="photo_analysis")
    query = callback(f"my_dish_pick:dinner:1:{result.dish.id}:products")
    keep_open = AsyncMock()
    monkeypatch.setattr(meals, "_keep_meal_entry_open_after_save", keep_open)

    async def scenario():
        await meals.my_dish_pick(query, state)
        query.data = f"my_dish_weight:{result.dish.id}"
        await meals.my_dish_weight_open(query, state)
        query.data = f"my_dish_wchg:{result.dish.id}:100"
        await meals.my_dish_weight_change(query, state)
        query.data = f"my_dish_wsave:{result.dish.id}"
        await meals.my_dish_weight_save(query, state)
        query.data = f"my_dish_add:{result.dish.id}"
        await meals.my_dish_add(query, state)

    asyncio.run(scenario())
    keep_open.assert_awaited_once()
    with catalog_db() as session:
        saved = session.query(Meal).order_by(Meal.id.desc()).first()
        assert saved.entry_kind == "dish" and saved.dish_id == result.dish.id
        assert saved.date == date(2026, 8, 20) and saved.meal_type == "dinner"
        assert saved.calories == pytest.approx(180)
        assert [i["grams"] for i in json.loads(saved.products_json)] == [150, 150]
        assert "Борщ" in format_meal_message("dinner", [saved])
    items = meals._get_my_products_catalog("42", "photo_analysis")
    assert len(items) == 1 and items[0].amount_g == 200
    entry_markup = meals._build_my_products_entry_keyboard("dinner")
    assert any(b.text == "🍽 Мои блюда" and b.callback_data == "meal_entry_my_dishes:dinner:1"
               for row in entry_markup.inline_keyboard for b in row)


def test_dishes_catalog_is_not_limited_to_repository_default(catalog_db):
    for index in range(51):
        save_photo(f"Блюдо {index}", token=f"dish-{index}")
    page_items, page, total = meals._get_my_products_page_items("42", 1, source_filter="photo_analysis")
    assert (len(page_items), page, total) == (8, 1, 7)
    state = State()
    message = callback("").message
    asyncio.run(meals._show_saved_dishes_page(message, state, user_id="42", meal_type="lunch", page=1))
    assert indicator(message.answer.await_args.kwargs["reply_markup"]) == "1/7"
