import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from handlers import meals
from utils.sensitive_meal_text import SensitiveDataType, check_sensitive_meal_text


@pytest.mark.parametrize(
    "text",
    [
        "колбаса 200 г",
        "гречка 150 г, куриная грудка 200 г, огурец",
        "тарелка борща и кусок хлеба",
        "творожные вафли 200 г, на 100 г 137 ккал, Б 14.5 Ж 3.5 У 11.7",
        "2 яйца, сыр 50 г, хлеб 30 г",
        "чай с сахаром 10 г",
        "без сахара",
        "раки варёные 300 г",
        "мясо рака 120 г",
        "запекать при температуре 180 градусов",
        "посыпать салат солью",
        "добавить больше салата",
        "100/50 г творога",
        "2/3 порции каши",
        "кефир 2.5%, 200 г",
        "московский салат",
        "бургеры Санкт-Петербург",
        "принимаю пищу: суп 300 г",
        "рецепт борща: свёкла, капуста и мясо",
    ],
)
def test_regular_food_text_is_not_blocked(text):
    result = check_sensitive_meal_text(text)

    assert result.is_sensitive is False
    assert result.reason is None


@pytest.mark.parametrize(
    "text",
    [
        "+7 999 123-45-67",
        "+7 (999) 123-45-67",
        "8 999 123 45 67",
        "8 (999) 123-45-67",
        "+79991234567",
        "89991234567",
    ],
)
def test_russian_phone_numbers_are_blocked(text):
    result = check_sensitive_meal_text(f"мой телефон {text}, колбаса 200 г")

    assert result.is_sensitive is True
    assert result.reason is SensitiveDataType.PHONE


@pytest.mark.parametrize(
    "email",
    [
        "example@mail.ru",
        "user.name@gmail.com",
        "test+food@yandex.ru",
    ],
)
def test_email_addresses_are_blocked(email):
    result = check_sensitive_meal_text(f"email {email}, хлеб 100 г")

    assert result.is_sensitive is True
    assert result.reason is SensitiveDataType.EMAIL


@pytest.mark.parametrize(
    ("text", "expected_reason"),
    [
        ("паспорт: 45 01 123456, суп 300 г", SensitiveDataType.DOCUMENT),
        ("номер паспорта: 45 01 123456", SensitiveDataType.DOCUMENT),
        ("СНИЛС: 123-456-789 00", SensitiveDataType.DOCUMENT),
        ("ИНН 123456789012", SensitiveDataType.DOCUMENT),
        ("ФИО: Иванов Петр Иванович, колбаса 200 г", SensitiveDataType.PERSONAL_IDENTITY),
        ("меня зовут Иванов Петр Иванович, хлеб 30 г", SensitiveDataType.PERSONAL_IDENTITY),
        ("я Иванов Петр Иванович, съел суп", SensitiveDataType.PERSONAL_IDENTITY),
        ("мой адрес: Москва, улица Пушкина, гречка 200 г", SensitiveDataType.ADDRESS),
        ("живу по адресу Москва, хлеб 30 г", SensitiveDataType.ADDRESS),
    ],
)
def test_explicit_identity_document_and_address_data_is_blocked(text, expected_reason):
    result = check_sensitive_meal_text(text)

    assert result.is_sensitive is True
    assert result.reason is expected_reason


