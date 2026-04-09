import os

os.environ.setdefault("API_TOKEN", "test-token")
os.environ.setdefault("GEMINI_API_KEY", "test-gemini-key")

from services.gemini_service import GeminiService


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
