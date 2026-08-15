"""Локальная проверка свободного текста еды до отправки внешнему AI."""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from enum import Enum
from typing import Pattern


class SensitiveDataType(str, Enum):
    """Техническая категория риска без фрагментов пользовательского текста."""

    PHONE = "phone"
    EMAIL = "email"
    DOCUMENT = "document"
    PERSONAL_IDENTITY = "personal_identity"
    MEDICAL = "medical"
    ADDRESS = "address"


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
    r"\bснилс\b(?:\s*[:№]?\s*\d{3}[- ]?\d{3}[- ]?\d{3}[- ]?\d{2})?",
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

ADDRESS_PATTERNS = _compile(
    r"\bмой\s+адрес\b",
    r"\bадрес\s+проживания\b",
    r"\bживу\s+по\s+адресу\b",
    r"\bпрописан(?:а)?\s+по\s+адресу\b",
    r"\bместо\s+жительства\b",
)

MEDICAL_DISEASE_PATTERNS = _compile(
    r"\bдиагноз\w*\b",
    r"\bзаболеван\w*\b",
    r"\bболезн\w*\b",
    r"\bболею\b",
    r"\bдиабет\w*\b",
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
    r"\bдоктор\w*\b",
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


def _normalize_text(text: str) -> str:
    normalized = unicodedata.normalize("NFKC", text).replace("ё", "е").replace("Ё", "Е")
    return re.sub(r"\s+", " ", normalized).strip()


def _matches_any(text: str, patterns: tuple[Pattern[str], ...]) -> bool:
    return any(pattern.search(text) is not None for pattern in patterns)


def check_sensitive_meal_text(text: str) -> SensitiveMealTextCheck:
    """Проверяет копию текста локально и не возвращает найденные фрагменты."""
    normalized_original = _normalize_text(str(text or ""))
    normalized = normalized_original.casefold()

    checks = (
        (SensitiveDataType.PHONE, PHONE_PATTERNS, normalized),
        (SensitiveDataType.EMAIL, EMAIL_PATTERNS, normalized),
        (SensitiveDataType.DOCUMENT, DOCUMENT_PATTERNS, normalized),
        (SensitiveDataType.PERSONAL_IDENTITY, PERSONAL_IDENTITY_PATTERNS, normalized),
        (SensitiveDataType.PERSONAL_IDENTITY, PERSONAL_NAME_PATTERNS, normalized_original),
        (SensitiveDataType.ADDRESS, ADDRESS_PATTERNS, normalized),
        (SensitiveDataType.MEDICAL, MEDICAL_STRONG_PATTERNS, normalized),
        (SensitiveDataType.MEDICAL, MEDICAL_PHRASE_PATTERNS, normalized),
        (SensitiveDataType.MEDICAL, MEDICAL_CONTEXT_PATTERNS, normalized),
    )
    for reason, patterns, candidate in checks:
        if _matches_any(candidate, patterns):
            return SensitiveMealTextCheck(is_sensitive=True, reason=reason)
    return SensitiveMealTextCheck(is_sensitive=False)
