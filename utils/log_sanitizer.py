"""Privacy-safe helpers for application and provider logging."""
from __future__ import annotations

import copy
import logging
import os
import re
from types import TracebackType
from typing import Any, Mapping


REDACTED_CONTENT = "[REDACTED_CONTENT]"
REDACTED_SECRET = "[REDACTED_SECRET]"
REDACTED_BINARY = "[REDACTED_BINARY_DATA]"
REDACTED_ERROR = "error_detail_redacted"

_IDENTIFIER_RE = re.compile(r"^[\w.:/-]{1,128}$", re.UNICODE)
_LOG_LABEL_RE = re.compile(r"^[a-z][a-z0-9_.:/-]{0,127}$")
_TECHNICAL_CODE_RE = re.compile(r"^[a-z][a-z0-9_.:-]{0,79}$")
_EXCEPTION_SUMMARY_RE = re.compile(
    r"^[A-Za-z_][A-Za-z0-9_.]*(?:Error|Exception|Timeout)"
    r"(?: status=\d{3})?(?: code=[\w.:-]+)?$"
)
_SENSITIVE_KEY_PARTS = (
    "api_key",
    "authorization",
    "base64",
    "body",
    "comment",
    "content",
    "image",
    "message",
    "password",
    "photo",
    "prompt",
    "query",
    "request",
    "response",
    "secret",
    "text",
    "token",
)
_SAFE_METADATA_KEYS = {
    "attempt",
    "attempts",
    "code",
    "confidence",
    "delay_seconds",
    "error_type",
    "feature",
    "handler",
    "http_status",
    "image_bytes",
    "input_chars",
    "input_tokens",
    "latency_ms",
    "message_length",
    "mime_type",
    "model",
    "operation",
    "output_chars",
    "output_tokens",
    "parse_mode",
    "part_index",
    "parts_count",
    "provider",
    "response_id",
    "retries",
    "status",
    "stage",
    "total_tokens",
}


def content_length(value: Any) -> int:
    """Returns a diagnostic size without preserving the value itself."""
    if value is None:
        return 0
    if isinstance(value, (bytes, bytearray, memoryview, str, list, tuple, set, dict)):
        return len(value)
    return len(str(value))


def sanitize_identifier(value: Any, *, fallback: str | None = None) -> str | None:
    """Keeps only short operation-like identifiers suitable for indexed log fields."""
    if value is None:
        return fallback
    text = str(value).strip()
    if _IDENTIFIER_RE.fullmatch(text):
        return text
    return fallback


def sanitize_log_label(value: Any, *, fallback: str | None = None) -> str | None:
    """Keeps lowercase machine labels and rejects likely free-form user content."""
    if value is None:
        return fallback
    text = str(value).strip()
    return text if _LOG_LABEL_RE.fullmatch(text) else fallback


def sanitize_user_id(value: Any) -> str | None:
    """Keeps a Telegram-style numeric identifier and drops accidental names/text."""
    if value is None:
        return None
    text = str(value).strip()
    return text if re.fullmatch(r"-?\d{1,20}", text) else None


def _exception_status(error: BaseException) -> int | str | None:
    for candidate in (
        getattr(error, "status_code", None),
        getattr(error, "status", None),
        getattr(getattr(error, "response", None), "status_code", None),
    ):
        if isinstance(candidate, int) and 100 <= candidate <= 599:
            return candidate
        if isinstance(candidate, str) and candidate.isdigit() and 100 <= int(candidate) <= 599:
            return candidate
    return None


def safe_exception_summary(error: BaseException) -> str:
    """Returns exception type plus bounded technical attributes, never ``str(error)``."""
    parts = [type(error).__name__]
    status = _exception_status(error)
    if status is not None:
        parts.append(f"status={status}")
    code = sanitize_identifier(getattr(error, "code", None))
    if code:
        parts.append(f"code={code}")
    return " ".join(parts)


def safe_error_code(value: BaseException | str | None) -> str | None:
    """Normalizes persisted error details to a diagnostic code without raw API content."""
    if value is None:
        return None
    if isinstance(value, BaseException):
        return safe_exception_summary(value)

    text = redact_sensitive_text(str(value), max_length=512).strip()
    lowered = text.lower()
    if not lowered:
        return None
    if "timeout" in lowered or "timed out" in lowered:
        return "timeout"
    if "invalid json" in lowered or "non-object json" in lowered:
        return "invalid_json"
    if "empty" in lowered and "response" in lowered:
        return "empty_response"
    if "not configured" in lowered or "не настроен" in lowered:
        return "configuration_error"
    if "429" in lowered or "rate limit" in lowered or "quota" in lowered:
        return "rate_limit"
    if "401" in lowered or "403" in lowered or "auth" in lowered:
        return "authentication_error"
    if any(code in lowered for code in ("500", "502", "503", "504")):
        return "upstream_server_error"
    if _EXCEPTION_SUMMARY_RE.fullmatch(text):
        return text
    if _TECHNICAL_CODE_RE.fullmatch(text):
        return text
    return REDACTED_ERROR


