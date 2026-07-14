import ast
from pathlib import Path


def _meal_completion_prompt() -> str:
    source = Path("handlers/meals.py").read_text(encoding="utf-8")
    module = ast.parse(source)
    for node in module.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "MEAL_COMPLETION_COMMENT_SYSTEM_PROMPT":
                    return ast.literal_eval(node.value)
    raise AssertionError("MEAL_COMPLETION_COMMENT_SYSTEM_PROMPT not found")


def test_meal_completion_prompt_uses_friend_role_and_question_framing() -> None:
    prompt = _meal_completion_prompt()

    assert "умный, доброжелательный друг пользователя" in prompt
    assert "«Ну как тебе?»" in prompt
    assert "отреагируй естественно, тепло и по-человечески" in prompt
    assert "не пиши как диетолог" in prompt
    assert "не начинай с сухого перечисления" in prompt


def test_meal_completion_prompt_requires_food_character_and_real_products() -> None:
    prompt = _meal_completion_prompt()

    assert "почувствуй характер еды" in prompt
    assert "домашний, ресторанный, лёгкий, плотный" in prompt
    assert "Обязательно замечай саму еду" in prompt
    assert "домашний хачапури, чизкейк, камамбер" in prompt
    assert "не только цифры" in prompt
    assert "Не выдумывай атмосферу" in prompt


def test_meal_completion_prompt_forbids_dry_mini_analysis() -> None:
    prompt = _meal_completion_prompt()

    assert "Не превращай комментарий в мини-анализ" in prompt
    assert "калорийность → белок → жиры → углеводы → совет" in prompt
    assert "КБЖУ используй только как поддержку основной мысли" in prompt
    assert "Максимум одна-две важные цифры" in prompt
    assert "Если текст похож на отчёт — перепиши его живее" in prompt


def test_meal_completion_prompt_preserves_supportive_philosophy() -> None:
    prompt = _meal_completion_prompt()

    assert "Sumday77 всегда на стороне пользователя" in prompt
    assert "Всегда сначала ищи положительную сторону" in prompt
    assert "обязательно найди хотя бы одну реальную сильную сторону" in prompt
    assert "баланс уже не поправить" in prompt
    assert "придётся отрабатывать" in prompt
    assert "Не советуй голодать" in prompt
    assert "следующий хороший выбор всё ещё имеет значение" in prompt


def test_meal_completion_prompt_forbids_current_generation_time_assumptions() -> None:
    prompt = _meal_completion_prompt()

    assert "Не предполагай время употребления еды по времени генерации ответа" in prompt
    assert "Ориентируйся только на данные приёма пищи" in prompt
    for forbidden_phrase in ("сейчас", "вечером", "на ночь", "перед сном", "утром", "в начале дня", "завершить день", "закончить вечер"):
        assert forbidden_phrase in prompt


def test_meal_completion_prompt_mentions_edited_meal_context_and_format() -> None:
    prompt = _meal_completion_prompt()

    assert "завершил или отредактировал" in prompt
    assert "Если пользователь редактирует старый приём пищи" in prompt
    assert "2–4 коротких предложения" in prompt
    assert "Оптимальная длина — 250–600 символов" in prompt
