import asyncio
from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from handlers import meals


class _DummyState:
    def __init__(self):
        self._data = {}
        self.set_state = AsyncMock()
        self.clear = AsyncMock()

    async def update_data(self, **kwargs):
        self._data.update(kwargs)

    async def get_data(self):
        return dict(self._data)


def _build_message():
    return SimpleNamespace(answer=AsyncMock(), bot=SimpleNamespace())


def _build_callback(callback_data: str):
    callback = SimpleNamespace()
    callback.data = callback_data
    callback.from_user = SimpleNamespace(id=12345)
    callback.message = _build_message()
    callback.answer = AsyncMock()
    return callback


def test_show_input_methods_sends_add_menu():
    message = _build_message()
    message.from_user = SimpleNamespace(id=12345)
    state = _DummyState()

    with patch("handlers.meals.push_menu_stack") as push_stack, patch(
        "handlers.meals.MealRepository.get_recent_unique_meals", return_value=[]
    ):
        asyncio.run(meals._show_input_methods(message, state))

    state.set_state.assert_awaited_once_with(meals.MealEntryStates.choosing_meal_type)
    push_stack.assert_called_once_with(message.bot, meals.kbju_add_menu)
    assert message.answer.await_count == 1


def test_add_meal_from_diary_block_sets_context_and_opens_methods():
    target_date = date.today().isoformat()
    callback = _build_callback(f"add_meal:lunch:{target_date}")
    state = _DummyState()

    with patch("handlers.meals._show_input_methods", new=AsyncMock()) as show_methods:
        asyncio.run(meals.add_meal_from_diary_block(callback, state))

    callback.answer.assert_awaited_once()
    assert state._data["meal_type"] == "lunch"
    assert state._data["entry_date"] == target_date
    show_methods.assert_awaited_once_with(callback.message, state, user_id="12345")


def test_meal_type_navigation_back_supports_hook_arrow():
    message = _build_message()
    message.text = "↩️ Назад"
    state = SimpleNamespace(clear=AsyncMock())

    with patch("handlers.common.go_back", new=AsyncMock()) as go_back:
        asyncio.run(meals.handle_meal_type_menu_navigation(message, state))

    state.clear.assert_awaited_once()
    go_back.assert_awaited_once_with(message, state)


def test_meal_type_navigation_main_menu_alias():
    message = _build_message()
    message.text = "🔄 Главное меню"
    state = SimpleNamespace(clear=AsyncMock())

    with patch("handlers.common.go_main_menu", new=AsyncMock()) as go_main_menu:
        asyncio.run(meals.handle_meal_type_menu_navigation(message, state))

    state.clear.assert_awaited_once()
    go_main_menu.assert_awaited_once_with(message, state)


def test_extract_recent_meal_amount_g_from_products_json():
    meal = SimpleNamespace(products_json='[{"name":"x","grams":40}]')
    assert meals._extract_recent_meal_amount_g(meal) == 40


def test_extract_recent_meal_amount_g_fallback_to_100():
    meal = SimpleNamespace(products_json='[{"name":"x","grams":"oops"}]')
    assert meals._extract_recent_meal_amount_g(meal) == 100


def test_recent_weight_editor_keyboard_uses_tenth_scale_on_first_rows():
    keyboard = meals._build_recent_weight_editor_keyboard()

    rows = keyboard.inline_keyboard
    assert [button.text for button in rows[0]] == ["−100 г", "−50 г", "+50 г", "+100 г"]
    assert [button.callback_data for button in rows[0]] == [
        "recent_wchg:-100",
        "recent_wchg:-50",
        "recent_wchg:50",
        "recent_wchg:100",
    ]
    assert [button.text for button in rows[1]] == ["−25 г", "−10 г", "+10 г", "+25 г"]
    assert [button.callback_data for button in rows[1]] == [
        "recent_wchg:-25",
        "recent_wchg:-10",
        "recent_wchg:10",
        "recent_wchg:25",
    ]


