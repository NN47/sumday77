from __future__ import annotations

from contextlib import contextmanager
from io import StringIO
import json
import logging
from types import SimpleNamespace
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database.models import AIUsageLog, Base, ErrorLog
from database.account_deletion import delete_user_account
from database.repositories.error_log_repository import ErrorLogRepository
from database.repositories.gemini_repository import GeminiRepository
from services import ai_usage_logger as ai_usage_module
from services import deepseek_service as deepseek_module
from utils.log_sanitizer import PrivacySafeFormatter
from utils.logging_config import create_bot_log_handler


PRIVATE_TEXT = "PRIVATE_USER_TEXT_12345"
PRIVATE_SECRET = "PRIVATE_API_SECRET_67890"


def _session_provider(session_factory):
    @contextmanager
    def provider():
        session = session_factory()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    return provider


def test_file_and_stdout_formatters_redact_content_secrets_and_exception_details(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("TEST_PROVIDER_API_KEY", PRIVATE_SECRET)
    log_path = tmp_path / "bot.log"
    stream = StringIO()
    formatter = PrivacySafeFormatter("%(levelname)s - %(message)s")
    file_handler = logging.FileHandler(log_path, encoding="utf-8")
    stream_handler = logging.StreamHandler(stream)
    file_handler.setFormatter(formatter)
    stream_handler.setFormatter(formatter)

    logger = logging.getLogger("tests.logging_privacy.output")
    logger.handlers = [file_handler, stream_handler]
    logger.setLevel(logging.INFO)
    logger.propagate = False
    try:
        logger.info("message_text=%s", PRIVATE_TEXT)
        try:
            raise RuntimeError(f"{PRIVATE_TEXT}; api_key={PRIVATE_SECRET}")
        except RuntimeError as exc:
            logger.exception("Provider request failed error=%s", exc)
    finally:
        file_handler.close()
        stream_handler.close()
        logger.handlers = []

    for output in (log_path.read_text(encoding="utf-8"), stream.getvalue()):
        assert PRIVATE_TEXT not in output
        assert PRIVATE_SECRET not in output
        assert "RuntimeError" in output
        assert "[REDACTED_CONTENT]" in output


def test_successful_account_deletion_logs_fact_without_telegram_user_id(tmp_path) -> None:
    telegram_user_id = "987654321012345678"
    log_path = tmp_path / "bot.log"
    stdout = StringIO()
    formatter = PrivacySafeFormatter("%(levelname)s - %(message)s")
    file_handler = logging.FileHandler(log_path, encoding="utf-8")
    stdout_handler = logging.StreamHandler(stdout)
    file_handler.setFormatter(formatter)
    stdout_handler.setFormatter(formatter)

    deletion_logger = logging.getLogger("database.account_deletion")
    original_handlers = deletion_logger.handlers[:]
    original_level = deletion_logger.level
    original_propagate = deletion_logger.propagate
    deletion_logger.handlers = [file_handler, stdout_handler]
    deletion_logger.setLevel(logging.INFO)
    deletion_logger.propagate = False

    @contextmanager
    def successful_session_provider():
        yield object()

    try:
        with patch("database.account_deletion.delete_user_account_data") as delete_data:
            assert delete_user_account(
                telegram_user_id,
                session_provider=successful_session_provider,
            ) is True
        delete_data.assert_called_once()
    finally:
        file_handler.close()
        stdout_handler.close()
        deletion_logger.handlers = original_handlers
        deletion_logger.setLevel(original_level)
        deletion_logger.propagate = original_propagate

    for output in (log_path.read_text(encoding="utf-8"), stdout.getvalue()):
        assert telegram_user_id not in output
        assert "Account deletion completed successfully" in output


def test_rotating_bot_log_is_bounded_and_privacy_sanitized(tmp_path) -> None:
    log_path = tmp_path / "bot.log"
    handler = create_bot_log_handler(log_path, max_bytes=220, backup_count=2)
    logger = logging.getLogger("tests.logging_privacy.rotation")
    logger.handlers = [handler]
    logger.setLevel(logging.INFO)
    logger.propagate = False
    try:
        for index in range(30):
            logger.info("operation=rotation index=%s message_text=%s", index, PRIVATE_TEXT)
    finally:
        handler.close()
        logger.handlers = []

    log_files = sorted(tmp_path.glob("bot.log*"))
    assert log_path.exists()
    assert (tmp_path / "bot.log.1").exists()
    assert 2 <= len(log_files) <= 3

    combined_output = "\n".join(path.read_text(encoding="utf-8") for path in log_files)
    assert PRIVATE_TEXT not in combined_output
    assert "[REDACTED_CONTENT]" in combined_output


def test_error_log_repository_does_not_persist_raw_content(monkeypatch) -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr(
        "database.repositories.error_log_repository.get_db_session",
        _session_provider(Session),
    )

    ErrorLogRepository.log_error(
        source="telegram",
        error_type="ValueError",
        message=PRIVATE_TEXT,
        user_id="12345",
        context=PRIVATE_TEXT,
        severity="error",
        traceback_text=(
            "Traceback (most recent call last):\n"
            '  File "handlers/meals.py", line 10, in handler\n'
            f"ValueError: {PRIVATE_TEXT}"
        ),
        extra={"prompt": PRIVATE_TEXT},
    )

    with Session() as session:
        entry = session.query(ErrorLog).one()
        persisted = " ".join(
            str(value or "")
            for value in (
                entry.message,
                entry.error_message,
                entry.context,
                entry.traceback_text,
            )
        )
        assert PRIVATE_TEXT not in persisted
        assert entry.message == "error_detail_redacted"
        assert entry.context is None
        assert entry.error_type == "ValueError"
        assert entry.user_id == "12345"
        assert "ValueError" in (entry.traceback_text or "")

    engine.dispose()


def test_ai_usage_log_keeps_only_technical_metadata(monkeypatch) -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr(ai_usage_module, "get_db_session", _session_provider(Session))

    ai_usage_module.log_ai_usage(
        provider="deepseek",
        feature="text_meal",
        model="deepseek-chat",
        status="error",
        user_id="12345",
        latency_ms=250,
        input_tokens=12,
        output_tokens=3,
        error_message=PRIVATE_TEXT,
        raw_metadata={
            "prompt": PRIVATE_TEXT,
            "response": PRIVATE_TEXT,
            "message": PRIVATE_TEXT,
            "api_key": PRIVATE_SECRET,
            "response_id": "response_123",
            "input_chars": len(PRIVATE_TEXT),
        },
    )

    with Session() as session:
        entry = session.query(AIUsageLog).one()
        metadata_json = json.dumps(entry.raw_metadata, ensure_ascii=False)
        assert PRIVATE_TEXT not in metadata_json
        assert PRIVATE_SECRET not in metadata_json
        assert PRIVATE_TEXT not in (entry.error_message or "")
        assert entry.error_message == "error_detail_redacted"
        assert entry.raw_metadata["prompt_length"] == len(PRIVATE_TEXT)
        assert entry.raw_metadata["response_length"] == len(PRIVATE_TEXT)
        assert entry.raw_metadata["message_length"] == len(PRIVATE_TEXT)
        assert entry.raw_metadata["input_chars"] == len(PRIVATE_TEXT)
        assert entry.raw_metadata["response_id"] == "response_123"
        assert entry.user_id == "12345"

    engine.dispose()


def test_deepseek_logs_lengths_but_not_prompt_or_response(monkeypatch, caplog) -> None:
    response_text = json.dumps({"items": [{"name": PRIVATE_TEXT}]})
    response = SimpleNamespace(
        id="response_123",
        choices=[SimpleNamespace(message=SimpleNamespace(content=response_text))],
        usage=SimpleNamespace(prompt_tokens=10, completion_tokens=5, total_tokens=15),
    )
    fake_client = SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(create=lambda **_kwargs: response),
        )
    )
    usage_events = []
    service = deepseek_module.DeepSeekService()
    service._client = fake_client
    monkeypatch.setattr(deepseek_module, "DEEPSEEK_API_KEY", "configured-for-test")
    monkeypatch.setattr(deepseek_module, "log_ai_usage", lambda **kwargs: usage_events.append(kwargs))
    caplog.set_level(logging.INFO, logger=deepseek_module.__name__)

    result = service.analyze_food_text(PRIVATE_TEXT, user_id="12345")

    assert result == response_text
    assert PRIVATE_TEXT not in caplog.text
    assert usage_events[0]["raw_metadata"]["input_chars"] == len(PRIVATE_TEXT)
    assert usage_events[0]["raw_metadata"]["output_chars"] == len(response_text)
    assert PRIVATE_TEXT not in json.dumps(usage_events, ensure_ascii=False)


def test_gemini_key_fingerprint_does_not_retain_key_characters() -> None:
    masked = GeminiRepository.mask_api_key(PRIVATE_SECRET)

    assert masked.startswith("sha256:")
    assert PRIVATE_SECRET not in masked
    assert PRIVATE_SECRET[:6] not in masked
