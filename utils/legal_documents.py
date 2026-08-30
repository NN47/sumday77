"""Single source of document versions, public texts and support contact."""
from dataclasses import dataclass
from pathlib import Path

LEGAL_VERSION = "2026-08-31"
LEGAL_UPDATED_DATE = "31 августа 2026 года"
SUPPORT_CONTACT = "@nik_nickname7"
SUPPORT_URL = f"https://t.me/{SUPPORT_CONTACT.lstrip('@')}"
TERMS_BUTTON = "⚖️ Пользовательское соглашение"
PRIVACY_BUTTON = "🔒 Политика конфиденциальности"


@dataclass(frozen=True)
class LegalDocument:
    key: str
    title: str
    filename: str
    version: str = LEGAL_VERSION

    def read(self) -> str:
        path = Path(__file__).resolve().parents[1] / "docs" / "legal" / self.filename
        return (path.read_text(encoding="utf-8")
                .replace("{updated_date}", LEGAL_UPDATED_DATE)
                .replace("{support_contact}", SUPPORT_CONTACT))


LEGAL_DOCUMENTS = {
    "terms": LegalDocument("terms", TERMS_BUTTON, "user-agreement.txt"),
    "privacy": LegalDocument("privacy", PRIVACY_BUTTON, "data-policy.txt"),
}


def has_current_acceptance(user) -> bool:
    """Policy acknowledgement is recorded separately from acceptance of the offer."""
    return bool(
        user is not None
        and user.accepted_terms_version == LEGAL_DOCUMENTS["terms"].version
        and user.acknowledged_privacy_version == LEGAL_DOCUMENTS["privacy"].version
        and user.terms_accepted_at is not None
        and user.privacy_acknowledged_at is not None
    )