def test_recent_weight_editor_text_bolds_labels_and_uses_kbju_block():
    item = meals.RecentMealItem(
        source_meal_id=7,
        product_index=0,
        title="БАЛТИКА БЕЗАЛКОГОЛЬНОЕ ГРЕЙПФРУТ №0",
        amount_g=500,
        calories=176,
        protein=1.0,
        fat=0.1,
        carbs=41.6,
    )

    text = meals._render_recent_weight_editor_text(item, draft_amount_g=450)

    assert "<b>✏️ Изменение веса продукта</b>" in text
    assert "<b>Продукт:</b> БАЛТИКА БЕЗАЛКОГОЛЬНОЕ ГРЕЙПФРУТ №0" in text
    assert "⚖️ <b>Текущий вес:</b> 500 г" in text
    assert "⚖️ <b>Новый вес:</b> 450 г" in text
    assert "🔥 <b>Калории:</b> 158 ккал" in text
    assert "💪 <b>Белки:</b> 0.9 г" in text
    assert "🥑 <b>Жиры:</b> 0.1 г" in text
    assert "🍩 <b>Углеводы:</b> 37.4 г" in text
    assert "<b>Выбери действие:</b>" in text
    assert "Итого:" not in text
    assert "Б 0.9 / Ж 0.1 / У 37.4" not in text


def test_recent_confirm_text_uses_photo_style_kbju_and_escapes_html():
    item = meals.RecentMealItem(
        source_meal_id=7,
        product_index=0,
        title="Tea <green>",
        amount_g=300,
        calories=0,
        protein=0.3,
        fat=0.0,
        carbs=0.9,
    )

    text = meals._render_recent_meal_confirm_text("dinner", item, amount_g=300)

    assert "🍽 <b>Ужин</b> • <b>Добавить продукт?</b>" in text
    assert "<b>Продукт:</b> Tea &lt;green&gt;" in text
    assert "<b>Tea &lt;green&gt;</b>" not in text
    assert "⚖️ <b>Вес:</b> 300 г" in text
    assert "🔥 <b>Калории:</b> 0 ккал" in text
    assert "💪 <b>Белки:</b> 0.3 г" in text
    assert "🥑 <b>Жиры:</b> 0.0 г" in text
    assert "🍩 <b>Углеводы:</b> 0.9 г" in text
    assert "<b>Выбери действие:</b>" in text
    assert "Tea <green>" not in text


def test_recent_pick_sends_html_parse_mode_for_confirm_card():
    callback = _build_callback("recent_meal_pick:dinner:1:7:0")
    state = _DummyState()
    meal = SimpleNamespace(
        id=7,
        raw_query="Tea 300 г",
        description=None,
        products_json='[{"name":"Tea","grams":300,"kcal":0,"protein":0.3,"fat":0,"carbs":0.9}]',
        calories=0,
        protein=0.3,
        fat=0.0,
        carbs=0.9,
    )

    with patch("handlers.meals.MealRepository.get_meal_by_id", return_value=meal):
        asyncio.run(meals.recent_meal_pick(callback, state))

    assert callback.message.answer.await_args.kwargs["parse_mode"] == "HTML"


def test_label_intro_message_bolds_title_and_send_paragraph():
    message = _build_message()
    message.from_user = SimpleNamespace(id=12345)
    state = _DummyState()
    state._data["meal_type"] = meals.MealType.SNACK.value

    with patch("handlers.meals.push_menu_stack"):
        asyncio.run(meals.kbju_add_via_label(message, state))

    text = message.answer.await_args.args[0]

    assert text.startswith("<b>📋 Анализ этикетки/упаковки</b>")
    assert (
        "<b>Отправь мне фото этикетки или упаковки продукта, "
        "и я найду КБЖУ в тексте! 📸</b>"
    ) in text
    assert message.answer.await_args.kwargs["parse_mode"] == "HTML"


def test_expand_recent_meals_splits_multi_product_entries():
    meal = SimpleNamespace(
        id=7,
        raw_query="Кура гриль 150 г, окрошка 200 г",
        description=None,
        products_json=(
            '[{"name":"Кура гриль","grams":150,"kcal":249,"protein":35,"fat":9,"carbs":16},'
            '{"name":"Окрошка без заправки","grams":200,"kcal":120,"protein":6,"fat":3,"carbs":18}]'
        ),
        calories=369,
        protein=41,
        fat=12,
        carbs=34,
    )

    items = meals._expand_recent_meals([meal])

    assert [item.title for item in items] == ["Кура гриль", "Окрошка без заправки"]
    assert [item.product_index for item in items] == [0, 1]
    assert items[0].amount_g == 150
    assert items[1].calories == 120


