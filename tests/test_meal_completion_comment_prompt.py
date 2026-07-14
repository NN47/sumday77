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


def test_meal_completion_prompt_uses_daily_norm_context_and_remaining_day_advice() -> None:
    prompt = _meal_completion_prompt()

    assert "значительную часть дневной нормы" in prompt
    assert "больше половины дневного бюджета" in prompt
    assert "обед и ужин можно сделать немного легче" in prompt
    assert "остаток дня проще построить вокруг белка и овощей" in prompt


def test_meal_completion_prompt_stays_positive_and_finds_strengths() -> None:
    prompt = _meal_completion_prompt()

    assert "обязательно найди хотя бы одну сильную сторону" in prompt
    assert "Не своди весь комментарий только к высокой калорийности" in prompt
    assert "Завершай комментарий позитивно" in prompt
    assert "без ощущения критики или вины" in prompt


def test_meal_completion_prompt_requires_varied_natural_titles_not_mini_analysis() -> None:
    prompt = _meal_completion_prompt()

    assert "Заголовки придумывай разнообразные" in prompt
    assert "не используй один и тот же шаблон" in prompt
    assert "не как мини-анализ КБЖУ" in prompt
    assert "естественная реакция персонального помощника" in prompt


def test_meal_completion_prompt_encodes_supportive_philosophy() -> None:
    prompt = _meal_completion_prompt()

    assert "Sumday77 всегда на стороне пользователя" in prompt
    assert "сначала найди реальную сильную сторону" in prompt
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


def test_meal_completion_prompt_mentions_edited_meal_context() -> None:
    prompt = _meal_completion_prompt()

    assert "завершил или отредактировал" in prompt
    assert "Если пользователь редактирует старый приём пищи" in prompt
