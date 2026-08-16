import os

os.environ.setdefault("API_TOKEN", "test-token")
os.environ.setdefault("GEMINI_API_KEY", "test-gemini-key")

from handlers.meals import router
from services.gemini_service import GeminiService
from states.user_states import MealEntryStates


def test_legacy_gemini_meal_composition_flow_is_not_registered() -> None:
    registered_handlers = {handler.callback.__name__ for handler in router.message.handlers}

    assert "handle_meal_composition_edit" not in registered_handlers
    assert "handle_meal_edit_input" not in registered_handlers
    assert not hasattr(MealEntryStates, "editing_meal_composition")
    assert not hasattr(MealEntryStates, "editing_meal")
    assert not hasattr(GeminiService, "estimate_kbju")
    assert not hasattr(GeminiService, "_normalize_kbju_payload")
