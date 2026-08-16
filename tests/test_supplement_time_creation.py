import asyncio
import os
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from aiogram.types import ReplyKeyboardRemove

os.environ.setdefault("API_TOKEN", "test-token")

from handlers import supplements
from middlewares.supplement_notifications import SupplementNotificationMessageGuard
from utils.supplement_keyboards import (
    days_menu,
    supplement_creation_cancel_menu,
    supplement_catalog_categories_inline_menu,
    supplement_test_time_inline_menu,
    supplement_edit_time_inline_menu,
    supplement_edit_menu,
    supplement_notifications_inline_menu,
)


class _DummyState:
    def __init__(self, data=None, state=None):
        self._data = dict(data or {})
        self._state = state
        self.set_state = AsyncMock(side_effect=self._set_state)
        self.clear = AsyncMock()

    async def _set_state(self, value):
        self._state = value.state if hasattr(value, "state") else value

    async def update_data(self, *args, **kwargs):
        for data in args:
            self._data.update(data)
        self._data.update(kwargs)

    async def get_data(self):
        return dict(self._data)

    async def get_state(self):
        return self._state


def _build_message(text):
    return SimpleNamespace(
        text=text,
        from_user=SimpleNamespace(id=12345),
        bot=SimpleNamespace(),
        answer=AsyncMock(),
    )


def _build_callback(callback_data: str):
    return SimpleNamespace(
        data=callback_data,
        from_user=SimpleNamespace(id=12345),
        message=SimpleNamespace(
            bot=SimpleNamespace(),
            answer=AsyncMock(),
            edit_text=AsyncMock(),
            edit_reply_markup=AsyncMock(),
        ),
        answer=AsyncMock(),
    )


def test_creation_cancel_menu_only_contains_cancel_button():
    keyboard = supplement_creation_cancel_menu()
    button_texts = [button.text for row in keyboard.keyboard for button in row]

    assert button_texts == ["❌ Отменить"]


def test_start_create_supplement_shows_catalog_without_free_name_input():
    message = _build_message("➕ Создать добавку")
    state = _DummyState()

    asyncio.run(supplements.start_create_supplement(message, state))

    state.set_state.assert_awaited_once_with(supplements.SupplementStates.selecting_catalog_item)
    message.answer.assert_awaited_once()
    text, kwargs = message.answer.await_args
    assert "✨ Начинаем создание добавки!" in text[0]
    assert "Шаг 1 из 5" in text[0]
    keyboard = kwargs["reply_markup"]
    buttons = [button for row in keyboard.inline_keyboard for button in row]
    assert any(button.text == "Витамины" for button in buttons)
    assert any(button.text == "❌ Отменить" for button in buttons)
    assert state._data["identifier"] is None
    assert state._data["notifications_enabled"] is False


def test_catalog_callbacks_use_stable_identifiers_and_never_display_names():
    keyboard = supplement_catalog_categories_inline_menu()
    category_callbacks = [
        button.callback_data for row in keyboard.inline_keyboard for button in row
    ]

    assert "sup_catalog:category:vitamins" in category_callbacks
    assert all("Витамин" not in value for value in category_callbacks)


def test_arbitrary_supplement_name_is_rejected_without_state_storage():
    message = _build_message("Моя секретная добавка")
    state = _DummyState(
        {"supplement_id": None, "identifier": None, "name": "", "catalog_mode": "create"},
        supplements.SupplementStates.selecting_catalog_item.state,
    )

    asyncio.run(supplements.handle_supplement_catalog_text(message, state))

    assert state._data["identifier"] is None
    assert state._data["name"] == ""
    assert "нельзя вводить вручную" in message.answer.await_args.args[0]


def test_catalog_item_selection_stores_identifier_and_opens_time_step():
    callback = _build_callback("sup_catalog:item:magnesium")
    state = _DummyState(
        {"supplement_id": None, "catalog_mode": "create", "times": []},
        supplements.SupplementStates.selecting_catalog_item.state,
    )

    asyncio.run(supplements.handle_supplement_catalog_callback(callback, state))

    assert state._data["identifier"] == "magnesium"
    assert state._data["name"] == "Магний"
    assert state._state == supplements.SupplementStates.entering_time.state
    assert "Название:</b> Магний" in callback.message.answer.await_args.args[0]