def sanitize_metadata(metadata: Mapping[str, Any] | None) -> dict[str, Any] | None:
    """Keeps an allowlist of non-content AI/error metadata and content lengths only."""
    if not metadata:
        return None

    sanitized: dict[str, Any] = {}
    for raw_key, value in metadata.items():
        key = str(raw_key).strip().lower()
        if key in _SAFE_METADATA_KEYS:
            if value is None or isinstance(value, (bool, int, float)):
                sanitized[key] = value
            else:
                safe_value = redact_sensitive_text(str(value), max_length=160)
                sanitized[key] = safe_value
            continue
        if any(part in key for part in _SENSITIVE_KEY_PARTS):
            sanitized[f"{key}_length"] = content_length(value)
    return sanitized or None


def safe_traceback(
    error: BaseException,
    traceback_object: TracebackType | None = None,
) -> str:
    """Formats stack locations and exception type without exception text or local values."""
    frames = []
    current = traceback_object or error.__traceback__
    while current is not None:
        code = current.tb_frame.f_code
        frames.append(f'  File "{code.co_filename}", line {current.tb_lineno}, in {code.co_name}')
        current = current.tb_next
    lines = ["Traceback (most recent call last):", *frames, type(error).__name__]
    return "\n".join(lines)


def sanitize_traceback_text(value: str | None) -> str | None:
    """Defensively strips exception messages and source snippets from a supplied traceback."""
    if not value:
        return None
    safe_lines: list[str] = []
    for line in str(value).splitlines():
        stripped = line.strip()
        if stripped.startswith("Traceback (") or stripped.startswith("During handling"):
            safe_lines.append(stripped)
        elif stripped.startswith('File "'):
            safe_lines.append(redact_sensitive_text(line, max_length=500))
        else:
            error_type = stripped.split(":", 1)[0]
            if re.fullmatch(r"[A-Za-z_][\w.]*(?:Error|Exception|Timeout)", error_type):
                safe_lines.append(error_type)
    return "\n".join(safe_lines)[:4000] or None


def _environment_secrets() -> list[str]:
    markers = ("KEY", "TOKEN", "SECRET", "PASSWORD", "AUTH")
    values = []
    for name, value in os.environ.items():
        if any(marker in name.upper() for marker in markers) and len(value or "") >= 6:
            values.append(value)
    return sorted(set(values), key=len, reverse=True)


def redact_sensitive_text(value: Any, *, max_length: int = 4000) -> str:
    """Redacts credentials, encoded images and labelled prompt/response content."""
    text = str(value or "")
    for secret in _environment_secrets():
        text = text.replace(secret, REDACTED_SECRET)

    text = re.sub(
        r"(?i)data:[a-z0-9.+-]+/[a-z0-9.+-]+;base64,[a-z0-9+/=\r\n]+",
        REDACTED_BINARY,
        text,
    )
    text = re.sub(r"(?<![\w/+=])[A-Za-z0-9+/]{160,}={0,2}(?![\w/+=])", REDACTED_BINARY, text)
    text = re.sub(
        r"(?i)\b(authorization)\s*[:=]\s*(?:bearer|basic)?\s*[^\s,;]+",
        rf"\1={REDACTED_SECRET}",
        text,
    )
    text = re.sub(
        r"(?i)\b(api[_-]?key|access[_-]?token|bot[_-]?token|password|secret)\b"
        r"(\s*[:=]\s*)[\"']?[^\s,;\"']+",
        rf"\1\2{REDACTED_SECRET}",
        text,
    )
    text = re.sub(
        r'(?i)([\"\'](?:prompt|response|query|message|message_text|content|body|image_url|base64)[\"\']\s*:\s*)'
        r'([\"\']).*?\2',
        rf"\1\2{REDACTED_CONTENT}\2",
        text,
    )
    text = re.sub(
        r"(?i)\b(input text|prompt|response|query|message_text|content|body|base64)\s*[:=]\s*.*$",
        lambda match: f"{match.group(1)}={REDACTED_CONTENT}",
        text,
    )
    if len(text) > max_length:
        text = f"{text[:max_length]}…[TRUNCATED]"
    return text


def _safe_log_argument(value: Any) -> Any:
    if isinstance(value, BaseException):
        return safe_exception_summary(value)
    if isinstance(value, (bytes, bytearray, memoryview)):
        return f"<binary bytes={len(value)}>"
    if isinstance(value, Mapping):
        return sanitize_metadata(value) or f"<mapping keys={len(value)}>"
    if isinstance(value, (list, tuple, set, frozenset)):
        return f"<{type(value).__name__} count={len(value)}>"
    if isinstance(value, str):
        return redact_sensitive_text(value)
    return value


class PrivacySafeFormatter(logging.Formatter):
    """Formatter shared by stdout and file handlers with privacy-safe tracebacks."""

    def format(self, record: logging.LogRecord) -> str:
        safe_record = copy.copy(record)
        if isinstance(record.args, Mapping):
            safe_record.args = {key: _safe_log_argument(value) for key, value in record.args.items()}
        elif isinstance(record.args, tuple):
            safe_record.args = tuple(_safe_log_argument(value) for value in record.args)
        else:
            safe_record.args = _safe_log_argument(record.args)
        safe_record.exc_text = None
        rendered = super().format(safe_record)
        return redact_sensitive_text(rendered)

    def formatException(self, exc_info) -> str:  # noqa: N802 - logging API
        error = exc_info[1]
        if not isinstance(error, BaseException):
            return "Exception"
        return safe_traceback(error, exc_info[2])

    def formatStack(self, stack_info: str) -> str:  # noqa: N802 - logging API
        return sanitize_traceback_text(stack_info) or "Stack trace redacted"
