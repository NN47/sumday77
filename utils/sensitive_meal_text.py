"""Shared local sensitive-text checks with context-specific policies."""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from enum import Enum
from functools import lru_cache
from typing import Pattern

from natasha import Doc, NewsEmbedding, NewsNERTagger, Segmenter


class SensitiveDataType(str, Enum):
    """Техническая категория риска без фрагментов пользовательского текста."""

    PHONE = "phone"
    EMAIL = "email"
    DOCUMENT = "document"
    PERSONAL_IDENTITY = "personal_identity"
    MEDICAL = "medical"
    ADDRESS = "address"
    CREDENTIAL = "credential"
    BANKING = "banking"


class SensitiveTextPolicy(str, Enum):
    """Context-specific policies for the shared local text check."""

    MEAL = "meal"
    FOOD_NAME = "food_name"
    SUPPORT = "support"


@dataclass(frozen=True)
class SensitiveMealTextCheck:
    """Безопасный результат локальной проверки."""

    is_sensitive: bool
    reason: SensitiveDataType | None = None


def _compile(*patterns: str, ignore_case: bool = True) -> tuple[Pattern[str], ...]:
    flags = re.UNICODE | (re.IGNORECASE if ignore_case else 0)
    return tuple(re.compile(pattern, flags) for pattern in patterns)


PHONE_PATTERNS = _compile(
    r"(?<!\d)(?:\+7|8)\s*(?:\(\s*\d{3}\s*\)|\d{3})[\s-]*\d{3}[\s-]*\d{2}[\s-]*\d{2}(?!\d)",
)

EMAIL_PATTERNS = _compile(
    r"(?<![\w.+-])[a-z0-9.!#$%&'*+/=?^_`{|}~-]+@[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+(?![\w.-])",
)

DOCUMENT_PATTERNS = _compile(
    r"\b(?:мой\s+)?паспорт\b",
    r"\b(?:номер|серия)\s+паспорта\b",
    r"\bпаспорт\s*[:№]\s*\d{2}\s*\d{2}\s*\d{6}\b",
    r"(?<!\d)\d{2}\s+\d{2}\s+\d{6}(?!\d)",
    r"\bснилс\b(?:\s*[:№]?\s*\d{3}[- ]?\d{3}[- ]?\d{3}[- ]?\d{2})?",
    r"(?<!\d)\d{3}-\d{3}-\d{3}[ -]\d{2}(?!\d)",
    r"\bинн\s*[:№]?\s*\d{10,12}\b",
)

PERSONAL_IDENTITY_PATTERNS = _compile(
    r"\bменя\s+зовут\b",
    r"\bмо[её]\s+имя\b",
    r"\bфио\s*:",
    r"\b(?:фамилия|имя|отчество)\s*:",
    r"\bмой\s+(?:номер\s+)?телефон\b",
    r"\bтелефон\s*:",
    r"\bмой\s+(?:email|e-mail)\b",
    r"\bмоя\s+(?:электронная\s+)?почта\b",
)

# Отдельный аккуратный шаблон для явного «я Фамилия Имя Отчество».
PERSONAL_NAME_PATTERNS = _compile(
    r"\bя\s+[А-ЯЁ][а-яё-]+\s+[А-ЯЁ][а-яё-]+\s+[А-ЯЁ][а-яё-]+(?:ович|евич|ич|овна|евна|ична)\b",
    ignore_case=False,
)

DATE_OF_BIRTH_PATTERNS = _compile(
    r"\b(?:дата\s+рождения|д\.\s*р\.)\s*[:=-]?\s*\d{1,2}[./-]\d{1,2}[./-]\d{2,4}\b",
    r"\b(?:родил(?:ся|ась)|рожден(?:а)?)\s+\d{1,2}[./-]\d{1,2}[./-]\d{2,4}\b",
    r"\b(?:родил(?:ся|ась)|рожден(?:а)?)\s+\d{1,2}\s+"
    r"(?:января|февраля|марта|апреля|мая|июня|июля|августа|сентября|октября|ноября|декабря)"
    r"\s+\d{4}\b",
)