@pytest.mark.parametrize(
    "text",
    [
        # Диагнозы и заболевания.
        "у меня диабет, колбаса 200 г",
        "мой диагноз гипертония, сегодня съел борщ",
        "болею астмой, рис 150 г",
        "аллергия на орехи, йогурт 200 г",
        # Симптомы и показатели.
        "болит живот, суп 300 г",
        "кашель и одышка, хлеб 30 г",
        "пульс 120, яблоко 150 г",
        "сатурация 91, каша 200 г",
        # Лекарства и лечение.
        "принимаю инсулин, гречка 200 г",
        "врач назначил антибиотики, съела рис 150 г",
        "пью таблетки, творог 100 г",
        "назначенная доза препарата, банан 100 г",
        # Медицинские специалисты.
        "эндокринолог сказал, что можно суп",
        "психиатр назначил антидепрессанты, каша 200 г",
        # Анализы и обследования.
        "сдал анализ крови, съел курицу 200 г",
        "результаты анализов, салат 150 г",
        "по МРТ нашли изменения, салат 150 г",
        "гемоглобин низкий, яблоко 150 г",
        # Репродуктивное и психическое здоровье.
        "я беременна, творог 150 г",
        "менструальный цикл, банан 100 г",
        "у меня депрессия, банан 100 г",
        "паническая атака, съел кашу",
        # Операции, процедуры и медицинские документы.
        "после операции съел суп",
        "после процедуры съел суп",
        "моя медицинская карта, хлеб 20 г",
        "выписка из больницы, рис 150 г",
    ],
)
def test_medical_information_is_blocked(text):
    result = check_sensitive_meal_text(text)

    assert result.is_sensitive is True
    assert result.reason is SensitiveDataType.MEDICAL


@pytest.mark.parametrize(
    "text",
    [
        "у меня рак, суп 300 г",
        "диагноз рак желудка, хлеб 30 г",
        "сахар в крови высокий, яблоко 150 г",
        "низкий сахар, творог 100 г",
        "у меня температура 38, суп 300 г",
        "температура тела 38.2, рис 150 г",
        "давление 150/100, хлеб 30 г",
        "высокое давление, каша 200 г",
        "у меня кровь из носа, съел яблоко",
    ],
)
def test_ambiguous_medical_words_are_blocked_in_medical_context(text):
    result = check_sensitive_meal_text(text)

    assert result.is_sensitive is True
    assert result.reason is SensitiveDataType.MEDICAL


def test_check_result_never_contains_source_fragment():
    private_fragment = "+7 999 123-45-67"

    result = check_sensitive_meal_text(f"мой телефон {private_fragment}, хлеб 30 г")

    assert private_fragment not in repr(result)
    assert set(vars(result)) == {"is_sensitive", "reason"}


class _MealInputState:
    def __init__(self):
        self.current_state = meals.MealEntryStates.waiting_for_ai_food_input
        self._data = {"meal_type": meals.MealType.LUNCH.value}
        self.set_state = AsyncMock()
        self.clear = AsyncMock()

    async def update_data(self, **kwargs):
        self._data.update(kwargs)

    async def get_data(self):
        return dict(self._data)


def test_sensitive_input_is_rejected_before_ai_without_state_or_database_storage(caplog):
    source_text = (
        "я Иванов Петр Иванович у меня такие-то медицинские данные, "
        "вот мой номер телефона: +7 999 123-45-67, колбаса 200 г"
    )
    message = SimpleNamespace(
        text=source_text,
        from_user=SimpleNamespace(id=12345),
        answer=AsyncMock(),
        bot=SimpleNamespace(),
    )
    state = _MealInputState()

    with patch.object(meals.deepseek_service, "analyze_food_text") as analyze_food, patch(
        "handlers.meals.MealRepository.save_meal"
    ) as save_meal:
        caplog.set_level("INFO", logger="handlers.meals")
        asyncio.run(meals.handle_ai_food_input(message, state))

    analyze_food.assert_not_called()
    save_meal.assert_not_called()
    state.set_state.assert_not_awaited()
    assert state.current_state is meals.MealEntryStates.waiting_for_ai_food_input
    assert state._data == {"meal_type": meals.MealType.LUNCH.value}
    assert "ai_pending_meal" not in state._data

    message.answer.assert_awaited_once_with(
        meals.SENSITIVE_MEAL_INPUT_REJECTED_TEXT,
        parse_mode="HTML",
    )
    response_text = message.answer.await_args.args[0]
    assert "Иванов" not in response_text
    assert "+7 999" not in response_text
    assert "медицинские данные" not in response_text

    assert "Sensitive meal input rejected reason=phone" in caplog.text
    assert source_text not in caplog.text
    assert "Иванов" not in caplog.text
    assert "+7 999" not in caplog.text