def test_format_recent_meals_text_uses_meal_report_bold_style_and_escapes_html():
    item = meals.RecentMealItem(
        source_meal_id=7,
        product_index=0,
        title="Салат <Курочка>",
        amount_g=120,
        calories=67,
        protein=3.1,
        fat=5.3,
        carbs=1.7,
    )

    text = meals._format_recent_meals_text([item], page=1)

    assert "🕘 <b>Недавно добавленные • страница 1</b>" in text
    assert "1. <b>Салат &lt;Курочка&gt;</b>" in text
    assert "<b>120 г • 67 ккал</b>" in text
    assert "<i>Б 3.1 / Ж 5.3 / У 1.7</i>" in text
    assert "<Курочка>" not in text


def test_show_recent_meals_page_sends_html_parse_mode():
    message = _build_message()
    message.from_user = SimpleNamespace(id=12345)
    state = _DummyState()
    meal = SimpleNamespace(
        id=7,
        raw_query="Салат",
        description=None,
        products_json='[{"name":"Салат","grams":120,"kcal":67,"protein":3.1,"fat":5.3,"carbs":1.7}]',
        calories=67,
        protein=3.1,
        fat=5.3,
        carbs=1.7,
    )

    with patch("handlers.meals.MealRepository.get_recent_unique_meals", return_value=[meal]):
        asyncio.run(meals._show_recent_meals_page(message, state, meal_type="snack", page=1))

    assert message.answer.await_args.kwargs["parse_mode"] == "HTML"


def test_recent_search_start_bolds_prompt_first_sentence():
    callback = _build_callback("recent_search_start:snack")
    state = _DummyState()

    asyncio.run(meals.recent_search_start(callback, state))

    text = callback.message.answer.await_args.args[0]
    assert text.startswith("<b>Введите название продукта или часть названия 👇</b>\n\n")
    assert "Например:\nсыр\nйог\nкур" in text


def test_recent_meals_keyboard_has_search_button():
    item = meals.RecentMealItem(
        source_meal_id=7,
        product_index=0,
        title="Сыр творожный",
        amount_g=120,
        calories=180,
        protein=10,
        fat=12,
        carbs=3,
    )

    keyboard = meals._build_recent_meals_keyboard([item], meal_type="snack", page=1, has_prev=False, has_next=False)

    assert keyboard.inline_keyboard[0][0].text == "🔎 Поиск продукта"
    assert keyboard.inline_keyboard[0][0].callback_data == "recent_search_start:snack"


def test_search_recent_items_matches_any_part_case_insensitive():
    items = [
        meals.RecentMealItem(1, 0, "Плавленый сыр сливочный", 100, 250, 12, 20, 3),
        meals.RecentMealItem(2, 0, "Сыр творожный", 100, 180, 11, 12, 4),
        meals.RecentMealItem(3, 0, "Бутерброд с сыром", 100, 310, 13, 14, 32),
        meals.RecentMealItem(4, 0, "Курица гриль", 100, 180, 25, 8, 0),
    ]

    assert [item.title for item in meals._search_recent_items(items, "СЫР")] == [
        "Плавленый сыр сливочный",
        "Сыр творожный",
        "Бутерброд с сыром",
    ]
    assert [item.title for item in meals._search_recent_items(items, "кур")] == ["Курица гриль"]


def test_format_recent_search_results_text_uses_recent_format_and_escapes_query():
    item = meals.RecentMealItem(
        source_meal_id=7,
        product_index=0,
        title="Сыр <творожный>",
        amount_g=120,
        calories=67,
        protein=3.1,
        fat=5.3,
        carbs=1.7,
    )

    text = meals._format_recent_search_results_text("сыр <", [item], page=1)

    assert "🔎 <b>Результаты поиска: сыр &lt;</b>" in text
    assert "1. <b>Сыр &lt;творожный&gt;</b>" in text
    assert "<b>120 г • 67 ккал</b>" in text
    assert "<i>Б 3.1 / Ж 5.3 / У 1.7</i>" in text