ADDRESS_PATTERNS = _compile(
    r"\bмой\s+адрес\b",
    r"\bадрес\s+проживания\b",
    r"\bживу\s+по\s+адресу\b",
    r"\bпроживаю\s+по\s+адресу\b",
    r"\bпрописан(?:а)?\s+по\s+адресу\b",
    r"\bместо\s+жительства\b",
    r"\b(?:ул(?:ица)?\.?|проспект|пр-т|переулок|пер\.?|шоссе|набережная|наб\.?)"
    r"\s+[а-яё0-9 .'-]{2,40}(?:,|\s)\s*(?:д(?:ом)?\.?)\s*№?\s*\d+[а-я]?\b",
    r"\b(?:д(?:ом)?\.?)\s*№?\s*\d+[а-я]?\s*[,;]\s*(?:кв(?:артира)?\.?)\s*№?\s*\d+\b",
)

MEDICAL_DISEASE_PATTERNS = _compile(
    r"\bдиагноз\w*\b",
    r"\bзаболеван\w*\b",
    r"\bболезн\w*\b",
    r"\bболею\b",
    r"\bдиабет(?!ическ)\w*\b",
    r"\bпреддиабет\w*\b",
    r"\bинсулинорезистент\w*\b",
    r"\bгипертон\w*\b",
    r"\bгипотон\w*\b",
    r"\bастм\w*\b",
    r"\bэпилепс\w*\b",
    r"\bанеми\w*\b",
    r"\bартрит\w*\b",
    r"\bартроз\w*\b",
    r"\bгастрит\w*\b",
    r"\bязв\w*\b",
    r"\bпанкреатит\w*\b",
    r"\bхолецистит\w*\b",
    r"\bгепатит\w*\b",
    r"\bцирроз\w*\b",
    r"\b(?:вич|спид)\b",
    r"\bонколог\w*\b",
    r"\bопухол\w*\b",
    r"\bметастаз\w*\b",
    r"\bлейкоз\w*\b",
    r"\bинфаркт\w*\b",
    r"\bинсульт\w*\b",
    r"\bаритми\w*\b",
    r"\bтахикард\w*\b",
    r"\bбрадикард\w*\b",
    r"\bтромбоз\w*\b",
    r"\bатеросклероз\w*\b",
    r"\bмигрен\w*\b",
    r"\bпневмони\w*\b",
    r"\bбронхит\w*\b",
    r"\b(?:орви|грипп\w*|ковид\w*|covid(?:-?19)?|коронавирус\w*)\b",
    r"\bцистит\w*\b",
    r"\bпиелонефрит\w*\b",
    r"\bэндометриоз\w*\b",
    r"\bполикистоз\w*\b",
    r"\bтиреоидит\w*\b",
    r"\bгипотиреоз\w*\b",
    r"\bгипертиреоз\w*\b",
    r"\bаутоиммун\w*\b",
    r"\bаллерги\w*\b",
    r"\bнепереносимост\w*\b",
)

MEDICAL_SYMPTOM_PATTERNS = _compile(
    r"\bболит\w*\b",
    r"\bбол(?:ь|и|ью|ей|ям|ями|ях)\b",
    r"\bлихорад\w*\b",
    r"\bтошнот\w*\b",
    r"\bрвот\w*\b",
    r"\bдиаре\w*\b",
    r"\bпонос\w*\b",
    r"\bзапор\w*\b",
    r"\bголовокруж\w*\b",
    r"\bобморок\w*\b",
    r"\bслабост\w*\b",
    r"\bодышк\w*\b",
    r"\bкашел\w*\b",
    r"\bсып(?:ь|и|ью|ей|ям|ями|ях)\b",
    r"\bзуд\w*\b",
    r"\bотек\w*\b",
    r"\bкровотеч\w*\b",
    r"\bспазм\w*\b",
    r"\bсудорог\w*\b",
    r"\bонемен\w*\b",
    r"\bпульс\w*\b",
    r"\bсатурац\w*\b",
    r"\bсердцебиен\w*\b",
)

MEDICAL_MEDICATION_PATTERNS = _compile(
    r"\bлекарств\w*\b",
    r"\bмедикамент\w*\b",
    r"\bпрепарат\w*\b",
    r"\bтаблет\w*\b",
    r"\bкапсул\w*\b",
    r"\bантибиотик\w*\b",
    r"\bантидепрессант\w*\b",
    r"\bнейролептик\w*\b",
    r"\bтранквилизатор\w*\b",
    r"\bседатив\w*\b",
    r"\bобезболива\w*\b",
    r"\bгормон\w*\b",
    r"\bинсулин\w*\b",
    r"\bинъекц\w*\b",
    r"\bукол\w*\b",
    r"\bкапельниц\w*\b",
    r"\bдозировк\w*\b",
    r"\bдоз(?:а|ы|е|у|ой|ами|ах)\b",
    r"\bназначени\w*\b",
    r"\bназначил\w*\b",
    r"\bпринимаю\b(?!\s+пищу\b)",
    r"\bтерапи\w*\b",
    r"\bлечени\w*\b",
    r"\bлечусь\b",
)

