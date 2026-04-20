"""FSM состояния для пользователей."""
from aiogram.fsm.state import State, StatesGroup


class MealEntryStates(StatesGroup):
    """Состояния для добавления приёма пищи."""
    choosing_meal_type = State()  # Выбор типа приёма пищи
    waiting_for_food_input = State()  # CalorieNinjas
    waiting_for_ai_food_input = State()  # Gemini AI
    confirming_ai_meal = State()  # Подтверждение сохранения Gemini результата
    waiting_for_openrouter_food_input = State()  # OpenRouter free
    waiting_for_gigachat_food_input = State()  # GigaChat
    confirming_openrouter_meal = State()  # Подтверждение сохранения OpenRouter результата
    waiting_for_photo = State()  # Фото еды
    waiting_for_label_photo = State()  # Фото этикетки
    waiting_for_barcode_photo = State()  # Фото штрих-кода
    waiting_for_weight_input = State()  # Вес продукта (для этикетки)
    choosing_edit_type = State()  # Выбор типа редактирования
    editing_meal_weight = State()  # Редактирование веса продукта
    editing_meal_weight_manual_input = State()  # Ручной ввод веса для выбранного продукта
    editing_meal_kbju = State()  # Ручная правка КБЖУ выбранного продукта
    editing_meal_kbju_single_input = State()  # Ручной ввод одного поля КБЖУ
    editing_meal_kbju_all_input = State()  # Ручной ввод всех полей КБЖУ
    editing_meal_composition = State()  # Редактирование состава продуктов
    editing_meal = State()  # Старое состояние (для обратной совместимости)


class WorkoutStates(StatesGroup):
    """Состояния для добавления тренировки."""
    choosing_exercise = State()
    entering_custom_exercise = State()
    entering_duration = State()
    entering_steps = State()
    confirming_steps = State()
    browsing_recent_exercises = State()
    browsing_all_exercises = State()
    searching_exercise = State()
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
    entering_measurements_date = State()
    reviewing_measurements = State()
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
    entering_target_weight = State()
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
    """Состояния для дневных заметок."""
    note_rating = State()
    note_factors = State()
    note_text = State()