def test_recent_search_query_uses_full_user_history_not_recent_page():
    message = _build_message()
    message.from_user = SimpleNamespace(id=12345)
    message.text = "сыр"
    state = _DummyState()
    state._data.update({"meal_type": "snack"})
    meal = SimpleNamespace(
        id=70,
        raw_query="Плавленый сыр сливочный",
        description=None,
        products_json='[{"name":"Плавленый сыр сливочный","grams":50,"kcal":140,"protein":6,"fat":11,"carbs":2}]',
        calories=140,
        protein=6,
        fat=11,
        carbs=2,
    )

    with patch("handlers.meals.MealRepository.get_user_meal_history", return_value=[meal]) as history, patch(
        "handlers.meals.MealRepository.get_recent_unique_meals", return_value=[]
    ) as recent:
        asyncio.run(meals.handle_recent_meal_search_query(message, state))

    history.assert_called_once_with("12345")
    recent.assert_not_called()
    assert message.answer.await_args.kwargs["parse_mode"] == "HTML"
    assert "Плавленый сыр сливочный" in message.answer.await_args.args[0]


def test_recent_search_empty_result_shows_retry_back_and_main_buttons():
    message = _build_message()
    message.from_user = SimpleNamespace(id=12345)
    state = _DummyState()

    with patch("handlers.meals.MealRepository.get_user_meal_history", return_value=[]):
        asyncio.run(
            meals._show_recent_search_results(
                message,
                state,
                user_id="12345",
                meal_type="snack",
                query="сыр",
                page=1,
            )
        )

    text = message.answer.await_args.args[0]
    keyboard = message.answer.await_args.kwargs["reply_markup"]
    assert "Ничего не нашёл 😕" in text
    assert [row[0].text for row in keyboard.inline_keyboard] == [
        "🔎 Искать ещё",
        "⬅️ К недавним продуктам",
        "🔄 Главное меню",
    ]


def test_recent_search_results_keyboard_marks_pick_origin_as_search():
    item = meals.RecentMealItem(
        source_meal_id=7,
        product_index=2,
        title="Патиссоны маринованные",
        amount_g=20,
        calories=7,
        protein=0.2,
        fat=0.1,
        carbs=1.8,
    )

    keyboard = meals._build_recent_search_results_keyboard(
        [item], meal_type="dinner", page=1, has_prev=False, has_next=False
    )

    assert keyboard.inline_keyboard[0][0].callback_data == "recent_meal_pick:dinner:1:7:2:search"


def test_recent_meal_back_returns_to_search_results_when_product_opened_from_search():
    callback = _build_callback("recent_meal_back:dinner:1")
    state = _DummyState()
    state._data.update(
        {
            "recent_pick_origin": "search",
            "recent_search_query": "Марин",
            "recent_search_page": 2,
        }
    )

    with patch("handlers.meals._show_recent_search_results", new=AsyncMock()) as show_search, patch(
        "handlers.meals._show_recent_meals_page", new=AsyncMock()
    ) as show_recent:
        asyncio.run(meals.recent_meal_back(callback, state))

    show_search.assert_awaited_once_with(
        callback.message,
        state,
        user_id="12345",
        meal_type="dinner",
        query="Марин",
        page=2,
    )
    show_recent.assert_not_awaited()


def test_recent_meal_back_returns_to_recent_list_for_regular_recent_pick():
    callback = _build_callback("recent_meal_back:dinner:3")
    state = _DummyState()
    state._data.update({"recent_pick_origin": "recent", "recent_search_query": "Марин"})

    with patch("handlers.meals._show_recent_search_results", new=AsyncMock()) as show_search, patch(
        "handlers.meals._show_recent_meals_page", new=AsyncMock()
    ) as show_recent:
        asyncio.run(meals.recent_meal_back(callback, state))

    show_recent.assert_awaited_once_with(
        callback.message,
        state,
        meal_type="dinner",
        page=3,
        user_id="12345",
    )
    show_search.assert_not_awaited()