MEDICAL_DOCTOR_PATTERNS = _compile(
    r"\bврач\w*\b",
    r"\bдоктор(?!ск)\w*\b",
    r"\bтерапевт\w*\b",
    r"\bхирург\w*\b",
    r"\bэндокринолог\w*\b",
    r"\bгастроэнтеролог\w*\b",
    r"\bкардиолог\w*\b",
    r"\bневролог\w*\b",
    r"\bневропатолог\w*\b",
    r"\bпсихиатр\w*\b",
    r"\bпсихотерапевт\w*\b",
    r"\bгинеколог\w*\b",
    r"\bуролог\w*\b",
    r"\bдерматолог\w*\b",
    r"\bаллерголог\w*\b",
    r"\bиммунолог\w*\b",
    r"\bпульмонолог\w*\b",
    r"\bгематолог\w*\b",
    r"\bнефролог\w*\b",
    r"\bинфекционист\w*\b",
    r"\bстоматолог\w*\b",
)

MEDICAL_TEST_PATTERNS = _compile(
    r"\bанализ\w*\b",
    r"\bанализ\s+(?:крови|мочи)\b",
    r"\b(?:общий\s+анализ\s+крови|результат\w*\s+анализ\w*)\b",
    r"\b(?:сдал\w*|сдавала?|получил\w*)\s+анализ\w*\b",
    r"\bбиохими\w*\b",
    r"\b(?:оак|оам)\b",
    r"\bглюкоз\w*(?:\s+в)?\s+крови\b",
    r"\bгемоглобин\w*\b",
    r"\bхолестерин\w*\b",
    r"\bферритин\w*\b",
    r"\bлейкоцит\w*\b",
    r"\bэритроцит\w*\b",
    r"\bтромбоцит\w*\b",
    r"\bттг\b",
    r"\b(?:т3|т4)\b",
    r"\b(?:мрт|кт|узи|экг|ээг)\b",
    r"\bрентген\w*\b",
    r"\bфлюорограф\w*\b",
    r"\bбиопси\w*\b",
    r"\bэндоскоп\w*\b",
    r"\bгастроскоп\w*\b",
    r"\bколоноскоп\w*\b",
    r"\bобследован\w*\b",
    r"\bмедосмотр\w*\b",
)

MEDICAL_REPRODUCTIVE_PATTERNS = _compile(
    r"\bберемен\w*\b",
    r"\bменструац\w*\b",
    r"\bмесячн\w*\b",
    r"\bовуляц\w*\b",
    r"\bклимакс\w*\b",
    r"\bменопауз\w*\b",
    r"\bконтрацепц\w*\b",
    r"\bаборт\w*\b",
    r"\bвыкидыш\w*\b",
    r"\bбесплоди\w*\b",
    r"\bэко\b",
    r"\bменструальн\w*\s+цикл\w*\b",
)

MEDICAL_MENTAL_HEALTH_PATTERNS = _compile(
    r"\bдепресси\w*\b",
    r"\bдепрессив\w*\b",
    r"\bтревожност\w*\b",
    r"\bтревожн\w*\s+расстройств\w*\b",
    r"\bпаническ\w*\s+атак\w*\b",
    r"\b(?:птср|окр|сдвг|бар)\b",
    r"\bбиполярн\w*\b",
    r"\bшизофрен\w*\b",
    r"\bпсихоз\w*\b",
    r"\bпсихотерапи\w*\b",
)