def test_completed_creation_persists_identifier_instead_of_display_name():
    message = _build_message("✅ Выключить")
    state = _DummyState({
        "supplement_id": None,
        "identifier": "magnesium",
        "name": "Магний",
        "times": ["09:00"],
        "days": ["Пн"],
        "duration": "постоянно",
        "notifications_enabled": False,
    })

    with patch(
        "handlers.supplements.SupplementRepository.save_supplement",
        return_value=7,
    ) as save_supplement, patch("handlers.supplements.push_menu_stack"):
        asyncio.run(supplements.save_supplement_from_test(message, state))

    payload = save_supplement.call_args.args[1]
    assert payload["identifier"] == "magnesium"
    assert payload["name"] == "Магний"
    assert "Магний" in message.answer.await_args.args[0]


def test_supplement_edit_menu_replaces_save_and_cancel_with_back_button():
    keyboard = supplement_edit_menu(show_save=True)
    button_texts = [button.text for row in keyboard.keyboard for button in row]

    assert "💾 Сохранить" not in button_texts
    assert "❌ Отменить" not in button_texts
    assert button_texts[-1] == "⬅️ Назад"


def test_create_time_inline_menu_contains_hours_from_6_to_23_and_actions():
    keyboard = supplement_test_time_inline_menu([])
    buttons = [button for row in keyboard.inline_keyboard for button in row]

    assert buttons[0].text == "06:00"
    assert buttons[0].callback_data == "sup_create_time:add:06:00"
    assert any(button.text == "23:00" for button in buttons)
    assert not any(button.text == "05:00" for button in buttons)
    assert any(button.text == "⏭️ Пропустить" for button in buttons)


def test_create_time_inline_menu_marks_selected_time_and_shows_save():
    keyboard = supplement_test_time_inline_menu(["09:00"])
    buttons = [button for row in keyboard.inline_keyboard for button in row]

    assert any(button.text == "✅ 09:00" for button in buttons)
    assert any(button.text == "💾 Сохранить время" for button in buttons)


def test_selected_time_prompt_no_longer_offers_unavailable_skip_action():
    text = supplements.build_supplement_time_step_text("Магний", ["09:00"])

    assert "💾 Сохранить время" in text
    assert "⏭️ Пропустить" not in text


def test_creation_days_menu_contains_all_actions_including_skip():
    keyboard = days_menu(["Пн"], show_cancel=True, show_skip=True)
    button_texts = [button.text for row in keyboard.keyboard for button in row]

    assert "✅ Пн" in button_texts
    assert "Вт" in button_texts
    assert "Выбрать все" in button_texts
    assert "💾 Сохранить" in button_texts
    assert "⏭️ Пропустить" in button_texts
    assert "⬅️ Назад" in button_texts
    assert "❌ Отменить" in button_texts


def test_edit_days_menu_does_not_offer_creation_skip_action():
    keyboard = days_menu(["Пн"])
    button_texts = [button.text for row in keyboard.keyboard for button in row]

    assert "⏭️ Пропустить" not in button_texts


def test_edit_time_inline_menu_contains_hours_and_save_action():
    keyboard = supplement_edit_time_inline_menu(["09:00"])
    buttons = [button for row in keyboard.inline_keyboard for button in row]

    assert buttons[0].text == "06:00"
    assert buttons[0].callback_data == "sup_edit_time:toggle:06:00"
    assert any(button.text == "✅ 09:00" for button in buttons)
    assert any(button.text == "23:00" for button in buttons)
    assert any(button.text == "💾 Сохранить время" for button in buttons)
    assert any(button.text == "⬅️ Назад" for button in buttons)


def test_edit_supplement_time_shows_inline_time_buttons():
    message = _build_message("✏️ Редактировать время")
    state = _DummyState({"supplement_id": 7, "name": "Магний", "times": ["09:00"]})

    asyncio.run(supplements.edit_supplement_time(message, state))

    state.set_state.assert_awaited_once_with(supplements.SupplementStates.entering_time)
    message.answer.assert_awaited_once()
    text, kwargs = message.answer.await_args
    assert "Редактирование времени" in text[0]
    assert "Магний" in text[0]
    keyboard = kwargs["reply_markup"]
    assert any(
        button.callback_data == "sup_edit_time:toggle:09:00" and button.text == "✅ 09:00"
        for row in keyboard.inline_keyboard
        for button in row
    )


