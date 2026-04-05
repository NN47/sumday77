"""Функции форматирования текста."""
from database.models import KbjuSettings


def format_strategy_text(
    calories: float,
    protein: float,
    fat: float,
    carbs: float,
    goal: str,
) -> str:
    """Форматирует короткую интерпретацию стратегии КБЖУ."""
    strategy_by_goal = {
        "loss": (
            "мягкий дефицит",
            "Это позволит терять вес постепенно и комфортно.",
        ),
        "maintain": (
            "поддержание веса",
            "Это поможет удерживать текущий вес без дефицита и профицита.",
        ),
        "gain": (
            "умеренный профицит",
            "Это поможет набирать вес и сохранять энергию для тренировок.",
        ),
    }
    goal_type, goal_text = strategy_by_goal.get(
        goal,
        (
            "индивидуальная цель",
            "Это поможет придерживаться выбранной стратегии питания.",
        ),
    )

    return (
        "Твоя стратегия:\n\n"
        f"<b>{calories:.0f} ккал</b> — {goal_type}\n"
        f"{goal_text}\n\n"
        f"Белки: <b>{protein:.0f} г</b>\n"
        "Это поможет сохранять мышцы.\n\n"
        f"Жиры: <b>{fat:.0f} г</b>\n"
        "Это важно для гормонального баланса.\n\n"
        f"Углеводы: <b>{carbs:.0f} г</b>\n"
        "Это основной источник энергии."
    )


def format_kbju_goal_text(
    calories: float,
    protein: float,
    fat: float,
    carbs: float,
    goal_label: str,
    bmr_calories: float | None = None,
    maintenance_calories: float | None = None,
    goal_explanation: str | None = None,
) -> str:
    """Форматирует текст с целью КБЖУ."""
    if maintenance_calories is None:
        return (
            "🎯 Я настроил твою дневную норму КБЖУ!\n\n"
            f"🔥 Калории: <b>{calories:.0f} ккал</b>\n"
            f"💪 Белки: <b>{protein:.0f} г</b>\n"
            f"🧈 Жиры: <b>{fat:.0f} г</b>\n"
            f"🍞 Углеводы: <b>{carbs:.0f} г</b>\n\n"
            f"Цель: <b>{goal_label}</b>\n\n"
            "Теперь в разделе КБЖУ я буду сравнивать твой рацион с этой целью.\n"
            "В любой момент можно изменить параметры через кнопку «🎯 Цель / Норма КБЖУ»."
        )

    bmr_line = f"Твой базовый обмен: <b>{bmr_calories:.0f} ккал</b>\n" if bmr_calories is not None else ""
    goal_line = f"{goal_explanation}\n" if goal_explanation else f"Твоя цель: <b>{goal_label}</b>\n"

    return (
        "🎯 Я рассчитал:\n\n"
        f"{bmr_line}"
        f"С учетом активности: <b>{maintenance_calories:.0f} ккал</b>\n"
        f"{goal_line}\n"
        f"Твоя цель: <b>{calories:.0f} ккал</b>\n\n"
        f"💪 Белки: <b>{protein:.0f} г</b>\n"
        f"🧈 Жиры: <b>{fat:.0f} г</b>\n"
        f"🍞 Углеводы: <b>{carbs:.0f} г</b>\n\n"
        "Теперь в разделе КБЖУ я буду сравнивать твой рацион с этой целью.\n"
        "В любой момент можно изменить параметры через кнопку «🎯 Цель / Норма КБЖУ»."
    )


def format_onboarding_finish_text() -> str:
    """Форматирует финальное сообщение после завершения onboarding теста КБЖУ."""
    return (
        "Готово!\n\n"
        "Теперь я буду:\n\n"
        "— считать твой рацион\n"
        "— показывать отклонения от нормы\n"
        "— давать рекомендации\n\n"
        "По мере изменения веса твоя норма калорий будет автоматически меняться.\n\n"
        "Ты всегда можешь изменить цель и норму в разделе:\n"
        "🎯 Цель / Норма КБЖУ\n\n"
        "Начнем?"
    )


def get_kbju_goal_label(goal: str | None) -> str:
    """Возвращает читаемое название цели КБЖУ."""
    labels = {
        "loss": "Похудение",
        "maintain": "Поддержание веса",
        "gain": "Набор массы",
        "custom": "Своя норма",
    }
    if goal in labels:
        return labels[goal]
    if goal:
        return goal
    return "Своя норма"


def format_current_kbju_goal(settings: KbjuSettings) -> str:
    """Форматирует текущую цель КБЖУ."""
    goal_label = get_kbju_goal_label(settings.goal)
    return (
        "🎯 Твоя текущая цель по КБЖУ:\n\n"
        f"🔥 Калории: <b>{settings.calories:.0f} ккал</b>\n"
        f"💪 Белки: <b>{settings.protein:.0f} г</b>\n"
        f"🧈 Жиры: <b>{settings.fat:.0f} г</b>\n"
        f"🍞 Углеводы: <b>{settings.carbs:.0f} г</b>\n\n"
        f"Цель: <b>{goal_label}</b>"
    )


def format_count_with_unit(count: int | float, variant: str | None) -> str:
    """Форматирует количество с единицей измерения."""
    if variant == "раз":
        return f"{count} раз"
    elif variant == "сек":
        return f"{count} сек"
    elif variant == "мин":
        return f"{count} мин"
    elif variant == "км":
        return f"{count} км"
    elif variant == "м":
        return f"{count} м"
    else:
        return str(count)
