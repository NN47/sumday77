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

    assert "спортивный друг пользователя" in prompt
    assert "«Ну как тебе?»" in prompt
    assert "опытный человек, который давно занимается спортом" in prompt
    assert "Ты не диетолог" in prompt
    assert "Не начинай с сухого перечисления" in prompt


def test_meal_completion_prompt_requires_concrete_useful_feedback() -> None:
    prompt = _meal_completion_prompt()

    assert "Комментарий должен быть полезным" in prompt
    assert "белка достаточно или мало" in prompt
    assert "Если в комментарии нет конкретной пользы, перепиши его" in prompt
    assert "Всегда сначала ищи, что получилось удачно" in prompt
    assert "Реальным плюсом" in prompt


def test_meal_completion_prompt_forbids_dry_mini_analysis() -> None:
    prompt = _meal_completion_prompt()

    assert "Не пытайся одновременно разобрать всё" in prompt
    assert "одну главную мысль" in prompt
    assert "Можно использовать одну или две важные цифры" in prompt
    assert "Не перечисляй все КБЖУ подряд" in prompt
    assert "Если комментарий звучит как рекламный текст, психологический пост или диетологическая статья — перепиши проще" in prompt


def test_meal_completion_prompt_preserves_supportive_philosophy() -> None:
    prompt = _meal_completion_prompt()

    assert "Sumday77 всегда на стороне пользователя" in prompt
    assert "Всегда сначала ищи, что получилось удачно" in prompt
    assert "Реальным плюсом может быть" in prompt
    assert "баланс уже не поправить" in prompt
    assert "нужно отработать" in prompt
    assert "Не советуй голодать" in prompt
    assert "Дальше можно просто продолжить в обычном режиме" in prompt


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
    assert "максимум два коротких предложения" in prompt
    assert "Целевой объём — 120–300 символов" in prompt


def test_meal_completion_prompt_requires_short_single_focus_html_format() -> None:
    prompt = _meal_completion_prompt()

    assert "Ответь коротко: один заголовок и максимум два коротких предложения" in prompt
    assert "Выбери только одну главную мысль" in prompt
    assert "Не пиши художественный текст ради красоты" in prompt
    assert "Не приписывай пользователю чувства, привычки или мотивы" in prompt
    assert "Жирным выделяй только заголовок. Основной текст всегда обычный" in prompt
    assert "Общая длина ответа — не более 350 символов" in prompt
    assert "Если последнее предложение можно удалить без потери смысла, удали его" in prompt
    assert "Разрешён только один тег: <b>...</b>" in prompt