def test_edit_time_inline_callback_toggles_selected_time():
    callback = _build_callback("sup_edit_time:toggle:09:00")
    state = _DummyState(
        {"supplement_id": 7, "name": "Магний", "times": ["09:00"], "days": []},
        supplements.SupplementStates.entering_time.state,
    )

    asyncio.run(supplements.handle_edit_supplement_time_callback(callback, state))

    assert state._data["times"] == []
    callback.answer.assert_awaited_once_with("Удалено 09:00")
    callback.message.edit_text.assert_awaited_once()
    _, kwargs = callback.message.edit_text.await_args
    keyboard = kwargs["reply_markup"]
    assert any(
        button.callback_data == "sup_edit_time:toggle:09:00" and button.text == "09:00"
        for row in keyboard.inline_keyboard
        for button in row
    )


def test_parse_supplement_time_input_accepts_digits():
    assert supplements.parse_supplement_time_input("9") == "09:00"
    assert supplements.parse_supplement_time_input("09") == "09:00"
    assert supplements.parse_supplement_time_input("930") == "09:30"
    assert supplements.parse_supplement_time_input("09:30") == "09:30"
    assert supplements.parse_supplement_time_input("2360") is None


def test_manual_digit_time_is_added_during_creation():
    message = _build_message("930")
    state = _DummyState({"supplement_id": None, "name": "Магний", "times": []})

    asyncio.run(supplements.handle_time_value(message, state))

    assert state._data["times"] == ["09:30"]
    message.answer.assert_awaited_once()
    _, kwargs = message.answer.await_args
    keyboard = kwargs["reply_markup"]
    assert any(
        button.text == "💾 Сохранить время"
        for row in keyboard.inline_keyboard
        for button in row
    )


def test_inline_time_callback_adds_selected_time():
    callback = _build_callback("sup_create_time:add:06:00")
    state = _DummyState(
        {"supplement_id": None, "name": "Магний", "times": []},
        supplements.SupplementStates.entering_time.state,
    )

    asyncio.run(supplements.handle_create_supplement_time_callback(callback, state))

    assert state._data["times"] == ["06:00"]
    callback.answer.assert_awaited_once_with("Добавлено 06:00")
    callback.message.edit_text.assert_awaited_once()
    _, kwargs = callback.message.edit_text.await_args
    keyboard = kwargs["reply_markup"]
    assert any(
        button.text == "✅ 06:00"
        for row in keyboard.inline_keyboard
        for button in row
    )


def test_inline_time_save_moves_to_days_step():
    callback = _build_callback("sup_create_time:save")
    state = _DummyState(
        {"supplement_id": None, "name": "Магний", "times": ["06:00"]},
        supplements.SupplementStates.entering_time.state,
    )

    with patch("handlers.supplements.push_menu_stack"):
        asyncio.run(supplements.handle_create_supplement_time_callback(callback, state))

    state.set_state.assert_awaited_with(supplements.SupplementStates.selecting_days)
    callback.message.edit_reply_markup.assert_awaited_once_with(reply_markup=None)
    callback.message.answer.assert_awaited_once()
    _, kwargs = callback.message.answer.await_args
    button_texts = [
        button.text for row in kwargs["reply_markup"].keyboard for button in row
    ]
    assert "⏭️ Пропустить" in button_texts


def test_skip_days_clears_selection_and_moves_to_duration_step():
    message = _build_message("⏭️ Пропустить")
    state = _DummyState(
        {
            "supplement_id": None,
            "name": "Магний",
            "times": ["09:00"],
            "days": ["Пн", "Ср"],
        },
        supplements.SupplementStates.selecting_days.state,
    )

    with patch("handlers.supplements.push_menu_stack") as push_menu_stack:
        asyncio.run(supplements.toggle_day(message, state))

    assert state._data["days"] == []
    assert state._data["times"] == ["09:00"]
    assert state._state == supplements.SupplementStates.choosing_duration.state
    message.answer.assert_awaited_once()
    text, kwargs = message.answer.await_args
    assert "Дни пропущены" in text[0]
    button_texts = [
        button.text for row in kwargs["reply_markup"].keyboard for button in row
    ]
    assert "Постоянно" in button_texts
    assert "⏭️ Пропустить" in button_texts
    assert "⬅️ Назад" in button_texts
    assert "❌ Отменить" in button_texts
    pushed_keyboard = push_menu_stack.call_args.args[1]
    assert [
        button.text for row in pushed_keyboard.keyboard for button in row
    ] == button_texts


