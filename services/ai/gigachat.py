"""Клиент для работы с GigaChat API."""
from __future__ import annotations

import logging
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Any

import requests
import urllib3
from urllib3.exceptions import InsecureRequestWarning

from config import GIGACHAT_API_KEY, GIGACHAT_API_URL, GIGACHAT_MODEL, GIGACHAT_OAUTH_URL

logger = logging.getLogger(__name__)
urllib3.disable_warnings(InsecureRequestWarning)

_TOKEN_SCOPE = "GIGACHAT_API_PERS"
_OAUTH_REQUEST_TIMEOUT_SECONDS = 30
_CHAT_REQUEST_TIMEOUT_SECONDS = 60
_TOKEN_REFRESH_MARGIN_SECONDS = 180
_DEFAULT_TOKEN_TTL_SECONDS = 25 * 60


class GigaChatServiceError(Exception):
    """Базовая ошибка GigaChat."""


class GigaChatServiceConfigError(GigaChatServiceError):
    """Ошибка конфигурации GigaChat."""


class GigaChatServiceTemporaryError(GigaChatServiceError):
    """Временная ошибка GigaChat."""


class GigaChatService:
    """Сервис для запросов в GigaChat c OAuth token caching."""

    def __init__(self) -> None:
        self._token: str | None = None
        self._token_expires_at: float = 0.0
        self._token_lock = threading.Lock()

    def analyze_food_text(self, text: str) -> str:
        """Отправляет текст еды в GigaChat и возвращает сырой ответ модели."""
        if not text:
            raise ValueError("Text is empty")

        started = time.perf_counter()
        messages = [
            {
                "role": "system",
                "content": (
                    "Ты нутрициолог. Верни строго JSON без markdown и текста. "
                    "Формат ответа: "
                    '{"items":[{"name":"...", "grams":123, "kcal":100, "protein":10, "fat":5, "carbs":12}],'
                    '"total":{"kcal":200,"protein":20,"fat":10,"carbs":24}}. '
                    "Обязательно поля items и total. "
                    "Для КАЖДОГО элемента в items укажи grams, kcal, protein, fat, carbs числами (не null, не строки). "
                    "Если нет точных данных — оцени приблизительно, но не оставляй поля пустыми."
                ),
            },
            {"role": "user", "content": text},
        ]
        try:
            content = self._request_chat_completion(messages)
            elapsed_ms = int((time.perf_counter() - started) * 1000)
            logger.info("GigaChat: successful request in %sms", elapsed_ms)
            return content
        except GigaChatServiceError:
            raise
        except Exception as exc:  # pragma: no cover - network/SDK-level safety
            message = str(exc).lower()
            if any(token in message for token in ("timeout", "timed out", "429", "rate", "network", "connection")):
                raise GigaChatServiceTemporaryError(str(exc)) from exc
            raise GigaChatServiceError(str(exc)) from exc

    def analyze_activity_prompt(self, prompt: str) -> str:
        """Отправляет промпт анализа активности в GigaChat и возвращает текстовый ответ."""
        if not prompt:
            raise ValueError("Prompt is empty")

        started = time.perf_counter()
        messages = [
            {"role": "system", "content": "Ты фитнес-ассистент. Следуй инструкциям пользователя и верни только итоговый анализ."},
            {"role": "user", "content": prompt},
        ]
        try:
            content = self._request_chat_completion(messages)
            elapsed_ms = int((time.perf_counter() - started) * 1000)
            logger.info("GigaChat: successful activity analysis request in %sms", elapsed_ms)
            return content
        except GigaChatServiceError:
            raise
        except Exception as exc:  # pragma: no cover - network/SDK-level safety
            message = str(exc).lower()
            if any(token in message for token in ("timeout", "timed out", "429", "rate", "network", "connection")):
                raise GigaChatServiceTemporaryError(str(exc)) from exc
            raise GigaChatServiceError(str(exc)) from exc

    def _request_chat_completion(self, messages: list[dict[str, Any]]) -> str:
        token = self._get_access_token()
        response = self._chat_completion(token, messages)

        if response.status_code in (401, 403):
            logger.warning("GigaChat: auth error from model API, force refresh token and retry once")
            token = self._get_access_token(force_refresh=True)
            response = self._chat_completion(token, messages)

        if response.status_code >= 400:
            logger.error("GigaChat: request error status=%s body=%s", response.status_code, response.text[:1000])
            if response.status_code in (429, 500, 502, 503, 504):
                raise GigaChatServiceTemporaryError("GigaChat temporary unavailable")
            raise GigaChatServiceError("GigaChat request failed")

        payload = response.json()
        content = (
            (((payload.get("choices") or [None])[0] or {}).get("message") or {}).get("content")
            if isinstance(payload, dict)
            else None
        )
        if not content:
            logger.error("GigaChat: empty response payload=%s", str(payload)[:1500])
            raise GigaChatServiceTemporaryError("GigaChat returned empty response")
        return str(content).strip()

    def _chat_completion(self, token: str, messages: list[dict[str, Any]]) -> requests.Response:
        try:
            return requests.post(
                f"{GIGACHAT_API_URL.rstrip('/')}/chat/completions",
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": GIGACHAT_MODEL,
                    "messages": messages,
                    "temperature": 0.1,
                },
                timeout=_CHAT_REQUEST_TIMEOUT_SECONDS,
                verify=False,
            )
        except requests.RequestException as exc:
            logger.error("GigaChat: request exception=%s", exc)
            raise GigaChatServiceTemporaryError(str(exc)) from exc

    def _get_access_token(self, force_refresh: bool = False) -> str:
        if not GIGACHAT_API_KEY:
            raise GigaChatServiceConfigError("GIGACHAT_API_KEY is not configured")

        with self._token_lock:
            now = time.time()
            if (
                not force_refresh
                and self._token
                and now < (self._token_expires_at - _TOKEN_REFRESH_MARGIN_SECONDS)
            ):
                return self._token

            if force_refresh:
                logger.info("GigaChat: forced access token refresh")

            token, expires_at_ts = self._fetch_access_token()
            self._token = token
            self._token_expires_at = expires_at_ts
            return token

    def _fetch_access_token(self) -> tuple[str, float]:
        rq_uid = str(uuid.uuid4())
        try:
            response = requests.post(
                GIGACHAT_OAUTH_URL,
                headers={
                    "Authorization": f"Basic {GIGACHAT_API_KEY}",
                    "Content-Type": "application/x-www-form-urlencoded",
                    "Accept": "application/json",
                    "RqUID": rq_uid,
                },
                data={"scope": _TOKEN_SCOPE},
                timeout=_OAUTH_REQUEST_TIMEOUT_SECONDS,
                verify=False,
            )
        except requests.RequestException as exc:
            logger.error("GigaChat: access token request failed, request_exception=%s", exc)
            raise GigaChatServiceTemporaryError("Failed to fetch GigaChat access token") from exc

        if response.status_code >= 400:
            logger.error(
                "GigaChat: access token request failed status=%s body=%s",
                response.status_code,
                response.text[:1000],
            )
            if response.status_code in (429, 500, 502, 503, 504):
                raise GigaChatServiceTemporaryError("Failed to fetch GigaChat access token")
            raise GigaChatServiceError("Failed to fetch GigaChat access token")

        payload = response.json()
        access_token = payload.get("access_token")
        if not access_token:
            logger.error("GigaChat: access token is empty payload=%s", str(payload)[:1500])
            raise GigaChatServiceError("Failed to fetch GigaChat access token")

        expires_at_ts = self._parse_expires_at(payload)
        logger.info("GigaChat: access token received successfully, expires_at_ts=%s", int(expires_at_ts))
        return str(access_token), expires_at_ts

    @staticmethod
    def _parse_expires_at(payload: dict[str, Any]) -> float:
        expires_at = payload.get("expires_at")
        if isinstance(expires_at, (int, float)):
            return float(expires_at) / 1000 if float(expires_at) > 10_000_000_000 else float(expires_at)
        if isinstance(expires_at, str):
            raw = expires_at.strip()
            try:
                return float(raw) / 1000 if float(raw) > 10_000_000_000 else float(raw)
            except ValueError:
                try:
                    if raw.endswith("Z"):
                        raw = raw.replace("Z", "+00:00")
                    dt = datetime.fromisoformat(raw)
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=timezone.utc)
                    return dt.timestamp()
                except ValueError:
                    pass

        expires_in = payload.get("expires_in")
        if isinstance(expires_in, (int, float)):
            return time.time() + float(expires_in)

        return time.time() + _DEFAULT_TOKEN_TTL_SECONDS


gigachat_service = GigaChatService()
