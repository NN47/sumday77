"""Детерминированная preflight-классификация и postflight-проверка AI-чата."""
from enum import StrEnum
import re


class ChatSafetyCategory(StrEnum):
    ALLOWED = "ALLOWED"
    MEDICAL_RESTRICTED = "MEDICAL_RESTRICTED"
    OUT_OF_SCOPE = "OUT_OF_SCOPE"
    INVALID = "INVALID"


INJECTION = re.compile(r"игнорир\w* (?:все |предыдущ\w* )?инструкц|забудь (?:все )?правил|обычн\w+ chatgpt|системн\w+ (?:промпт|инструкц)|system prompt|api.?key|раскрой.*контекст", re.I)
MEDICAL = re.compile(r"диагноз|симптом|болит|головокруж|лечи|лечение|таблет|лекарств|дозиров|инсулин|болезн|врач", re.I)
DANGEROUS = re.compile(r"(?:300|[0-2]\d{2})\s*ккал|голода(?:ть|ние).*(?:дн|нед)|вызвать рвот|анорекс|булими|не есть", re.I)
DOMAIN = re.compile(r"питан|дневник|кбжу|калори|белк|жир|углевод|продукт|блюд|рецепт|ел\b|ела\b|съе|рацион|активност|трениров|шаг|вес|цель|недел|сегодня|вчера|прошл", re.I)
OUTSIDE = re.compile(r"политик|программир|код\b|отношени|истори[яию]|диплом|фильм|анекдот|домашн.*задан|стих", re.I)


def classify_question(text: str) -> ChatSafetyCategory:
    normalized = (text or "").strip()
    if len(normalized) < 3 or len(normalized) > 2000 or INJECTION.search(normalized):
        return ChatSafetyCategory.INVALID
    if MEDICAL.search(normalized) or DANGEROUS.search(normalized):
        return ChatSafetyCategory.MEDICAL_RESTRICTED
    if OUTSIDE.search(normalized) or not DOMAIN.search(normalized):
        return ChatSafetyCategory.OUT_OF_SCOPE
    return ChatSafetyCategory.ALLOWED


def validate_answer(answer: str, context: dict) -> bool:
    text = (answer or "").strip()
    if not text or len(text) > 5000 or INJECTION.search(text):
        return False
    if re.search(r"вам следует принимать|дозировк|поставлю диагноз|системный промпт|api.?key", text, re.I):
        return False
    # Числа допустимы только если приложение действительно передало данные.
    if re.search(r"\d", text) and not context.get("has_numeric_data"):
        return False
    return True
