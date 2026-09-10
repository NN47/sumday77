import httpx
import pytest
from openai import BadRequestError

from services import openai_text_service as service_module


def _bad_request(*, message: str, body: dict) -> BadRequestError:
    request = httpx.Request("POST", "https://api.openai.com/v1/responses")
    response = httpx.Response(400, request=request)
    return BadRequestError(message, response=response, body=body)


def test_openai_error_log_contains_only_allowlisted_diagnostics(monkeypatch, caplog):
    user_text = "private meal description"
    system_prompt = "private system instructions"
    error = _bad_request(
        message="SDK exception may include private data",
        body={
            "message": f"Invalid input: '{user_text}' for parameter `input`",
            "type": "invalid_request_error",
            "param": "input",
            "code": "invalid_value",
            "request_body": {"input": user_text, "instructions": system_prompt},
        },
    )

    class FakeResponses:
        def create(self, **kwargs):
            raise error

    class FakeClient:
        responses = FakeResponses()

    monkeypatch.setattr(service_module, "OpenAI", lambda **kwargs: FakeClient())
    service = service_module.OpenAITextService(api_key="test-key")

    with caplog.at_level("WARNING"), pytest.raises(service_module.OpenAITextServiceTemporaryError):
        service._complete(
            user_text,
            system_prompt=system_prompt,
            user_id="123456789",
            feature="meal_text_ai",
            json_output=True,
        )

    log_text = caplog.text
    assert "OpenAI text request failed feature=meal_text_ai" in log_text
    assert "http_status=400" in log_text
    assert "error_code=invalid_value" in log_text
    assert "openai_error_type=invalid_request_error" in log_text
    assert "param=input" in log_text
    assert "Invalid input:" in log_text
    assert user_text not in log_text
    assert system_prompt not in log_text
    assert "123456789" not in log_text
    assert "request_body" not in log_text


def test_openai_error_diagnostics_apply_to_other_service_features(monkeypatch, caplog):
    error = _bad_request(
        message="bad request",
        body={"message": "Unsupported parameter", "type": "invalid_request_error", "param": "temperature"},
    )

    class FakeResponses:
        def create(self, **kwargs):
            raise error

    class FakeClient:
        responses = FakeResponses()

    monkeypatch.setattr(service_module, "OpenAI", lambda **kwargs: FakeClient())
    service = service_module.OpenAITextService(api_key="test-key")

    with caplog.at_level("WARNING"), pytest.raises(service_module.OpenAITextServiceTemporaryError):
        service.generate_meal_completion_comment(
            "private prompt",
            user_id="987654321",
            system_prompt="private instructions",
            feature="meal_completion_comment",
        )

    assert "OpenAI text request failed feature=meal_completion_comment" in caplog.text
    assert "Unsupported parameter" in caplog.text
    assert "987654321" not in caplog.text
