"""OpenAI-first provider for text meal, meal comment, and day analysis flows."""
from __future__ import annotations

import logging
import re
import time
from collections.abc import Mapping
from typing import Any

from openai import APITimeoutError, OpenAI, OpenAIError

from config import OPENAI_API_KEY, OPENAI_TEXT_MODEL
from services.ai_food_parser import AI_FOOD_TEXT_SYSTEM_PROMPT
from services.ai_usage_logger import calculate_ai_cost, log_ai_usage
from utils.log_sanitizer import REDACTED_CONTENT, redact_sensitive_text, safe_exception_summary, sanitize_identifier

logger = logging.getLogger(__name__)

JSON_OUTPUT_INPUT_INSTRUCTION = "Return the response as a JSON object."


def _safe_openai_error_field(value: Any) -> str | None:
    """Return only identifier-like provider metadata, never arbitrary body values."""
    return sanitize_identifier(value)


def _safe_openai_server_message(value: Any, *, sensitive_values: tuple[str, ...]) -> str | None:
    """Keep a bounded provider explanation while removing possibly echoed input."""
    if not isinstance(value, str) or not value.strip():
        return None

    message = value
    for sensitive_value in sensitive_values:
        if sensitive_value:
            message = message.replace(sensitive_value, REDACTED_CONTENT)
    message = redact_sensitive_text(message, max_length=240)
    message = re.sub(r"(['\"`]).*?\1", REDACTED_CONTENT, message)
    message = re.sub(r"\b-?\d{6,}\b", REDACTED_CONTENT, message)
    message = re.sub(r"[^\x20-\x7E]+", REDACTED_CONTENT, message)
    return message.strip() or None


def _safe_openai_error_details(exc: OpenAIError, *, sensitive_values: tuple[str, ...]) -> dict[str, Any]:
    """Extract the allowlisted diagnostic fields from an OpenAI SDK error."""
    body = getattr(exc, "body", None)
    if not isinstance(body, Mapping):
        body = {}
    nested_error = body.get("error")
    if isinstance(nested_error, Mapping):
        body = nested_error

    response = getattr(exc, "response", None)
    status = getattr(exc, "status_code", None) or getattr(response, "status_code", None)
    details = {
        "http_status": status if isinstance(status, int) and 100 <= status <= 599 else None,
        "error_code": _safe_openai_error_field(getattr(exc, "code", None) or body.get("code")),
        "error_type": _safe_openai_error_field(getattr(exc, "type", None) or body.get("type")),
        "param": _safe_openai_error_field(getattr(exc, "param", None) or body.get("param")),
        "server_message": _safe_openai_server_message(body.get("message"), sensitive_values=sensitive_values),
    }
    return {key: value for key, value in details.items() if value is not None}


class OpenAITextServiceError(Exception):
    """A safe domain error which allows callers to continue their fallback chain."""


class OpenAITextServiceConfigError(OpenAITextServiceError):
    pass


class OpenAITextServiceTemporaryError(OpenAITextServiceError):
    pass


class OpenAITextService:
    def __init__(self, api_key: str | None = OPENAI_API_KEY, *, model: str = OPENAI_TEXT_MODEL) -> None:
        self.api_key = api_key
        self.model = model
        self.timeout_seconds = 75.0

    def _complete(
        self,
        prompt: str,
        *,
        system_prompt: str,
        user_id: str | int | None,
        feature: str,
        json_output: bool = False,
    ) -> tuple[str, dict]:
        if not prompt:
            raise ValueError("Prompt is empty")
        if not self.api_key:
            raise OpenAITextServiceConfigError("OPENAI_API_KEY is not configured")

        started = time.perf_counter()
        try:
            effective_input: str | list[dict[str, str]] = prompt
            if json_output:
                effective_input = [
                    {"role": "developer", "content": JSON_OUTPUT_INPUT_INSTRUCTION},
                    {"role": "user", "content": prompt},
                ]
            response = OpenAI(api_key=self.api_key, timeout=self.timeout_seconds).responses.create(
                model=self.model,
                instructions=system_prompt,
                input=effective_input,
                **({"text": {"format": {"type": "json_object"}}} if json_output else {}),
            )
            content = (getattr(response, "output_text", "") or "").strip()
            if not content:
                raise OpenAITextServiceTemporaryError("OpenAI returned empty response")
        except OpenAITextServiceError:
            raise
        except (APITimeoutError, OpenAIError) as exc:
            safe_details = _safe_openai_error_details(exc, sensitive_values=(prompt, system_prompt))
            logger.warning(
                "OpenAI text request failed feature=%s error_type=%s "
                "safe_reason=http_status=%s error_code=%s openai_error_type=%s param=%s server_message=%s",
                feature,
                safe_exception_summary(exc),
                safe_details.get("http_status"),
                safe_details.get("error_code"),
                safe_details.get("error_type"),
                safe_details.get("param"),
                safe_details.get("server_message"),
            )
            raise OpenAITextServiceTemporaryError("OpenAI text request failed") from exc

        latency_ms = int((time.perf_counter() - started) * 1000)
        usage = getattr(response, "usage", None)
        input_tokens = getattr(usage, "input_tokens", None) if usage else None
        output_tokens = getattr(usage, "output_tokens", None) if usage else None
        total_tokens = getattr(usage, "total_tokens", None) if usage else None
        metadata = {
            "model": self.model,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": total_tokens,
            "estimated_cost_usd": calculate_ai_cost("openai", self.model, input_tokens, output_tokens),
            "response_id": getattr(response, "id", None),
        }
        log_ai_usage(
            provider="openai", feature=feature, model=self.model, status="success", user_id=user_id,
            latency_ms=latency_ms, input_tokens=input_tokens, output_tokens=output_tokens,
            total_tokens=total_tokens, estimated_cost_usd=metadata["estimated_cost_usd"],
            raw_metadata={"response_id": metadata["response_id"], "input_chars": len(prompt), "output_chars": len(content)},
        )
        return content, metadata

    def analyze_food_text(self, text: str, *, user_id=None, feature: str = "meal_text_ai") -> str:
        return self._complete(text, system_prompt=AI_FOOD_TEXT_SYSTEM_PROMPT, user_id=user_id, feature=feature, json_output=True)[0]

    def generate_meal_completion_comment(self, prompt: str, *, user_id=None, system_prompt: str, feature: str = "meal_completion_comment") -> tuple[str, dict]:
        return self._complete(prompt, system_prompt=system_prompt, user_id=user_id, feature=feature)

    def analyze_activity_prompt(self, prompt: str, *, user_id=None, system_prompt: str, feature: str = "detailed_activity_analysis") -> str:
        return self._complete(prompt, system_prompt=system_prompt, user_id=user_id, feature=feature)[0]


openai_text_service = OpenAITextService()