def test_creation_continues_through_duration_and_notifications_after_days_skip():
    message = _build_message("⏭️ Пропустить")
    state = _DummyState(
        {
            "supplement_id": None,
            "identifier": "magnesium",
            "name": "Магний",
            "times": ["09:00"],
            "days": [],
        },
        supplements.SupplementStates.choosing_duration.state,
    )

    with patch("handlers.supplements.push_menu_stack"):
        asyncio.run(supplements.handle_duration_or_notifications(message, state))

    assert state._data["duration"] == "постоянно"
    assert state._data["days"] == []
    assert state._state == supplements.SupplementStates.choosing_notifications.state
    assert "Шаг 5" in message.answer.await_args.args[0]
    assert isinstance(message.answer.await_args.kwargs["reply_markup"], ReplyKeyboardRemove)
    inline_markup = message.answer.return_value.edit_reply_markup.await_args.kwargs[
        "reply_markup"
    ]
    notification_buttons = [
        button.text
        for row in inline_markup.inline_keyboard
        for button in row
    ]
    assert "✅ Включить" in notification_buttons
    assert "⏭️ Пропустить" in notification_buttons
    assert "⬅️ Назад" in notification_buttons
    assert "❌ Отменить" in notification_buttons
    assert "❌ Выключить" not in notification_buttons

    callback = _build_callback("sup_notifications:skip")
    with patch(
        "handlers.supplements.save_supplement_from_test", new=AsyncMock()
    ) as save_supplement:
        asyncio.run(supplements.handle_supplement_notifications_callback(callback, state))

    assert state._data["notifications_enabled"] is False
    save_supplement.assert_awaited_once_with(
        callback.message,
        state,
        user_id="12345",
    )


def test_creation_notification_inline_menu_never_contains_disable():
    keyboard = supplement_notifications_inline_menu(
        creation=True,
        notifications_enabled=True,
    )
    buttons = [button for row in keyboard.inline_keyboard for button in row]

    assert [button.text for button in buttons] == [
        "✅ Включить",
        "⏭️ Пропустить",
        "⬅️ Назад",
        "❌ Отменить",
    ]
    assert all(button.callback_data.startswith("sup_notifications:") for button in buttons)


def test_creation_notification_enable_requires_both_time_and_days():
    callback = _build_callback("sup_notifications:enable")
    state = _DummyState(
        {
            "supplement_id": None,
            "notification_mode": "create",
            "times": ["09:00"],
            "days": [],
            "notifications_enabled": False,
        },
        supplements.SupplementStates.choosing_notifications.state,
    )

    with patch(
        "handlers.supplements.save_supplement_from_test", new=AsyncMock()
    ) as save_supplement:
        asyncio.run(supplements.handle_supplement_notifications_callback(callback, state))

    assert state._state == supplements.SupplementStates.choosing_notifications.state
    assert state._data["notifications_enabled"] is False
    save_supplement.assert_not_awaited()
    callback.message.edit_text.assert_awaited_once()
    error_text = callback.message.edit_text.await_args.args[0]
    assert "Время:</b> 09:00" in error_text
    assert "Дни:</b> не выбрано" in error_text
    assert (
        "Вернись назад и укажи время и дни приёма либо пропусти настройку уведомлений."
        in error_text
    )


def test_creation_notification_enable_saves_when_schedule_is_complete():
    callback = _build_callback("sup_notifications:enable")
    state = _DummyState(
        {
            "supplement_id": None,
            "notification_mode": "create",
            "times": ["09:00"],
            "days": ["Пн"],
            "notifications_enabled": False,
        },
        supplements.SupplementStates.choosing_notifications.state,
    )

    with patch(
        "handlers.supplements.save_supplement_from_test", new=AsyncMock()
    ) as save_supplement:
        asyncio.run(supplements.handle_supplement_notifications_callback(callback, state))

    assert state._data["notifications_enabled"] is True
    save_supplement.assert_awaited_once_with(
        callback.message,
        state,
        user_id="12345",
    )