MEDICAL_PROCEDURE_PATTERNS = _compile(
    r"\bопераци\w*\b",
    r"\bоперационн\w*\b",
    r"\bхирурги\w*\b",
    r"\bгоспитализац\w*\b",
    r"\bбольниц\w*\b",
    r"\bстационар\w*\b",
    r"\bреанимац\w*\b",
    r"\bпроцедур\w*\b",
    r"\bпереливан\w*\b",
    r"\bхимиотерапи\w*\b",
    r"\bлучев\w*\s+терапи\w*\b",
    r"\bреабилитац\w*\b",
    r"\bмедицинск\w*\s+(?:карт\w*|заключен\w*|справк\w*|данн\w*|информац\w*)\b",
    r"\bмедкарт\w*\b",
    r"\bистори\w*\s+болезн\w*\b",
    r"\bзаключени\w*\s+врач\w*\b",
    r"\bвыписк\w*\s+из\s+больниц\w*\b",
    r"\bэпикриз\w*\b",
    r"\bсправк\w*\s+от\s+врач\w*\b",
    r"\bрецепт\w*\s+врач\w*\b",
)

MEDICAL_PHRASE_PATTERNS = _compile(
    r"\b(?:врач|доктор)\s+назначил\w*\b",
    r"\bмне\s+назначил\w*\b",
    r"\bпринимаю\s+(?:препарат\w*|лекарств\w*|таблет\w*|антибиотик\w*|антидепрессант\w*)\b",
    r"\bпью\s+таблет\w*\b",
    r"\bколю\s+инсулин\w*\b",
    r"\bлечусь\s+от\b",
    r"\bназначенн\w*\s+терапи\w*\b",
    r"\b(?:медицинск\w*|врачебн\w*)\s+рецепт\w*\b",
    r"\bназначенн\w*\s+доз\w*\b",
)

# Неоднозначные слова проверяются только вместе с медицинским контекстом.
MEDICAL_CONTEXT_PATTERNS = _compile(
    r"\b(?:у\s+меня|диагноз\s*[:—-]?|лечусь\s+от)\s+рак(?:а|ом)?\b",
    r"\bрак\s+(?:желудка|груди|легк\w*|кишечник\w*|кожи|крови|простаты|печени|почек)\b",
    r"\bсахар\s+в\s+крови\b",
    r"\b(?:высокий|низкий|повышенн\w*|пониженн\w*)\s+сахар\b",
    r"\bглюкоз\w*\s+крови\b",
    r"\bтемператур\w*\s+тела\b",
    r"\b(?:у\s+меня|поднялась|держится|заболел\w*)\s+температур\w*\b",
    r"\bтемператур\w*\s+(?:3[5-9]|4[0-3])(?:[.,]\d)?\b",
    r"\bдавлени\w*\s+\d{2,3}\s*/\s*\d{2,3}\b",
    r"\b(?:высок\w*|низк\w*|повышенн\w*|пониженн\w*)\s+давлени\w*\b",
    r"\b(?:врач|доктор)\s+измерил\w*\s+давлени\w*\b",
    r"\b(?:у\s+меня|обнаружил\w*|нашли)\s+кровь\b",
    r"\bкровь\s+(?:в|из)\b",
    r"\bиз\s+носа\s+кровь\b",
)

MEDICAL_STRONG_PATTERNS = (
    MEDICAL_DISEASE_PATTERNS
    + MEDICAL_SYMPTOM_PATTERNS
    + MEDICAL_MEDICATION_PATTERNS
    + MEDICAL_DOCTOR_PATTERNS
    + MEDICAL_TEST_PATTERNS
    + MEDICAL_REPRODUCTIVE_PATTERNS
    + MEDICAL_MENTAL_HEALTH_PATTERNS
    + MEDICAL_PROCEDURE_PATTERNS
)


# Support intentionally uses narrower, high-confidence patterns than meal input.
# Mentioning a password, a doctor, an analysis screen, or a procedure is not by
# itself treated as sensitive data.
CREDENTIAL_PATTERNS = _compile(
    r"\b(?:мой\s+)?(?:пароль|password|токен|token|api[-_ ]?key|секрет(?:ный)?\s+ключ)\s*(?:[:=]|[—-])\s*[^\s,;]{4,}\b",
    r"\b(?:код\s+(?:доступа|подтверждения)|одноразовый\s+код|otp)\s*(?:[:=]|[—-])?\s*\d{4,8}\b",
    r"\b(?:bearer|basic)\s+[a-z0-9._~+/=-]{12,}\b",
)

SUPPORT_DOCUMENT_PATTERNS = _compile(
    r"\b(?:паспорт|номер\s+паспорта|серия\s+паспорта)\s*[:№-]?\s*\d{2}\s*\d{2}\s*\d{6}\b",
    r"\bснилс\s*[:№-]?\s*\d{3}[- ]?\d{3}[- ]?\d{3}[- ]?\d{2}\b",
    r"\bинн\s*[:№-]?\s*\d{10,12}\b",
)

