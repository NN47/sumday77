import os

os.environ.setdefault("API_TOKEN", "test-token")
os.environ.setdefault("GEMINI_API_KEY", "test-gemini-key")

from services.gemini_service import GeminiService, GeminiServiceNoAvailableAccountError


def _service_stub() -> GeminiService:
    svc = GeminiService.__new__(GeminiService)
    svc.backoff_schedule = [2, 4, 8]
    svc.backoff_jitter_seconds = 0.0
    return svc


def test_classify_temporary_error_503() -> None:
    svc = _service_stub()
    assert svc.classify_gemini_error(Exception("503 UNAVAILABLE: service overloaded")) == "temporary"


def test_classify_quota_error_429() -> None:
    svc = _service_stub()
    assert svc.classify_gemini_error(Exception("429 RESOURCE_EXHAUSTED quota exceeded")) == "quota"


def test_classify_auth_error_401() -> None:
    svc = _service_stub()
    assert svc.classify_gemini_error(Exception("401 unauthorized invalid API key")) == "auth"


def test_should_retry_only_temporary() -> None:
    svc = _service_stub()
    assert svc.should_retry("temporary") is True
    assert svc.should_retry("quota") is False
    assert svc.should_retry("auth") is False
    assert svc.should_retry("unknown") is False


def test_backoff_schedule() -> None:
    svc = _service_stub()
    assert svc.get_backoff_delay(1) == 2.0
    assert svc.get_backoff_delay(2) == 4.0
    assert svc.get_backoff_delay(3) == 8.0
    assert svc.get_backoff_delay(5) == 8.0


def test_classify_failed_precondition_location_as_explicit_non_retryable_error() -> None:
    svc = _service_stub()
    error = Exception("400 FAILED_PRECONDITION User location is not supported for the API use.")

    error_type = svc.classify_gemini_error(error)

    assert error_type == "location"
    assert svc.should_retry(error_type) is False


def test_execute_reports_when_no_configured_account_is_available(monkeypatch) -> None:
    svc = GeminiService.__new__(GeminiService)
    svc.model = "gemini-2.5-flash"
    svc._ensure_accounts_synced = lambda: None
    svc._select_next_available_key = lambda **_kwargs: None
    finished = []

    monkeypatch.setattr(
        "services.gemini_service.GeminiRepository.log_user_request_started",
        lambda **_kwargs: None,
    )
    monkeypatch.setattr(
        "services.gemini_service.GeminiRepository.get_active_account",
        lambda: None,
    )
    monkeypatch.setattr(
        "services.gemini_service.GeminiRepository.log_user_request_finished",
        lambda **kwargs: finished.append(kwargs),
    )

    try:
        svc.execute_gemini_request_with_failover(lambda: None)
    except GeminiServiceNoAvailableAccountError as error:
        assert error.code == "no_available_account"
    else:
        raise AssertionError("GeminiServiceNoAvailableAccountError was not raised")

    assert finished == [
        {
            "status": "request_finished_failed",
            "model_name": "gemini-2.5-flash",
            "attempts": 0,
            "retries": 0,
            "error_type": "unavailable",
            "error_message": "no_available_account",
        }
    ]
