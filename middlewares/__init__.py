"""Project middlewares."""

from .onboarding import OnboardingMiddleware
from .user_activity import UserActivityMiddleware

__all__ = ["OnboardingMiddleware", "UserActivityMiddleware"]
