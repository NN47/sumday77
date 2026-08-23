"""Public shared API for context-aware local sensitive-text checks."""
from utils.sensitive_meal_text import (
    SensitiveDataType,
    SensitiveMealTextCheck,
    SensitiveTextPolicy,
    check_sensitive_food_name,
    check_sensitive_meal_text,
    check_sensitive_support_text,
    check_sensitive_text,
)

SensitiveTextCheck = SensitiveMealTextCheck

__all__ = [
    "SensitiveDataType",
    "SensitiveTextCheck",
    "SensitiveTextPolicy",
    "check_sensitive_food_name",
    "check_sensitive_meal_text",
    "check_sensitive_support_text",
    "check_sensitive_text",
]