BANKING_PATTERNS = _compile(
    r"\b(?:номер\s+)?(?:банковской\s+)?карт(?:ы|а)\s*[:№-]?\s*(?:\d[ -]?){13,19}\b",
    r"\b(?:cvv|cvc)\s*[:=-]?\s*\d{3,4}\b",
    r"\b(?:расч[её]тный\s+сч[её]т|корреспондентский\s+сч[её]т)\s*[:№-]?\s*\d{20}\b",
)

SUPPORT_PERSON_MARKER_PATTERNS = _compile(
    r"\bу\s+меня\b",
    r"\bмо(?:й|я|е|и|его|ей|их)\b",
    r"\bмне\b",
    r"\bя\s+(?:болею|лечусь|принимаю|пью|колю|чувствую)\b",
    r"\b(?:болею|лечусь|принимаю|пью|колю)\b",
    r"\b(?:у|для)\s+(?:сына|дочери|мужа|жены|матери|отца|реб[её]нка)\b",
)

SUPPORT_MEDICAL_DETAIL_PATTERNS = (
    MEDICAL_DISEASE_PATTERNS
    + MEDICAL_SYMPTOM_PATTERNS
    + MEDICAL_MEDICATION_PATTERNS
    + MEDICAL_REPRODUCTIVE_PATTERNS
    + MEDICAL_MENTAL_HEALTH_PATTERNS
    + MEDICAL_PHRASE_PATTERNS
    + MEDICAL_CONTEXT_PATTERNS
    + _compile(
        r"\bрезультат\w*\s+(?:анализ\w*|обследован\w*|мрт|кт|узи|экг|ээг)\b",
        r"\b(?:анализ\w*|мрт|кт|узи|экг|ээг)\s+(?:показал\w*|выявил\w*|обнаружил\w*)\b",
    )
)


def _normalize_text(text: str) -> str:
    normalized = unicodedata.normalize("NFKC", text).replace("ё", "е").replace("Ё", "Е")
    return re.sub(r"\s+", " ", normalized).strip()


def _matches_any(text: str, patterns: tuple[Pattern[str], ...]) -> bool:
    return any(pattern.search(text) is not None for pattern in patterns)


def _check_meal_text(normalized: str, normalized_original: str) -> SensitiveMealTextCheck:
    checks = (
        (SensitiveDataType.PHONE, PHONE_PATTERNS, normalized),
        (SensitiveDataType.EMAIL, EMAIL_PATTERNS, normalized),
        (SensitiveDataType.DOCUMENT, DOCUMENT_PATTERNS, normalized),
        (SensitiveDataType.PERSONAL_IDENTITY, PERSONAL_IDENTITY_PATTERNS, normalized),
        (SensitiveDataType.PERSONAL_IDENTITY, PERSONAL_NAME_PATTERNS, normalized_original),
        (SensitiveDataType.PERSONAL_IDENTITY, DATE_OF_BIRTH_PATTERNS, normalized),
        (SensitiveDataType.ADDRESS, ADDRESS_PATTERNS, normalized),
        (SensitiveDataType.CREDENTIAL, CREDENTIAL_PATTERNS, normalized),
        (SensitiveDataType.BANKING, BANKING_PATTERNS, normalized),
        (SensitiveDataType.MEDICAL, MEDICAL_STRONG_PATTERNS, normalized),
        (SensitiveDataType.MEDICAL, MEDICAL_PHRASE_PATTERNS, normalized),
        (SensitiveDataType.MEDICAL, MEDICAL_CONTEXT_PATTERNS, normalized),
    )
    for reason, patterns, candidate in checks:
        if _matches_any(candidate, patterns):
            return SensitiveMealTextCheck(is_sensitive=True, reason=reason)
    if _contains_payment_card_number(normalized):
        return SensitiveMealTextCheck(is_sensitive=True, reason=SensitiveDataType.BANKING)
    return SensitiveMealTextCheck(is_sensitive=False)


@lru_cache(maxsize=1)
def _food_name_ner() -> tuple[Segmenter, NewsNERTagger]:
    """Load the compact CPU-only Natasha NER model once per process."""
    embedding = NewsEmbedding()
    return Segmenter(), NewsNERTagger(embedding)