def test_recent_confirm_uses_single_selected_product():
    callback = _build_callback("recent_meal_confirm:dinner:1:7:1")
    state = _DummyState()
    meal = SimpleNamespace(
        id=7,
        raw_query="Кура гриль 150 г, окрошка 200 г",
        description=None,
        products_json=(
            '[{"name":"Кура гриль","grams":150,"kcal":249,"protein":35,"fat":9,"carbs":16},'
            '{"name":"Окрошка без заправки","grams":200,"kcal":120,"protein":6,"fat":3,"carbs":18}]'
        ),
        calories=369,
        protein=41,
        fat=12,
        carbs=34,
        api_details=None,
    )
    saved = SimpleNamespace(id=99)

    with patch("handlers.meals.MealRepository.get_meal_by_id", return_value=meal), patch(
        "handlers.meals.MealRepository.save_meal", return_value=saved
    ) as save_meal, patch("handlers.meals._return_to_food_diary", new=AsyncMock()) as return_to_diary:
        asyncio.run(meals.recent_meal_confirm(callback, state))

    save_meal.assert_called_once()
    return_to_diary.assert_awaited_once()
    kwargs = save_meal.call_args.kwargs
    assert kwargs["raw_query"] == "Окрошка без заправки"
    assert kwargs["calories"] == 120
    assert kwargs["protein"] == 6
    assert kwargs["products_json"] == '[{"name": "Окрошка без заправки", "grams": 200, "kcal": 120.0, "protein": 6.0, "fat": 3.0, "carbs": 18.0}]'


def test_edit_last_meal_single_label_product_opens_product_actions_menu():
    meal_date = date.today()
    meal = SimpleNamespace(
        id=42,
        date=meal_date,
        products_json=(
            '[{"name":"Eichbaum Radler Lemon","grams":350,"kcal":112,'
            '"protein":1.8,"fat":1.8,"carbs":27.3}]'
        ),
    )
    message = _build_message()
    message.from_user = SimpleNamespace(id=12345)
    message.text = "✏️ Редактировать"
    message.bot.last_meal_ids = {"12345": 42}
    state = _DummyState()

    with patch("handlers.meals.MealRepository.get_meal_by_id", return_value=meal):
        asyncio.run(meals.edit_last_meal(message, state))

    state.set_state.assert_awaited_once_with(meals.MealEntryStates.editing_meal_weight)
    assert state._data["editing_product_idx"] == 0
    answer_kwargs = message.answer.await_args.kwargs
    answer_text = message.answer.await_args.args[0]
    assert "✏️ Редактирование продукта" in answer_text
    assert "<b>Продукт:</b> Eichbaum Radler Lemon" in answer_text
    assert "<b>Eichbaum Radler Lemon</b>" not in answer_text
    assert "⚖️ <b>Вес:</b>" in answer_text
    button_texts = [button.text for row in answer_kwargs["reply_markup"].inline_keyboard for button in row]
    assert button_texts == ["✏️ Изменить название", "⚖️ Изменить вес", "🧮 Изменить КБЖУ", "🗑 Удалить", "⬅️ Назад"]


def test_return_to_food_diary_sends_diary_menu_and_refreshes_today():
    message = _build_message()

    with patch("handlers.meals.push_menu_stack") as push_stack, patch(
        "handlers.meals._render_day_meals_messages", new=AsyncMock()
    ) as render_day:
        asyncio.run(meals._return_to_food_diary(message, "12345", date.today()))

    push_stack.assert_called_once_with(message.bot, meals.kbju_menu)
    message.answer.assert_awaited_once_with("🍱 Дневник питания", reply_markup=meals.kbju_menu)
    render_day.assert_awaited_once_with(
        message,
        "12345",
        date.today(),
        include_back=False,
        force_refresh=True,
    )


def test_format_label_result_header_bolds_label_and_escapes_product_name():
    assert meals._format_label_result_header("label", "Салат <Курочка>") == (
        "📋 <b>Анализ этикетки:</b> Салат &lt;Курочка&gt;\n"
    )


def test_format_kbju_summary_block_bolds_only_names_by_default():
    text = meals._format_kbju_summary_block(
        {"calories": 67, "protein": 3.1, "fat": 5.3, "carbs": 1.7}
    )

    assert "🔥 <b>Калории:</b> 67 ккал" in text
    assert "💪 <b>Белки:</b> 3.1 г" in text
    assert "🥑 <b>Жиры:</b> 5.3 г" in text
    assert "🍩 <b>Углеводы:</b> 1.7 г" in text
    assert "<b>67 ккал</b>" not in text
