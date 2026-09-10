from types import SimpleNamespace

from services import openai_text_service as service_module
from services.ai_food_parser import AI_FOOD_TEXT_SYSTEM_PROMPT, parse_kbju_json


def test_openai_food_analysis_explicitly_requests_and_parses_json(monkeypatch):
    captured = {}
    response_text = (
        '{"status":"ok","items":[{"name":"яблоко","grams":100,'
        '"kcal":52,"protein":0.3,"fat":0.2,"carbs":14}],'
        '"total":{"kcal":52,"protein":0.3,"fat":0.2,"carbs":14}}'
    )

    class FakeResponses:
        def create(self, **kwargs):
            captured.update(kwargs)
            return SimpleNamespace(output_text=response_text, id="response_123", usage=None)

    class FakeClient:
        responses = FakeResponses()

    monkeypatch.setattr(service_module, "OpenAI", lambda **_kwargs: FakeClient())
    monkeypatch.setattr(service_module, "log_ai_usage", lambda **_kwargs: None)

    result = service_module.OpenAITextService(api_key="test-key").analyze_food_text("яблоко 100 г")

    assert "Ответ должен быть только в формате JSON" in AI_FOOD_TEXT_SYSTEM_PROMPT
    assert "Не используй Markdown" in AI_FOOD_TEXT_SYSTEM_PROMPT
    assert "включая ```json" in AI_FOOD_TEXT_SYSTEM_PROMPT
    assert "не добавляй никаких пояснений до или после JSON" in AI_FOOD_TEXT_SYSTEM_PROMPT
    assert captured["instructions"] == AI_FOOD_TEXT_SYSTEM_PROMPT
    assert captured["input"] == [
        {"role": "developer", "content": service_module.JSON_OUTPUT_INPUT_INSTRUCTION},
        {"role": "user", "content": "яблоко 100 г"},
    ]
    assert "json" in captured["input"][0]["content"].lower()
    assert AI_FOOD_TEXT_SYSTEM_PROMPT not in str(captured["input"])
    assert captured["text"] == {"format": {"type": "json_object"}}
    assert parse_kbju_json(result) == {
        "status": "ok",
        "items": [
            {
                "name": "яблоко",
                "grams": 100.0,
                "kcal": 52.0,
                "protein": 0.3,
                "fat": 0.2,
                "carbs": 14.0,
            }
        ],
        "total": {"kcal": 52.0, "protein": 0.3, "fat": 0.2, "carbs": 14.0},
    }


def test_non_json_completion_keeps_plain_string_input(monkeypatch):
    captured = {}

    class FakeResponses:
        def create(self, **kwargs):
            captured.update(kwargs)
            return SimpleNamespace(output_text="Готово", id="response_456", usage=None)

    class FakeClient:
        responses = FakeResponses()

    monkeypatch.setattr(service_module, "OpenAI", lambda **_kwargs: FakeClient())
    monkeypatch.setattr(service_module, "log_ai_usage", lambda **_kwargs: None)

    result, _ = service_module.OpenAITextService(api_key="test-key").generate_meal_completion_comment(
        "user prompt",
        system_prompt="system prompt",
    )

    assert result == "Готово"
    assert captured["instructions"] == "system prompt"
    assert captured["input"] == "user prompt"
    assert "text" not in captured
