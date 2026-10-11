"""Local X / Instagram collection, independent of storage and AI providers."""
from .common import SocialError
from .session import capabilities, login
from .collector import collect

__all__ = ["SocialError", "capabilities", "login", "collect"]
