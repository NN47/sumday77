"""Provider-independent orchestration for AI food photo analysis."""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from enum import Enum

from services.ai_quota_service import ai_quota_service
from services.gemini_service import GeminiServiceTemporaryUnavailableError, gemini_service
from services.openai_label_service import (
    OpenAILabelServiceTimeoutError,
    openai_label_service,
)
from services.openai_token_budget_service import (
    OpenAIDailyTokenLimitExceeded,
    openai_token_budget_service,
)
from services.photo_food_validator import validate_photo_food_payload
from services.yandex_ai_service import YandexAIServiceError, yandex_ai_service
from utils.log_sanitizer import safe_exception_summary


logger = logging.getLogger(__name__)
FOOD_PHOTO_FEATURE = "food_photo_analysis"


class FoodPhotoProvider(str, Enum):
    GEMINI = "gemini"
    OPENAI = "openai"
    YANDEX = "yandex"


class FoodPhotoAnalysisError(RuntimeError):
    """Base class for expected food-photo analysis failures."""


class FoodPhotoProvidersUnavailableError(FoodPhotoAnalysisError):
    """No configured provider returned a valid food-photo result."""


@dataclass(frozen=True)
class FoodPhotoAnalysisResult:
    """Validated result which does not expose a provider-specific payload."""

    status: str
    items: list[dict]
    total: dict
    dish_name: str | None
    dishes: list[dict]
    provider_used: str
    fallback_used: bool


class FoodPhotoAnalysisService:
    """Run and validate the Gemini → OpenAI → Yandex food-photo chain."""

    async def analyze(
        self,
        *,
        image_bytes: bytes,
        user_id: str | int | None = None,
        comment: str | None = None,
        quota_request_id: str | None = None,
        primary_provider: FoodPhotoProvider | str = FoodPhotoProvider.GEMINI,
    ) -> FoodPhotoAnalysisResult:
        primary = FoodPhotoProvider(primary_provider)
        providers = (
            [FoodPhotoProvider.GEMINI, FoodPhotoProvider.OPENAI, FoodPhotoProvider.YANDEX]
            if primary is FoodPhotoProvider.GEMINI
            else [FoodPhotoProvider.OPENAI, FoodPhotoProvider.YANDEX]
        )
        last_error: Exception | None = None

        for attempt_index, provider in enumerate(providers):
            try:
                payload = await self._call_provider(
                    provider,
                    image_bytes,
                    user_id=user_id,
                    comment=comment,
                    quota_request_id=quota_request_id,
                    additional_attempt=attempt_index > 0,
                )
                validated = validate_photo_food_payload(payload)
                if validated is not None:
                    logger.info("final_food_photo_analysis_provider=%s", provider.value)
                    return self._build_result(validated, provider, attempt_index > 0)
                logger.warning("%s returned no usable food photo result", provider.value)
            except OpenAIDailyTokenLimitExceeded as error:
                last_error = error
                logger.info("OpenAI skipped for food photo analysis fallback_reason=openai_daily_token_limit")
            except Exception as error:
                last_error = error
                logger.error(
                    "%s food photo analysis failed error_type=%s",
                    provider.value,
                    safe_exception_summary(error),
                    exc_info=True,
                )

        raise FoodPhotoProvidersUnavailableError("All providers unavailable") from last_error

    async def _call_provider(
        self,
        provider: FoodPhotoProvider,
        image_bytes: bytes,
        *,
        user_id: str | int | None,
        comment: str | None,
        quota_request_id: str | None,
        additional_attempt: bool,
    ) -> dict | None:
        if provider is FoodPhotoProvider.GEMINI:
            self._register_provider_attempt(quota_request_id, additional_attempt)
            if gemini_service is None:
                raise GeminiServiceTemporaryUnavailableError("Gemini service is not initialized")
            args = (image_bytes, comment) if comment else (image_bytes,)
            try:
                return await asyncio.wait_for(
                    asyncio.to_thread(gemini_service.estimate_kbju_from_photo, *args),
                    timeout=45.0,
                )
            except asyncio.TimeoutError as error:
                raise GeminiServiceTemporaryUnavailableError("AI request timed out") from error

        if provider is FoodPhotoProvider.OPENAI:
            with openai_token_budget_service.reservation(user_id=user_id, feature=FOOD_PHOTO_FEATURE):
                self._register_provider_attempt(quota_request_id, additional_attempt)
                kwargs = {"user_id": user_id, "feature": FOOD_PHOTO_FEATURE}
                if comment:
                    kwargs["comment"] = comment
                try:
                    return await asyncio.wait_for(
                        asyncio.to_thread(
                            openai_label_service.analyze_food_photo_openai,
                            image_bytes,
                            **kwargs,
                        ),
                        timeout=45.0,
                    )
                except asyncio.TimeoutError as error:
                    raise OpenAILabelServiceTimeoutError("OpenAI API request timed out") from error

        self._register_provider_attempt(quota_request_id, additional_attempt)
        kwargs = {"user_id": user_id, "feature": FOOD_PHOTO_FEATURE}
        if comment:
            kwargs["comment"] = comment
        try:
            return await asyncio.wait_for(
                yandex_ai_service.analyze_food_photo(image_bytes, **kwargs),
                timeout=95.0,
            )
        except asyncio.TimeoutError as error:
            raise YandexAIServiceError("Yandex AI Studio request timed out") from error

    @staticmethod
    def _register_provider_attempt(quota_request_id: str | None, additional_attempt: bool) -> None:
        if not quota_request_id:
            return
        if additional_attempt:
            ai_quota_service.register_additional_provider_attempt(quota_request_id)
        else:
            ai_quota_service.mark_provider_started(quota_request_id)

    @staticmethod
    def _build_result(payload: dict, provider: FoodPhotoProvider, fallback_used: bool) -> FoodPhotoAnalysisResult:
        dishes = payload.get("dishes") or []
        return FoodPhotoAnalysisResult(
            status="ok",
            items=list(payload.get("items") or []),
            total=dict(payload.get("total") or {}),
            dish_name=payload.get("dish_name"),
            dishes=list(dishes),
            provider_used=provider.value,
            fallback_used=fallback_used,
        )


food_photo_analysis_service = FoodPhotoAnalysisService()
