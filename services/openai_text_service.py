"""OpenAI-first provider for text meal, meal comment, and day analysis flows."""
from __future__ import annotations

import logging
import time

from openai import APITimeoutError, OpenAI, OpenAIError

from config import OPENAI_API_KEY, OPENAI_TEXT_MODEL
from services.ai_food_parser import AI_FOOD_TEXT_SYSTEM_PROMPT
from services.ai_usage_logger import calculate_ai_cost, log_ai_usage
from utils.log_sanitizer import safe_exception_summary

logger = logging.getLogger(__name__)


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
            response = OpenAI(api_key=self.api_key, timeout=self.timeout_seconds).responses.create(
                model=self.model,
                instructions=system_prompt,
                input=prompt,
                **({"text": {"format": {"type": "json_object"}}} if json_output else {}),
            )
            content = (getattr(response, "output_text", "") or "").strip()
            if not content:
                raise OpenAITextServiceTemporaryError("OpenAI returned empty response")
        except OpenAITextServiceError:
            raise
        except (APITimeoutError, OpenAIError) as exc:
            logger.warning("OpenAI text request failed feature=%s error_type=%s", feature, safe_exception_summary(exc))
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