def _contains_confident_full_name(text: str) -> bool:
    """Use NER only for high-confidence full names, never for lone food-like names."""
    segmenter, ner_tagger = _food_name_ner()
    doc = Doc(text)
    doc.segment(segmenter)
    doc.tag_ner(ner_tagger)
    patronymic = re.compile(r"(?:ович|евич|ич|овна|евна|ична)$", re.IGNORECASE)
    for span in doc.spans:
        if span.type != "PER":
            continue
        words = re.findall(r"[А-ЯЁа-яё]+(?:-[А-ЯЁа-яё]+)?", span.text)
        if len(words) >= 3 and any(patronymic.search(word) for word in words):
            return True
    return False


def _check_food_name(normalized: str, normalized_original: str) -> SensitiveMealTextCheck:
    base_check = _check_meal_text(normalized, normalized_original)
    if base_check.is_sensitive:
        return base_check
    if _contains_confident_full_name(normalized_original):
        return SensitiveMealTextCheck(
            is_sensitive=True,
            reason=SensitiveDataType.PERSONAL_IDENTITY,
        )
    return SensitiveMealTextCheck(is_sensitive=False)


def _contains_payment_card_number(text: str) -> bool:
    """Detect plausible card numbers with a Luhn check and no retained value."""
    for match in re.finditer(r"(?<!\d)(?:\d[ -]?){13,19}(?!\d)", text):
        digits = re.sub(r"\D", "", match.group(0))
        if not 13 <= len(digits) <= 19 or len(set(digits)) == 1:
            continue
        checksum = 0
        parity = len(digits) % 2
        for index, character in enumerate(digits):
            digit = int(character)
            if index % 2 == parity:
                digit *= 2
                if digit > 9:
                    digit -= 9
            checksum += digit
        if checksum % 10 == 0:
            return True
    return False


def _check_support_text(normalized: str) -> SensitiveMealTextCheck:
    checks = (
        (SensitiveDataType.CREDENTIAL, CREDENTIAL_PATTERNS),
        (SensitiveDataType.DOCUMENT, SUPPORT_DOCUMENT_PATTERNS),
        (SensitiveDataType.BANKING, BANKING_PATTERNS),
    )
    for reason, patterns in checks:
        if _matches_any(normalized, patterns):
            return SensitiveMealTextCheck(is_sensitive=True, reason=reason)

    if _contains_payment_card_number(normalized):
        return SensitiveMealTextCheck(is_sensitive=True, reason=SensitiveDataType.BANKING)

    has_person = _matches_any(normalized, SUPPORT_PERSON_MARKER_PATTERNS)
    has_medical_detail = _matches_any(normalized, SUPPORT_MEDICAL_DETAIL_PATTERNS)
    if has_person and has_medical_detail:
        return SensitiveMealTextCheck(is_sensitive=True, reason=SensitiveDataType.MEDICAL)

    return SensitiveMealTextCheck(is_sensitive=False)


def check_sensitive_text(
    text: str,
    *,
    policy: SensitiveTextPolicy,
) -> SensitiveMealTextCheck:
    """Check text locally without returning or logging matching fragments."""
    normalized_original = _normalize_text(str(text or ""))
    normalized = normalized_original.casefold()
    if policy is SensitiveTextPolicy.MEAL:
        return _check_meal_text(normalized, normalized_original)
    if policy is SensitiveTextPolicy.FOOD_NAME:
        return _check_food_name(normalized, normalized_original)
    if policy is SensitiveTextPolicy.SUPPORT:
        return _check_support_text(normalized)
    raise ValueError(f"Unsupported sensitive-text policy: {policy!r}")


def check_sensitive_meal_text(text: str) -> SensitiveMealTextCheck:
    """Backward-compatible meal-specific wrapper."""
    return check_sensitive_text(text, policy=SensitiveTextPolicy.MEAL)


def check_sensitive_food_name(text: str) -> SensitiveMealTextCheck:
    """Strict local check for user-defined product and dish names."""
    return check_sensitive_text(text, policy=SensitiveTextPolicy.FOOD_NAME)


def check_sensitive_support_text(text: str) -> SensitiveMealTextCheck:
    """High-confidence support-message policy."""
    return check_sensitive_text(text, policy=SensitiveTextPolicy.SUPPORT)
