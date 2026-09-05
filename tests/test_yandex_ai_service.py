import asyncio
from types import SimpleNamespace

import pytest

from services import yandex_ai_service as yandex_module
from services.ai_food_parser import AI_FOOD_TEXT_SYSTEM_PROMPT
from services.yandex_ai_service import (
    YANDEX_FOOD_PHOTO_PROMPT,
    YandexAIService,
    YandexAIServiceConfigError,
    YandexAIServiceTemporaryError,
)


def _response(content: str):
    return SimpleNamespace(
        id="response-1",
        choices=[SimpleNamespace(message=SimpleNamespace(content=content))],
        usage=SimpleNamespace(prompt_tokens=11, completion_tokens=7, total_tokens=18),
    )


class _Completions:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def _service(outcomes, **overrides):
    completions = _Completions(outcomes)
    service = YandexAIService(
        api_key="test-key",
        folder_id="folder-123",
        max_retries=overrides.pop("max_retries", 0),
        retry_backoff_seconds=overrides.pop("retry_backoff_seconds", (0,)),
        **overrides,
    )
    service._client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    return service, completions


def test_client_uses_yandex_openai_compatible_configuration(monkeypatch):
    captured = {}

    def fake_client(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace()

    monkeypatch.setattr(yandex_module, "AsyncOpenAI", fake_client)
    service = YandexAIService(api_key="secret", folder_id="folder-123")

    service._get_client()

    assert captured == {
        "api_key": "secret",
        "base_url": "https://ai.api.cloud.yandex.net/v1",
        "project": "folder-123",
        "timeout": 45.0,
        "max_retries": 0,
    }


@pytest.mark.parametrize("api_key,folder_id", [(None, "folder"), ("key", None), (None, None)])
def test_client_requires_key_and_folder(api_key, folder_id):
    with pytest.raises(YandexAIServiceConfigError):
        YandexAIService(api_key=api_key, folder_id=folder_id)._get_client()


def test_text_food_uses_alice_flash_and_shared_structured_prompt(monkeypatch):
    raw = (
        '{"status":"ok","items":[{"name":"Творог","grams":200,"kcal":242,'
        '"protein":34,"fat":10,"carbs":6}],'
        '"total":{"kcal":242,"protein":34,"fat":10,"carbs":6}}'
    )
    service, completions = _service([_response(raw)])
    events = []
    monkeypatch.setattr(yandex_module, "log_ai_usage", lambda **kwargs: events.append(kwargs))

    result = asyncio.run(service.analyze_food_text("творог 200 г", user_id="42"))

    assert result == raw
    request = completions.calls[0]
    assert request["model"] == "gpt://folder-123/aliceai-llm-flash"
    assert request["messages"][0] == {"role": "system", "content": AI_FOOD_TEXT_SYSTEM_PROMPT}
    assert events[0]["provider"] == "yandex"
    assert events[0]["status"] == "success"


def test_food_photo_uses_qwen_multimodal_request(monkeypatch):
    raw = (
        '{"status":"ok","dishes":[{"dish_name":"Омлет","confidence":"high",'
        '"ingredients":[{"name":"Омлет","grams":220,"kcal":310,'
        '"protein":22,"fat":23,"carbs":4}]}]}'
    )
    service, completions = _service([_response(raw)])
    monkeypatch.setattr(yandex_module, "log_ai_usage", lambda **_kwargs: None)

    result = asyncio.run(
        service.analyze_food_photo(b"\x89PNG\r\n\x1a\nimage", comment="без масла")
    )

    assert result["dish_name"] == "Омлет"
    assert result["source"] == "yandex"
    request = completions.calls[0]
    assert request["model"] == "gpt://folder-123/qwen3.6-35b-a3b"
    assert request["timeout"] == 90.0
    content = request["messages"][0]["content"]
    assert YANDEX_FOOD_PHOTO_PROMPT in content[0]["text"]
    assert "без масла" in content[0]["text"]
    assert content[1]["image_url"]["url"].startswith("data:image/png;base64,")


def test_temporary_error_is_retried(monkeypatch):
    service, completions = _service(
        [
            YandexAIServiceTemporaryError("temporary"),
            _response('{"status":"no_food","items":[]}'),
        ],
        max_retries=1,
    )
    monkeypatch.setattr(yandex_module, "log_ai_usage", lambda **_kwargs: None)
    async def no_sleep(_delay):
        return None

    monkeypatch.setattr(yandex_module.asyncio, "sleep", no_sleep)

    result = asyncio.run(service.analyze_food_text("не еда"))

    assert result == '{"status":"no_food","items":[]}'
    assert len(completions.calls) == 2
