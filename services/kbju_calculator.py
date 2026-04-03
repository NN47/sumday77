"""Сервис для расчёта КБЖУ на основе теста."""
from typing import Tuple


def calculate_kbju_from_test(data: dict) -> Tuple[float, float, float, float, str]:
    """
    Рассчитывает КБЖУ на основе данных теста.
    
    Args:
        data: Словарь с данными:
            - gender: 'male' или 'female'
            - age: возраст (число)
            - height: рост в см (число)
            - weight: вес в кг (число)
            - activity: 'low', 'medium' или 'high'
            - goal: 'loss', 'maintain' или 'gain'
    
    Returns:
        Кортеж: (calories, protein, fat, carbs, goal_label)
    """
    gender = data.get("gender")
    age = float(data.get("age", 30))
    height = float(data.get("height", 170))
    weight = float(data.get("weight", 70))
    activity = data.get("activity", "medium")
    goal = data.get("goal", "maintain")

    # BMR по Mifflin-St Jeor
    if gender == "female":
        bmr = 10 * weight + 6.25 * height - 5 * age - 161
    else:
        bmr = 10 * weight + 6.25 * height - 5 * age + 5

    activity_factor = {
        "low": 1.2,
        "medium": 1.4,
        "high": 1.6,
    }.get(activity, 1.4)

    tdee = bmr * activity_factor

    if goal == "loss":
        calories = tdee * 0.8   # -20%
        goal_label = "Похудение"
    elif goal == "gain":
        calories = tdee * 1.1   # +10%
        goal_label = "Набор массы"
    else:
        calories = tdee
        goal_label = "Поддержание веса"

    # Макросы
    protein = weight * 1.8
    fat = weight * 0.9
    used_kcal = protein * 4 + fat * 9
    carbs = max((calories - used_kcal) / 4, 0)

    return calories, protein, fat, carbs, goal_label