def test_creation_notification_back_restores_duration_reply_menu():
    callback = _build_callback("sup_notifications:back")
    state = _DummyState(
        {
            "supplement_id": None,
            "notification_mode": "create",
            "duration": "14 дней",
            "times": ["09:00"],
            "days": ["Пн"],
        },
        supplements.SupplementStates.choosing_notifications.state,
    )

    with patch("handlers.supplements.push_menu_stack"):
        asyncio.run(supplements.handle_supplement_notifications_callback(callback, state))

    assert state._state == supplements.SupplementStates.choosing_duration.state
    assert state._data["notification_mode"] is None
    reply_markup = callback.message.answer.await_args.kwargs["reply_markup"]
    button_texts = [button.text for row in reply_markup.keyboard for button in row]
    assert "Постоянно" in button_texts
    assert "⏭️ Пропустить" in button_texts
    assert "⬅️ Назад" in button_texts
    assert "❌ Отменить" in button_texts


def test_creation_notification_cancel_uses_callback_user_and_clears_state():
    callback = _build_callback("sup_notifications:cancel")
    state = _DummyState(
        {
            "supplement_id": None,
            "notification_mode": "create",
        },
        supplements.SupplementStates.choosing_notifications.state,
    )

    with patch(
        "handlers.supplements.supplements", new=AsyncMock()
    ) as show_supplements:
        asyncio.run(supplements.handle_supplement_notifications_callback(callback, state))

    state.clear.assert_awaited_once()
    show_supplements.assert_awaited_once_with(
        callback.message,
        user_id="12345",
    )


def test_notification_step_message_guard_blocks_stale_reply_actions():
    middleware = SupplementNotificationMessageGuard()
    handler = AsyncMock()
    message = _build_message("💊 Добавки")
    state = _DummyState(
        {"notification_mode": "create"},
        supplements.SupplementStates.choosing_notifications.state,
    )

    result = asyncio.run(middleware(handler, message, {"state": state}))

    assert result is None
    handler.assert_not_awaited()
    assert state._state == supplements.SupplementStates.choosing_notifications.state
    assert isinstance(message.answer.await_args.kwargs["reply_markup"], ReplyKeyboardRemove)


def test_edit_notifications_uses_inline_disable_only_when_currently_enabled():
    message = _build_message("🔔 Уведомления")
    state = _DummyState(
        {
            "supplement_id": 7,
            "name": "Магний",
            "times": ["09:00"],
            "days": ["Пн"],
            "notifications_enabled": True,
        },
        supplements.SupplementStates.editing_supplement.state,
    )

    asyncio.run(supplements.toggle_notifications(message, state))

    assert state._state == supplements.SupplementStates.choosing_notifications.state
    inline_markup = message.answer.return_value.edit_reply_markup.await_args.kwargs[
        "reply_markup"
    ]
    button_texts = [
        button.text for row in inline_markup.inline_keyboard for button in row
    ]
    assert "❌ Выключить" in button_texts
    assert "✅ Включить" not in button_texts
    assert "⏭️ Пропустить" not in button_texts

    disabled_keyboard = supplement_notifications_inline_menu(
        creation=False,
        notifications_enabled=False,
    )
    disabled_button_texts = [
        button.text
        for row in disabled_keyboard.inline_keyboard
        for button in row
    ]
    assert "✅ Включить" in disabled_button_texts
    assert "❌ Выключить" not in disabled_button_texts


def test_edit_notification_disable_returns_to_edit_menu():
    callback = _build_callback("sup_notifications:disable")
    state = _DummyState(
        {
            "supplement_id": 7,
            "notification_mode": "edit",
            "name": "Магний",
            "times": ["09:00"],
            "days": ["Пн"],
            "duration": "постоянно",
            "notifications_enabled": True,
        },
        supplements.SupplementStates.choosing_notifications.state,
    )

    with patch("handlers.supplements.push_menu_stack"):
        asyncio.run(supplements.handle_supplement_notifications_callback(callback, state))

    assert state._data["notifications_enabled"] is False
    assert state._state == supplements.SupplementStates.editing_supplement.state
    callback.message.answer.assert_awaited_once()
    reply_markup = callback.message.answer.await_args.kwargs["reply_markup"]
    assert hasattr(reply_markup, "keyboard")


