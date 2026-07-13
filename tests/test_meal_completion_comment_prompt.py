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
