"""FSM состояния для пользователей."""
from aiogram.fsm.state import State, StatesGroup


class MealEntryStates(StatesGroup):
    """Состояния для добавления приёма пищи."""
    waiting_for_food_input = State()  # CalorieNinjas
    waiting_for_ai_food_input = State()  # Gemini AI
    waiting_for_photo = State()  # Фото еды
    waiting_for_label_photo = State()  # Фото этикетки
    waiting_for_barcode_photo = State()  # Фото штрих-кода
    waiting_for_weight_input = State()  # Вес продукта (для этикетки)
    choosing_edit_type = State()  # Выбор типа редактирования
    editing_meal_weight = State()  # Редактирование веса продукта
    editing_meal_composition = State()  # Редактирование состава продуктов
    editing_meal = State()  # Старое состояние (для обратной совместимости)


class WorkoutStates(StatesGroup):
    """Состояния для добавления тренировки."""
    choosing_category = State()
    choosing_exercise = State()
    entering_custom_exercise = State()
    choosing_grip_type = State()  # Выбор типа хвата для подтягиваний
    entering_count = State()
    choosing_date = State()
    entering_custom_date = State()
    editing_count = State()  # Редактирование количества


class WeightStates(StatesGroup):
    """Состояния для работы с весом."""
    entering_weight = State()
    choosing_period = State()
    entering_measurements = State()
    choosing_date_for_weight = State()
    choosing_date_for_measurements = State()


class SupplementStates(StatesGroup):
    """Состояния для работы с добавками."""
    entering_name = State()
    entering_time = State()
    selecting_days = State()
    choosing_duration = State()
    logging_intake = State()
    choosing_date_for_intake = State()
    entering_amount = State()
    entering_history_time = State()
    entering_history_amount = State()
    editing_supplement = State()
    viewing_history = State()


class WaterStates(StatesGroup):
    """Состояния для работы с водой."""
    entering_amount = State()


class KbjuTestStates(StatesGroup):
    """Состояния для теста КБЖУ."""
    entering_gender = State()
    entering_age = State()
    entering_height = State()
    entering_weight = State()
    entering_goal = State()
    entering_goal_speed = State()
    entering_activity = State()
    entering_manual_calories = State()
    entering_manual_protein = State()
    entering_manual_fat = State()
    entering_manual_carbs = State()


class ActivityAnalysisStates(StatesGroup):
    """Состояния для календаря ИИ-анализа деятельности."""
    entering_manual_analysis = State()

class ProcedureStates(StatesGroup):
    """Состояния для работы с процедурами."""
    entering_name = State()


class WaterStates(StatesGroup):
    """Состояния для работы с водой."""
    entering_amount = State()


class SupportStates(StatesGroup):
    """Состояния для работы с поддержкой."""
    waiting_for_message = State()


class WellbeingStates(StatesGroup):
    """Состояния для отметки самочувствия."""
    choosing_mode = State()
    quick_mood = State()
    quick_influence = State()
    quick_difficulty = State()
    comment = State()
    editing_quick_mood = State()
    editing_quick_influence = State()
    editing_quick_difficulty = State()
    editing_comment = State()