def test_back_from_duration_restores_selected_days_with_skip_action():
    message = _build_message("⬅️ Назад")
    state = _DummyState(
        {
            "supplement_id": None,
            "name": "Магний",
            "times": ["09:00"],
            "days": ["Пн", "Ср"],
        },
        supplements.SupplementStates.choosing_duration.state,
    )

    with patch("handlers.supplements.push_menu_stack"):
        asyncio.run(supplements.handle_duration_or_notifications(message, state))

    assert state._state == supplements.SupplementStates.selecting_days.state
    button_texts = [
        button.text
        for row in message.answer.await_args.kwargs["reply_markup"].keyboard
        for button in row
    ]
    assert "✅ Пн" in button_texts
    assert "✅ Ср" in button_texts
    assert "⏭️ Пропустить" in button_texts
    assert "⬅️ Назад" in button_texts
    assert "❌ Отменить" in button_texts


def test_inline_time_callback_resumes_from_unselected_days_step():
    callback = _build_callback("sup_create_time:add:07:00")
    state = _DummyState(
        {"supplement_id": None, "name": "Магний", "times": [], "days": []},
        supplements.SupplementStates.selecting_days.state,
    )

    asyncio.run(supplements.handle_create_supplement_time_callback(callback, state))

    assert state._state == supplements.SupplementStates.entering_time.state
    assert state._data["times"] == ["07:00"]
    callback.answer.assert_awaited_once_with("Добавлено 07:00")
    callback.message.edit_text.assert_awaited_once()


def test_inline_time_callback_still_rejects_after_days_selected():
    callback = _build_callback("sup_create_time:add:07:00")
    state = _DummyState(
        {"supplement_id": None, "name": "Магний", "times": ["06:00"], "days": ["Пн"]},
        supplements.SupplementStates.selecting_days.state,
    )

    asyncio.run(supplements.handle_create_supplement_time_callback(callback, state))

    assert state._state == supplements.SupplementStates.selecting_days.state
    assert state._data["times"] == ["06:00"]
    callback.answer.assert_awaited_once_with("Этот шаг уже завершён", show_alert=True)
    callback.message.edit_text.assert_not_awaited()


def test_supplement_delete_button_asks_for_confirmation_before_delete():
    message = _build_message("🗑 Удалить добавку")
    state = _DummyState({"viewing_supplement_id": 7, "viewing_index": 0})

    with patch(
        "handlers.supplements.SupplementRepository.get_supplements",
        return_value=[{"id": 7, "name": "Магний", "times": [], "days": []}],
    ), patch(
        "handlers.supplements.SupplementRepository.delete_supplement"
    ) as delete_supplement, patch("handlers.supplements.push_menu_stack"):
        asyncio.run(supplements.delete_supplement(message, state))

    delete_supplement.assert_not_called()
    state.set_state.assert_awaited_once_with(supplements.SupplementStates.confirming_delete)
    assert state._data["delete_supplement_id"] == 7
    assert state._data["delete_supplement_name"] == "Магний"
    message.answer.assert_awaited_once()
    text, kwargs = message.answer.await_args
    assert "Вы точно хотите удалить добавку «Магний»" in text[0]
    buttons = [button.text for row in kwargs["reply_markup"].keyboard for button in row]
    assert "✅ Да, удалить добавку" in buttons
    assert "❌ Отменить удаление" in buttons


def test_supplement_delete_confirmation_deletes_selected_supplement():
    message = _build_message("✅ Да, удалить добавку")
    state = _DummyState({"delete_supplement_id": 7, "delete_supplement_name": "Магний"})

    with patch(
        "handlers.supplements.SupplementRepository.delete_supplement",
        return_value=True,
    ) as delete_supplement, patch(
        "handlers.supplements.supplements_list_view", new=AsyncMock()
    ) as supplements_list_view:
        asyncio.run(supplements.confirm_delete_supplement(message, state))

    delete_supplement.assert_called_once_with("12345", 7)
    message.answer.assert_awaited_once_with("🗑 Добавка Магний удалена.")
    state.clear.assert_awaited_once()
    supplements_list_view.assert_awaited_once_with(message, state)
